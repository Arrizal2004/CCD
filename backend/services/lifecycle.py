"""
Masa berlaku akun dan masa sewa VM, dijalankan tiap menit oleh run_lifecycle_job().

Akun: setelah users.expires_at lewat, login dan token lamanya sudah ditolak oleh auth.verify_token().
Job ini menjalankan efek sampingnya sekali: akun Guacamole dinonaktifkan, sesi remote yang masih
berjalan diputus, dan kejadiannya dicatat di Activity Log.

VM: setelah vms.lease_until lewat, VM yang masih menyala dimatikan sekali (shutdown, lalu dipaksa
mati kalau tidak merespons dalam 2 menit). Mahasiswa tidak bisa menyalakannya lagi sampai admin
memperpanjang masa sewa; admin tetap bisa, dan job tidak mematikannya lagi.
"""
import asyncio
import logging

from database import get_pool
from services import proxmox_instances as pve_instances
from services.audit import log_activity

log = logging.getLogger("lifecycle")

INTERVAL_SECONDS = 60


async def expire_accounts() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """UPDATE users SET expiry_enforced_at = NOW()
               WHERE expires_at IS NOT NULL AND expires_at <= NOW() AND expiry_enforced_at IS NULL
                     AND deleted_at IS NULL
               RETURNING id, username, role, expires_at""")
    if not rows:
        return 0
    from auth import forget_account
    from routers.guac import disconnect_user
    from services.guac_sync import sync_user_disabled, with_retry as guac_retry
    for r in rows:
        forget_account(r["id"])
        try:
            await disconnect_user(r["username"])
        except Exception as e:
            log.warning("gagal memutus sesi %s: %s", r["username"], e)
        asyncio.create_task(guac_retry(sync_user_disabled, r["username"], disabled=True,
                                       op_name="lifecycle:expire_account"))
        await log_activity({"sub": r["id"], "username": r["username"], "role": r["role"]},
                           "ACCOUNT_EXPIRED", "WARNING", None,
                           f"Masa berlaku akun {r['username']} habis ({r['expires_at']:%Y-%m-%d %H:%M} UTC); login ditolak")
    return len(rows)


async def enforce_leases() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT vm_id, host_name, vm_name, ccd_id, lease_until FROM vms
               WHERE lease_until IS NOT NULL AND lease_until <= NOW() AND lease_enforced_at IS NULL""")
    done = 0
    for r in rows:
        label, _, node = r["host_name"].rpartition("__")
        vmid = int(r["vm_id"])
        try:
            client = await pve_instances.get_client(label)
            status = (await client.get_vm_status(node, vmid)).get("status")
            if status == "running":
                await client.shutdown_vm(node, vmid, timeout=120)
        except Exception as e:
            # Proxmox tidak terjangkau: dicoba lagi menit berikutnya.
            log.warning("masa sewa VM %s/%s habis tetapi gagal dimatikan: %s", r["host_name"], vmid, e)
            continue
        async with pool.acquire() as conn:
            await conn.execute("UPDATE vms SET lease_enforced_at = NOW() WHERE vm_id = $1 AND host_name = $2",
                               r["vm_id"], r["host_name"])
        what = "dimatikan otomatis" if status == "running" else "sudah dalam keadaan mati"
        await log_activity(None, "VM_LEASE_EXPIRED", "WARNING", {"id": r["vm_id"], "name": f"{label}/{node}/{vmid}"},
                           f"Masa sewa VM {r['vm_name']} (CCD-{r['ccd_id']:04d}) habis; VM {what}")
        done += 1
    return done


async def run_lifecycle_job() -> None:
    log.info("Lifecycle job started (tiap %ss)", INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(INTERVAL_SECONDS)
        for job in (expire_accounts, enforce_leases):
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("%s gagal: %s", job.__name__, e)
