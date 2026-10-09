from fastapi import APIRouter, Request, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional, List
import logging
from pydantic import BaseModel
from database import get_pool
from schemas import VmMetadataResponse
from auth import get_current_user, Role
from services.ssh_client import encrypt_secret, decrypt_secret
from i18n import tr

router = APIRouter()
log = logging.getLogger("vm_metadata")

_ADMIN_ROLES = (Role.SUPERADMIN, Role.SYSADMIN)


async def _student_owns_vm(user: dict, host_name: str, vm_id: str) -> bool:
    from database import get_student_vm_ids
    allowed = await get_student_vm_ids(int(user["sub"]), host_name)
    return (vm_id, host_name) in allowed


@router.get("/{host_name}", response_model=list[VmMetadataResponse])
async def list_metadata(host_name: str, request: Request, user: dict = Depends(get_current_user)):
    """Ambil semua metadata VM untuk satu host."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM vm_metadata WHERE host_name = $1", host_name)
    return [_for_viewer(_normalize(dict(r)), user) for r in rows]


@router.get("/{host_name}/{vm_id}", response_model=VmMetadataResponse)
async def get_metadata(host_name: str, vm_id: str, user: dict = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM vm_metadata WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name)
    if not row:
        raise HTTPException(status_code=404, detail="Metadata not found")
    return _for_viewer(_normalize(dict(row)), user)


class VmMetadataUpsert(BaseModel):
    description:        Optional[str]       = None
    owner:              Optional[str]       = None
    borrow_until:       Optional[datetime]  = None
    clear_borrow_until: bool                = False
    notes:              Optional[str]       = None
    tags:               Optional[List[str]] = None
    vm_username:        Optional[str]       = None
    vm_password:        Optional[str]       = None


@router.put("/{host_name}/{vm_id}")
async def upsert_metadata(
    host_name: str, vm_id: str, body: VmMetadataUpsert,
    user: dict = Depends(get_current_user),
):
    is_admin = user["role"] in _ADMIN_ROLES

    # RBAC: student hanya boleh ubah description untuk VM yang di-assign ke dirinya
    if not is_admin:
        if user["role"] != Role.STUDENT:
            raise HTTPException(403, tr("Role tidak diizinkan", "Role not allowed"))
        if not await _student_owns_vm(user, host_name, vm_id):
            raise HTTPException(403, tr("Anda tidak punya akses ke VM ini",
                                        "You do not have access to this VM"))
        # Field privileged diabaikan untuk student (hanya description yang dipakai)
        body = VmMetadataUpsert(description=body.description)

    update_borrow = body.borrow_until is not None or body.clear_borrow_until
    pw_enc = encrypt_secret(body.vm_password) if body.vm_password else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO vm_metadata
                (vm_id, host_name, description, owner, borrow_until, notes, tags, vm_username, vm_password_enc, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $9, $10, NOW())
            ON CONFLICT (vm_id, host_name) DO UPDATE SET
                description    = COALESCE($3, vm_metadata.description),
                owner          = COALESCE($4, vm_metadata.owner),
                borrow_until   = CASE WHEN $8 THEN $5 ELSE vm_metadata.borrow_until END,
                notes          = COALESCE($6, vm_metadata.notes),
                tags           = COALESCE($7, vm_metadata.tags),
                vm_username    = COALESCE($9, vm_metadata.vm_username),
                vm_password_enc = COALESCE($10, vm_metadata.vm_password_enc),
                updated_at     = NOW()
            RETURNING *
        """,
        vm_id, host_name,
        body.description, body.owner, body.borrow_until, body.notes,
        body.tags, update_borrow, body.vm_username, pw_enc)
    return _normalize(dict(row))


@router.delete("/{host_name}/{vm_id}")
async def delete_metadata(host_name: str, vm_id: str, user: dict = Depends(get_current_user)):
    if user["role"] not in _ADMIN_ROLES:
        raise HTTPException(403, tr("Aksi ini hanya untuk admin/sysadmin",
                                    "Only admins/sysadmins can do this"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM vm_metadata WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name)
    return {"status": "deleted"}


def _for_viewer(row: dict, user: dict) -> dict:
    """Login VM yang tersimpan di metadata hanya untuk admin. Student mengambil kredensialnya lewat
    /ssh-creds/my-vm-cred, yang menolak VM dengan akses 'Hanya Open Web'."""
    if user["role"] not in _ADMIN_ROLES:
        row["vm_username"] = ""
        row["vm_password"] = ""
    return row


def _normalize(row: dict) -> dict:
    for f in ('description', 'owner', 'notes', 'vm_username'):
        if row.get(f) is None:
            row[f] = ''
    enc = row.pop('vm_password_enc', None)
    if enc:
        try:
            row['vm_password'] = decrypt_secret(enc)
        except Exception:
            log.warning("decrypt vm_password_enc failed for vm_id=%s host=%s — returning empty",
                        row.get('vm_id'), row.get('host_name'))
            row['vm_password'] = ''
    else:
        row['vm_password'] = ''
    if row.get('tags') is None:
        row['tags'] = []
    return row
