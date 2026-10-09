"""
Assign VM ke mahasiswa: catat di vm_assignments lalu beri akses koneksi Guacamole-nya di latar belakang.
Dipakai endpoint assign (Users), request VPS yang selesai, dan pembuatan VM massal.
"""
import asyncio

from database import get_pool
from services.guac_sync import (
    grant_connection, sync_os_account_connection, sync_vm_assignments, with_retry as guac_retry,
)
from services.ssh_client import decrypt_secret


async def assign_vm(user_id: int, vm_id: str, host_name: str, vm_name: str | None = None,
                    os_account_id: int | None = None, access: str = "full") -> bool:
    """False kalau user tidak ada. Assignment yang sudah ada diperbarui, bukan diduplikasi.
    access='web' (hanya Open Web) tidak membawa OS account dan tidak diberi koneksi Guacamole: koneksi
    yang sudah ada dicabut oleh sinkronisasi di bawah."""
    if access == "web":
        os_account_id = None
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not vm_name:
            vm_name = await conn.fetchval("SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2", vm_id, host_name)
        username = await conn.fetchval("SELECT username FROM users WHERE id = $1 AND deleted_at IS NULL", user_id)
        if not username:
            return False
        await conn.execute(
            """INSERT INTO vm_assignments (user_id, vm_id, host_name, vm_name, os_account_id, access)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (user_id, vm_id, host_name) WHERE deleted_at IS NULL DO UPDATE
               SET vm_name = COALESCE($4, vm_assignments.vm_name), os_account_id = $5, access = $6""",
            user_id, vm_id, host_name, vm_name, os_account_id, access)
        if os_account_id:
            os_acc = await conn.fetchrow(
                """SELECT voa.*, v.vm_name
                   FROM vm_os_accounts voa
                   LEFT JOIN vms v ON v.vm_id = $1 AND v.host_name = voa.host_name
                   WHERE voa.id = $2""", vm_id, os_account_id)
            arows = []
        else:
            os_acc = None
            arows = await conn.fetch(
                """SELECT va.vm_id, COALESCE(va.vm_name, v.vm_name) AS vm_name
                   FROM vm_assignments va
                   LEFT JOIN vms v ON v.vm_id = va.vm_id AND v.host_name = va.host_name
                   WHERE va.user_id = $1 AND va.host_name = $2 AND va.deleted_at IS NULL
                     AND va.access = 'full'""",
                user_id, host_name)

    if os_acc and os_acc["vm_name"]:
        async def _grant_os_acc():
            creds = {
                "os_type":       os_acc["os_type"],
                "guac_protocol": os_acc["guac_protocol"],
                "ssh_host":      os_acc["ssh_host"],
                "ssh_port":      os_acc["ssh_port"],
                "username":      os_acc["os_username"],
                "password":      decrypt_secret(os_acc["password_enc"]) if os_acc["password_enc"] else "",
                "pkey":          decrypt_secret(os_acc["pkey_enc"]) if os_acc["pkey_enc"] else "",
            }
            conn_id = await guac_retry(sync_os_account_connection, host_name, os_acc["vm_name"],
                                       os_acc["os_username"], creds, op_name="assign_vm:sync_os_conn")
            if conn_id:
                await guac_retry(grant_connection, username, conn_id, op_name="assign_vm:grant_conn")
            return bool(conn_id)
        asyncio.create_task(guac_retry(_grant_os_acc, op_name="assign_vm:os_account"))
    elif not os_account_id:
        vm_names = [r["vm_name"] for r in arows if r["vm_name"]]
        asyncio.create_task(guac_retry(sync_vm_assignments, username, host_name, vm_names,
                                       op_name="assign_vm:sync_assignments"))
    return True
