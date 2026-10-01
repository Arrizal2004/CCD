"""
VM credentials (vm_credentials table) + the Guacamole connection built from them.

Shared by the credentials form (routers/ssh_creds.py) and VM provisioning (routers/proxmox.py),
so a VM created from the dashboard is Connect-ready exactly like one configured by hand.
"""
import asyncio

from database import get_pool
from services.ssh_client import encrypt_secret
from services.guac_sync import sync_vm_connection, grant_vm_to_all_admins


async def save_vm_credentials(host_name: str, vm_id: str, *, os_type: str, cred_type: str,
                              guac_protocol: str, ssh_host: str, ssh_port: int, username: str,
                              password: str = "", pkey: str = "", wait_for_guac: bool = False) -> None:
    """Store credentials (encrypted) and (re)build the VM's Guacamole connection. The form fires the
    Guacamole sync in the background; provisioning awaits it so Connect works as soon as it returns.
    No ssh_host yet (e.g. DHCP VM whose IP isn't known) → credentials are stored, sync is skipped."""
    password_enc = encrypt_secret(password) if password else None
    pkey_enc = encrypt_secret(pkey) if pkey else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO vm_credentials
                (vm_id, host_name, os_type, cred_type, guac_protocol, ssh_host, ssh_port, username, password_enc, pkey_enc, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
            ON CONFLICT (vm_id, host_name) DO UPDATE SET
                os_type       = EXCLUDED.os_type,
                cred_type     = EXCLUDED.cred_type,
                guac_protocol = EXCLUDED.guac_protocol,
                ssh_host      = EXCLUDED.ssh_host,
                ssh_port      = EXCLUDED.ssh_port,
                username      = EXCLUDED.username,
                password_enc  = EXCLUDED.password_enc,
                pkey_enc      = EXCLUDED.pkey_enc,
                updated_at    = NOW()
        """, vm_id, host_name, os_type, cred_type, guac_protocol, ssh_host, ssh_port, username, password_enc, pkey_enc)
        vm_row = await conn.fetchrow("SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2", vm_id, host_name)
    if not vm_row or not ssh_host:
        return

    creds = {"os_type": os_type, "guac_protocol": guac_protocol, "ssh_host": ssh_host, "ssh_port": ssh_port,
             "username": username, "password": password, "pkey": pkey}

    async def _sync_and_grant():
        conn_id = await sync_vm_connection(host_name, vm_row["vm_name"], vm_id, creds)
        if conn_id:
            await grant_vm_to_all_admins(conn_id)

    if wait_for_guac:
        await _sync_and_grant()
    else:
        asyncio.create_task(_sync_and_grant())
