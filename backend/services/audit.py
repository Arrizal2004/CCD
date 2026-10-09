"""
Audit Trail / Activity Log.

log_activity() bersifat fire-and-forget & airtight: kegagalan logging TIDAK
boleh mengganggu endpoint inti. Panggil di dalam router (auth, VM control,
credential, RBAC) untuk merekam event.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from database import get_pool
from i18n import current as current_lang, lang_as

log = logging.getLogger("audit")

# action_type enum (longgar — string), severity_level: INFO | WARNING | CRITICAL
SEVERITIES = ("INFO", "WARNING", "CRITICAL")


class Bi(str):
    """Kalimat dua bahasa. Nilai str-nya bahasa Indonesia (disimpan di detail_message); `.en` berisi
    versi Inggris (detail_en); `.t()` memilih sesuai bahasa permintaan yang sedang berjalan."""
    en: str

    def __new__(cls, id_text: str, en_text: str):
        obj = super().__new__(cls, id_text)
        obj.en = en_text
        return obj

    def t(self) -> str:
        return self.en if current_lang() == "en" else str(self)


def both(build) -> Bi:
    """Susun kalimat dua kali, sekali per bahasa, dari fungsi `build` yang memakai tr(). Potongan kalimat
    di dalamnya (daftar perubahan, keterangan) cukup memakai tr() atau Bi.t() supaya ikut dua bahasa."""
    with lang_as("id"):
        id_text = build()
    with lang_as("en"):
        en_text = build()
    return Bi(id_text, en_text)


def _parse_dt(s: str) -> Optional[datetime]:
    """Parse ISO-8601 string (termasuk 'Z' suffix) ke datetime object untuk asyncpg."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def _client_ip(request) -> str:
    """IP klien dari X-Real-IP, yang selalu ditimpa nginx dengan $remote_addr (IP asli, termasuk di
    belakang Cloudflare lewat modul realip). X-Forwarded-For tidak dipakai: entri pertamanya bisa
    diisi sendiri oleh klien."""
    if request is None:
        return ""
    try:
        return request.headers.get("x-real-ip") or (request.client.host if request.client else "")
    except Exception:
        return ""


async def log_activity(
    user: Optional[dict],
    action: str,
    severity: str = "INFO",
    target: Optional[dict] = None,
    detail: str = "",
    request=None,
    ip: Optional[str] = None,
) -> None:
    """
    Rekam satu event audit. Tidak pernah raise.
      user   : dict JWT ({sub, username, role}) atau None
      action : action_type, mis. 'AUTH_LOGIN', 'VM_POWER', 'CRED_UPDATE'
      target : {"id": vm_id/host, "name": vm_name/host_name}
      ip     : IP klien kalau bukan dari request (mis. login SSH yang dilaporkan bastion)
    """
    try:
        sev = severity if severity in SEVERITIES else "INFO"
        uid = None
        uname = role = None
        if user:
            try:
                uid = int(user.get("sub")) if user.get("sub") is not None else None
            except (TypeError, ValueError):
                uid = None
            uname = user.get("username")
            role = user.get("role")
        tgt_id = (target or {}).get("id")
        tgt_name = (target or {}).get("name")
        ip = ip if ip is not None else _client_ip(request)

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO audit_logs
                   (user_id, username, user_role, action_type, severity_level,
                    target_server_id, target_server_name, client_ip, detail_message, detail_en)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                uid, uname, role, action, sev,
                str(tgt_id) if tgt_id is not None else None,
                tgt_name, ip, str(detail), detail.en if isinstance(detail, Bi) else None,
            )
    except Exception as e:
        # Airtight: jangan pernah menggagalkan request inti
        log.warning("audit log gagal (%s): %s", action, e)


async def query_logs(
    limit: int = 50, offset: int = 0,
    search: str = "", severity: str = "",
    start: Optional[str] = None, end: Optional[str] = None,
    action: str = "", username: str = "", hide_instances=None,
) -> dict:
    """Ambil audit logs dengan pagination + filter. Return {total, items}.
    action: satu action_type persis; username: satu akun persis (tanpa beda huruf besar/kecil).
    hide_instances: label Proxmox yang catatannya disembunyikan (sysadmin yang tidak memegangnya);
    catatan tanpa nama Proxmox, mis. soal akun, tetap tampil."""
    where = []
    params = []
    i = 1
    if hide_instances:
        esc = [l.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for l in hide_instances]
        params.append(list(hide_instances)); params.append([p for l in esc for p in (f"{l}/%", f"{l}\\_\\_%")])
        where.append(f"NOT (COALESCE(target_server_name, '') = ANY(${i}) OR COALESCE(target_server_name, '') LIKE ANY(${i + 1}))")
        i += 2
    if search:
        where.append(f"(username ILIKE ${i} OR action_type ILIKE ${i} OR detail_message ILIKE ${i} "
                     f"OR detail_en ILIKE ${i} OR target_server_name ILIKE ${i})")
        params.append(f"%{search}%"); i += 1
    if severity in SEVERITIES:
        where.append(f"severity_level = ${i}"); params.append(severity); i += 1
    if action:
        where.append(f"action_type = ${i}"); params.append(action); i += 1
    if username:
        where.append(f"lower(username) = lower(${i})"); params.append(username); i += 1
    start_dt = _parse_dt(start) if start else None
    end_dt   = _parse_dt(end)   if end   else None
    if start_dt:
        where.append(f"created_at >= ${i}"); params.append(start_dt); i += 1
    if end_dt:
        where.append(f"created_at <= ${i}"); params.append(end_dt); i += 1
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM audit_logs {clause}", *params)
        rows = await conn.fetch(
            f"""SELECT id, created_at, user_id, username, user_role, action_type,
                       severity_level, target_server_id, target_server_name,
                       client_ip, detail_message, detail_en
                FROM audit_logs {clause}
                ORDER BY created_at DESC
                LIMIT ${i} OFFSET ${i+1}""",
            *params, limit, offset,
        )
    return {
        "total": total or 0,
        "items": [
            {
                "id": r["id"],
                "timestamp": r["created_at"].isoformat(),
                "user_id": r["user_id"],
                "username": r["username"] or "system",
                "user_role": r["user_role"] or "-",
                "action_type": r["action_type"],
                "severity": r["severity_level"],
                "target_id": r["target_server_id"],
                "target_name": r["target_server_name"] or "-",
                "client_ip": r["client_ip"] or "-",
                # Bahasa admin yang membuka; catatan lama tanpa versi Inggris tetap berbahasa Indonesia.
                "detail": (r["detail_en"] if current_lang() == "en" and r["detail_en"] else r["detail_message"]) or "",
            }
            for r in rows
        ],
    }


async def action_types() -> list[str]:
    """Jenis aksi yang pernah tercatat, untuk pilihan filter di Activity Log."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT action_type FROM audit_logs ORDER BY action_type")
    return [r["action_type"] for r in rows]


async def failed_logins(days: int, limit: int = 50) -> dict:
    """Rekap AUTH_LOGIN_FAILED dalam `days` hari terakhir, per akun dan per IP, supaya tebak-tebakan
    password terlihat tanpa harus membaca Activity Log baris demi baris."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM audit_logs WHERE action_type = 'AUTH_LOGIN_FAILED' AND created_at >= $1", since)
        by_user = await conn.fetch(
            """SELECT a.username, count(*) AS attempts, max(a.created_at) AS last_at,
                      array_agg(DISTINCT a.client_ip) FILTER (WHERE a.client_ip <> '') AS ips,
                      bool_or(u.id IS NOT NULL) AS known
               FROM audit_logs a
               LEFT JOIN users u ON u.username = a.username AND u.deleted_at IS NULL
               WHERE a.action_type = 'AUTH_LOGIN_FAILED' AND a.created_at >= $1
               GROUP BY a.username ORDER BY attempts DESC, last_at DESC LIMIT $2""", since, limit)
        by_ip = await conn.fetch(
            """SELECT client_ip AS ip, count(*) AS attempts, max(created_at) AS last_at,
                      count(DISTINCT username) AS accounts,
                      (array_agg(DISTINCT username))[1:10] AS usernames
               FROM audit_logs
               WHERE action_type = 'AUTH_LOGIN_FAILED' AND created_at >= $1 AND client_ip <> ''
               GROUP BY client_ip ORDER BY attempts DESC, last_at DESC LIMIT $2""", since, limit)
    from services.login_rate_limit import seconds_locked
    users = []
    for r in by_user:
        users.append({"username": r["username"], "attempts": r["attempts"], "last_at": r["last_at"].isoformat(),
                      "ips": sorted(r["ips"] or []), "known": bool(r["known"]),
                      "locked_for": await seconds_locked(r["username"] or "")})
    ips = [{"ip": r["ip"], "attempts": r["attempts"], "last_at": r["last_at"].isoformat(),
            "accounts": r["accounts"], "usernames": sorted(r["usernames"] or [])} for r in by_ip]
    return {"days": days, "total": total or 0, "by_user": users, "by_ip": ips}
