"""
Audit Trail / Activity Log.

log_activity() bersifat fire-and-forget & airtight: kegagalan logging TIDAK
boleh mengganggu endpoint inti. Panggil di dalam router (auth, VM control,
credential, RBAC) untuk merekam event.
"""
import logging
from datetime import datetime
from typing import Optional

from database import get_pool

log = logging.getLogger("audit")

# action_type enum (longgar — string), severity_level: INFO | WARNING | CRITICAL
SEVERITIES = ("INFO", "WARNING", "CRITICAL")


def _parse_dt(s: str) -> Optional[datetime]:
    """Parse ISO-8601 string (termasuk 'Z' suffix) ke datetime object untuk asyncpg."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def _client_ip(request) -> str:
    if request is None:
        return ""
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        return request.client.host if request.client else ""
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
                    target_server_id, target_server_name, client_ip, detail_message)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                uid, uname, role, action, sev,
                str(tgt_id) if tgt_id is not None else None,
                tgt_name, ip, detail,
            )
    except Exception as e:
        # Airtight: jangan pernah menggagalkan request inti
        log.warning("audit log gagal (%s): %s", action, e)


async def query_logs(
    limit: int = 50, offset: int = 0,
    search: str = "", severity: str = "",
    start: Optional[str] = None, end: Optional[str] = None,
) -> dict:
    """Ambil audit logs dengan pagination + filter. Return {total, items}."""
    where = []
    params = []
    i = 1
    if search:
        where.append(f"(username ILIKE ${i} OR action_type ILIKE ${i} OR detail_message ILIKE ${i} OR target_server_name ILIKE ${i})")
        params.append(f"%{search}%"); i += 1
    if severity in SEVERITIES:
        where.append(f"severity_level = ${i}"); params.append(severity); i += 1
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
                       client_ip, detail_message
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
                "detail": r["detail_message"] or "",
            }
            for r in rows
        ],
    }
