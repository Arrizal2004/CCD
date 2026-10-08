"""
ReBAC Groups — manajemen kelas/grup dan akses VM.

Relasi yang didukung:
  user ──member──▶ group ──access──▶ vm

Endpoint:
  GET    /                        list semua grup (sysadmin+)
  POST   /                        buat grup (sysadmin+)
  PUT    /{id}                    update nama/deskripsi (sysadmin+)
  DELETE /{id}                    hapus grup (sysadmin+)

  GET    /{id}/members            list anggota
  POST   /{id}/members            tambah anggota
  DELETE /{id}/members/{user_id}  hapus anggota

  GET    /{id}/vms                list VM yang di-assign ke grup
  POST   /{id}/vms                assign VM ke grup (dengan opsi auth_mode)
  PUT    /{id}/vms                update auth_mode / credentials untuk VM yang sudah di-assign
  DELETE /{id}/vms                cabut akses VM dari grup
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from auth import require_sysadmin, get_current_user
from database import get_pool
from services import guest_accounts
from i18n import tr
from services.audit import log_activity

log = logging.getLogger("groups")
router = APIRouter()


# ── Schemas ─────────────────────────────────────────────────────────────────

class GroupBody(BaseModel):
    name: str
    description: str = ""

class MemberBody(BaseModel):
    user_id: int

class VmAccessBody(BaseModel):
    vm_id:        str
    host_name:    str
    auth_mode:    str           = "mandiri"   # "mandiri" | "credentials"
    os_type:      str           = "linux"     # "linux" | "windows"  (untuk credentials)
    guac_protocol: str          = ""          # "ssh" | "rdp"        (untuk credentials)
    os_username:  Optional[str] = None        # untuk credentials mode
    os_password:  Optional[str] = None        # untuk credentials mode
    apply_in_vm:  bool          = False       # buat/perbarui akun ini di dalam VM lewat QEMU Guest Agent


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _get_group_or_404(conn, group_id: int) -> dict:
    row = await conn.fetchrow("SELECT * FROM groups WHERE id = $1", group_id)
    if not row:
        raise HTTPException(status_code=404, detail=tr("Group tidak ditemukan", "Group not found"))
    return dict(row)


def _mode_text(body: VmAccessBody) -> str:
    if body.auth_mode == "credentials":
        return f"kredensial bersama, akun OS '{(body.os_username or '').strip()}'"
    return "akun mandiri"


# ── Group CRUD ───────────────────────────────────────────────────────────────

@router.get("")
async def list_groups(_: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT g.id, g.name, g.description, g.created_at,
                   COUNT(DISTINCT gm.user_id)       AS member_count,
                   COUNT(DISTINCT gva.vm_id || gva.host_name) AS vm_count
            FROM groups g
            LEFT JOIN group_members   gm  ON gm.group_id  = g.id
            LEFT JOIN group_vm_access gva ON gva.group_id = g.id
            GROUP BY g.id
            ORDER BY g.name
        """)
    return [dict(r) for r in rows]


@router.post("", status_code=201)
async def create_group(body: GroupBody, request: Request, user: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "INSERT INTO groups (name, description) VALUES ($1, $2) RETURNING *",
                body.name.strip(), body.description.strip()
            )
        except Exception:
            raise HTTPException(status_code=409, detail=tr("Nama grup sudah digunakan",
                                                           "This group name is already used"))
    await log_activity(user, "GROUP_CREATE", "INFO", None,
                       f"{user.get('username')} membuat grup '{row['name']}'", request)
    return dict(row)


@router.put("/{group_id}")
async def update_group(group_id: int, body: GroupBody, request: Request, user: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        old = await _get_group_or_404(conn, group_id)
        try:
            row = await conn.fetchrow(
                "UPDATE groups SET name=$1, description=$2 WHERE id=$3 RETURNING *",
                body.name.strip(), body.description.strip(), group_id
            )
        except Exception:
            raise HTTPException(status_code=409, detail=tr("Nama grup sudah digunakan",
                                                           "This group name is already used"))
    changes = []
    if row["name"] != old["name"]:
        changes.append(f"nama '{old['name']}' → '{row['name']}'")
    if (row["description"] or "") != (old["description"] or ""):
        changes.append("deskripsi diubah")
    if changes:
        await log_activity(user, "GROUP_UPDATE", "INFO", None,
                           f"{user.get('username')} mengubah grup '{old['name']}': {'; '.join(changes)}", request)
    return dict(row)


@router.delete("/{group_id}", status_code=204)
async def delete_group(group_id: int, request: Request, user: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        group = await _get_group_or_404(conn, group_id)
        members = await conn.fetchval("SELECT count(*) FROM group_members WHERE group_id = $1", group_id)
        vms = await conn.fetchval("SELECT count(*) FROM group_vm_access WHERE group_id = $1", group_id)
        await conn.execute("DELETE FROM groups WHERE id = $1", group_id)
    await log_activity(user, "GROUP_DELETE", "WARNING", None,
                       f"{user.get('username')} menghapus grup '{group['name']}' ({members} anggota, {vms} VM)",
                       request)


# ── Members ──────────────────────────────────────────────────────────────────

@router.get("/{group_id}/members")
async def list_members(group_id: int, _: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_group_or_404(conn, group_id)
        rows = await conn.fetch("""
            SELECT u.id, u.username, u.full_name, u.email, gm.joined_at
            FROM group_members gm
            JOIN users u ON u.id = gm.user_id
            WHERE gm.group_id = $1
            ORDER BY u.full_name
        """, group_id)
    return [dict(r) for r in rows]


@router.post("/{group_id}/members", status_code=201)
async def add_member(group_id: int, body: MemberBody, request: Request, actor: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        group = await _get_group_or_404(conn, group_id)
        user = await conn.fetchrow("SELECT id, username, full_name FROM users WHERE id = $1", body.user_id)
        if not user:
            raise HTTPException(status_code=404, detail=tr("User tidak ditemukan", "User not found"))
        try:
            await conn.execute(
                "INSERT INTO group_members (group_id, user_id) VALUES ($1, $2)",
                group_id, body.user_id
            )
        except Exception:
            raise HTTPException(status_code=409, detail=tr("User sudah menjadi anggota grup ini",
                                                           "The user is already a member of this group"))
    await log_activity(actor, "GROUP_MEMBER_ADD", "WARNING", None,
                       f"{actor.get('username')} menambahkan '{user['username']}' ke grup '{group['name']}'", request)
    return {"group_id": group_id, "user_id": body.user_id, "username": user["username"]}


@router.delete("/{group_id}/members/{user_id}", status_code=204)
async def remove_member(group_id: int, user_id: int, request: Request, actor: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        group = await _get_group_or_404(conn, group_id)
        deleted = await conn.fetchval(
            "DELETE FROM group_members WHERE group_id=$1 AND user_id=$2 RETURNING user_id",
            group_id, user_id
        )
        if not deleted:
            raise HTTPException(status_code=404, detail=tr("Anggota tidak ditemukan di grup ini",
                                                           "Member not found in this group"))
        username = await conn.fetchval("SELECT username FROM users WHERE id = $1", user_id)
    await log_activity(actor, "GROUP_MEMBER_REMOVE", "WARNING", None,
                       f"{actor.get('username')} mengeluarkan '{username or f'#{user_id}'}' dari grup '{group['name']}'",
                       request)


# ── VM Access ────────────────────────────────────────────────────────────────

@router.get("/{group_id}/vms")
async def list_group_vms(group_id: int, _: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_group_or_404(conn, group_id)
        rows = await conn.fetch("""
            SELECT vm_id, host_name, assigned_at,
                   auth_mode, os_type, guac_protocol,
                   os_username,
                   (os_password_enc IS NOT NULL) AS has_password
            FROM group_vm_access
            WHERE group_id = $1
            ORDER BY host_name, vm_id
        """, group_id)
    return [dict(r) for r in rows]


def _validate_vm_access_body(body: VmAccessBody, password_required: bool = True):
    if body.auth_mode not in ("mandiri", "credentials"):
        raise HTTPException(400, tr("auth_mode harus 'mandiri' atau 'credentials'",
                                    "auth_mode must be 'mandiri' or 'credentials'"))
    if body.auth_mode == "credentials":
        if not body.os_username or not body.os_username.strip():
            raise HTTPException(400, tr("os_username wajib diisi untuk mode credentials",
                                        "os_username is required for credentials mode"))
        if password_required and not body.os_password:
            raise HTTPException(400, tr("os_password wajib diisi untuk mode credentials",
                                        "os_password is required for credentials mode"))
    if body.os_type not in ("linux", "windows"):
        raise HTTPException(400, tr("os_type harus 'linux' atau 'windows'",
                                    "os_type must be 'linux' or 'windows'"))
    protocol = (body.guac_protocol or "").lower()
    if protocol and protocol not in ("ssh", "rdp"):
        raise HTTPException(400, tr("guac_protocol harus 'ssh' atau 'rdp'",
                                    "guac_protocol must be 'ssh' or 'rdp'"))


async def _apply_in_vm(body: VmAccessBody, group_id: int, user: dict, request: Request) -> None:
    """Kredensial grup dengan apply_in_vm: buat user-nya di dalam VM, atau ganti password-nya kalau
    sudah ada, lalu samakan password tersimpan lain untuk user yang sama di VM ini."""
    if body.auth_mode != "credentials" or not body.apply_in_vm:
        return
    if not body.os_password:
        raise HTTPException(400, tr("Isi password untuk membuat atau memperbarui akun ini di dalam VM",
                                    "Enter a password to create or update this account inside the VM"))
    username = guest_accounts.check_username(body.os_username)
    password = guest_accounts.check_password(body.os_password)
    result = await guest_accounts.ensure_user(body.host_name, body.vm_id, username, password)
    await guest_accounts.update_stored_password(body.host_name, body.vm_id, username, password)
    action = "dibuat" if result == "created" else "diganti password-nya"
    await log_activity(user, "OS_ACCOUNT_CREATE" if result == "created" else "VM_PASSWORD_RESET", "WARNING",
                       {"id": body.vm_id, "name": body.host_name},
                       f"User '{username}' {action} di dalam VM {body.vm_id} untuk kredensial grup {group_id}", request)


@router.post("/{group_id}/vms", status_code=201)
async def add_group_vm(group_id: int, body: VmAccessBody, request: Request, user: dict = Depends(require_sysadmin)):
    _validate_vm_access_body(body)
    password_enc = None
    if body.auth_mode == "credentials" and body.os_password:
        from services.ssh_client import encrypt_secret
        password_enc = encrypt_secret(body.os_password)
    protocol = (body.guac_protocol or "").lower() or ("ssh" if body.os_type == "linux" else "rdp")

    pool = await get_pool()
    async with pool.acquire() as conn:
        group = await _get_group_or_404(conn, group_id)
        if await conn.fetchval("SELECT 1 FROM group_vm_access WHERE group_id = $1 AND vm_id = $2 AND host_name = $3",
                               group_id, body.vm_id, body.host_name):
            raise HTTPException(status_code=409, detail=tr("VM sudah memiliki akses ke grup ini",
                                                           "The VM already has access in this group"))
    await _apply_in_vm(body, group_id, user, request)
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """INSERT INTO group_vm_access
                       (group_id, vm_id, host_name, auth_mode, os_username, os_password_enc, os_type, guac_protocol)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   RETURNING vm_id, host_name, assigned_at, auth_mode, os_type, guac_protocol, os_username""",
                group_id, body.vm_id, body.host_name,
                body.auth_mode, (body.os_username or "").strip() or None, password_enc, body.os_type, protocol
            )
        except Exception:
            raise HTTPException(status_code=409, detail=tr("VM sudah memiliki akses ke grup ini",
                                                           "The VM already has access in this group"))
    await log_activity(user, "GROUP_VM_ADD", "WARNING", {"id": body.vm_id, "name": body.host_name},
                       f"{user.get('username')} memberi grup '{group['name']}' akses ke VM {body.vm_id} di "
                       f"{body.host_name} ({_mode_text(body)})", request)
    return dict(row)


@router.put("/{group_id}/vms")
async def update_group_vm_access(group_id: int, body: VmAccessBody, request: Request,
                                 user: dict = Depends(require_sysadmin)):
    """Update auth_mode dan credentials untuk VM yang sudah di-assign ke grup. Password kosong berarti
    password tersimpan tidak diubah (boleh selama username-nya sama dan password sudah pernah diisi)."""
    _validate_vm_access_body(body, password_required=False)
    password_enc = None
    if body.auth_mode == "credentials" and body.os_password:
        from services.ssh_client import encrypt_secret
        password_enc = encrypt_secret(body.os_password)
    protocol = (body.guac_protocol or "").lower() or ("ssh" if body.os_type == "linux" else "rdp")

    pool = await get_pool()
    async with pool.acquire() as conn:
        group = await _get_group_or_404(conn, group_id)
        current = await conn.fetchrow(
            "SELECT os_username, os_password_enc FROM group_vm_access WHERE group_id = $1 AND vm_id = $2 AND host_name = $3",
            group_id, body.vm_id, body.host_name)
    if not current:
        raise HTTPException(404, tr("Akses VM tidak ditemukan di grup ini",
                                    "VM access not found in this group"))
    if body.auth_mode == "credentials" and not body.os_password and (
            not current["os_password_enc"] or current["os_username"] != body.os_username.strip()):
        raise HTTPException(400, tr("os_password wajib diisi untuk mode credentials",
                                    "os_password is required for credentials mode"))
    await _apply_in_vm(body, group_id, user, request)
    async with pool.acquire() as conn:
        result = await conn.fetchrow(
            """UPDATE group_vm_access
               SET auth_mode=$4, os_username=$5, os_password_enc=COALESCE($6, os_password_enc),
                   os_type=$7, guac_protocol=$8
               WHERE group_id=$1 AND vm_id=$2 AND host_name=$3
               RETURNING vm_id, host_name, auth_mode, os_type, guac_protocol, os_username""",
            group_id, body.vm_id, body.host_name,
            body.auth_mode,
            body.os_username.strip() if body.auth_mode == "credentials" else None,
            password_enc, body.os_type, protocol
        )
        if not result:
            raise HTTPException(404, tr("Akses VM tidak ditemukan di grup ini",
                                        "VM access not found in this group"))
    await log_activity(user, "GROUP_VM_UPDATE", "WARNING", {"id": body.vm_id, "name": body.host_name},
                       f"{user.get('username')} mengubah akses grup '{group['name']}' ke VM {body.vm_id} di "
                       f"{body.host_name} ({_mode_text(body)}{', password diganti' if body.os_password else ''})",
                       request)
    return dict(result)


@router.delete("/{group_id}/vms", status_code=204)
async def remove_group_vm(group_id: int, body: VmAccessBody, request: Request, user: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        group = await _get_group_or_404(conn, group_id)
        deleted = await conn.fetchval(
            """DELETE FROM group_vm_access
               WHERE group_id=$1 AND vm_id=$2 AND host_name=$3
               RETURNING group_id""",
            group_id, body.vm_id, body.host_name
        )
        if not deleted:
            raise HTTPException(status_code=404, detail=tr("Akses VM tidak ditemukan di grup ini",
                                                           "VM access not found in this group"))
    await log_activity(user, "GROUP_VM_REMOVE", "WARNING", {"id": body.vm_id, "name": body.host_name},
                       f"{user.get('username')} mencabut akses grup '{group['name']}' ke VM {body.vm_id} di "
                       f"{body.host_name}", request)


# ── User's groups (untuk student melihat grup mereka) ────────────────────────

@router.get("/my")
async def my_groups(user: dict = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT g.id, g.name, g.description, gm.joined_at
            FROM group_members gm
            JOIN groups g ON g.id = gm.group_id
            WHERE gm.user_id = $1
            ORDER BY g.name
        """, int(user["sub"]))
    return [dict(r) for r in rows]
