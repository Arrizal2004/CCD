"""
Open Web — proxy nginx ke alamat privat (mis. web di VM) supaya bisa ditampilkan di iframe
dashboard tanpa terblokir "Local Network Access" browser.

Alur: frontend minta tiket (POST /ticket) → dapat path /openweb/<tiket>/<ip>[:port]/...
nginx memanggil GET /auth (auth_request) untuk tiap request dan hanya meneruskan bila tiket sah.
Izin diputuskan saat tiket dibuat: admin → semua IP privat; student → hanya IP VM miliknya.
"""
import base64, hashlib, hmac, ipaddress, json, os, re, time, uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth import get_current_user, Role, SECRET_KEY
from database import get_pool, get_student_vm_ids
from services.audit import both, log_activity
from i18n import tr

router = APIRouter()

TICKET_TTL = 3600  # detik
_KEY = hashlib.sha256((SECRET_KEY + ":openweb").encode()).digest()  # kunci terpisah dari JWT
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale
_PATH_RE = re.compile(r"^/openweb/([A-Za-z0-9_\-]+)/([0-9.]+)(?::(\d{1,5}))?(?:/|$)")


# Cache status sesi (dicabut/kedaluwarsa) singkat + tulis statistik akses dibatasi frekuensinya,
# supaya tiap request proxy (termasuk aset CSS/JS) tidak membebani database.
_CHECK_TTL = 3.0
_FLUSH_EVERY = 10.0
_cache: dict = {}


class TicketBody(BaseModel):
    url: str


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: bytes) -> str:
    return _b64(payload) + "_" + _b64(hmac.new(_KEY, payload, hashlib.sha256).digest())


def _verify(ticket: str):
    """Kembalikan payload dict bila tanda tangan sah dan belum kedaluwarsa, selain itu None."""
    try:
        p, sig = ticket.split("_", 1)
        payload = _unb64(p)
        if not hmac.compare_digest(_unb64(sig), hmac.new(_KEY, payload, hashlib.sha256).digest()):
            return None
        data = json.loads(payload)
        return data if data.get("e", 0) > time.time() else None
    except Exception:
        return None


def _is_private_target(ip: ipaddress.IPv4Address) -> bool:
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return False
    return ip.is_private or ip in _CGNAT


async def _student_ips(user_id: int) -> set:
    """IP VM yang boleh diakses student: via assignment langsung maupun grup."""
    allowed = await get_student_vm_ids(user_id)
    if not allowed:
        return set()
    pool = await get_pool()
    ips = set()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT vm_id, host_name, ssh_host FROM vm_credentials WHERE ssh_host <> ''")
        ips |= {r["ssh_host"] for r in rows if (r["vm_id"], r["host_name"]) in allowed}
        rows = await conn.fetch(
            """SELECT voa.ssh_host FROM vm_assignments va
               JOIN vm_os_accounts voa ON voa.id = va.os_account_id
               WHERE va.user_id = $1 AND va.deleted_at IS NULL AND voa.ssh_host <> ''""", user_id)
        ips |= {r["ssh_host"] for r in rows}
    return ips


async def touch_session(sid: str, client_ip: str) -> bool:
    """True bila sesi masih sah (ada, belum dicabut, belum kedaluwarsa). Mencatat akses terakhir."""
    now = time.monotonic()
    st = _cache.get(sid)
    if st is None or now - st["checked"] > _CHECK_TTL:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT (revoked_at IS NULL AND expires_at > NOW()) AS ok FROM openweb_sessions WHERE id = $1", sid)
        base = st or {"pending": 0, "flushed": 0.0, "ips": set()}
        st = _cache[sid] = {**base, "ok": bool(row and row["ok"]), "checked": now}
        if len(_cache) > 2000:  # buang entri lama
            for k in [k for k, v in _cache.items() if now - v["checked"] > 7200]:
                _cache.pop(k, None)
    if not st["ok"]:
        return False
    st["pending"] += 1
    new_ip = bool(client_ip) and client_ip not in st["ips"]
    if new_ip:
        st["ips"].add(client_ip)
    if new_ip or now - st["flushed"] >= _FLUSH_EVERY:
        n, st["pending"], st["flushed"] = st["pending"], 0, now
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE openweb_sessions SET hits = hits + $2, last_seen = NOW(), last_ip = $3,
                       client_ips = CASE WHEN $3 = '' OR $3 = ANY(client_ips) THEN client_ips
                                         ELSE array_append(client_ips, $3) END
                   WHERE id = $1""", sid, n, client_ip or "")
    return True


async def list_sessions(active_only: bool, limit: int = 50, offset: int = 0, username: str = "") -> dict:
    where = ["revoked_at IS NULL AND expires_at > NOW()"] if active_only else []
    args: list = []
    if username:
        args.append(username)
        where.append(f"lower(username) = lower(${len(args)})")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM openweb_sessions {clause}", *args)
        rows = await conn.fetch(
            f"""SELECT id, username, role, target_ip, created_at, expires_at, revoked_at, revoked_by,
                       last_seen, hits, last_ip, client_ips,
                       CASE WHEN revoked_at IS NOT NULL THEN 'revoked'
                            WHEN expires_at <= NOW() THEN 'expired' ELSE 'active' END AS status
                FROM openweb_sessions {clause} ORDER BY created_at DESC
                LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}""", *args, limit, offset)
    return {"total": total, "items": [dict(r) for r in rows]}


async def revoke_session(sid: str, by: str) -> dict | None:
    """Cabut sesi. Mengembalikan baris sesi (untuk audit) atau None bila tidak ada / sudah dicabut."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE openweb_sessions SET revoked_at = NOW(), revoked_by = $2
               WHERE id = $1 AND revoked_at IS NULL RETURNING username, role, target_ip""", sid, by)
    _cache.pop(sid, None)
    return dict(row) if row else None


@router.post("/ticket")
async def create_ticket(body: TicketBody, request: Request, user: dict = Depends(get_current_user)):
    u = urlsplit(body.url.strip())
    if u.scheme != "http":
        raise HTTPException(400, tr("Proxy hanya mendukung alamat http:// untuk IP privat",
                                    "The proxy only supports http:// addresses for private IPs"))
    try:
        ip = ipaddress.IPv4Address(u.hostname or "")
        port = u.port
    except ValueError:
        raise HTTPException(400, tr("Alamat harus berupa IP privat yang valid",
                                    "The address must be a valid private IP"))
    if not _is_private_target(ip):
        raise HTTPException(400, tr("Alamat bukan IP privat", "The address is not a private IP"))

    if user["role"] == Role.STUDENT:
        if str(ip) not in await _student_ips(int(user["sub"])):
            raise HTTPException(403, tr("Anda hanya boleh membuka web di VM yang ditugaskan kepada Anda",
                                        "You may only open web pages on VMs assigned to you"))
    elif not Role.has_permission(user["role"], Role.SYSADMIN):
        raise HTTPException(403, tr("Tidak diizinkan", "Not allowed"))

    host = f"{ip}:{port}" if port else str(ip)
    sid = uuid.uuid4().hex
    expires = datetime.now(timezone.utc) + timedelta(seconds=TICKET_TTL)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO openweb_sessions (id, user_id, username, role, target_ip, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            sid, int(user["sub"]), user.get("username") or "", user.get("role") or "", str(ip), expires)
    await log_activity(user, "OPENWEB_OPEN", "INFO", {"id": str(ip), "name": host},
                       both(lambda: tr(f"{user.get('username')} membuka Open Web ke {host} (sesi {sid[:8]})",
                                    f"{user.get('username')} opened Open Web to {host} (session {sid[:8]})")), request)
    ticket = _sign(json.dumps({"h": str(ip), "u": user["sub"], "s": sid, "e": int(expires.timestamp())},
                              separators=(",", ":")).encode())
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    return {"proxy_path": f"/openweb/{ticket}/{host}{path}"}


@router.get("/auth")
async def auth_check(request: Request):
    """Dipanggil nginx (auth_request) untuk tiap request ke /openweb/. 204 = boleh, 403 = tolak."""
    m = _PATH_RE.match(request.headers.get("X-Original-URI", ""))
    data = _verify(m.group(1)) if m else None
    if not data or data.get("h") != m.group(2) or not data.get("s"):
        raise HTTPException(403, tr("Tiket tidak valid atau kedaluwarsa", "Invalid or expired ticket"))
    if not await touch_session(data["s"], request.headers.get("X-Real-IP", "")):
        raise HTTPException(403, tr("Sesi sudah dicabut atau kedaluwarsa",
                                    "The session was revoked or has expired"))
    from fastapi import Response
    return Response(status_code=204)
