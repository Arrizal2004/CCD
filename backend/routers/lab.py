"""
Fitur kelas (admin): reset lab, mode ujian, dan laporan pemakaian. Lihat services/lab_reset.py, exam_mode.py, usage_report.py.

  GET/PUT /api/v1/lab/settings                 mahasiswa boleh me-reset VM sendiri (superadmin)
  POST    /api/v1/lab/groups/{gid}/reset       reset semua VM kelas ke baseline (latar belakang)
  GET     /api/v1/lab/resets                   eksekusi reset terbaru
  GET     /api/v1/lab/resets/{id}              hasil per VM
  GET     /api/v1/lab/exams                    daftar ujian (?group_id=)
  POST    /api/v1/lab/exams                    jadwalkan ujian
  PUT     /api/v1/lab/exams/{id}               ubah ujian yang belum mulai
  POST    /api/v1/lab/exams/{id}/start-now     mulai sekarang
  POST    /api/v1/lab/exams/{id}/end-now       akhiri sekarang
  POST    /api/v1/lab/exams/{id}/cancel        batalkan (belum mulai atau sedang berjalan)
  DELETE  /api/v1/lab/exams/{id}               hapus (belum mulai, selesai, atau dibatalkan)
  GET     /api/v1/lab/groups/{gid}/usage-report  laporan pemakaian (?from=&to=&format=csv)
"""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth import get_current_user, require_superadmin, require_sysadmin
from database import get_pool
from i18n import tr
from services import exam_mode, lab_reset, scope, usage_report
from services import system_settings as ss
from services.audit import both, log_activity
from services.csv_export import csv_response

router = APIRouter()


async def _group(group_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, name FROM groups WHERE id = $1", group_id)
    if not row:
        raise HTTPException(404, tr("Grup tidak ditemukan", "Group not found"))
    return dict(row)


async def _host_filter(user: dict):
    """Sysadmin hanya boleh menyentuh VM di Proxmox yang ditugaskan kepadanya."""
    allowed = await scope.allowed_labels(user)
    return (lambda host: True) if allowed is None else (lambda host: scope.host_label(host) in allowed)


# ── pengaturan ────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_lab_settings(user: dict = Depends(require_sysadmin)):
    return await lab_reset.get_settings()


@router.put("/settings")
async def put_lab_settings(body: dict, request: Request, user: dict = Depends(require_superadmin)):
    data = await lab_reset.save_settings(body, user.get("username") or "")
    await log_activity(user, "LAB_SETTINGS", "WARNING", None,
                       both(lambda: tr("Mahasiswa boleh me-reset VM sendiri", "Students may reset their own VMs") if data["students_can_reset"]
                            else tr("Mahasiswa tidak boleh me-reset VM sendiri", "Students may not reset their own VMs")), request)
    return data


@router.get("/groups/{group_id}/vms")
async def group_lab_vms(group_id: int, user: dict = Depends(require_sysadmin)):
    """VM sebuah kelas beserta status baseline-nya (untuk panel Lab)."""
    await _group(group_id)
    ok = await _host_filter(user)
    out = []
    for v in await lab_reset.group_vms(group_id):
        if ok(v["host_name"]):
            out.append({**v, "baseline": v["vm_id"] in await lab_reset.baselines(v["host_name"])})
    return out


@router.get("/my-exam")
async def my_exam(user: dict = Depends(get_current_user)):
    """Ujian yang sedang atau akan segera berlangsung untuk kelas pengguna ini (untuk spanduk di dashboard)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT e.name, e.starts_at, e.ends_at, e.block_open_web FROM exams e JOIN group_members gm ON gm.group_id = e.group_id
               WHERE gm.user_id = $1 AND e.status IN ('scheduled', 'running') AND e.ends_at > NOW() AND e.starts_at <= NOW() + interval '24 hours'
               ORDER BY e.starts_at LIMIT 1""", int(user["sub"]))
    if not row:
        return {"exam": None}
    now = datetime.now(timezone.utc)
    return {"exam": {"name": row["name"], "starts_at": row["starts_at"].isoformat(), "ends_at": row["ends_at"].isoformat(),
                     "block_open_web": row["block_open_web"], "live": row["starts_at"] <= now < row["ends_at"]}}


# ── reset kelas ───────────────────────────────────────────────────────────────

class GroupResetBody(BaseModel):
    start: bool = True


@router.post("/groups/{group_id}/reset", status_code=202)
async def reset_group(group_id: int, body: GroupResetBody, request: Request, user: dict = Depends(require_sysadmin)):
    g = await _group(group_id)
    if (await exam_mode_running(group_id)):
        raise HTTPException(409, tr("Kelas ini sedang ujian; reset ditunda sampai ujian selesai", "This class is in an exam; the reset waits until it ends"))
    run_id = await lab_reset.start_group_reset(await _host_filter(user), group_id, user.get("username") or "", body.start)
    run = await lab_reset.get_run(run_id)
    await log_activity(user, "LAB_RESET_GROUP", "WARNING", {"id": str(group_id), "name": g["name"]},
                       both(lambda: tr(f"{user.get('username')} mereset lab kelas '{g['name']}' ke kondisi awal ({len(run['items'])} VM, proses #{run_id})",
                                       f"{user.get('username')} reset the lab of class '{g['name']}' to its baseline ({len(run['items'])} VMs, job #{run_id})")), request)
    return {"id": run_id, "total": len(run["items"])}


async def exam_mode_running(group_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM exams WHERE group_id = $1 AND status IN ('scheduled', 'running') AND starts_at <= NOW() AND ends_at > NOW()", group_id))


def _run_out(run: dict) -> dict:
    items = [{**i, "reason_text": lab_reset.reason_text(i["reason"]) if i.get("reason") else ""} for i in run["items"]]
    return {**{k: run[k] for k in ("id", "scope", "group_id", "exam_id", "requested_by", "status", "start_after")},
            "started_at": run["started_at"].isoformat(), "finished_at": run["finished_at"].isoformat() if run["finished_at"] else None,
            "items": items, "total": len(items), "ok": sum(1 for i in items if i.get("ok"))}


@router.get("/resets")
async def list_resets(user: dict = Depends(require_sysadmin)):
    return [{**r, "started_at": r["started_at"].isoformat(), "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None}
            for r in await lab_reset.recent_runs()]


@router.get("/resets/{run_id}")
async def get_reset(run_id: int, user: dict = Depends(require_sysadmin)):
    run = await lab_reset.get_run(run_id)
    if not run:
        raise HTTPException(404, tr("Proses reset tidak ditemukan", "Reset job not found"))
    return _run_out(run)


# ── ujian ─────────────────────────────────────────────────────────────────────

class ExamBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    group_id: int
    starts_at: datetime
    ends_at: datetime
    reset_at_start: bool = True
    start_vms: bool = True
    shutdown_at_end: bool = True
    block_open_web: bool = True


def _exam_out(r, vm_count: int | None = None) -> dict:
    out = {k: r[k] for k in ("id", "name", "group_id", "reset_at_start", "start_vms", "shutdown_at_end", "block_open_web", "status", "created_by")}
    for k in ("starts_at", "ends_at", "started_at", "finished_at", "created_at"):
        out[k] = r[k].isoformat() if r[k] else None
    for k in ("start_result", "end_result"):
        import json
        out[k] = json.loads(r[k]) if isinstance(r[k], str) else (r[k] or {})
    if "group_name" in r.keys():
        out["group_name"] = r["group_name"]
    now = datetime.now(timezone.utc)
    out["live"] = r["status"] in ("scheduled", "running") and r["starts_at"] <= now < r["ends_at"]
    if vm_count is not None:
        out["vm_count"] = vm_count
    return out


def _check_window(body: ExamBody) -> tuple[datetime, datetime]:
    a = body.starts_at if body.starts_at.tzinfo else body.starts_at.replace(tzinfo=timezone.utc)
    b = body.ends_at if body.ends_at.tzinfo else body.ends_at.replace(tzinfo=timezone.utc)
    if b <= a:
        raise HTTPException(400, tr("Waktu selesai harus setelah waktu mulai", "The end time must be after the start time"))
    if (b - a).total_seconds() > 24 * 3600:
        raise HTTPException(400, tr("Ujian paling lama 24 jam", "An exam can last at most 24 hours"))
    if b <= datetime.now(timezone.utc):
        raise HTTPException(400, tr("Waktu selesai sudah lewat", "The end time has already passed"))
    return a, b


async def _overlap(conn, group_id: int, a: datetime, b: datetime, exclude: int | None = None) -> None:
    clash = await conn.fetchrow(
        """SELECT id, name FROM exams WHERE group_id = $1 AND status IN ('scheduled', 'running') AND id <> COALESCE($4, 0)
           AND starts_at < $3 AND ends_at > $2 LIMIT 1""", group_id, a, b, exclude)
    if clash:
        raise HTTPException(409, tr(f"Bentrok dengan ujian '{clash['name']}' (#{clash['id']}) di kelas yang sama",
                                    f"Overlaps with the exam '{clash['name']}' (#{clash['id']}) of the same class"))


async def _exam(exam_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT e.*, g.name AS group_name FROM exams e JOIN groups g ON g.id = e.group_id WHERE e.id = $1", exam_id)
    if not row:
        raise HTTPException(404, tr("Ujian tidak ditemukan", "Exam not found"))
    return row


@router.get("/exams")
async def list_exams(group_id: int | None = Query(None), user: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT e.*, g.name AS group_name FROM exams e JOIN groups g ON g.id = e.group_id
               WHERE ($1::int IS NULL OR e.group_id = $1) ORDER BY e.starts_at DESC LIMIT 200""", group_id)
    return [_exam_out(r) for r in rows]


@router.post("/exams", status_code=201)
async def create_exam(body: ExamBody, request: Request, user: dict = Depends(require_sysadmin)):
    g = await _group(body.group_id)
    a, b = _check_window(body)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _overlap(conn, body.group_id, a, b)
        eid = await conn.fetchval(
            """INSERT INTO exams (name, group_id, starts_at, ends_at, reset_at_start, start_vms, shutdown_at_end, block_open_web, created_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
            body.name.strip(), body.group_id, a, b, body.reset_at_start, body.start_vms, body.shutdown_at_end, body.block_open_web,
            user.get("username") or "")
    exam_mode.forget()
    await log_activity(user, "EXAM_CREATE", "INFO", {"id": str(eid), "name": body.name.strip()},
                       both(lambda: tr(f"{user.get('username')} menjadwalkan ujian '{body.name.strip()}' untuk kelas '{g['name']}'",
                                       f"{user.get('username')} scheduled the exam '{body.name.strip()}' for class '{g['name']}'")), request)
    vms = [v for v in await lab_reset.group_vms(body.group_id)]
    return _exam_out(await _exam(eid), len(vms))


@router.get("/exams/{exam_id}")
async def get_exam(exam_id: int, user: dict = Depends(require_sysadmin)):
    row = await _exam(exam_id)
    return _exam_out(row, len(await lab_reset.group_vms(row["group_id"])))


@router.put("/exams/{exam_id}")
async def update_exam(exam_id: int, body: ExamBody, request: Request, user: dict = Depends(require_sysadmin)):
    row = await _exam(exam_id)
    if row["status"] != "scheduled" or row["starts_at"] <= datetime.now(timezone.utc):
        raise HTTPException(409, tr("Hanya ujian yang belum mulai yang bisa diubah", "Only an exam that has not started can be changed"))
    await _group(body.group_id)
    a, b = _check_window(body)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _overlap(conn, body.group_id, a, b, exclude=exam_id)
        await conn.execute(
            """UPDATE exams SET name = $2, group_id = $3, starts_at = $4, ends_at = $5, reset_at_start = $6, start_vms = $7,
                      shutdown_at_end = $8, block_open_web = $9 WHERE id = $1""",
            exam_id, body.name.strip(), body.group_id, a, b, body.reset_at_start, body.start_vms, body.shutdown_at_end, body.block_open_web)
    exam_mode.forget()
    await log_activity(user, "EXAM_UPDATE", "INFO", {"id": str(exam_id), "name": body.name.strip()},
                       both(lambda: tr(f"{user.get('username')} mengubah ujian '{body.name.strip()}' (#{exam_id})",
                                       f"{user.get('username')} changed the exam '{body.name.strip()}' (#{exam_id})")), request)
    return _exam_out(await _exam(exam_id))


@router.post("/exams/{exam_id}/start-now")
async def start_exam_now(exam_id: int, request: Request, user: dict = Depends(require_sysadmin)):
    row = await _exam(exam_id)
    if row["status"] != "scheduled":
        raise HTTPException(409, tr("Hanya ujian yang belum mulai yang bisa dimulai", "Only an exam that has not started can be started"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        # jendela dimajukan: mulai sekarang, selesai tetap (atau 1 menit lagi kalau jendela aslinya sudah hampir habis)
        done = await conn.fetchval(
            """UPDATE exams SET starts_at = NOW(), ends_at = GREATEST(ends_at, NOW() + interval '1 minute'), status = 'running', started_at = NOW()
               WHERE id = $1 AND status = 'scheduled' RETURNING id""", exam_id)
    if not done:
        raise HTTPException(409, tr("Ujian sudah berubah status", "The exam changed status"))
    exam_mode.forget()
    asyncio.create_task(exam_mode.begin(exam_id, user.get("username") or ""))
    await log_activity(user, "EXAM_START_NOW", "WARNING", {"id": str(exam_id), "name": row["name"]},
                       both(lambda: tr(f"{user.get('username')} memulai ujian '{row['name']}' sekarang", f"{user.get('username')} started the exam '{row['name']}' now")), request)
    return _exam_out(await _exam(exam_id))


@router.post("/exams/{exam_id}/end-now")
async def end_exam_now(exam_id: int, request: Request, user: dict = Depends(require_sysadmin)):
    row = await _exam(exam_id)
    if row["status"] != "running":
        raise HTTPException(409, tr("Hanya ujian yang sedang berjalan yang bisa diakhiri", "Only a running exam can be ended"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        done = await conn.fetchval(
            "UPDATE exams SET status = 'finished', finished_at = NOW(), ends_at = LEAST(ends_at, NOW()) WHERE id = $1 AND status = 'running' RETURNING id", exam_id)
    if not done:
        raise HTTPException(409, tr("Ujian sudah berubah status", "The exam changed status"))
    exam_mode.forget()
    asyncio.create_task(exam_mode.conclude(exam_id, user.get("username") or ""))
    await log_activity(user, "EXAM_END_NOW", "WARNING", {"id": str(exam_id), "name": row["name"]},
                       both(lambda: tr(f"{user.get('username')} mengakhiri ujian '{row['name']}' sekarang", f"{user.get('username')} ended the exam '{row['name']}' now")), request)
    return _exam_out(await _exam(exam_id))


@router.post("/exams/{exam_id}/cancel")
async def cancel_exam(exam_id: int, request: Request, user: dict = Depends(require_sysadmin)):
    row = await _exam(exam_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        done = await conn.fetchval(
            "UPDATE exams SET status = 'cancelled', finished_at = NOW() WHERE id = $1 AND status IN ('scheduled', 'running') RETURNING id", exam_id)
    if not done:
        raise HTTPException(409, tr("Ujian ini sudah selesai atau dibatalkan", "This exam already ended or was cancelled"))
    exam_mode.forget()
    await log_activity(user, "EXAM_CANCEL", "WARNING", {"id": str(exam_id), "name": row["name"]},
                       both(lambda: tr(f"{user.get('username')} membatalkan ujian '{row['name']}'; pembatasan dicabut, VM dibiarkan apa adanya",
                                       f"{user.get('username')} cancelled the exam '{row['name']}'; restrictions lifted, VMs left as they are")), request)
    return _exam_out(await _exam(exam_id))


@router.delete("/exams/{exam_id}")
async def delete_exam(exam_id: int, request: Request, user: dict = Depends(require_sysadmin)):
    row = await _exam(exam_id)
    if row["status"] == "running":
        raise HTTPException(409, tr("Ujian yang sedang berjalan tidak bisa dihapus; batalkan atau akhiri dulu", "A running exam cannot be deleted; cancel or end it first"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM exams WHERE id = $1", exam_id)
    exam_mode.forget()
    await log_activity(user, "EXAM_DELETE", "INFO", {"id": str(exam_id), "name": row["name"]},
                       both(lambda: tr(f"{user.get('username')} menghapus ujian '{row['name']}'", f"{user.get('username')} deleted the exam '{row['name']}'")), request)
    return {"deleted": exam_id}


# ── laporan pemakaian ─────────────────────────────────────────────────────────

@router.get("/groups/{group_id}/usage-report")
async def group_usage_report(group_id: int, date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                             format: str = Query("json"), user: dict = Depends(require_sysadmin)):
    g = await _group(group_id)
    tz = ss.tzinfo(await ss.get_settings())
    try:
        lo, hi = usage_report.parse_range(date_from, date_to, tz)
    except ValueError as e:
        why = str(e)
        raise HTTPException(400, {"format": tr("Tanggal harus berformat YYYY-MM-DD", "Dates must be YYYY-MM-DD"),
                                  "order": tr("Tanggal akhir tidak boleh sebelum tanggal awal", "The end date cannot be before the start date"),
                                  "span": tr(f"Rentang paling lama {usage_report.MAX_DAYS} hari", f"The range can be at most {usage_report.MAX_DAYS} days")}.get(why, why))
    report = await usage_report.class_report(group_id, lo, hi, tz)
    report["group"] = g
    if format == "csv":
        from services.csv_export import fmt_time
        header = ["Username", "Nama", "Login", "Sesi Remote", "Menit Remote", "Sesi Web", "Menit Web", "Sesi SSH", "Menit SSH",
                  "Total sesi", "Total menit", "Hari aktif", "VM dipakai", "Terakhir aktif"]
        rows = [[s["username"], s["full_name"], s["logins"], s["remote_sessions"], s["remote_minutes"], s["web_sessions"], s["web_minutes"],
                 s["ssh_sessions"], s["ssh_minutes"], s["total_sessions"], s["total_minutes"], s["active_days"], s["vms"],
                 fmt_time(s["last_active"], tz)] for s in report["students"]]
        return csv_response(f"pemakaian-kelas-{group_id}", header, rows, tz)
    if format != "json":
        raise HTTPException(400, tr("format harus json atau csv", "format must be json or csv"))
    return report
