"""
Reset lab ke kondisi awal. Kondisi awal sebuah VM adalah snapshot `ccd-baseline` yang dibuat admin saat lab sedang
bersih (VM dimatikan dulu supaya konsisten). Reset mengembalikan disk VM ke snapshot itu, lalu menyalakannya lagi.

  - satu VM: admin, atau mahasiswa untuk VM miliknya kalau superadmin mengizinkan (app_settings 'lab_reset')
  - satu kelas: semua VM milik anggota kelas dan VM grupnya, berjalan di latar belakang dengan hasil per VM
  - dari mode ujian: dipakai saat ujian dimulai (services/exam_mode.py)

VM tanpa baseline dilewati dan dilaporkan; reset tidak pernah menyentuh VM yang tidak punya snapshot itu.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from database import get_pool
from i18n import tr
from services import app_settings
from services import proxmox_instances as pve_instances
from services.proxmox_client import ProxmoxError

log = logging.getLogger("lab_reset")

BASELINE = "ccd-baseline"
MAX_PARALLEL = 3
STUDENT_COOLDOWN = 120          # detik antara dua reset VM yang sama oleh mahasiswa
SETTINGS_KEY = "lab_reset"
SETTINGS_DEFAULTS = {"students_can_reset": False}
_recent: dict[tuple[str, str], float] = {}
_tasks: set = set()


async def get_settings() -> dict:
    return await app_settings.get(SETTINGS_KEY, SETTINGS_DEFAULTS)


async def save_settings(body: dict, by: str) -> dict:
    data = {"students_can_reset": bool((body or {}).get("students_can_reset"))}
    await app_settings.save(SETTINGS_KEY, data, by)
    return data


def split_host(host_name: str) -> tuple[str, str]:
    label, _, node = host_name.rpartition("__")
    return label, node


# ── baseline ──────────────────────────────────────────────────────────────────

async def set_baseline(host_name: str, vm_id: str, by: str) -> dict:
    """Buat (atau ganti) snapshot ccd-baseline. VM harus mati: snapshot dari VM yang sedang jalan bisa tidak konsisten."""
    label, node = split_host(host_name)
    vmid = int(vm_id)
    client = await pve_instances.get_client(label)
    status = (await client.get_vm_status(node, vmid)).get("status")
    if status == "running":
        raise ValueError("running")
    existing = {str(s.get("name")) for s in await client.list_snapshots(node, vmid)}
    if BASELINE in existing:
        upid = await client.delete_snapshot(node, vmid, BASELINE)
        if upid:
            await client.wait_task(node, upid)
    upid = await client.create_snapshot(node, vmid, BASELINE, "Kondisi awal lab (CCD)")
    if upid:
        await client.wait_task(node, upid)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vm_baselines (host_name, vm_id, snapname, created_by, created_at) VALUES ($1, $2, $3, $4, NOW())
               ON CONFLICT (host_name, vm_id) DO UPDATE SET created_by = EXCLUDED.created_by, created_at = NOW()""",
            host_name, vm_id, BASELINE, by)
    return {"host_name": host_name, "vm_id": vm_id, "snapname": BASELINE}


async def remove_baseline(host_name: str, vm_id: str) -> bool:
    label, node = split_host(host_name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        had = await conn.fetchval("DELETE FROM vm_baselines WHERE host_name = $1 AND vm_id = $2 RETURNING 1", host_name, vm_id)
    try:
        client = await pve_instances.get_client(label)
        if BASELINE in {str(s.get("name")) for s in await client.list_snapshots(node, int(vm_id))}:
            upid = await client.delete_snapshot(node, int(vm_id), BASELINE)
            if upid:
                await client.wait_task(node, upid)
    except Exception as e:
        log.warning("baseline %s/%s tidak bisa dihapus dari Proxmox: %s", host_name, vm_id, e)
    return bool(had)


async def baselines(host_name: str) -> set[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {r["vm_id"] for r in await conn.fetch("SELECT vm_id FROM vm_baselines WHERE host_name = $1", host_name)}


# ── reset ─────────────────────────────────────────────────────────────────────

async def reset_vm(host_name: str, vm_id: str, start: bool = True) -> dict:
    """Kembalikan satu VM ke baseline. Hasil: {ok, started, reason}. reason: no_baseline | failed:<pesan>."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        has = await conn.fetchval("SELECT 1 FROM vm_baselines WHERE host_name = $1 AND vm_id = $2", host_name, vm_id)
    if not has:
        return {"ok": False, "started": False, "reason": "no_baseline"}
    label, node = split_host(host_name)
    vmid = int(vm_id)
    try:
        client = await pve_instances.get_client(label)
        upid = await client.rollback_snapshot(node, vmid, BASELINE)
        if upid:
            await client.wait_task(node, upid, timeout=600)
        started = False
        if start:
            await client.wait_task(node, await client.vm_action(node, vmid, "start"), timeout=120)
            started = True
        return {"ok": True, "started": started, "reason": ""}
    except Exception as e:
        detail = e.detail if isinstance(e, ProxmoxError) else str(e)
        log.warning("reset %s/%s gagal: %s", host_name, vm_id, detail)
        return {"ok": False, "started": False, "reason": f"failed:{detail[:200]}"}
    finally:
        _recent[(host_name, vm_id)] = time.monotonic()


def cooldown_left(host_name: str, vm_id: str) -> int:
    last = _recent.get((host_name, vm_id))
    return max(0, int(STUDENT_COOLDOWN - (time.monotonic() - last))) if last else 0


def reason_text(code: str) -> str:
    if code == "no_baseline":
        return tr("belum punya baseline", "has no baseline yet")
    if code.startswith("failed:"):
        return tr(f"gagal ({code[7:]})", f"failed ({code[7:]})")
    return code


async def group_vms(group_id: int) -> list[dict]:
    """VM sebuah kelas: yang di-assign langsung ke anggotanya, ditambah VM milik grup."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT x.host_name, x.vm_id, COALESCE(v.vm_name, x.vm_id) AS vm_name FROM (
                   SELECT va.host_name, va.vm_id FROM vm_assignments va
                   JOIN group_members gm ON gm.user_id = va.user_id
                   WHERE gm.group_id = $1 AND va.deleted_at IS NULL
                   UNION
                   SELECT host_name, vm_id FROM group_vm_access WHERE group_id = $1) x
               LEFT JOIN vms v ON v.vm_id = x.vm_id AND v.host_name = x.host_name
               ORDER BY x.host_name, vm_name""", group_id)
    return [dict(r) for r in rows]


async def _new_run(scope: str, group_id, exam_id, by: str, start: bool, items: list[dict]) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO lab_resets (scope, group_id, exam_id, requested_by, start_after, items)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb) RETURNING id""",
            scope, group_id, exam_id, by, start, json.dumps(items))


async def _save_items(run_id: int, items: list[dict], done: bool) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE lab_resets SET items = $2::jsonb, status = $3, finished_at = CASE WHEN $4 THEN NOW() ELSE NULL END WHERE id = $1",
            run_id, json.dumps(items), "done" if done else "running", done)


async def run_items(run_id: int, items: list[dict], start: bool) -> list[dict]:
    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def one(it: dict) -> None:
        async with sem:
            it.update(await reset_vm(it["host_name"], it["vm_id"], start))
            it["pending"] = False
            await _save_items(run_id, items, False)

    await asyncio.gather(*(one(it) for it in items))
    await _save_items(run_id, items, True)
    return items


async def start_group_reset(host_filter, group_id: int, by: str, start: bool = True, scope: str = "group", exam_id=None) -> int:
    """Mulai reset semua VM kelas di latar belakang. host_filter(host_name) -> bool membatasi (sysadmin per Proxmox)."""
    vms = [v for v in await group_vms(group_id) if host_filter(v["host_name"])]
    items = [{**v, "pending": True, "ok": False, "started": False, "reason": ""} for v in vms]
    run_id = await _new_run(scope, group_id, exam_id, by, start, items)
    task = asyncio.create_task(run_items(run_id, items, start))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run_id


async def get_run(run_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM lab_resets WHERE id = $1", run_id)
    if not row:
        return None
    out = dict(row)
    out["items"] = json.loads(out["items"]) if isinstance(out["items"], str) else out["items"]
    return out


async def recent_runs(limit: int = 20) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT r.id, r.scope, r.group_id, g.name AS group_name, r.exam_id, r.requested_by, r.status,
                      r.started_at, r.finished_at, jsonb_array_length(r.items) AS total,
                      (SELECT count(*) FROM jsonb_array_elements(r.items) i WHERE (i->>'ok')::boolean) AS ok
               FROM lab_resets r LEFT JOIN groups g ON g.id = r.group_id ORDER BY r.id DESC LIMIT $1""", limit)
    return [dict(r) for r in rows]


async def recover_interrupted() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE lab_resets SET status = 'failed', finished_at = NOW() WHERE status = 'running'")
        await conn.execute("DELETE FROM lab_resets WHERE started_at < NOW() - INTERVAL '90 days'")
