"""
Buat VM massal per kelas (admin).

  POST /api/v1/vm-batches/preview              rencana: nama VM per mahasiswa, bentrok nama, kapasitas RAM dan IP
  POST /api/v1/vm-batches                      mulai membuat (berjalan di latar belakang)
  GET  /api/v1/vm-batches                      batch terbaru
  GET  /api/v1/vm-batches/{id}                 progres per mahasiswa
  GET  /api/v1/vm-batches/{id}/credentials.csv kredensial OS semua VM (tercatat di Activity Log)
  POST /api/v1/vm-batches/{id}/retry           ulangi yang gagal / lanjutkan yang terputus
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth import Role, get_current_user
from services import vm_batches as vb
from services.audit import log_activity
from i18n import tr

router = APIRouter()


def _require_admin(user: dict):
    if user["role"] not in (Role.SUPERADMIN, Role.SYSADMIN):
        raise HTTPException(403, tr("Aksi ini hanya untuk admin/sysadmin",
                                    "Only admins/sysadmins can do this"))


class BatchBody(BaseModel):
    instance: str
    node: str
    group_id: int
    template_vmid: int
    prefix: str | None = None             # awalan nama VM; kosong = nama grup
    network_id: int | None = None         # switch CCD (IP statis otomatis); kosong = bridge template, DHCP
    os_username: str | None = None        # kosong = username tiap mahasiswa
    cores: int | None = Field(None, ge=1, le=64)
    memory_mb: int | None = Field(None, ge=256, le=262144)
    disk_gb: int | None = Field(None, ge=1, le=4096)
    lease_days: int | None = Field(None, ge=1, le=3650)
    start: bool = False
    user_ids: list[int] | None = None     # anggota yang dipilih; kosong = semua anggota grup


@router.post("/preview")
async def preview(body: BatchBody, user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await vb.plan(body.instance, body.node, body.group_id, body.template_vmid, body.prefix, body.network_id,
                         body.os_username, body.memory_mb, body.start, body.user_ids)


@router.post("")
async def create(body: BatchBody, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    batch_id = await vb.start(body.instance, body.node, body.model_dump(), user)
    batch = await vb.get(batch_id)
    await log_activity(user, "VM_BATCH_CREATE", "WARNING", {"id": str(batch_id), "name": f"{body.instance}/{body.node}"},
                       f"{user.get('username')} memulai pembuatan {len(batch['items'])} VM untuk grup {batch['group_name']} "
                       f"dari template {body.template_vmid} di {body.instance}/{body.node}", request)
    return batch


@router.get("")
async def list_batches(user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await vb.recent()


@router.get("/{batch_id}")
async def get_batch(batch_id: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await vb.get(batch_id)


@router.get("/{batch_id}/credentials.csv")
async def credentials(batch_id: int, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    text = await vb.credentials_csv(batch_id)
    await log_activity(user, "CRED_VIEW", "WARNING", {"id": str(batch_id), "name": "vm-batch"},
                       f"{user.get('username')} mengunduh kredensial VM massal batch #{batch_id}", request)
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="vm-massal-{batch_id}.csv"'})


@router.post("/{batch_id}/retry")
async def retry(batch_id: int, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    await vb.retry(batch_id)
    await log_activity(user, "VM_BATCH_RETRY", "INFO", {"id": str(batch_id), "name": "vm-batch"},
                       f"{user.get('username')} mengulang VM yang gagal di batch #{batch_id}", request)
    return await vb.get(batch_id)
