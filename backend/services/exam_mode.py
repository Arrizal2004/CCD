"""
Mode ujian per kelas (grup). Jadwal: mulai dan selesai. Selama ujian berjalan (waktu sekarang di antara keduanya):

  - mahasiswa kelas itu hanya melihat dan membuka VM ujian, yaitu VM yang di-assign ke anggota kelas dan VM milik grup;
    VM lain mereka tidak terlihat sampai ujian selesai (database.get_student_vm_access);
  - mereka tidak bisa menyalakan, mematikan, snapshot, rollback, atau me-reset VM (proxmox._require_admin_or_assigned);
  - Open Web ditutup kalau block_open_web (openweb._authorize);
  - admin tidak terpengaruh.

Efek samping dijalankan sekali oleh lifecycle job (tiap menit):
  mulai  -> VM dikembalikan ke baseline (reset_at_start), dinyalakan (start_vms), sesi Remote kelas diputus agar masuk
            ulang ke kondisi bersih;
  selesai -> VM dimatikan (shutdown_at_end) dan sesi Remote kelas diputus.
Kalau backend baru menemukan ujian yang jendelanya sudah lewat, ujian ditandai selesai tanpa menjalankan aksi mulai.
Pembatasan mengikuti jam, bukan job, jadi tetap tepat waktu walau job terlambat satu menit.
"""
import logging
import time
from datetime import datetime, timezone

from fastapi import HTTPException

from database import get_pool
from i18n import tr
from services import lab_reset
from services import proxmox_instances as pve_instances
from services.audit import both, log_activity

log = logging.getLogger("exam_mode")

_CACHE_TTL = 10.0
_cache: dict[int, tuple[float, dict | None]] = {}
_vm_cache: dict[int, tuple[float, set]] = {}


def forget() -> None:
    _cache.clear()
    _vm_cache.clear()


async def active_for(user_id: int) -> dict | None:
    """Ujian yang sedang berjalan untuk kelas milik mahasiswa ini (yang berakhir paling cepat), atau None."""
    now = time.monotonic()
    hit = _cache.get(user_id)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT e.id, e.name, e.group_id, e.starts_at, e.ends_at, e.block_open_web
               FROM exams e JOIN group_members gm ON gm.group_id = e.group_id
               WHERE gm.user_id = $1 AND e.status IN ('scheduled', 'running')
                 AND e.starts_at <= NOW() AND e.ends_at > NOW()
               ORDER BY e.ends_at LIMIT 1""", user_id)
    exam = dict(row) if row else None
    if len(_cache) > 5000:
        _cache.clear()
    _cache[user_id] = (now, exam)
    return exam


async def exam_vms(group_id: int) -> set[tuple[str, str]]:
    """{(vm_id, host_name)} yang termasuk ujian kelas ini."""
    hit = _vm_cache.get(group_id)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    vms = {(v["vm_id"], v["host_name"]) for v in await lab_reset.group_vms(group_id)}
    _vm_cache[group_id] = (time.monotonic(), vms)
    return vms


async def restrict_levels(user_id: int, levels: dict) -> dict:
    """Dipanggil get_student_vm_access: selama ujian, hanya VM ujian yang tersisa."""
    exam = await active_for(user_id)
    if not exam:
        return levels
    allowed = await exam_vms(exam["group_id"])
    return {k: v for k, v in levels.items() if k in allowed}


def exam_error(exam: dict) -> HTTPException:
    return HTTPException(403, tr(f"Sedang ujian '{exam['name']}': aksi ini dinonaktifkan sampai ujian selesai",
                                 f"Exam '{exam['name']}' is in progress: this action is disabled until it ends"))


async def deny_if_exam(user: dict, open_web: bool = False) -> None:
    """403 untuk mahasiswa yang sedang ujian (aksi yang mengubah VM; open_web=True untuk Open Web, kalau ditutup)."""
    if user.get("role") != "student":
        return
    exam = await active_for(int(user["sub"]))
    if exam and (not open_web or exam["block_open_web"]):
        raise exam_error(exam)


# ── siklus hidup ──────────────────────────────────────────────────────────────

def _summary(items: list[dict]) -> dict:
    return {"total": len(items), "ok": sum(1 for i in items if i.get("ok")),
            "failed": [{"vm": i["vm_name"], "reason": i["reason"]} for i in items if not i.get("ok")]}


async def _members(group_id: int) -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT u.username FROM group_members gm JOIN users u ON u.id = gm.user_id WHERE gm.group_id = $1", group_id)
    return [r["username"] for r in rows]


async def _disconnect(group_id: int) -> None:
    try:
        from routers.guac import disconnect_user
        for name in await _members(group_id):
            await disconnect_user(name)
    except Exception as e:
        log.warning("memutus sesi kelas %s gagal: %s", group_id, e)


async def _power(vms: list[dict], action: str) -> dict:
    """Nyalakan atau matikan semua VM; hasil per VM dalam ringkasan. Yang sudah dalam keadaan itu dilewati."""
    items = []
    for v in vms:
        label, node = lab_reset.split_host(v["host_name"])
        entry = {**v, "ok": True, "reason": ""}
        try:
            client = await pve_instances.get_client(label)
            status = (await client.get_vm_status(node, int(v["vm_id"]))).get("status")
            if action == "shutdown" and status == "running":
                await client.shutdown_vm(node, int(v["vm_id"]), timeout=120)
            elif action == "start" and status != "running":
                await client.wait_task(node, await client.vm_action(node, int(v["vm_id"]), "start"), timeout=120)
        except Exception as e:
            entry.update(ok=False, reason=f"failed:{str(getattr(e, 'detail', e))[:200]}")
        items.append(entry)
    return _summary(items)


async def begin(exam_id: int, by: str = "") -> None:
    """Aksi saat ujian mulai: reset ke baseline, nyalakan VM, putus sesi kelas."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        e = await conn.fetchrow("SELECT * FROM exams WHERE id = $1", exam_id)
    if not e:
        return
    result: dict = {}
    vms = await lab_reset.group_vms(e["group_id"])
    if e["reset_at_start"] and vms:
        items = [{**v, "ok": False, "started": False, "reason": ""} for v in vms]
        run_id = await lab_reset._new_run("exam", e["group_id"], exam_id, by or "sistem", e["start_vms"], items)
        items = await lab_reset.run_items(run_id, items, e["start_vms"])
        result["reset"] = _summary(items)
        result["reset_run"] = run_id
    elif e["start_vms"] and vms:
        result["start"] = await _power(vms, "start")
    await _disconnect(e["group_id"])
    async with pool.acquire() as conn:
        await conn.execute("UPDATE exams SET start_result = $2::jsonb WHERE id = $1", exam_id, _json(result))
    await log_activity(None, "EXAM_START", "WARNING", {"id": str(exam_id), "name": e["name"]},
                       both(lambda: tr(f"Ujian '{e['name']}' dimulai ({len(vms)} VM)", f"Exam '{e['name']}' started ({len(vms)} VMs)")))


async def conclude(exam_id: int, by: str = "") -> None:
    """Aksi saat ujian selesai: matikan VM dan putus sesi kelas."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        e = await conn.fetchrow("SELECT * FROM exams WHERE id = $1", exam_id)
    if not e:
        return
    result: dict = {}
    vms = await lab_reset.group_vms(e["group_id"])
    if e["shutdown_at_end"] and vms:
        result["shutdown"] = await _power(vms, "shutdown")
    await _disconnect(e["group_id"])
    async with pool.acquire() as conn:
        await conn.execute("UPDATE exams SET end_result = $2::jsonb WHERE id = $1", exam_id, _json(result))
    await log_activity(None, "EXAM_END", "WARNING", {"id": str(exam_id), "name": e["name"]},
                       both(lambda: tr(f"Ujian '{e['name']}' selesai", f"Exam '{e['name']}' ended")))


def _json(value) -> str:
    import json
    return json.dumps(value)


async def tick(now: datetime | None = None) -> int:
    """Gerakkan ujian: scheduled -> running -> finished. Return jumlah transisi."""
    pool = await get_pool()
    done = 0
    async with pool.acquire() as conn:
        late = await conn.fetch(      # jendela sudah lewat sebelum sempat dimulai: langsung selesai, tanpa aksi
            """UPDATE exams SET status = 'finished', finished_at = NOW()
               WHERE status = 'scheduled' AND ends_at <= NOW() RETURNING id, name""")
        starting = await conn.fetch(
            """UPDATE exams SET status = 'running', started_at = NOW()
               WHERE status = 'scheduled' AND starts_at <= NOW() AND ends_at > NOW() RETURNING id""")
        ending = await conn.fetch(
            """UPDATE exams SET status = 'finished', finished_at = NOW()
               WHERE status = 'running' AND ends_at <= NOW() RETURNING id""")
    if late or starting or ending:
        forget()
    for r in late:
        await log_activity(None, "EXAM_END", "WARNING", {"id": str(r["id"]), "name": r["name"]},
                           both(lambda: tr(f"Ujian '{r['name']}' dilewati: jendelanya sudah lewat sebelum dimulai",
                                           f"Exam '{r['name']}' was skipped: its window passed before it started")))
        done += 1
    for r in starting:
        await begin(r["id"])
        done += 1
    for r in ending:
        await conclude(r["id"])
        done += 1
    return done
