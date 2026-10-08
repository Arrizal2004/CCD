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

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from fastapi.responses import Response

from auth import get_current_user, require_superadmin
from services import system_settings as ss
from services.audit import both, log_activity
from i18n import tr

router = APIRouter()


@router.get("/branding")
async def branding():
    return ss.public_view(await ss.get_settings())


@router.get("/config")
async def config(user: dict = Depends(get_current_user)):
    s = await ss.get_settings()
    return {"announcement": ss.active_announcement(s), "default_vm_lease_days": s["default_vm_lease_days"],
            "ticket_categories": s["ticket_categories"], "vps_os_options": s["vps_os_options"],
            "os_logos": _os_logo_map(s), "timezone": s["timezone"]}


def _os_logo_map(s: dict) -> dict:
    """{nama OS: versi logo} untuk OS di daftar yang punya logo."""
    return {o: v for o in s["vps_os_options"] if (v := s["os_logos"].get(o.lower()))}


def _with_ssh_env(s: dict) -> dict:
    """Keterangan untuk kartu SSH di halaman Sistem: apakah bastion aktif dan apakah .env punya alamat cadangan.
    Nilai alamat dari .env sengaja tidak dikirim, supaya domain server tidak muncul di tampilan."""
    from routers.ssh_keys import bastion_enabled, public_port
    return {**s, "ssh_env": {"enabled": bastion_enabled(), "env_host_set": bool(os.getenv("BASTION_PUBLIC_HOST")),
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
                           both(lambda: tr(f"{user.get('username')} mengubah pengaturan sistem: {', '.join(changed)}",
                       f"{user.get('username')} changed the system settings: {', '.join(changed)}")), request)
    return _with_ssh_env(after)


class CategoriesBody(BaseModel):
    categories: list[dict]


class OsOptionsBody(BaseModel):
    options: list[str | dict]


@router.put("/ticket-categories")
async def put_ticket_categories(body: CategoriesBody, request: Request, user: dict = Depends(require_superadmin)):
    """Kategori helpdesk, diatur dari halaman Helpdesk."""
    try:
        after = await ss.save_ticket_categories(body.categories, user.get("username") or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    await log_activity(user, "TICKET_CATEGORIES_UPDATE", "INFO", {"id": "system", "name": after["name"]},
                       both(lambda: tr(f"{user.get('username')} mengubah kategori helpdesk: "
                                       f"{', '.join(c['label'] or c['key'] for c in after['ticket_categories'])}",
                                       f"{user.get('username')} changed the helpdesk categories: "
                                       f"{', '.join(c['label'] or c['key'] for c in after['ticket_categories'])}")), request)
    return {"ticket_categories": after["ticket_categories"]}


@router.put("/os-options")
async def put_os_options(body: OsOptionsBody, request: Request, user: dict = Depends(require_superadmin)):
    """Pilihan OS di Infra Request, diatur dari halaman Infra Requests."""
    try:
        after = await ss.save_os_options(body.options, user.get("username") or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    await log_activity(user, "OS_OPTIONS_UPDATE", "INFO", {"id": "system", "name": after["name"]},
                       both(lambda: tr(f"{user.get('username')} mengubah pilihan OS Infra Request: {', '.join(after['vps_os_options'])}",
                       f"{user.get('username')} changed the infrastructure request OS choices: {', '.join(after['vps_os_options'])}")),
                       request)
    return {"vps_os_options": after["vps_os_options"], "os_logos": _os_logo_map(after)}


@router.post("/os-logo")
async def upload_os_logo(request: Request, name: str = Query(..., max_length=40), file: UploadFile = File(...),
                         user: dict = Depends(require_superadmin)):
    s = await ss.get_settings()
    option = ss.match_os(s, name)
    if not option:
        raise HTTPException(404, tr("OS ini belum ada di daftar; simpan daftarnya dulu",
                                    "This OS is not in the list yet; save the list first"))
    data = await file.read(ss.OS_LOGO_MAX_BYTES + 1)
    if len(data) > ss.OS_LOGO_MAX_BYTES:
        raise HTTPException(400, tr("Logo OS maksimal 256 KB", "An OS logo may be at most 256 KB"))
    mime = ss.logo_type(data)
    if not mime:
        raise HTTPException(400, tr("Logo harus berupa PNG, JPG, atau WebP",
                                    "The logo must be PNG, JPG or WebP"))
    await ss.save_os_logo(option, data, mime)
    await log_activity(user, "OS_LOGO_UPDATE", "INFO", {"id": "system", "name": option},
                       both(lambda: tr(f"{user.get('username')} mengganti logo OS {option} ({mime}, {len(data) // 1024} KB)",
                       f"{user.get('username')} changed the logo of OS {option} ({mime}, {len(data) // 1024} KB)")), request)
    return {"os_logos": _os_logo_map(await ss.get_settings())}


@router.delete("/os-logo")
async def remove_os_logo(request: Request, name: str = Query(..., max_length=40), user: dict = Depends(require_superadmin)):
    option = ss.match_os(await ss.get_settings(), name) or name
    if await ss.delete_os_logo(option):
        await log_activity(user, "OS_LOGO_UPDATE", "INFO", {"id": "system", "name": option},
                           both(lambda: tr(f"{user.get('username')} menghapus logo OS {option}", f"{user.get('username')} removed the logo of OS {option}")), request)
    return {"status": "deleted"}


@router.get("/os-logo")
async def get_os_logo(name: str = Query(..., max_length=40)):
    """Dipakai sebagai gambar di form Infra Request. Tanpa login, seperti logo sistem. URL-nya memuat
    ?v=<versi>, jadi aman disimpan lama di cache browser."""
    logo = await ss.get_os_logo(name)
    if not logo:
        raise HTTPException(404, tr("Logo belum diunggah", "No logo has been uploaded"))
    return Response(logo[0], media_type=logo[1], headers={
        "Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'"})


@router.get("/audit-stats")
async def audit_stats(user: dict = Depends(require_superadmin)):
    """Isi dan ukuran log audit, untuk ditampilkan di samping pengaturan lama penyimpanan."""
    from database import AUDIT_RETENTION_DAYS, get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT count(*) AS rows, min(created_at) AS oldest,
                      pg_total_relation_size('audit_logs') + pg_total_relation_size('openweb_sessions')
                      + pg_total_relation_size('ssh_sessions') AS bytes FROM audit_logs""")
    return {"rows": row["rows"], "oldest": row["oldest"].isoformat() if row["oldest"] else None,
            "bytes": row["bytes"], "env_default": AUDIT_RETENTION_DAYS,
            "effective_days": await ss.audit_retention_days()}


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
                       both(lambda: tr(f"{user.get('username')} mengganti logo sistem ({mime}, {len(data) // 1024} KB)",
                       f"{user.get('username')} changed the system logo ({mime}, {len(data) // 1024} KB)")), request)
    return {"logo_version": s["logo_version"]}


@router.delete("/logo")
async def delete_logo(request: Request, user: dict = Depends(require_superadmin)):
    s = await ss.save_logo(None, None)
    await log_activity(user, "SYSTEM_LOGO_UPDATE", "WARNING", {"id": "system", "name": s["name"]},
                       both(lambda: tr(f"{user.get('username')} menghapus logo sistem", f"{user.get('username')} removed the system logo")), request)
    return {"logo_version": None}
