"""
Kuota per mahasiswa (services/quotas.py).

  GET /api/v1/quotas/me             kuota dan pemakaian sendiri
  GET /api/v1/quotas/defaults       bawaan sistem (admin)
  PUT /api/v1/quotas/defaults       superadmin
  GET /api/v1/quotas/users/{id}     batas yang berlaku, penggantinya, dan pemakaian (admin)
  PUT /api/v1/quotas/users/{id}     atur kuota pengguna (admin); semua kosong = hapus
  GET /api/v1/quotas/groups/{id}    kuota grup (admin)
  PUT /api/v1/quotas/groups/{id}    atur kuota grup (admin)
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user, require_superadmin, require_sysadmin
from database import get_pool
from i18n import tr
from services import quotas
from services.audit import both, log_activity

router = APIRouter()


async def _summary(user_id: int) -> dict:
    eff = await quotas.effective(user_id)
    return {**eff, "usage": await quotas.usage(user_id)}


@router.get("/me")
async def my_quota(user: dict = Depends(get_current_user)):
    return await _summary(int(user["sub"]))


@router.get("/defaults")
async def get_defaults(user: dict = Depends(require_sysadmin)):
    return await quotas.get_defaults()


@router.put("/defaults")
async def put_defaults(body: dict, request: Request, user: dict = Depends(require_superadmin)):
    data = await quotas.save_defaults(body, user.get("username") or "")
    await log_activity(user, "QUOTA_DEFAULTS", "WARNING", None,
                       both(lambda: tr(f"Kuota bawaan diubah: {_fmt(data)}", f"The default quota was changed: {_fmt(data)}")), request)
    return data


def _fmt(d: dict) -> str:
    return ", ".join(f"{k}={'∞' if v == 0 else v if v is not None else '-'}" for k, v in d.items())


async def _student(user_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, username, role FROM users WHERE id = $1 AND deleted_at IS NULL", user_id)
    if not row:
        raise HTTPException(404, tr("User tidak ditemukan", "User not found"))
    return dict(row)


@router.get("/users/{user_id}")
async def get_user_quota(user_id: int, user: dict = Depends(require_sysadmin)):
    await _student(user_id)
    return {**await _summary(user_id), "override": await quotas.get_override("user_quotas", "user_id", user_id)}


@router.put("/users/{user_id}")
async def put_user_quota(user_id: int, body: dict, request: Request, user: dict = Depends(require_sysadmin)):
    target = await _student(user_id)
    data = await quotas.set_override("user_quotas", "user_id", user_id, body, user.get("username") or "")
    await log_activity(user, "QUOTA_USER", "INFO", {"id": str(user_id), "name": target["username"]},
                       both(lambda: tr(f"{user.get('username')} mengatur kuota '{target['username']}': {_fmt(data)}",
                                       f"{user.get('username')} set the quota of '{target['username']}': {_fmt(data)}")), request)
    return {**await _summary(user_id), "override": data}


async def _group(group_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, name FROM groups WHERE id = $1", group_id)
    if not row:
        raise HTTPException(404, tr("Grup tidak ditemukan", "Group not found"))
    return dict(row)


@router.get("/groups/{group_id}")
async def get_group_quota(group_id: int, user: dict = Depends(require_sysadmin)):
    await _group(group_id)
    return await quotas.get_override("group_quotas", "group_id", group_id)


@router.put("/groups/{group_id}")
async def put_group_quota(group_id: int, body: dict, request: Request, user: dict = Depends(require_sysadmin)):
    g = await _group(group_id)
    data = await quotas.set_override("group_quotas", "group_id", group_id, body, user.get("username") or "")
    await log_activity(user, "QUOTA_GROUP", "INFO", {"id": str(group_id), "name": g["name"]},
                       both(lambda: tr(f"{user.get('username')} mengatur kuota grup '{g['name']}': {_fmt(data)}",
                                       f"{user.get('username')} set the quota of the group '{g['name']}': {_fmt(data)}")), request)
    return data
