"""
Admin Control Panel — Audit Trail + Guacamole Remote Access Monitoring.
Semua endpoint admin-only (admin / sysadmin / superadmin).
Guacamole polling dibungkus airtight: gagal → list kosong, tidak crash.
"""
import logging
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from pydantic import BaseModel

from auth import require_sysadmin, get_current_user
from services.audit import query_logs, log_activity

router = APIRouter()
log = logging.getLogger("admin")


# ── Audit Trail ──────────────────────────────────────────────────────────────
@router.get("/audit-logs")
async def get_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = "",
    severity: str = "",
    start: str = "",
    end: str = "",
    user: dict = Depends(require_sysadmin),
):
    try:
        return await query_logs(
            limit=page_size, offset=(page - 1) * page_size,
            search=search.strip(), severity=severity.strip().upper(),
            start=start or None, end=end or None,
        )
    except Exception as e:
        log.warning("audit query gagal: %s", e)
        return {"total": 0, "items": []}


# ── Guacamole Remote Access Monitoring ───────────────────────────────────────
@router.get("/remote/sessions")
async def remote_sessions(user: dict = Depends(require_sysadmin)):
    """Sesi remote aktif saat ini."""
    try:
        from services.guac_sync import get_active_sessions
        return {"sessions": await get_active_sessions()}
    except Exception as e:
        log.warning("active sessions gagal: %s", e)
        return {"sessions": []}


class KillReq(BaseModel):
    active_id: str


@router.post("/remote/kill-session")
async def kill_remote_session(body: KillReq, request: Request, user: dict = Depends(require_sysadmin)):
    try:
        from services.guac_sync import kill_session
        ok = await kill_session(body.active_id)
    except Exception as e:
        log.warning("kill session gagal: %s", e)
        raise HTTPException(502, "Gagal terhubung ke Guacamole")
    await log_activity(
        user, "REMOTE_KILL", "WARNING",
        {"id": body.active_id, "name": body.active_id},
        f"Force-terminate sesi Guacamole {body.active_id}", request,
    )
    if not ok:
        raise HTTPException(500, "Guacamole menolak terminasi sesi")
    return {"status": "killed", "active_id": body.active_id}


@router.post("/guac-grant-admins")
async def guac_grant_all_admins(user: dict = Depends(require_sysadmin)):
    """
    One-time: grant semua koneksi Guacamole ke seluruh user admin/sysadmin/superadmin.
    Berguna untuk fix admin yang sudah ada tapi belum punya akses koneksi.
    """
    try:
        from services.guac_sync import grant_all_connections_to_admin, ADMIN_ROLES
        from database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT username FROM users WHERE role = ANY($1) AND is_active = true",
                list(ADMIN_ROLES),
            )
        results = {}
        for row in rows:
            uname = row["username"]
            try:
                await grant_all_connections_to_admin(uname)
                results[uname] = "ok"
            except Exception as e:
                results[uname] = str(e)
        return {"granted": results}
    except Exception as e:
        log.warning("guac_grant_all_admins gagal: %s", e)
        raise HTTPException(502, f"Gagal sync Guacamole: {e}")


@router.get("/remote/history")
async def remote_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: dict = Depends(require_sysadmin),
):
    """History koneksi remote (paginated client-side dari hasil Guacamole)."""
    try:
        from services.guac_sync import get_connection_history
        all_rows = await get_connection_history(limit=500)
    except Exception as e:
        log.warning("remote history gagal: %s", e)
        all_rows = []
    total = len(all_rows)
    s = (page - 1) * page_size
    return {"total": total, "items": all_rows[s:s + page_size]}


# ── Open Web sessions (proxy ke IP privat) ──────────────────────────────────
@router.get("/openweb/sessions")
async def openweb_active(user: dict = Depends(require_sysadmin)):
    """Sesi Open Web yang masih berlaku (belum dicabut, belum kedaluwarsa)."""
    from routers.openweb import list_sessions
    return {"sessions": (await list_sessions(active_only=True, limit=200))["items"]}


@router.get("/openweb/history")
async def openweb_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: dict = Depends(require_sysadmin),
):
    from routers.openweb import list_sessions
    return await list_sessions(active_only=False, limit=page_size, offset=(page - 1) * page_size)


class OpenWebKillReq(BaseModel):
    session_id: str


@router.post("/openweb/kill")
async def openweb_kill(body: OpenWebKillReq, request: Request, user: dict = Depends(require_sysadmin)):
    """Cabut sesi Open Web: link/tiketnya langsung berhenti bekerja."""
    from routers.openweb import revoke_session
    row = await revoke_session(body.session_id, user.get("username") or "")
    if not row:
        raise HTTPException(404, "Sesi tidak ditemukan atau sudah dicabut")
    await log_activity(
        user, "OPENWEB_KILL", "WARNING",
        {"id": row["target_ip"], "name": row["target_ip"]},
        f"{user.get('username')} mencabut sesi Open Web {row['username']} -> {row['target_ip']} ({body.session_id[:8]})",
        request,
    )
    return {"status": "revoked", "session_id": body.session_id}
