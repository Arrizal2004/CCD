"""
Operasi VM tambahan untuk admin, di bawah /api/v1/proxmox:
  PUT    .../vms/{vmid}/protection       kunci / buka kunci VM (Protection Proxmox)
  GET    .../vms/{vmid}/schedules        daftar jadwal (nyala, mati, snapshot otomatis)
  POST   .../vms/{vmid}/schedules        tambah jadwal
  PUT    .../vms/{vmid}/schedules/{sid}  ubah jadwal
  DELETE .../vms/{vmid}/schedules/{sid}  hapus jadwal
"""
import re
from datetime import time as dtime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth import Role, get_current_user
from database import get_pool, get_student_vm_access
from i18n import tr
from services import exam_mode, lab_reset, scope, vm_access, vm_lock
from services.audit import both, log_activity
from services.proxmox_client import ProxmoxError
from routers.proxmox import _get_client, _host_key, _require_admin

router = APIRouter(dependencies=[Depends(scope.enforce_path)])


# ── kunci VM ──────────────────────────────────────────────────────────────────

class ProtectionRequest(BaseModel):
    locked: bool


def locked_error() -> HTTPException:
    return HTTPException(409, tr("VM ini terkunci. Buka kuncinya dulu sebelum menghapus.",
                                 "This VM is locked. Unlock it before deleting."))


@router.put("/instances/{label}/nodes/{node}/vms/{vmid}/protection")
async def put_vm_protection(label: str, node: str, vmid: int, body: ProtectionRequest, request: Request,
                            user: dict = Depends(get_current_user)):
    """Kunci atau buka kunci VM (admin). Kunci memakai Protection Proxmox, jadi berlaku juga di luar dashboard."""
    _require_admin(user)
    client = await _get_client(label)
    try:
        cfg = await client.get_vm_config(node, vmid)
    except ProxmoxError as e:
        if "does not exist" in e.detail or e.status_code == 403:
            raise HTTPException(404, tr(f"VM {vmid} tidak ditemukan di node {node} (atau di luar pool dashboard)",
                                        f"VM {vmid} not found on node {node} (or outside the dashboard pool)"))
        raise HTTPException(status_code=502, detail=e.detail)
    name = cfg.get("name") or str(vmid)
    if vm_lock.is_protected(cfg) != body.locked:
        try:
            await client.update_vm_config(node, vmid, {"protection": 1 if body.locked else 0})
        except ProxmoxError as e:
            if e.status_code == 403:
                raise HTTPException(403, tr("Token Proxmox tidak punya izin VM.Config.Options untuk VM ini. Tambahkan izin itu di Proxmox (lihat docs/PANDUAN_DEPLOYMENT.md).",
                                            "The Proxmox token lacks VM.Config.Options on this VM. Grant it in Proxmox (see docs/PANDUAN_DEPLOYMENT.md)."))
            raise HTTPException(status_code=502, detail=e.detail)
        target = {"id": str(vmid), "name": f"{label}/{node}/{vmid}"}
        if body.locked:
            await log_activity(user, "VM_LOCK", "INFO", target,
                               both(lambda: tr(f"{user.get('username')} mengunci VM {name} ({vmid}) di {label}/{node}",
                                               f"{user.get('username')} locked VM {name} ({vmid}) on {label}/{node}")), request)
        else:
            await log_activity(user, "VM_UNLOCK", "WARNING", target,
                               both(lambda: tr(f"{user.get('username')} membuka kunci VM {name} ({vmid}) di {label}/{node}; VM bisa dihapus lagi",
                                               f"{user.get('username')} unlocked VM {name} ({vmid}) on {label}/{node}; the VM can be deleted again")), request)
    vm_lock.remember(label, node, vmid, body.locked)
    return {"vmid": vmid, "locked": body.locked}


# ── jadwal VM (services/vm_schedule.py) ───────────────────────────────────────

class ScheduleRequest(BaseModel):
    action:  Literal["start", "shutdown", "snapshot"]
    keep:    int = Field(3, ge=1, le=30)          # snapshot: simpan sebanyak ini yang terbaru (ccd-auto-*)
    days:    list[int] = Field(..., min_length=1, max_length=7)
    time:    str                                  # "HH:MM", zona waktu Pengaturan Sistem
    enabled: bool = True
    note:    str = Field("", max_length=200)


def _schedule_values(body: ScheduleRequest):
    days = sorted(set(body.days))
    if any(d < 1 or d > 7 for d in days):
        raise HTTPException(400, tr("Hari harus 1 (Senin) sampai 7 (Minggu)", "Days must be 1 (Monday) to 7 (Sunday)"))
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", body.time.strip())
    if not m:
        raise HTTPException(400, tr("Jam harus berformat HH:MM", "The time must be HH:MM"))
    return days, dtime(int(m.group(1)), int(m.group(2))), body.note.strip()


def _schedule_out(r) -> dict:
    from services.vm_schedule import result_text
    return {"id": r["id"], "action": r["action"], "keep": r["keep"], "days": list(r["days"]), "time": r["at_time"].strftime("%H:%M"),
            "enabled": r["enabled"], "note": r["note"], "created_by": r["created_by"],
            "last_run_at": r["last_run_at"].isoformat() if r["last_run_at"] else None,
            "last_result": r["last_result"], "last_result_text": result_text(r["last_result"]) if r["last_result"] else None}


def _day_name(d: int) -> str:
    return {1: tr("Sen", "Mon"), 2: tr("Sel", "Tue"), 3: tr("Rab", "Wed"), 4: tr("Kam", "Thu"),
            5: tr("Jum", "Fri"), 6: tr("Sab", "Sat"), 7: tr("Min", "Sun")}[d]


def _schedule_label(action: str, days: list[int], at) -> str:
    what = {"start": tr("nyalakan", "start"), "snapshot": "snapshot"}.get(action, tr("matikan", "shut down"))
    return f"{what} {','.join(_day_name(d) for d in days)} {at.strftime('%H:%M')}"


@router.get("/instances/{label}/nodes/{node}/vms/{vmid}/schedules")
async def list_vm_schedules(label: str, node: str, vmid: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    from services import system_settings as ss
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM vm_schedules WHERE host_name = $1 AND vm_id = $2 ORDER BY at_time, id",
                                _host_key(label, node), str(vmid))
    return {"timezone": (await ss.get_settings())["timezone"], "schedules": [_schedule_out(r) for r in rows]}


@router.post("/instances/{label}/nodes/{node}/vms/{vmid}/schedules", status_code=201)
async def create_vm_schedule(label: str, node: str, vmid: int, body: ScheduleRequest, request: Request,
                             user: dict = Depends(get_current_user)):
    _require_admin(user)
    days, at, note = _schedule_values(body)
    host_key = _host_key(label, node)
    pool = await get_pool()
    async with pool.acquire() as conn:
        name = await conn.fetchval("SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2", str(vmid), host_key)
        if name is None:
            raise HTTPException(404, tr("VM belum tercatat di dashboard. Buka daftar VM-nya dulu.",
                                        "The VM is not recorded in the dashboard yet. Open its VM list first."))
        if await conn.fetchval("SELECT count(*) FROM vm_schedules WHERE host_name = $1 AND vm_id = $2", host_key, str(vmid)) >= 20:
            raise HTTPException(400, tr("Maksimal 20 jadwal per VM", "At most 20 schedules per VM"))
        row = await conn.fetchrow(
            """INSERT INTO vm_schedules (host_name, vm_id, action, days, at_time, enabled, note, created_by, keep)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING *""",
            host_key, str(vmid), body.action, days, at, body.enabled, note, user.get("username") or "", body.keep)
    await log_activity(user, "VM_SCHEDULE_CREATE", "INFO", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
                       both(lambda: tr(f"{user.get('username')} menambah jadwal VM {name}: {_schedule_label(body.action, days, at)}",
                                       f"{user.get('username')} added a schedule for VM {name}: {_schedule_label(body.action, days, at)}")), request)
    return _schedule_out(row)


async def _own_schedule(conn, label: str, node: str, vmid: int, sid: int):
    row = await conn.fetchrow("SELECT * FROM vm_schedules WHERE id = $1 AND host_name = $2 AND vm_id = $3",
                              sid, _host_key(label, node), str(vmid))
    if not row:
        raise HTTPException(404, tr("Jadwal tidak ditemukan", "Schedule not found"))
    return row


@router.put("/instances/{label}/nodes/{node}/vms/{vmid}/schedules/{sid}")
async def update_vm_schedule(label: str, node: str, vmid: int, sid: int, body: ScheduleRequest, request: Request,
                             user: dict = Depends(get_current_user)):
    _require_admin(user)
    days, at, note = _schedule_values(body)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _own_schedule(conn, label, node, vmid, sid)
        row = await conn.fetchrow(
            "UPDATE vm_schedules SET action = $2, days = $3, at_time = $4, enabled = $5, note = $6, keep = $7 WHERE id = $1 RETURNING *",
            sid, body.action, days, at, body.enabled, note, body.keep)
    state = (lambda: tr("aktif", "on")) if body.enabled else (lambda: tr("nonaktif", "off"))
    await log_activity(user, "VM_SCHEDULE_UPDATE", "INFO", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
                       both(lambda: tr(f"{user.get('username')} mengubah jadwal #{sid} VM {vmid}: {_schedule_label(body.action, days, at)} ({state()})",
                                       f"{user.get('username')} changed schedule #{sid} of VM {vmid}: {_schedule_label(body.action, days, at)} ({state()})")),
                       request)
    return _schedule_out(row)


@router.delete("/instances/{label}/nodes/{node}/vms/{vmid}/schedules/{sid}", status_code=204)
async def delete_vm_schedule(label: str, node: str, vmid: int, sid: int, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await _own_schedule(conn, label, node, vmid, sid)
        await conn.execute("DELETE FROM vm_schedules WHERE id = $1", sid)
    await log_activity(user, "VM_SCHEDULE_DELETE", "INFO", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
                       both(lambda: tr(f"{user.get('username')} menghapus jadwal VM {vmid}: {_schedule_label(row['action'], list(row['days']), row['at_time'])}",
                                       f"{user.get('username')} deleted a schedule of VM {vmid}: {_schedule_label(row['action'], list(row['days']), row['at_time'])}")),
                       request)
    return Response(status_code=204)


# ── konsol VM (noVNC, lihat routers/console.py) ───────────────────────────────

CONSOLE_TICKET_TTL = 60


@router.post("/instances/{label}/nodes/{node}/vms/{vmid}/console")
async def open_vm_console(label: str, node: str, vmid: int, request: Request, user: dict = Depends(get_current_user)):
    """Siapkan sesi konsol VNC (admin). Return id sesi (dipakai sekali oleh WebSocket /ws/console/{id}, berlaku 60 detik)
    dan password VNC sesi itu. Browser tidak pernah tersambung langsung ke Proxmox."""
    from services import onetime
    _require_admin(user)
    client = await _get_client(label)
    try:
        cfg = await client.get_vm_config(node, vmid)
        status = (await client.get_vm_status(node, vmid)).get("status")
        if status != "running":
            raise HTTPException(409, tr("Konsol hanya tersedia saat VM menyala", "The console is only available while the VM is running"))
        proxy = await client.vnc_proxy(node, vmid)
    except ProxmoxError as e:
        if e.status_code == 403:
            raise HTTPException(403, tr("Token Proxmox tidak punya izin VM.Console untuk VM ini", "The Proxmox token lacks VM.Console on this VM"))
        raise HTTPException(status_code=502, detail=e.detail)
    sid = onetime.new_id()
    name = cfg.get("name") or str(vmid)
    await onetime.put("console", sid, {"label": label, "node": node, "vmid": vmid, "port": proxy["port"],
                                        "ticket": proxy["ticket"], "uid": int(user["sub"]), "vm_name": name}, CONSOLE_TICKET_TTL)
    await log_activity(user, "VM_CONSOLE_OPEN", "WARNING", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
                       both(lambda: tr(f"{user.get('username')} membuka konsol VM {name} ({vmid}) di {label}/{node}",
                                       f"{user.get('username')} opened the console of VM {name} ({vmid}) on {label}/{node}")), request)
    return {"session": sid, "password": proxy["ticket"], "vm_name": name}


# ── reset lab (services/lab_reset.py) ─────────────────────────────────────────

class ResetBody(BaseModel):
    start: bool = True


@router.put("/instances/{label}/nodes/{node}/vms/{vmid}/baseline")
async def put_vm_baseline(label: str, node: str, vmid: int, request: Request, user: dict = Depends(get_current_user)):
    """Jadikan keadaan VM sekarang sebagai kondisi awal lab (snapshot ccd-baseline). VM harus mati."""
    _require_admin(user)
    host = _host_key(label, node)
    try:
        out = await lab_reset.set_baseline(host, str(vmid), user.get("username") or "")
    except ValueError:
        raise HTTPException(409, tr("Matikan VM dulu sebelum menjadikannya kondisi awal, supaya snapshot-nya konsisten",
                                    "Shut the VM down before making it the baseline, so the snapshot is consistent"))
    except ProxmoxError as e:
        raise HTTPException(502, e.detail)
    await log_activity(user, "LAB_BASELINE_SET", "INFO", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
                       both(lambda: tr(f"{user.get('username')} menetapkan kondisi awal lab untuk VM {vmid} di {label}/{node}",
                                       f"{user.get('username')} set the lab baseline of VM {vmid} on {label}/{node}")), request)
    return out


@router.delete("/instances/{label}/nodes/{node}/vms/{vmid}/baseline")
async def delete_vm_baseline(label: str, node: str, vmid: int, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    had = await lab_reset.remove_baseline(_host_key(label, node), str(vmid))
    if had:
        await log_activity(user, "LAB_BASELINE_REMOVE", "INFO", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
                           both(lambda: tr(f"{user.get('username')} menghapus kondisi awal lab VM {vmid} di {label}/{node}",
                                           f"{user.get('username')} removed the lab baseline of VM {vmid} on {label}/{node}")), request)
    return {"removed": had}


@router.post("/instances/{label}/nodes/{node}/vms/{vmid}/reset-lab")
async def reset_vm_lab(label: str, node: str, vmid: int, body: ResetBody, request: Request, user: dict = Depends(get_current_user)):
    """Kembalikan VM ke kondisi awal lab. Admin kapan saja; mahasiswa hanya untuk VM miliknya (akses penuh), kalau
    superadmin mengizinkan, di luar ujian, dan dengan jeda antar-reset."""
    host = _host_key(label, node)
    if user.get("role") not in (Role.SUPERADMIN, Role.SYSADMIN):
        if not (await lab_reset.get_settings())["students_can_reset"]:
            raise HTTPException(403, tr("Reset lab oleh mahasiswa belum diizinkan", "Lab reset by students is not enabled"))
        level = (await get_student_vm_access(int(user["sub"]), host)).get((str(vmid), host))
        if level is None:
            raise vm_access.no_access_error()
        if level != "full":
            raise vm_access.web_only_error()
        await exam_mode.deny_if_exam(user)
        wait = lab_reset.cooldown_left(host, str(vmid))
        if wait:
            raise HTTPException(429, tr(f"VM ini baru saja di-reset. Coba lagi dalam {wait} detik.", f"This VM was just reset. Try again in {wait} seconds."))
    result = await lab_reset.reset_vm(host, str(vmid), body.start)
    if not result["ok"]:
        code = 409 if result["reason"] == "no_baseline" else 502
        raise HTTPException(code, tr(f"Reset gagal: VM {lab_reset.reason_text(result['reason'])}", f"Reset failed: the VM {lab_reset.reason_text(result['reason'])}"))
    await log_activity(user, "LAB_RESET_VM", "WARNING", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
                       both(lambda: tr(f"{user.get('username')} mereset VM {vmid} di {label}/{node} ke kondisi awal lab",
                                       f"{user.get('username')} reset VM {vmid} on {label}/{node} to the lab baseline")), request)
    return {"vmid": vmid, **result}
