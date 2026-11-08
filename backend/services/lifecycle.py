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
from i18n import tr
from services.audit import both, log_activity

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
                           both(lambda: tr(f"Masa berlaku akun {r['username']} habis ({r['expires_at']:%Y-%m-%d %H:%M} UTC); login ditolak",
                                           f"The account {r['username']} expired ({r['expires_at']:%Y-%m-%d %H:%M} UTC); sign-in refused")))
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
        def _detail():
            what = (tr("dimatikan otomatis", "was shut down automatically") if status == "running"
                    else tr("sudah dalam keadaan mati", "was already off"))
            return tr(f"Masa sewa VM {r['vm_name']} (CCD-{r['ccd_id']:04d}) habis; VM {what}",
                      f"The lease of VM {r['vm_name']} (CCD-{r['ccd_id']:04d}) expired; the VM {what}")
        await log_activity(None, "VM_LEASE_EXPIRED", "WARNING", {"id": r["vm_id"], "name": f"{label}/{node}/{vmid}"},
                           both(_detail))
        done += 1
    return done


async def warn_leases() -> int:
    """Email peringatan ke pemilik VM beberapa hari sebelum masa sewa habis, sekali per batas sewa."""
    from services import mailer, system_settings as ss
    cfg = await mailer.get_config()
    if not (cfg["smtp"]["enabled"] and cfg["notify"].get("lease")):
        return 0
    days = int(cfg["notify"].get("lease_days_before") or 3)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT vm_id, host_name, vm_name, ccd_id, lease_until FROM vms
               WHERE lease_until IS NOT NULL AND lease_until > NOW() AND lease_until <= NOW() + make_interval(days => $1)
                     AND lease_enforced_at IS NULL AND lease_warned_for IS DISTINCT FROM lease_until""", days)
    brand = (await ss.get_settings()).get("name") or "CCD"
    done = 0
    for r in rows:
        async with pool.acquire() as conn:
            owners = await conn.fetch(
                """SELECT u.email FROM vm_assignments a JOIN users u ON u.id = a.user_id
                   WHERE a.vm_id = $1 AND a.host_name = $2 AND u.email IS NOT NULL AND u.email <> ''
                         AND u.deleted_at IS NULL AND u.is_active""", r["vm_id"], r["host_name"])
            await conn.execute("UPDATE vms SET lease_warned_for = lease_until WHERE vm_id = $1 AND host_name = $2",
                               r["vm_id"], r["host_name"])
        when = r["lease_until"].astimezone(ss.tzinfo()).strftime("%Y-%m-%d %H:%M")
        name = f"{r['vm_name']} (CCD-{r['ccd_id']:04d})"
        text, html_body = mailer.render(
            brand, tr("Masa sewa VM segera habis", "Your VM lease ends soon"),
            [tr(f"Masa sewa VM {name} berakhir pada {when}. Setelah itu VM dimatikan otomatis.",
                f"The lease of VM {name} ends on {when}. The VM is then shut down automatically."),
             tr("Perlu waktu lebih lama? Minta perpanjangan lewat Helpdesk sebelum waktunya habis.",
                "Need more time? Ask for an extension through the Helpdesk before it ends.")],
            mailer.dashboard_url(cfg) or None, tr("Buka dashboard", "Open the dashboard"))
        done += await mailer.enqueue("lease", [o["email"] for o in owners],
                                     tr(f"Masa sewa VM {name} segera habis", f"The lease of VM {name} ends soon"),
                                     text, html_body, cfg=cfg)
    return done


async def run_schedules() -> int:
    from services import vm_schedule
    return await vm_schedule.run_due()


async def run_exams() -> int:
    from services import exam_mode
    return await exam_mode.tick()


async def run_lifecycle_job() -> None:
    log.info("Lifecycle job started (tiap %ss)", INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(INTERVAL_SECONDS)
        for job in (expire_accounts, enforce_leases, warn_leases, run_schedules, run_exams):
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("%s gagal: %s", job.__name__, e)
