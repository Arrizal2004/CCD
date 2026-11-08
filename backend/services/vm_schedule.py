"""
Jadwal VM: nyala (start), mati (shutdown), dan snapshot otomatis pada hari dan jam tertentu, menurut zona
waktu di Pengaturan Sistem. Dijalankan tiap menit oleh lifecycle job.

Sebuah jadwal dijalankan sekali untuk setiap waktu terjadwal: saat waktu itu sudah lewat, belum pernah
dijalankan untuk waktu itu, dan paling lama 15 menit yang lalu (kalau backend mati lebih lama dari itu, waktu
yang terlewat tidak dikejar, supaya VM tidak tiba-tiba menyala atau mati di jam yang salah).

Start dilewati kalau masa sewa VM sudah habis. Shutdown dimatikan paksa oleh Proxmox kalau sistem operasinya
tidak merespons dalam 2 menit. Snapshot otomatis bernama ccd-auto-<waktu>; hanya `keep` terbaru yang disimpan
dan snapshot dengan nama lain tidak pernah disentuh.
"""
import logging
from datetime import datetime, timedelta, timezone

from database import get_pool
from i18n import tr
from services import proxmox_instances as pve_instances
from services.audit import both, log_activity

log = logging.getLogger("vm_schedule")
GRACE = timedelta(minutes=15)
AUTO_PREFIX = "ccd-auto-"


def due_moment(days: list[int], at_time, now_local: datetime) -> datetime | None:
    """Waktu terjadwal terakhir yang sudah lewat hari ini (zona lokal), atau None kalau hari ini tidak terjadwal
    atau jamnya belum tiba."""
    if now_local.isoweekday() not in days:
        return None
    moment = now_local.replace(hour=at_time.hour, minute=at_time.minute, second=0, microsecond=0)
    return moment if moment <= now_local else None


def result_text(code: str) -> str:
    """Kode hasil (disimpan di last_result) sebagai kalimat dalam bahasa yang aktif."""
    if code.startswith("failed:"):
        return tr(f"gagal ({code[7:].strip()})", f"failed ({code[7:].strip()})")
    return {
        "started": tr("VM dinyalakan", "VM started"),
        "shutdown": tr("VM dimatikan", "VM shut down"),
        "running": tr("dilewati, VM sudah menyala", "skipped, the VM was already running"),
        "off": tr("dilewati, VM sudah mati", "skipped, the VM was already off"),
        "lease": tr("dilewati, masa sewa VM sudah habis", "skipped, the VM lease has ended"),
        "snapshot": tr("snapshot dibuat", "snapshot created"),
    }.get(code, code)


async def run_due(now: datetime | None = None) -> int:
    from services import system_settings as ss
    now = now or datetime.now(timezone.utc)
    now_local = now.astimezone(ss.tzinfo(await ss.get_settings()))
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.*, v.vm_name, v.lease_until FROM vm_schedules s
               LEFT JOIN vms v ON v.vm_id = s.vm_id AND v.host_name = s.host_name WHERE s.enabled""")
    done = 0
    for r in rows:
        moment = due_moment(list(r["days"]), r["at_time"], now_local)
        if moment is None or now_local - moment > GRACE:
            continue
        if r["last_run_at"] and r["last_run_at"] >= moment.astimezone(timezone.utc):
            continue
        result = await execute(r)
        async with pool.acquire() as conn:
            await conn.execute("UPDATE vm_schedules SET last_run_at = $2, last_result = $3 WHERE id = $1", r["id"], now, result)
        name = r["vm_name"] or r["vm_id"]
        label, _, node = r["host_name"].rpartition("__")
        await log_activity(None, "VM_SCHEDULE_RUN", "WARNING" if result.startswith("failed") else "INFO",
                           {"id": r["vm_id"], "name": f"{label}/{node}/{r['vm_id']}"},
                           both(lambda: tr(f"Jadwal VM {name} pukul {r['at_time'].strftime('%H:%M')}: ",
                                           f"VM schedule for {name} at {r['at_time'].strftime('%H:%M')}: ") + result_text(result)))
        done += 1
    return done


async def snapshot_keep(client, node: str, vmid: int, keep: int) -> str:
    """Buat snapshot ccd-auto-<waktu>, lalu hapus yang lebih lama dari `keep` terbaru."""
    name = AUTO_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    upid = await client.create_snapshot(node, vmid, name, "Snapshot otomatis dari jadwal CCD")
    if upid:
        await client.wait_task(node, upid)
    autos = sorted((s["name"] for s in await client.list_snapshots(node, vmid) if str(s.get("name", "")).startswith(AUTO_PREFIX)))
    for old in autos[:max(0, len(autos) - keep)]:
        await client.delete_snapshot(node, vmid, old)
    return "snapshot"


async def execute(r) -> str:
    """Jalankan satu jadwal. Return kode hasil: started, shutdown, running, off, lease, snapshot, atau failed: <pesan>."""
    label, _, node = r["host_name"].rpartition("__")
    vmid = int(r["vm_id"])
    if r["action"] == "start" and r["lease_until"] and r["lease_until"] <= datetime.now(timezone.utc):
        return "lease"
    try:
        client = await pve_instances.get_client(label)
        if r["action"] == "snapshot":
            return await snapshot_keep(client, node, vmid, int(r["keep"] or 3))
        status = (await client.get_vm_status(node, vmid)).get("status")
        if r["action"] == "start":
            if status == "running":
                return "running"
            await client.vm_action(node, vmid, "start")
            return "started"
        if status != "running":
            return "off"
        await client.shutdown_vm(node, vmid, timeout=120)
        return "shutdown"
    except Exception as e:
        log.warning("jadwal VM %s/%s gagal: %s", r["host_name"], r["vm_id"], e)
        return f"failed: {str(e)[:200]}"
