"""
Remove every dashboard-side trace of a VM, keyed by (host_name = "{instance}__{node}", vm_id).

Proxmox reuses freed VMIDs, so anything left behind here would silently attach to the *next* VM that
gets the same id — a student assigned to the old VM would gain the new one. Audit logs are kept on
purpose (audit trail).
"""
from database import get_pool
from services import guac_sync as g
from i18n import tr

# FKs between these are ON DELETE SET NULL / CASCADE, so the order is not load-bearing.
_TABLES = ("vm_assignments", "group_vm_access", "vm_os_accounts", "vm_credentials", "vm_metadata",
           "vm_iops_history", "vm_metrics_history", "vms")


async def purge_vm_records(host_name: str, vm_id: str) -> dict[str, int]:
    removed = {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for table in _TABLES:
                status = await conn.execute(f"DELETE FROM {table} WHERE vm_id = $1 AND host_name = $2", vm_id, host_name)
                removed[table] = int(status.split()[-1])
            # Requests are history — keep them (and linked_vm_name), just stop pointing at a VMID that may be reused.
            status = await conn.execute(
                "UPDATE infrastructure_requests SET linked_vm_id = NULL, linked_host_name = NULL "
                "WHERE linked_vm_id = $1 AND linked_host_name = $2", vm_id, host_name)
            removed["infrastructure_requests_unlinked"] = int(status.split()[-1])
    return removed


async def remove_guac_connections(host_name: str, vm_names: set[str | None]) -> int:
    """Delete the VM's main connection and its per-OS-account / @mandiri / @grp_* variants, ending
    live sessions on them first. Returns how many connections were removed."""
    mains = {g._conn_name(host_name, n) for n in vm_names if n}
    conns, status = await g._fetch("GET", f"/session/data/{g.GUAC_DS}/connections")
    if status != 200 or not isinstance(conns, dict):
        raise RuntimeError(tr(f"Guacamole tidak tersedia (HTTP {status})",
                              f"Guacamole is unavailable (HTTP {status})"))
    targets = {
        cid: c.get("name", "") for cid, c in conns.items()
        if isinstance(c, dict) and any(c.get("name") == m or c.get("name", "").startswith(m + "@") for m in mains)
    }
    if not targets:
        return 0
    for session in await g.get_active_sessions():
        if session.get("connection") in targets.values():
            await g.kill_session(session["active_id"])
    removed = 0
    for cid in targets:
        _, s = await g._fetch("DELETE", f"/session/data/{g.GUAC_DS}/connections/{cid}")
        removed += s < 300
    return removed
