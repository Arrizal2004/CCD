"""
Buat VM dari template di latar belakang, dengan riwayat (admin).

  POST   /api/v1/vm-jobs              mulai satu proses (langsung dijawab; VM dibuat di latar belakang)
  GET    /api/v1/vm-jobs              proses terbaru (berjalan dan riwayat)
  GET    /api/v1/vm-jobs/{id}         satu proses
  POST   /api/v1/vm-jobs/{id}/retry   ulangi yang gagal / terputus
  DELETE /api/v1/vm-jobs/{id}         hapus dari riwayat (yang sudah selesai)
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from auth import Role, get_current_user
from i18n import tr
from routers.proxmox import CreateVmRequest
from services import scope, vm_jobs
from services.audit import both, log_activity

router = APIRouter()


def _require_admin(user: dict):
    if user["role"] not in (Role.SUPERADMIN, Role.SYSADMIN):
        raise HTTPException(403, tr("Aksi ini hanya untuk admin/sysadmin", "Only admins/sysadmins can do this"))


class VmJobBody(CreateVmRequest):
    instance: str
    node: str


@router.post("", status_code=201)
async def create(body: VmJobBody, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    await scope.require_instance(user, body.instance)
    data = body.model_dump(exclude={"instance", "node"})
    job_id = await vm_jobs.start(user, body.instance, body.node, data)
    await log_activity(user, "VM_CREATE_START", "INFO", {"id": str(job_id), "name": f"{body.instance}/{body.node}/{body.name}"},
                       both(lambda: tr(f"{user.get('username')} memulai pembuatan VM {body.name} dari template {body.template_vmid} di {body.instance}/{body.node} (proses #{job_id})",
                                       f"{user.get('username')} started creating VM {body.name} from template {body.template_vmid} on {body.instance}/{body.node} (job #{job_id})")), request)
    return await vm_jobs.get(job_id)


@router.get("")
async def list_jobs(user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await vm_jobs.recent(await scope.allowed_labels(user))


async def _job(user: dict, job_id: int) -> dict:
    job = await vm_jobs.get(job_id)
    await scope.require_instance(user, job["instance"])
    return job


@router.get("/{job_id}")
async def get_job(job_id: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await _job(user, job_id)


@router.post("/{job_id}/retry")
async def retry(job_id: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    await _job(user, job_id)
    return await vm_jobs.retry(job_id)


@router.delete("/{job_id}")
async def remove(job_id: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    await _job(user, job_id)
    await vm_jobs.remove(job_id)
    return {"deleted": job_id}
