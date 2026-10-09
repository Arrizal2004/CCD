"""
Admin Control Panel — Audit Trail + Guacamole Remote Access Monitoring.
Semua endpoint admin-only (admin / sysadmin / superadmin).
Guacamole polling dibungkus airtight: gagal → list kosong, tidak crash.

Memutus sesi (Remote, Web, SSH) bisa disertai pemblokiran supaya pengguna tidak langsung masuk lagi:
  block = "none"     hanya memutus sesi itu
  block = "account"  nonaktifkan akun dan putus semua sesinya (services/account_block.py)
  block = "vm"       (Remote) cabut penugasan langsung VM itu dari pengguna
"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from pydantic import BaseModel

from auth import require_sysadmin, Role
from database import get_pool
from services.audit import query_logs, log_activity, action_types, failed_logins, both
from services.csv_export import csv_response, fmt_time, MAX_ROWS
from services import account_block, remote_history, scope
from services import system_settings as ss
from i18n import tr

router = APIRouter()
log = logging.getLogger("admin")

Block = Literal["none", "account", "vm"]


# ── Audit Trail ──────────────────────────────────────────────────────────────
def _audit_filters(search: str, severity: str, start: str, end: str, action: str, username: str) -> dict:
    return {"search": search.strip(), "severity": severity.strip().upper(), "start": start or None,
            "end": end or None, "action": action.strip(), "username": username.strip()}


@router.get("/audit-logs")
async def get_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = "",
    severity: str = "",
    start: str = "",
    end: str = "",
    action: str = Query("", max_length=64),
    username: str = Query("", max_length=128),
    user: dict = Depends(require_sysadmin),
):
    try:
        return await query_logs(limit=page_size, offset=(page - 1) * page_size,
                                hide_instances=await scope.hidden_labels(user),
                                **_audit_filters(search, severity, start, end, action, username))
    except Exception as e:
        log.warning("audit query gagal: %s", e)
        return {"total": 0, "items": []}


@router.get("/audit-logs/actions")
async def get_audit_actions(user: dict = Depends(require_sysadmin)):
    """Jenis aksi yang pernah tercatat (pilihan filter)."""
    return {"actions": await action_types()}


@router.get("/audit-logs/export")
async def export_audit_logs(
    request: Request,
    search: str = "",
    severity: str = "",
    start: str = "",
    end: str = "",
    action: str = Query("", max_length=64),
    username: str = Query("", max_length=128),
    user: dict = Depends(require_sysadmin),
):
    """Activity Log sesuai filter yang sedang dipakai, sebagai CSV (maks. 50.000 baris terbaru)."""
    tz = ss.tzinfo(await ss.get_settings())
    tzn = tz.key
    filters = _audit_filters(search, severity, start, end, action, username)
    data = await query_logs(limit=MAX_ROWS, offset=0, hide_instances=await scope.hidden_labels(user), **filters)
    await log_activity(user, "AUDIT_EXPORT", "INFO", None,
                       both(lambda: tr(f"{user.get('username')} mengekspor {len(data['items'])} baris Activity Log ke CSV",
                       f"{user.get('username')} exported {len(data['items'])} activity log rows to CSV")), request)
    header = [tr(f"Waktu ({tzn})", f"Time ({tzn})"), "User", tr("Peran", "Role"), tr("Aksi", "Action"),
              tr("Tingkat", "Severity"), "Detail", "Server", "IP"]
    rows = ([fmt_time(i["timestamp"], tz), i["username"], i["user_role"], i["action_type"], i["severity"],
             i["detail"], i["target_name"], i["client_ip"]] for i in data["items"])
    return csv_response("activity-log", header, rows, tz)


@router.get("/audit-logs/failed-logins")
async def get_failed_logins(days: int = Query(7, ge=1, le=90), user: dict = Depends(require_sysadmin)):
    """Rekap login gagal per akun dan per IP."""
    return await failed_logins(days)


# ── Guacamole Remote Access Monitoring ───────────────────────────────────────
@router.get("/remote/sessions")
async def remote_sessions(user: dict = Depends(require_sysadmin)):
    """Sesi remote aktif saat ini."""
    try:
        from services.guac_sync import get_active_sessions
        allowed = await scope.allowed_labels(user)
        return {"sessions": [s for s in await get_active_sessions()
                             if allowed is None or scope.host_label(s["host"]) in allowed]}
    except Exception as e:
        log.warning("active sessions gagal: %s", e)
        return {"sessions": []}


class KillReq(BaseModel):
    active_id: str
    block: Block = "none"


async def _direct_assignment(username: str, host: str, vm: str):
    """(user_id, vm_id) penugasan langsung VM `vm` di `host` milik `username`, atau 409 dengan alasannya."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id, role FROM users WHERE username = $1 AND deleted_at IS NULL", username)
        if not user:
            raise HTTPException(404, tr(f"Akun '{username}' tidak ditemukan di dashboard",
                                        f"The account '{username}' was not found in the dashboard"))
        if user["role"] != Role.STUDENT:
            raise HTTPException(409, tr("Akun admin bisa membuka semua VM; akses per VM tidak bisa dicabut. "
                                        "Pilih nonaktifkan akun bila perlu.",
                                        "Admin accounts can open every VM, so per-VM access cannot be revoked. "
                                        "Deactivate the account instead if needed."))
        vm_id = await conn.fetchval(
            """SELECT va.vm_id FROM vm_assignments va
               LEFT JOIN vms v ON v.vm_id = va.vm_id AND v.host_name = va.host_name
               WHERE va.user_id = $1 AND va.host_name = $2 AND COALESCE(va.vm_name, v.vm_name) = $3
                 AND va.deleted_at IS NULL LIMIT 1""", user["id"], host, vm)
        if vm_id:
            return user["id"], vm_id
        groups = await conn.fetch(
            """SELECT DISTINCT g.name FROM group_members gm
               JOIN groups g ON g.id = gm.group_id
               JOIN group_vm_access gva ON gva.group_id = gm.group_id AND gva.host_name = $2
               LEFT JOIN vms v ON v.vm_id = gva.vm_id AND v.host_name = gva.host_name
               WHERE gm.user_id = $1 AND (v.vm_name = $3 OR gva.vm_id = $3) ORDER BY g.name""",
            user["id"], host, vm)
    if groups:
        names = ", ".join(g["name"] for g in groups)
        raise HTTPException(409, tr(f"Akses ke VM ini berasal dari grup {names}. Keluarkan pengguna dari grup "
                                    f"itu di halaman Groups, atau pilih nonaktifkan akun.",
                                    f"Access to this VM comes from the group {names}. Remove the user from that "
                                    f"group on the Groups page, or deactivate the account."))
    raise HTTPException(409, tr("VM ini tidak ditugaskan langsung ke pengguna tersebut, jadi tidak ada akses "
                                "yang bisa dicabut dari sini.",
                                "This VM is not assigned directly to that user, so there is no access to "
                                "revoke here."))


@router.post("/remote/kill-session")
async def kill_remote_session(body: KillReq, request: Request, user: dict = Depends(require_sysadmin)):
    from services.guac_sync import get_active_sessions, kill_session
    try:
        sessions = await get_active_sessions()
    except Exception as e:
        log.warning("active sessions gagal: %s", e)
        raise HTTPException(502, tr("Gagal terhubung ke Guacamole", "Could not connect to Guacamole"))
    sess = next((s for s in sessions if s["active_id"] == body.active_id), None)
    if not sess:
        raise HTTPException(404, tr("Sesi tidak ditemukan atau sudah berakhir",
                                    "Session not found or already ended"))
    who, vm, host = sess["username"], sess["vm"], sess["host"]
    await scope.require_host(user, host)

    # Pemeriksaan pemblokiran dilakukan sebelum sesi diputus, supaya pilihan yang tidak bisa dijalankan
    # tidak meninggalkan sesi yang terputus tanpa blokir.
    target = await account_block.target(user, who) if body.block == "account" else None
    assignment = await _direct_assignment(who, host, vm) if body.block == "vm" else None

    try:
        ok = await kill_session(body.active_id)
    except Exception as e:
        log.warning("kill session gagal: %s", e)
        raise HTTPException(502, tr("Gagal terhubung ke Guacamole", "Could not connect to Guacamole"))
    if not ok:
        raise HTTPException(500, tr("Guacamole menolak terminasi sesi",
                                    "Guacamole refused to terminate the session"))

    def _where():
        os_account = sess.get("os_account")
        return (f"VM {vm}" + (tr(f" (akun OS {os_account})", f" (OS account {os_account})") if os_account else "")
                + (tr(f" di {host}", f" on {host}") if host else ""))
    detail = both(lambda: tr(f"{user.get('username')} memutus sesi Remote '{who}' ke {_where()}",
                             f"{user.get('username')} disconnected the Remote session of '{who}' to {_where()}")
                  + (tr(" dan mencabut penugasan VM tersebut", " and revoked that VM assignment") if assignment else ""))
    result = {"status": "killed", "active_id": body.active_id, "block": body.block}
    if assignment:
        from routers.users import unassign_vm
        await unassign_vm(*assignment)
    await log_activity(user, "REMOTE_KILL", "WARNING", {"id": vm, "name": host or vm}, detail, request)
    if target:
        result["sessions"] = await account_block.lock_out(
            user, target, request, both(lambda: tr(f"dari sesi Remote ke {_where()}", f"from the Remote session to {_where()}")))
    return result


@router.post("/guac-grant-admins")
async def guac_grant_all_admins(request: Request, user: dict = Depends(require_sysadmin)):
    """
    One-time: grant semua koneksi Guacamole ke seluruh user admin/sysadmin/superadmin.
    Berguna untuk fix admin yang sudah ada tapi belum punya akses koneksi.
    """
    try:
        from services.guac_sync import grant_all_connections_to_admin, ADMIN_ROLES
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
    except Exception as e:
        log.warning("guac_grant_all_admins gagal: %s", e)
        raise HTTPException(502, tr(f"Gagal sync Guacamole: {e}", f"Guacamole sync failed: {e}"))
    ok = sum(1 for v in results.values() if v == "ok")
    await log_activity(user, "GUAC_GRANT_ADMINS", "WARNING", None,
                       both(lambda: tr(f"{user.get('username')} memberi akses semua koneksi Guacamole ke {ok} akun admin",
                       f"{user.get('username')} granted access to every Guacamole connection to {ok} admin accounts")), request)
    return {"granted": results}


@router.get("/remote/history")
async def remote_history_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = Query("", max_length=128),
    username: str = Query("", max_length=128),
    user: dict = Depends(require_sysadmin),
):
    """Riwayat sesi Remote, terbaru dulu. search: sebagian username/nama VM; username: satu akun."""
    try:
        return await remote_history.history(page_size, (page - 1) * page_size, search.strip(), username.strip(),
                                            labels=await scope.allowed_labels(user))
    except Exception as e:
        log.warning("remote history gagal: %s", e)
        return {"total": 0, "items": []}


@router.get("/remote/history/export")
async def export_remote_history(
    request: Request,
    search: str = Query("", max_length=128),
    username: str = Query("", max_length=128),
    user: dict = Depends(require_sysadmin),
):
    tz = ss.tzinfo(await ss.get_settings())
    tzn = tz.key
    data = await remote_history.history(MAX_ROWS, 0, search.strip(), username.strip(), labels=await scope.allowed_labels(user))
    await log_activity(user, "AUDIT_EXPORT", "INFO", None,
                       both(lambda: tr(f"{user.get('username')} mengekspor {len(data['items'])} baris riwayat Remote ke CSV",
                       f"{user.get('username')} exported {len(data['items'])} Remote history rows to CSV")), request)
    header = ["User", "VM", tr("Akun OS", "OS account"), "Host", tr("Protokol", "Protocol"),
              tr("IP Klien", "Client IP"), tr(f"Mulai ({tzn})", f"Start ({tzn})"), tr(f"Selesai ({tzn})", f"End ({tzn})"),
              tr("Durasi (detik)", "Duration (seconds)")]
    rows = ([h["username"], h["vm"], h.get("os_account", ""), h["host"], h.get("protocol", ""),
             h.get("remote_host", ""), fmt_time(h["start_date"], tz), fmt_time(h["end_date"], tz), h["duration_s"]]
            for h in data["items"])
    return csv_response("remote", header, rows, tz)


# ── Open Web sessions (proxy ke IP privat) ──────────────────────────────────
@router.get("/openweb/sessions")
async def openweb_active(user: dict = Depends(require_sysadmin)):
    """Sesi Open Web yang masih berlaku (belum dicabut, belum kedaluwarsa)."""
    from routers.openweb import list_sessions
    return {"sessions": (await list_sessions(active_only=True, limit=200, ips=await scope.scope_ips(user)))["items"]}


@router.get("/openweb/history")
async def openweb_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    username: str = Query("", max_length=128),
    user: dict = Depends(require_sysadmin),
):
    from routers.openweb import list_sessions
    return await list_sessions(active_only=False, limit=page_size, offset=(page - 1) * page_size,
                               username=username.strip(), ips=await scope.scope_ips(user))


@router.get("/openweb/history/export")
async def export_openweb_history(request: Request, username: str = Query("", max_length=128),
                                 user: dict = Depends(require_sysadmin)):
    tz = ss.tzinfo(await ss.get_settings())
    tzn = tz.key
    from routers.openweb import list_sessions
    data = await list_sessions(active_only=False, limit=MAX_ROWS, offset=0, username=username.strip(),
                               ips=await scope.scope_ips(user))
    await log_activity(user, "AUDIT_EXPORT", "INFO", None,
                       both(lambda: tr(f"{user.get('username')} mengekspor {len(data['items'])} baris riwayat Open Web ke CSV",
                       f"{user.get('username')} exported {len(data['items'])} Open Web history rows to CSV")), request)
    header = ["User", tr("Peran", "Role"), tr("Tujuan", "Target"), tr(f"Dibuat ({tzn})", f"Created ({tzn})"),
              tr(f"Berlaku s/d ({tzn})", f"Valid until ({tzn})"), tr(f"Akses terakhir ({tzn})", f"Last access ({tzn})"),
              "Request", tr("IP Pengakses", "Accessed from"), "Status", tr("Dicabut oleh", "Revoked by")]
    rows = ([s["username"], s["role"], s["target_ip"], fmt_time(s["created_at"], tz), fmt_time(s["expires_at"], tz),
             fmt_time(s["last_seen"], tz), s["hits"], s["client_ips"], s["status"], s["revoked_by"]]
            for s in data["items"])
    return csv_response("open-web", header, rows, tz)


class OpenWebKillReq(BaseModel):
    session_id: str
    block: Literal["none", "account"] = "none"


@router.post("/openweb/kill")
async def openweb_kill(body: OpenWebKillReq, request: Request, user: dict = Depends(require_sysadmin)):
    """Cabut sesi Open Web: link/tiketnya langsung berhenti bekerja."""
    from routers.openweb import revoke_session
    target = None
    if scope.is_scoped(user):
        pool = await get_pool()
        async with pool.acquire() as conn:
            ip = await conn.fetchval("SELECT target_ip FROM openweb_sessions WHERE id = $1", body.session_id)
        if ip is not None and ip not in set(await scope.scope_ips(user) or []):
            raise HTTPException(403, tr("Anda tidak ditugaskan untuk mengelola Proxmox ini",
                                        "You are not assigned to manage this Proxmox"))
    if body.block == "account":
        pool = await get_pool()
        async with pool.acquire() as conn:
            owner = await conn.fetchval(
                "SELECT username FROM openweb_sessions WHERE id = $1 AND revoked_at IS NULL", body.session_id)
        if not owner:
            raise HTTPException(404, tr("Sesi tidak ditemukan atau sudah dicabut",
                                        "Session not found or already revoked"))
        target = await account_block.target(user, owner)
    row = await revoke_session(body.session_id, user.get("username") or "")
    if not row:
        raise HTTPException(404, tr("Sesi tidak ditemukan atau sudah dicabut",
                                    "Session not found or already revoked"))
    await log_activity(
        user, "OPENWEB_KILL", "WARNING",
        {"id": row["target_ip"], "name": row["target_ip"]},
        both(lambda: tr(f"{user.get('username')} mencabut sesi Open Web {row['username']} -> {row['target_ip']} ({body.session_id[:8]})",
                        f"{user.get('username')} revoked the Open Web session {row['username']} -> {row['target_ip']} ({body.session_id[:8]})")),
        request,
    )
    result = {"status": "revoked", "session_id": body.session_id, "block": body.block}
    if target:
        result["sessions"] = await account_block.lock_out(
            user, target, request, both(lambda: tr(f"dari link Open Web ke {row['target_ip']}",
                                                   f"from the Open Web link to {row['target_ip']}")))
    return result


# ── SSH sessions (bastion) ──────────────────────────────────────────────────
@router.get("/ssh/sessions")
async def ssh_active(user: dict = Depends(require_sysadmin)):
    """Sesi SSH lewat bastion yang masih tersambung."""
    from services.ssh_audit import list_sessions
    return {"sessions": (await list_sessions(active_only=True, limit=200, ips=await scope.scope_ips(user)))["items"]}


@router.get("/ssh/history")
async def ssh_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    username: str = Query("", max_length=128),
    user: dict = Depends(require_sysadmin),
):
    from services.ssh_audit import list_sessions
    return await list_sessions(active_only=False, limit=page_size, offset=(page - 1) * page_size,
                               username=username.strip(), ips=await scope.scope_ips(user))


@router.get("/ssh/history/export")
async def export_ssh_history(request: Request, username: str = Query("", max_length=128),
                             user: dict = Depends(require_sysadmin)):
    tz = ss.tzinfo(await ss.get_settings())
    tzn = tz.key
    from services.ssh_audit import list_sessions
    data = await list_sessions(active_only=False, limit=MAX_ROWS, offset=0, username=username.strip(),
                               ips=await scope.scope_ips(user))
    await log_activity(user, "AUDIT_EXPORT", "INFO", None,
                       both(lambda: tr(f"{user.get('username')} mengekspor {len(data['items'])} baris riwayat SSH ke CSV",
                       f"{user.get('username')} exported {len(data['items'])} SSH history rows to CSV")), request)
    header = ["User", tr("Peran", "Role"), tr("Dari IP", "From IP"), "Key", "Fingerprint", tr("Tujuan", "Targets"),
              tr("Ditolak", "Denied"), tr(f"Mulai ({tzn})", f"Start ({tzn})"), tr(f"Selesai ({tzn})", f"End ({tzn})"),
              tr("Durasi (detik)", "Duration (seconds)"), tr("Data terkirim (byte)", "Bytes sent"),
              tr("Data diterima (byte)", "Bytes received"), "Status", tr("Diputus oleh", "Killed by")]
    rows = ([s["username"], s["role"], s["client_ip"], s["key_name"], s["fingerprint"],
             [f"{t['target']} ({t['vm']})" if t["vm"] else t["target"] for t in s["targets"]],
             s["denied_targets"], fmt_time(s["started_at"], tz), fmt_time(s["ended_at"], tz), s["duration"],
             s["bytes_sent"], s["bytes_received"], s["status"], s["killed_by"]]
            for s in data["items"])
    return csv_response("ssh", header, rows, tz)


class SshKillReq(BaseModel):
    session_id: int
    block: Literal["none", "account"] = "none"


@router.post("/ssh/kill")
async def ssh_kill_session(body: SshKillReq, request: Request, user: dict = Depends(require_sysadmin)):
    """Putuskan satu sesi SSH di bastion. Menunggu konfirmasi dari bastion (lihat services/ssh_kill.py)."""
    from services import ssh_kill
    pool = await get_pool()
    async with pool.acquire() as conn:
        sess = await conn.fetchrow(
            "SELECT id, username, client_ip, monitor_pid, child_pid, targets FROM ssh_sessions WHERE id = $1 AND ended_at IS NULL",
            body.session_id)
    if not sess:
        raise HTTPException(404, tr("Sesi tidak ditemukan atau sudah berakhir",
                                    "Session not found or already ended"))
    if scope.is_scoped(user):
        mine = set(await scope.scope_ips(user) or [])
        if not any(t.split(":")[0] in mine for t in sess["targets"]):
            raise HTTPException(403, tr("Anda tidak ditugaskan untuk mengelola Proxmox ini",
                                        "You are not assigned to manage this Proxmox"))
    target = None
    if body.block == "account":
        if not sess["username"]:
            raise HTTPException(409, tr("Key sesi ini tidak terdaftar di akun mana pun",
                                        "This session's key is not registered to any account"))
        target = await account_block.target(user, sess["username"])
    if not await ssh_kill.kill_and_wait(sess, user.get("username") or ""):
        raise HTTPException(504, tr("Bastion tidak menjawab perintah pemutusan. Pastikan container bastion "
                                    "memakai versi terbaru (docker compose --profile ssh up -d --build bastion).",
                                    "The bastion did not answer the disconnect request. Make sure the bastion "
                                    "container runs the latest version "
                                    "(docker compose --profile ssh up -d --build bastion)."))
    def _detail():
        who = sess["username"] or tr("(key tidak terdaftar)", "(unregistered key)")
        return tr(f"{user.get('username')} memutus sesi SSH '{who}' dari {sess['client_ip']} di bastion",
                  f"{user.get('username')} disconnected the SSH session of '{who}' from {sess['client_ip']} at the bastion")
    await log_activity(user, "SSH_KILL", "WARNING", {"id": str(sess["id"]), "name": sess["client_ip"]},
                       both(_detail), request)
    result = {"status": "killed", "session_id": sess["id"], "block": body.block}
    if target:
        result["sessions"] = await account_block.lock_out(
            user, target, request, both(lambda: tr("dari sesi SSH di bastion", "from the SSH session at the bastion")))
    return result


# ── Resource VPS dashboard (halaman Status) ─────────────────────────────────
@router.get("/vps/live")
async def vps_live(user: dict = Depends(require_sysadmin)):
    """Sampel terbaru dan 15 menit terakhir (resolusi 5 detik)."""
    from services.vps_metrics import live
    return live()


@router.get("/vps/history")
async def vps_history(range: str = Query("24h", pattern="^(1h|24h|7d|30d)$"), user: dict = Depends(require_sysadmin)):
    """Riwayat per menit dari tabel vps_metrics, dirata-rata per ember sesuai rentang."""
    from services.vps_metrics import history
    return await history(range)
