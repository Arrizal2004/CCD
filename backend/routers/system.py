"""
Pengaturan sistem, lihat services/system_settings.py.

  GET    /api/v1/system/branding   publik: nama, logo, warna, bahasa, pengumuman untuk halaman login
  GET    /api/v1/system/config     pengguna yang login: pengumuman, nilai bawaan, kategori tiket, pilihan OS
  GET    /api/v1/system/logo       publik: berkas logo
  GET    /api/v1/system/settings   superadmin (ssh_env: status bastion dan nilai .env, hanya untuk ditampilkan)
  PUT    /api/v1/system/settings   superadmin, tercatat di Activity Log
  POST   /api/v1/system/logo       superadmin, unggah logo
  DELETE /api/v1/system/logo       superadmin
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from auth import get_current_user, require_superadmin
from services import system_settings as ss
from services.audit import log_activity
from i18n import tr

router = APIRouter()


@router.get("/branding")
async def branding():
    return ss.public_view(await ss.get_settings())


@router.get("/config")
async def config(user: dict = Depends(get_current_user)):
    s = await ss.get_settings()
    return {"announcement": ss.active_announcement(s), "default_vm_lease_days": s["default_vm_lease_days"],
            "ticket_categories": s["ticket_categories"], "vps_os_options": s["vps_os_options"]}


def _with_ssh_env(s: dict) -> dict:
    """Keterangan untuk kartu SSH di halaman Sistem: apakah bastion aktif dan nilai cadangan dari .env."""
    from routers.ssh_keys import bastion_enabled, public_port
    return {**s, "ssh_env": {"enabled": bastion_enabled(), "env_host": os.getenv("BASTION_PUBLIC_HOST") or "",
                             "port": public_port()}}


@router.get("/settings")
async def get_settings(user: dict = Depends(require_superadmin)):
    return _with_ssh_env(await ss.get_settings())


@router.put("/settings")
async def put_settings(body: dict, request: Request, user: dict = Depends(require_superadmin)):
    try:
        data = ss.normalize(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    before = await ss.get_settings()
    after = await ss.save_settings(data, user.get("username") or "")
    changed = [k for k in ss.DEFAULTS if before.get(k) != after.get(k)]
    if changed:
        await log_activity(user, "SYSTEM_SETTINGS_UPDATE", "WARNING", {"id": "system", "name": after["name"]},
                           f"{user.get('username')} mengubah pengaturan sistem: {', '.join(changed)}", request)
    return _with_ssh_env(after)


@router.get("/logo")
async def get_logo():
    logo = await ss.get_logo()
    if not logo:
        raise HTTPException(404, tr("Logo belum diunggah", "No logo has been uploaded"))
    # URL logo memuat ?v=<versi>, jadi aman disimpan lama di cache browser.
    return Response(logo[0], media_type=logo[1], headers={
        "Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'"})


@router.post("/logo")
async def upload_logo(request: Request, file: UploadFile = File(...), user: dict = Depends(require_superadmin)):
    data = await file.read(ss.LOGO_MAX_BYTES + 1)
    if len(data) > ss.LOGO_MAX_BYTES:
        raise HTTPException(400, tr("Logo maksimal 512 KB", "The logo may be at most 512 KB"))
    mime = ss.logo_type(data)
    if not mime:
        raise HTTPException(400, tr("Logo harus berupa PNG, JPG, atau WebP",
                                    "The logo must be PNG, JPG or WebP"))
    s = await ss.save_logo(data, mime)
    await log_activity(user, "SYSTEM_LOGO_UPDATE", "WARNING", {"id": "system", "name": s["name"]},
                       f"{user.get('username')} mengganti logo sistem ({mime}, {len(data) // 1024} KB)", request)
    return {"logo_version": s["logo_version"]}


@router.delete("/logo")
async def delete_logo(request: Request, user: dict = Depends(require_superadmin)):
    s = await ss.save_logo(None, None)
    await log_activity(user, "SYSTEM_LOGO_UPDATE", "WARNING", {"id": "system", "name": s["name"]},
                       f"{user.get('username')} menghapus logo sistem", request)
    return {"logo_version": None}
