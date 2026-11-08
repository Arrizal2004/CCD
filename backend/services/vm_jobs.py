"""
Buat VM dari template sebagai proses latar belakang. Berbeda dari POST /proxmox/.../vms yang menahan satu permintaan HTTP
sampai VM jadi, di sini permintaan langsung dijawab dengan nomor proses; VM dibuat di latar belakang lewat jalur yang sama
(routers.proxmox.create_vm_core), beberapa proses bisa berjalan bersamaan, dan statusnya tersimpan di vm_jobs sehingga
tetap terlihat setelah halaman ditutup atau backend restart.

Password cloud-init disimpan terenkripsi hanya selama VM belum jadi (supaya "Ulangi" bisa jalan) dan dikosongkan setelahnya.
"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import HTTPException

from database import get_pool
from i18n import tr
from services import networks, proxmox_provision as provision
from services.ssh_client import decrypt_secret, encrypt_secret

log = logging.getLogger("vm_jobs")

MAX_PARALLEL = 3            # proses sekaligus per Proxmox; sisanya menunggu giliran
KEEP_DAYS = 30
ACTIVE = ("pending", "running")
REQUEST_FIELDS = ("template_vmid", "name", "username", "ip_mode", "ip_cidr", "gateway", "dns", "cores", "memory_mb",
                  "disk_gb", "bridge", "full_clone", "start", "lease_days", "network_id")
_COLS = ("id, instance, node, name, template_vmid, request, status, step, vmid, result, error, created_by_id, created_by, "
         "created_at, updated_at, finished_at, (password_enc IS NOT NULL) AS can_retry")
_sems: dict[str, asyncio.Semaphore] = {}
_tasks: set = set()


def _j(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def _out(row) -> dict:
    out = dict(row)
    out["request"], out["result"] = _j(out["request"]), _j(out["result"])
    return out


def validate(body: dict) -> None:
    """Pemeriksaan murah yang sama dengan jalur sinkron, supaya salah ketik langsung ditolak dan tidak gagal di latar belakang."""
    try:
        if not provision.NAME_RE.match(body["name"] or ""):
            raise provision.ProvisionError(tr("Nama VM/hostname hanya huruf, angka, dan '-' (maks. 63, tidak diawali/diakhiri '-')",
                                              "The VM name/hostname may only contain letters, digits and '-' (max 63, not starting or ending with '-')"))
        if not provision.USER_RE.match(body.get("username") or "") or body["username"] == "root":
            raise provision.ProvisionError(tr("Username Linux tidak valid (huruf kecil/angka/_/-, diawali huruf, bukan 'root')",
                                              "Invalid Linux username (lowercase letters/digits/_/-, starting with a letter, not 'root')"))
        if not body.get("password") or len(body["password"]) > 128:
            raise provision.ProvisionError(tr("Password wajib diisi (maks. 128 karakter)", "A password is required (max 128 characters)"))
        if body.get("bridge") and not provision.BRIDGE_RE.match(body["bridge"]):
            raise provision.ProvisionError(tr("Nama bridge tidak valid", "Invalid bridge name"))
        if body.get("network_id") is None:
            provision.build_ipconfig(body["ip_mode"], body.get("ip_cidr"), body.get("gateway"))
        provision.parse_dns(body.get("dns"))
    except provision.ProvisionError as e:
        raise HTTPException(e.status_code, e.detail)


def _static_ip(request: dict) -> str | None:
    cidr = (request.get("ip_cidr") or "").split("/")[0].strip()
    return cidr if request.get("ip_mode") == "static" and cidr and request.get("network_id") is None else None


async def start(user: dict, instance: str, node: str, body: dict) -> int:
    body = dict(body)
    validate(body)
    password = body.pop("password", "") or ""
    request = {k: body.get(k) for k in REQUEST_FIELDS}
    request["name"] = request["name"].strip()
    request["username"] = (request["username"] or "").strip()
    actor = {"sub": str(user.get("sub")), "username": user.get("username"), "role": user.get("role")}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, request FROM vm_jobs WHERE instance = $1 AND status = ANY($2)", instance, list(ACTIVE))
        for r in rows:
            if r["name"].lower() == request["name"].lower():
                raise HTTPException(409, tr(f"Proses #{r['id']} sedang membuat VM bernama '{request['name']}'",
                                            f"Job #{r['id']} is already creating a VM named '{request['name']}'"))
            ip = _static_ip(request)
            if ip and ip == _static_ip(_j(r["request"])):
                raise HTTPException(409, tr(f"IP {ip} sedang dipakai proses #{r['id']}", f"IP {ip} is in use by job #{r['id']}"))
        job_id = await conn.fetchval(
            """INSERT INTO vm_jobs (instance, node, name, template_vmid, request, password_enc, actor, created_by_id, created_by)
               VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7::jsonb,$8,$9) RETURNING id""",
            instance, node, request["name"], int(request.get("template_vmid") or 0), json.dumps(request), encrypt_secret(password),
            json.dumps(actor), int(user["sub"]), user.get("username") or "")
    spawn(job_id)
    return job_id


def spawn(job_id: int) -> None:
    task = asyncio.create_task(run(job_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _update(job_id: int, **fields) -> None:
    sets, args = ["updated_at = NOW()"], [job_id]
    for k, v in fields.items():
        args.append(json.dumps(v) if k == "result" else v)
        sets.append(f"{k} = ${len(args)}" + ("::jsonb" if k == "result" else ""))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE vm_jobs SET {', '.join(sets)} WHERE id = $1", *args)


async def run(job_id: int) -> None:
    from routers.proxmox import CreateVmRequest, create_vm_core        # di sini supaya tidak saling impor
    pool = await get_pool()
    async with pool.acquire() as conn:
        job = await conn.fetchrow("SELECT instance, node FROM vm_jobs WHERE id = $1", job_id)
    if not job:
        return
    async with _sems.setdefault(job["instance"], asyncio.Semaphore(MAX_PARALLEL)):
        async with pool.acquire() as conn:           # klaim: hanya satu pelaksana per proses
            row = await conn.fetchrow(
                """UPDATE vm_jobs SET status = 'running', step = 'clone', error = '', updated_at = NOW()
                   WHERE id = $1 AND status = 'pending' RETURNING request, password_enc, actor, instance, node""", job_id)
        if not row:
            return
        actor = _j(row["actor"])
        try:
            async def progress(step: str) -> None:
                await _update(job_id, step=step)
            password = decrypt_secret(row["password_enc"])
            req = CreateVmRequest(**_j(row["request"]), password=password)
            vm = await create_vm_core(row["instance"], row["node"], req, actor, None, progress=progress)
        except HTTPException as e:
            await _fail(job_id, e.detail if isinstance(e.detail, str) else json.dumps(e.detail))
        except Exception as e:
            log.exception("Proses VM %s gagal", job_id)
            await _fail(job_id, f"{type(e).__name__}: {str(e)[:250]}")
        else:
            keep = ("vmid", "name", "static_ip", "agent_ip", "connect_ready", "clone", "switch")
            await _update(job_id, status="done", step="", vmid=vm.get("vmid"), password_enc=None,
                          result={k: vm.get(k) for k in keep}, finished_at=datetime.now().astimezone())


async def _fail(job_id: int, detail: str) -> None:
    await _update(job_id, status="failed", error=detail[:500], finished_at=datetime.now().astimezone())


async def recover_interrupted() -> None:
    """Saat backend mulai: proses yang tadinya berjalan ditandai terputus, dan riwayat lama dibersihkan."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE vm_jobs SET status = 'interrupted', updated_at = NOW(),
                   error = 'Backend berhenti saat VM ini dibuat. Cek di Proxmox dan hapus VM setengah jadi sebelum mengulang'
               WHERE status IN ('pending', 'running')""")
        await conn.execute("UPDATE vm_jobs SET password_enc = NULL WHERE finished_at < NOW() - INTERVAL '1 day'")
        await conn.execute(f"DELETE FROM vm_jobs WHERE finished_at < NOW() - INTERVAL '{KEEP_DAYS} days'")


async def get(job_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT {_COLS} FROM vm_jobs WHERE id = $1", job_id)
    if not row:
        raise HTTPException(404, tr("Proses tidak ditemukan", "Job not found"))
    return _out(row)


async def recent(labels: set[str] | None, limit: int = 50) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT {_COLS} FROM vm_jobs ORDER BY id DESC LIMIT 300")
    return [_out(r) for r in rows if labels is None or r["instance"] in labels][:limit]


async def retry(job_id: int) -> dict:
    job = await get(job_id)
    if job["status"] not in ("failed", "interrupted"):
        raise HTTPException(409, tr("Hanya proses yang gagal atau terputus yang bisa diulang", "Only failed or interrupted jobs can be retried"))
    if not job["can_retry"]:
        raise HTTPException(409, tr("Password sudah dihapus dari proses ini; buat VM baru dari form", "The password was already removed from this job; create a new VM from the form"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        clash = await conn.fetchval("SELECT id FROM vm_jobs WHERE instance = $1 AND lower(name) = lower($2) AND status = ANY($3) AND id <> $4",
                                    job["instance"], job["name"], list(ACTIVE), job_id)
    if clash:
        raise HTTPException(409, tr(f"Proses #{clash} sedang membuat VM bernama '{job['name']}'", f"Job #{clash} is already creating a VM named '{job['name']}'"))
    await _update(job_id, status="pending", step="", error="", finished_at=None)
    spawn(job_id)
    return await get(job_id)


async def remove(job_id: int) -> dict:
    job = await get(job_id)
    if job["status"] in ACTIVE:
        raise HTTPException(409, tr("Proses yang masih berjalan tidak bisa dihapus dari riwayat", "A running job cannot be removed from the history"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM vm_jobs WHERE id = $1", job_id)
    return job
