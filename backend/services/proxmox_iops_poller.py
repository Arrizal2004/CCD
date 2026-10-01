"""
Disk IOPS sampler for Proxmox VMs.

Proxmox's RRD only keeps disk *throughput* (diskread/diskwrite bytes/s), not operation counts,
so IOPS history has to be built here: every POLL_INTERVAL seconds, for each running VM, read the
cumulative per-drive op counters from status/current (blockstat.rd_operations/wr_operations),
turn the delta into ops/s and store it in vm_iops_history (host_name = "{instance}__{node}",
same key as the rest of the Proxmox integration). Retention follows the metrics cleanup job.
"""
import asyncio
import logging
import os
import time

from database import get_pool
from services import proxmox_instances as pve

log = logging.getLogger("proxmox_iops_poller")

POLL_INTERVAL = int(os.getenv("IOPS_POLL_INTERVAL", "15"))
_MAX_CONCURRENCY = 8
_WARN_EVERY_SEC = 600

_prev: dict[tuple[str, str], tuple[float, int, int]] = {}  # (host_key, vmid) → (ts, rd_ops, wr_ops)
_last_warn: dict[str, float] = {}


def forget(host_key: str, vmid) -> None:
    """Drop the counter baseline of a deleted VM, so a new VM reusing its VMID starts clean."""
    _prev.pop((host_key, str(vmid)), None)


def _op_totals(status: dict) -> tuple[int, int] | None:
    blocks = status.get("blockstat") or {}
    if not blocks:
        return None
    rd = sum(int(b.get("rd_operations") or 0) for b in blocks.values())
    wr = sum(int(b.get("wr_operations") or 0) for b in blocks.values())
    return rd, wr


def compute_iops(prev: tuple[float, int, int] | None, now: float, rd: int, wr: int) -> tuple[float, float] | None:
    """Pure: ops/s since the previous sample, or None when there is no usable baseline
    (first sample, or counters went backwards because the VM was restarted)."""
    if not prev:
        return None
    t0, rd0, wr0 = prev
    dt = now - t0
    if dt <= 0 or rd < rd0 or wr < wr0:
        return None
    return round((rd - rd0) / dt, 2), round((wr - wr0) / dt, 2)


def _warn(label: str, msg: str, *args):
    now = time.monotonic()
    if now - _last_warn.get(label, 0) >= _WARN_EVERY_SEC:
        _last_warn[label] = now
        log.warning(msg, *args)


async def _sample_instance(label: str, sem: asyncio.Semaphore) -> list[tuple]:
    rows = []
    try:
        client = await pve.get_client(label)
        nodes = await client.list_nodes()
    except Exception as e:
        _warn(label, "IOPS poller: instance %s unreachable: %s", label, e)
        return rows

    async def one(node: str, vmid: int):
        async with sem:
            try:
                status = await client.get_vm_status(node, vmid)
            except Exception as e:
                log.debug("IOPS poller: %s/%s/%s status failed: %s", label, node, vmid, e)
                return
        totals = _op_totals(status)
        if not totals:
            return
        key, now = (f"{label}__{node}", str(vmid)), time.time()
        iops = compute_iops(_prev.get(key), now, *totals)
        _prev[key] = (now, *totals)
        if iops:
            rows.append((key[1], key[0], iops[0], iops[1]))

    tasks = []
    for n in nodes:
        if n.get("status") != "online" or not n.get("node"):
            continue
        try:
            vms = await client.list_vms(n["node"])
        except Exception as e:
            _warn(label, "IOPS poller: listing VMs on %s/%s failed: %s", label, n["node"], e)
            continue
        tasks += [one(n["node"], vm["vmid"]) for vm in vms if vm.get("status") == "running"]
    await asyncio.gather(*tasks)
    return rows


async def _tick():
    instances = await pve.list_instances()
    if not instances:
        return
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    batches = await asyncio.gather(*(_sample_instance(i["label"], sem) for i in instances))
    rows = [r for batch in batches for r in batch]
    if not rows:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO vm_iops_history (vm_id, host_name, read_iops, write_iops) VALUES ($1, $2, $3, $4)", rows)


async def run_iops_poller():
    log.info("Proxmox IOPS poller started (interval=%ss)", POLL_INTERVAL)
    while True:
        try:
            await _tick()
        except Exception as e:
            log.warning("IOPS poller tick error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)
