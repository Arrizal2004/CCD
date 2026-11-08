"""
Template otomatis dari cloud image (Ubuntu 24.04 dan openSUSE Leap 16.0), khusus admin. Lihat services/cloud_images.py.

  GET    /api/v1/images/catalog           daftar image yang tersedia
  GET    /api/v1/images/options           storage, pool, bridge, dan kesiapan satu node (?instance=&node=)
  POST   /api/v1/images/jobs              mulai proses (dijawab langsung; berjalan di latar belakang)
  GET    /api/v1/images/jobs              proses terbaru
  GET    /api/v1/images/jobs/{id}         satu proses
  POST   /api/v1/images/jobs/{id}/retry   ulangi yang gagal / terputus
  DELETE /api/v1/images/jobs/{id}         hapus dari riwayat
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from auth import Role, get_current_user
from i18n import tr
from services import cloud_images as ci
from services import proxmox_instances as pve_instances
from services import scope
from services.audit import both, log_activity
from services.proxmox_client import ProxmoxError

router = APIRouter()


def _require_admin(user: dict):
    if user["role"] not in (Role.SUPERADMIN, Role.SYSADMIN):
        raise HTTPException(403, tr("Aksi ini hanya untuk admin/sysadmin", "Only admins/sysadmins can do this"))


def _wrap(e: ci.ImageError) -> HTTPException:
    return HTTPException(e.status_code, e.detail)


@router.get("/catalog")
async def get_catalog(user: dict = Depends(get_current_user)):
    _require_admin(user)
    return ci.catalog()


@router.get("/options")
async def get_options(instance: str = Query(...), node: str = Query(...), user: dict = Depends(get_current_user)):
    _require_admin(user)
    await scope.require_instance(user, instance)
    try:
        client = await pve_instances.get_client(instance)
    except ValueError as e:
        raise HTTPException(404, str(e))
    try:
        return await ci.node_options(client, node)
    except ProxmoxError as e:
        raise HTTPException(502, e.detail)


class ImageJobBody(BaseModel):
    instance: str
    node: str
    image_key: str
    storage: str
    disk_storage: str
    pool: str
    bridge: str | None = None
    template_name: str | None = None
    cores: int | None = None
    memory_mb: int | None = None
    disk_gb: int | None = None
    force_download: bool = False


@router.post("/jobs", status_code=201)
async def create_job(body: ImageJobBody, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    await scope.require_instance(user, body.instance)
    try:
        await pve_instances.get_client(body.instance)
    except ValueError as e:
        raise HTTPException(404, str(e))
    try:
        image = ci.get_image(body.image_key)
        job_id = await ci.start_job(user, body.instance, body.node, image, body.model_dump(exclude={"instance", "node", "image_key"}))
    except ci.ImageError as e:
        raise _wrap(e)
    await log_activity(user, "IMAGE_JOB_START", "INFO", {"id": str(job_id), "name": f"{body.instance}/{body.node}/{image['key']}"},
                       both(lambda: tr(f"{user.get('username')} memulai pembuatan template dari image {image['name']} di {body.instance}/{body.node} (proses #{job_id})",
                                       f"{user.get('username')} started building a template from the image {image['name']} on {body.instance}/{body.node} (job #{job_id})")), request)
    return await ci.get_job(job_id)


@router.get("/jobs")
async def list_jobs(user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await ci.list_jobs(await scope.allowed_labels(user))


async def _job(user: dict, job_id: int) -> dict:
    try:
        job = await ci.get_job(job_id)
    except ci.ImageError as e:
        raise _wrap(e)
    await scope.require_instance(user, job["instance"])
    return job


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await _job(user, job_id)


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    await _job(user, job_id)
    try:
        return await ci.retry_job(job_id)
    except ci.ImageError as e:
        raise _wrap(e)


@router.delete("/jobs/{job_id}")
async def remove_job(job_id: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    await _job(user, job_id)
    try:
        await ci.remove_job(job_id)
    except ci.ImageError as e:
        raise _wrap(e)
    return {"deleted": job_id}
