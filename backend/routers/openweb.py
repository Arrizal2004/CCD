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

TICKET_TTL = 3600           # masa sesi saat dibuka (detik)
EXTEND_STEP = 3600          # tiap perpanjangan menambah 1 jam
EXTEND_WINDOW = 1800        # perpanjangan hanya boleh bila sisa waktu kurang dari 30 menit
SESSION_MAX = 24 * 3600     # batas umur sebuah sesi; setelah itu buka sesi baru
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


def _parse_target(raw: str):
    """(ip, port, path+query) dari alamat yang diketik; HTTPException 400 bila bukan http:// ke IP privat."""
    u = urlsplit(raw.strip())
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
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    return ip, port, path


async def _authorize(user: dict, ip) -> None:
    """Admin: semua IP privat. Student: hanya IP VM yang ditugaskan kepadanya (dicek lagi tiap membuka atau
    memperpanjang, supaya akses yang sudah dicabut tidak bisa dilanjutkan lewat riwayat)."""
    if user["role"] == Role.STUDENT:
        if str(ip) not in await _student_ips(int(user["sub"])):
            raise HTTPException(403, tr("Anda hanya boleh membuka web di VM yang ditugaskan kepada Anda",
                                        "You may only open web pages on VMs assigned to you"))
    elif not Role.has_permission(user["role"], Role.SYSADMIN):
        raise HTTPException(403, tr("Tidak diizinkan", "Not allowed"))


def _proxy_path(sid: str, ip, port, path: str, created_at: datetime, user_id) -> str:
    """Path proxy untuk sesi ini. Tiket berlaku sampai batas umur sesi; yang menentukan masa berlaku
    sebenarnya adalah baris sesi di database, jadi perpanjangan tidak mengubah path yang sedang terbuka."""
    host = f"{ip}:{port}" if port else str(ip)
    exp = int((created_at + timedelta(seconds=SESSION_MAX)).timestamp())
    ticket = _sign(json.dumps({"h": str(ip), "u": str(user_id), "s": sid, "e": exp}, separators=(",", ":")).encode())
    return f"/openweb/{ticket}/{host}{path}"


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
    ip, port, path = _parse_target(body.url)
    await _authorize(user, ip)

    host = f"{ip}:{port}" if port else str(ip)
    pool = await get_pool()
    # Alamat yang sama dan sesinya masih berlaku: pakai lagi, jangan menumpuk sesi baru.
    async with pool.acquire() as conn:
        same = await conn.fetchrow(
            """SELECT id, created_at, expires_at FROM openweb_sessions
               WHERE user_id = $1 AND url = $2 AND revoked_at IS NULL AND expires_at > NOW()
               ORDER BY created_at DESC LIMIT 1""", int(user["sub"]), body.url.strip()[:2000])
    if same:
        return {"proxy_path": _proxy_path(same["id"], ip, port, path, same["created_at"], user["sub"]),
                "session_id": same["id"], "expires_at": same["expires_at"].isoformat(), "reused": True}
    sid = uuid.uuid4().hex
    created = datetime.now(timezone.utc)
    expires = created + timedelta(seconds=TICKET_TTL)
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO openweb_sessions (id, user_id, username, role, target_ip, url, created_at, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            sid, int(user["sub"]), user.get("username") or "", user.get("role") or "", str(ip),
            body.url.strip()[:2000], created, expires)
    await log_activity(user, "OPENWEB_OPEN", "INFO", {"id": str(ip), "name": host},
                       both(lambda: tr(f"{user.get('username')} membuka Open Web ke {host} (sesi {sid[:8]})",
                                    f"{user.get('username')} opened Open Web to {host} (session {sid[:8]})")), request)
    return {"proxy_path": _proxy_path(sid, ip, port, path, created, user["sub"]),
            "session_id": sid, "expires_at": expires.isoformat()}


_HISTORY_SQL = """SELECT id, url, target_ip, created_at, expires_at,
       CASE WHEN revoked_at IS NOT NULL THEN 'revoked' WHEN expires_at <= NOW() THEN 'expired' ELSE 'active' END AS status,
       GREATEST(EXTRACT(EPOCH FROM (expires_at - NOW())), 0)::int AS remaining
       FROM openweb_sessions WHERE user_id = $1 AND url IS NOT NULL ORDER BY created_at DESC LIMIT $2"""


@router.get("/history")
async def history(limit: int = 20, user: dict = Depends(get_current_user)):
    """Sesi Open Web milik pengguna ini (semua peran): alamat, status, dan sampai kapan berlaku."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_HISTORY_SQL, int(user["sub"]), max(1, min(limit, 50)))
    items = []
    for r in rows:
        active = r["status"] == "active"
        age_left = SESSION_MAX - (datetime.now(timezone.utc) - r["created_at"]).total_seconds()
        items.append({
            "id": r["id"], "url": r["url"], "status": r["status"],
            "created_at": r["created_at"].isoformat(), "expires_at": r["expires_at"].isoformat(),
            "remaining": r["remaining"] if active else 0,
            # Boleh ditambah bila sisa waktu < 30 menit dan umur sesi belum mencapai batas.
            "can_extend": active and r["remaining"] < EXTEND_WINDOW and age_left > r["remaining"],
            "extend_in": max(0, r["remaining"] - EXTEND_WINDOW) if active else 0,
        })
    return {"items": items, "extend_window": EXTEND_WINDOW, "extend_step": EXTEND_STEP}


async def _own_session(conn, sid: str, user: dict):
    row = await conn.fetchrow(
        """SELECT id, url, target_ip, created_at, expires_at, revoked_at FROM openweb_sessions
           WHERE id = $1 AND user_id = $2 AND url IS NOT NULL""", sid, int(user["sub"]))
    if not row:
        raise HTTPException(404, tr("Sesi Open Web tidak ditemukan", "Open Web session not found"))
    return row


def _require_active(row) -> None:
    if row["revoked_at"] is not None:
        raise HTTPException(409, tr("Sesi ini sudah dicabut. Buka sesi baru", "This session was revoked. Open a new session"))
    if row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(409, tr("Sesi ini sudah habis. Buka sesi baru", "This session has expired. Open a new session"))


@router.post("/sessions/{sid}/open")
async def reopen(sid: str, user: dict = Depends(get_current_user)):
    """Buka lagi sesi yang masih berlaku (mis. setelah tidak sengaja keluar), tanpa membuat sesi baru."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await _own_session(conn, sid, user)
    _require_active(row)
    ip, port, path = _parse_target(row["url"])
    await _authorize(user, ip)
    return {"proxy_path": _proxy_path(sid, ip, port, path, row["created_at"], user["sub"]),
            "session_id": sid, "expires_at": row["expires_at"].isoformat(), "url": row["url"]}


@router.post("/sessions/{sid}/extend")
async def extend(sid: str, request: Request, user: dict = Depends(get_current_user)):
    """Tambah 1 jam. Hanya bila sisa waktu kurang dari 30 menit, dan sampai batas umur sesi (24 jam)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await _own_session(conn, sid, user)
    _require_active(row)
    ip, port, _ = _parse_target(row["url"])
    await _authorize(user, ip)
    async with pool.acquire() as conn:
        new = await conn.fetchval(
            """UPDATE openweb_sessions
                  SET expires_at = LEAST(expires_at + make_interval(secs => $2), created_at + make_interval(secs => $4))
                WHERE id = $1 AND revoked_at IS NULL AND expires_at > NOW()
                  AND expires_at <= NOW() + make_interval(secs => $3)
                  AND expires_at < created_at + make_interval(secs => $4)
                RETURNING expires_at""", sid, EXTEND_STEP, EXTEND_WINDOW, SESSION_MAX)
        if new is None:
            left = int((row["expires_at"] - datetime.now(timezone.utc)).total_seconds())
            if left >= EXTEND_WINDOW:
                mins = max(1, (left - EXTEND_WINDOW + 59) // 60)
                raise HTTPException(409, tr(f"Waktu baru bisa ditambah saat sisanya kurang dari 30 menit (sekitar {mins} menit lagi)",
                                            f"Time can only be added when less than 30 minutes remain (in about {mins} minutes)"))
            raise HTTPException(409, tr("Sesi ini sudah mencapai batas 24 jam. Buka sesi baru",
                                        "This session reached its 24-hour limit. Open a new session"))
    _cache.pop(sid, None)
    host = f"{ip}:{port}" if port else str(ip)
    stamp = new.strftime("%H:%M")
    await log_activity(user, "OPENWEB_EXTEND", "INFO", {"id": str(ip), "name": host},
                       both(lambda: tr(f"{user.get('username')} menambah waktu sesi Open Web ke {host} (sesi {sid[:8]}) sampai {stamp} UTC",
                                       f"{user.get('username')} extended the Open Web session to {host} (session {sid[:8]}) until {stamp} UTC")), request)
    return {"expires_at": new.isoformat(), "remaining": max(0, int((new - datetime.now(timezone.utc)).total_seconds()))}


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
