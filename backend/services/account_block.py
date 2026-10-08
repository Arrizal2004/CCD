"""
Menonaktifkan akun sekaligus memutus semua aksesnya yang sedang berjalan.

Dipakai saat admin memutus sesi (Remote, Web, atau SSH) dengan pilihan "nonaktifkan akun", supaya
pengguna tidak bisa langsung masuk lagi. Akun yang nonaktif ditolak di semua jalur: dashboard
(auth.account_state), Guacamole (akunnya dinonaktifkan juga), Open Web (link yang masih berlaku
dicabut), dan bastion SSH (/ssh-keys/authorized memeriksa is_active).

Sysadmin hanya boleh memakainya untuk akun mahasiswa, sama dengan reset password. Superadmin boleh
untuk akun lain kecuali dirinya sendiri.
"""
import asyncio
import logging

from fastapi import HTTPException

from auth import Role, forget_account
from database import get_pool
from i18n import tr
from services.audit import Bi, both, log_activity

log = logging.getLogger("account_block")


async def target(current: dict, username: str):
    """Akun yang boleh dinonaktifkan oleh `current`, atau HTTPException."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, role, is_active FROM users WHERE username = $1 AND deleted_at IS NULL", username)
    if not row:
        raise HTTPException(404, tr(f"Akun '{username}' tidak ditemukan di dashboard",
                                    f"The account '{username}' was not found in the dashboard"))
    if str(row["id"]) == str(current.get("sub")):
        raise HTTPException(400, tr("Tidak bisa menonaktifkan akun Anda sendiri",
                                    "You cannot deactivate your own account"))
    if current.get("role") != Role.SUPERADMIN and row["role"] != Role.STUDENT:
        raise HTTPException(403, tr("Sysadmin hanya bisa menonaktifkan akun mahasiswa",
                                    "Sysadmins can only deactivate student accounts"))
    return row


async def lock_out(current: dict, row, request, reason: str) -> dict:
    """Nonaktifkan akun `row` dan putus semua sesinya. Return jumlah sesi yang diputus per jenis."""
    from routers.guac import disconnect_user
    from services import ssh_kill
    from services.guac_sync import get_active_sessions, kill_session, sync_user_disabled, with_retry

    username = row["username"]
    by = current.get("username") or ""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_active = FALSE, updated_at = NOW() WHERE id = $1", row["id"])
        web = await conn.fetch(
            """UPDATE openweb_sessions SET revoked_at = NOW(), revoked_by = $2
               WHERE lower(username) = lower($1) AND revoked_at IS NULL AND expires_at > NOW() RETURNING id""",
            username, by)
        ssh = await conn.fetch(
            "SELECT id, monitor_pid, child_pid FROM ssh_sessions WHERE lower(username) = lower($1) AND ended_at IS NULL",
            username)
    forget_account(row["id"])
    from routers import openweb
    for r in web:
        openweb._cache.pop(r["id"], None)

    remote = 0
    try:
        for s in await get_active_sessions():
            if s["username"].lower() == username.lower() and await kill_session(s["active_id"]):
                remote += 1
    except Exception as e:
        log.warning("memutus sesi Remote %s gagal: %s", username, e)
    try:
        await disconnect_user(username)
    except Exception:
        pass
    asyncio.create_task(with_retry(sync_user_disabled, username, disabled=True, op_name="lock_out:guac"))

    if ssh:
        await ssh_kill.mark_requested([s["id"] for s in ssh], by)
        await ssh_kill.enqueue(ssh)

    counts = {"remote": remote, "web": len(web), "ssh": len(ssh)}
    why = reason if isinstance(reason, Bi) else Bi(str(reason), str(reason))
    await log_activity(current, "USER_LOCKOUT", "CRITICAL", None,
                       both(lambda: tr(f"{by} menonaktifkan akun '{username}' dan memutus semua sesinya "
                                       f"(Remote {remote}, Web {len(web)}, SSH {len(ssh)}): {why.t()}",
                                       f"{by} deactivated the account '{username}' and disconnected all of its sessions "
                                       f"(Remote {remote}, Web {len(web)}, SSH {len(ssh)}): {why.t()}")), request)
    return counts
