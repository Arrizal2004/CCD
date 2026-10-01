"""
User management + auth endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends, status, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone

from database import get_pool
from auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_superadmin, require_sysadmin, Role
)
from services.guac_sync import (
    sync_user, sync_user_disabled, delete_user as guac_delete_user,
    sync_vm_assignments, grant_all_connections_to_admin, ADMIN_ROLES,
    sync_os_account_connection, grant_connection, revoke_connection, _find_connection_id, _conn_name_os,
    with_retry as guac_retry,
    get_user_token,
)
from services.audit import log_activity
from services.login_rate_limit import seconds_locked, record_failure, record_success
import asyncio

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username:  str
    password:  str
    full_name: str
    email:     Optional[str] = None

class CreateUserRequest(BaseModel):
    username:  str
    password:  str
    full_name: str
    role:      str  # superadmin | admin | sysadmin | student
    email:     Optional[str] = None

class UpdateUserRequest(BaseModel):
    full_name:   Optional[str]  = None
    role:        Optional[str]  = None
    is_active:   Optional[bool] = None
    is_verified: Optional[bool] = None
    email:       Optional[str]  = None
    password:    Optional[str]  = None

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

class AssignVmRequest(BaseModel):
    user_id:       int
    vm_id:         str
    host_name:     str
    vm_name:       Optional[str] = None
    os_account_id: Optional[int] = None


# ── Auth ──────────────────────────────────────────────────────
async def _guac_login(user, password: str) -> dict:
    """Guacamole token for this user. Accounts whose sync failed earlier (or that were created by raw
    SQL, like the bootstrap admin) are (re)created here, since login is the only moment the plaintext
    password is available."""
    from services.guac_sync import grant_all_connections_to_admin, ADMIN_ROLES as GUAC_ADMIN_ROLES
    auth = await get_user_token(user["username"], password)
    if auth:
        return auth
    if not await sync_user(user["username"], password, user["full_name"] or ""):
        return {}
    if user["role"] in GUAC_ADMIN_ROLES:
        await grant_all_connections_to_admin(user["username"])
    return await get_user_token(user["username"], password)


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    locked_for = await seconds_locked(body.username)
    if locked_for:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Terlalu banyak percobaan login gagal untuk akun ini. Coba lagi dalam {locked_for} detik."
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE username = $1",
            body.username
        )

    if not user or user.get("deleted_at") or not verify_password(body.password, user["password_hash"]):
        await record_failure(body.username)
        await log_activity(
            {"username": body.username, "role": "-"}, "AUTH_LOGIN_FAILED", "WARNING",
            None, f"Login gagal untuk '{body.username}'", request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau password salah"
        )

    await record_success(body.username)

    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun belum diaktifkan. Silakan hubungi administrator."
        )

    # Update last_login
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET last_login = NOW() WHERE id = $1", user["id"]
        )

    token = create_token(user["id"], user["username"], user["role"])
    await log_activity(
        {"sub": user["id"], "username": user["username"], "role": user["role"]},
        "AUTH_LOGIN", "INFO", None, "Login berhasil", request)

    # Login ke Guacamole sebagai user — non-blocking, gagal tidak menghentikan login
    guac_auth: dict = {}
    try:
        guac_auth = await asyncio.wait_for(_guac_login(user, body.password), timeout=10.0)
    except Exception:
        pass

    return {
        "access_token": token,
        "token_type":   "bearer",
        "guac_auth":    guac_auth,   # { authToken, dataSource, username, availableDataSources }
        "user": {
            "id":          user["id"],
            "username":    user["username"],
            "full_name":   user["full_name"],
            "role":        user["role"],
            "email":       user["email"],
            "is_verified": user["is_verified"],
        }
    }


@router.post("/register")
async def register_student(body: RegisterRequest, request: Request):
    """Public student self-registration.
    Creates account with is_active=False — admin must activate before first login.
    """
    if len(body.username.strip()) < 3:
        raise HTTPException(400, "Username minimal 3 karakter")
    if len(body.password) < 8:
        raise HTTPException(400, "Password minimal 8 karakter")
    if not body.full_name.strip():
        raise HTTPException(400, "Nama lengkap wajib diisi")

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM users WHERE username = $1", body.username.strip()
        )
        if existing:
            raise HTTPException(409, "Username sudah dipakai, silakan pilih yang lain")

        await conn.execute(
            """INSERT INTO users (username, password_hash, full_name, role, email, is_active, is_verified)
               VALUES ($1, $2, $3, 'student', $4, true, false)""",
            body.username.strip(),
            hash_password(body.password),
            body.full_name.strip(),
            body.email,
        )

    await log_activity(
        {"username": body.username, "role": "student"},
        "AUTH_REGISTER", "INFO", None,
        f"Registrasi baru: '{body.username}' ({body.full_name})", request
    )

    asyncio.create_task(guac_retry(
        sync_user, body.username.strip(), body.password, body.full_name.strip(),
        op_name="register:sync_user",
    ))

    return {"message": "Registrasi berhasil! Silakan login sekarang."}


@router.post("/logout")
async def logout(user: dict = Depends(get_current_user)):
    """Revoke JWT dan putuskan semua sesi guacd aktif untuk user."""
    from routers.guac import disconnect_user

    jti = user.get("jti")
    if jti:
        exp = user.get("exp", 0)
        remaining = max(0, int(exp) - int(datetime.now(timezone.utc).timestamp()))
        import token_blocklist
        await token_blocklist.revoke(jti, remaining)

    username = user.get("username", "")
    if username:
        disconnected = await disconnect_user(username)
        if disconnected:
            import logging
            logging.getLogger("users").info(
                "logout: disconnected %d guacd session(s) for '%s'", disconnected, username
            )

    return {"message": "Logout berhasil"}


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Ambil info user yang sedang login."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, full_name, role, email, is_verified, last_login, created_at FROM users WHERE id = $1",
            int(user["sub"])
        )
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@router.post("/me/change-password")
async def change_password(body: ChangePasswordRequest, request: Request, user: dict = Depends(get_current_user)):
    """Ganti password sendiri — wajib verifikasi password lama terlebih dahulu."""
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password baru minimal 8 karakter")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash FROM users WHERE id = $1",
            int(user["sub"])
        )
    if not row:
        raise HTTPException(404, "User tidak ditemukan")
    if not verify_password(body.old_password, row["password_hash"]):
        raise HTTPException(400, "Password lama tidak sesuai")

    new_hash = hash_password(body.new_password)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET password_hash = $1, updated_at = NOW() WHERE id = $2",
            new_hash, row["id"]
        )

    asyncio.create_task(guac_retry(
        sync_user, row["username"], body.new_password, user.get("full_name", "") or "",
        op_name="change_password:sync_user",
    ))

    await log_activity(
        user, "AUTH_CHANGE_PASSWORD", "INFO", None,
        f"User '{user.get('username')}' mengganti password", request
    )
    return {"message": "Password berhasil diubah"}


# ── User CRUD (superadmin only) ───────────────────────────────
@router.get("")
async def list_users(user: dict = Depends(require_sysadmin)):
    """List semua users — admin dan superadmin saja."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, username, full_name, role, email,
                      is_active, is_verified, last_login, created_at
               FROM users WHERE deleted_at IS NULL ORDER BY role, username"""
        )
    return [dict(r) for r in rows]


@router.post("")
async def create_user(body: CreateUserRequest, user: dict = Depends(require_superadmin)):
    """Buat user baru — superadmin saja."""
    valid_roles = {Role.SUPERADMIN, Role.SYSADMIN, Role.STUDENT}
    if body.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Role tidak valid: {body.role}")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password minimal 8 karakter")

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM users WHERE username = $1", body.username
        )
        if existing:
            raise HTTPException(status_code=409, detail="Username sudah dipakai")

        row = await conn.fetchrow(
            """INSERT INTO users (username, password_hash, full_name, role, email)
               VALUES ($1, $2, $3, $4, $5) RETURNING id, username, full_name, role, email""",
            body.username, hash_password(body.password),
            body.full_name, body.role, body.email
        )
    async def _setup_guac_user():
        await guac_retry(sync_user, body.username, body.password, body.full_name or "",
                         op_name="create_user:sync_user")
        if body.role in ADMIN_ROLES:
            await guac_retry(grant_all_connections_to_admin, body.username,
                             op_name="create_user:grant_admin")
    asyncio.create_task(_setup_guac_user())
    return dict(row)


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    body: UpdateUserRequest,
    current: dict = Depends(require_superadmin)
):
    if body.password is not None and len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password minimal 8 karakter")

    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        updates, values, idx = [], [], 1
        if body.full_name is not None:
            updates.append(f"full_name = ${idx}"); values.append(body.full_name); idx += 1
        if body.role is not None:
            updates.append(f"role = ${idx}"); values.append(body.role); idx += 1
        if body.is_active is not None:
            updates.append(f"is_active = ${idx}"); values.append(body.is_active); idx += 1
        if body.is_verified is not None:
            updates.append(f"is_verified = ${idx}"); values.append(body.is_verified); idx += 1
        if body.email is not None:
            updates.append(f"email = ${idx}"); values.append(body.email); idx += 1
        if body.password is not None:
            updates.append(f"password_hash = ${idx}"); values.append(hash_password(body.password)); idx += 1

        if not updates:
            return dict(user)

        values.append(user_id)
        row = await conn.fetchrow(
            f"UPDATE users SET {', '.join(updates)}, updated_at = NOW() WHERE id = ${idx} RETURNING id, username, full_name, role, is_active",
            *values
        )
    result = dict(row)
    if body.password:
        async def _update_guac_pwd():
            await guac_retry(
                sync_user,
                result["username"], body.password, result.get("full_name", ""),
                disabled=not result.get("is_active", True),
                op_name="update_user:sync_user",
            )
            if result.get("role") in ADMIN_ROLES:
                await guac_retry(grant_all_connections_to_admin, result["username"],
                                 op_name="update_user:grant_admin")
        asyncio.create_task(_update_guac_pwd())
    elif body.is_active is not None:
        asyncio.create_task(guac_retry(
            sync_user_disabled,
            result["username"], disabled=not result.get("is_active", True),
            op_name="update_user:disabled",
        ))
    if body.role in ADMIN_ROLES and not body.password:
        asyncio.create_task(guac_retry(
            grant_all_connections_to_admin, result["username"],
            op_name="update_user:grant_admin_role",
        ))
    return result


@router.delete("/{user_id}")
async def delete_user_endpoint(user_id: int, current: dict = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT username FROM users WHERE id = $1 AND deleted_at IS NULL", user_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        await conn.execute(
            "UPDATE users SET deleted_at = NOW() WHERE id = $1", user_id
        )
        # Soft-delete semua assignment aktif milik user ini
        await conn.execute(
            "UPDATE vm_assignments SET deleted_at = NOW() WHERE user_id = $1 AND deleted_at IS NULL",
            user_id
        )
    asyncio.create_task(guac_retry(guac_delete_user, row["username"], op_name="delete_user:guac"))
    return {"status": "deleted", "user_id": user_id}


# ── VM Assignment (untuk student role) ───────────────────────
@router.post("/vm-assignments")
async def assign_vm(body: AssignVmRequest, request: Request, current: dict = Depends(require_sysadmin)):
    """Assign VM ke student, opsional dengan OS account tertentu."""
    detail = f"Assign VM {body.vm_id} ke user #{body.user_id}"
    if body.os_account_id:
        detail += f" (OS account #{body.os_account_id})"
    await log_activity(current, "RBAC_ASSIGN_VM", "WARNING",
                       {"id": body.vm_id, "name": body.host_name}, detail, request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Resolve vm_name from entity table if caller didn't provide it
        vm_name = body.vm_name
        if not vm_name:
            v = await conn.fetchrow(
                "SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2",
                body.vm_id, body.host_name
            )
            vm_name = v["vm_name"] if v else None

        await conn.execute(
            """INSERT INTO vm_assignments (user_id, vm_id, host_name, vm_name, os_account_id)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (user_id, vm_id, host_name) WHERE deleted_at IS NULL DO UPDATE
               SET vm_name = COALESCE($4, vm_assignments.vm_name), os_account_id = $5""",
            body.user_id, body.vm_id, body.host_name, vm_name, body.os_account_id
        )
        urow = await conn.fetchrow(
            "SELECT username FROM users WHERE id = $1 AND deleted_at IS NULL", body.user_id
        )

        if body.os_account_id:
            # Fetch OS account details untuk sync Guacamole per-account
            os_acc = await conn.fetchrow(
                """SELECT voa.*, v.vm_name
                   FROM vm_os_accounts voa
                   LEFT JOIN vms v ON v.vm_id = $1 AND v.host_name = voa.host_name
                   WHERE voa.id = $2""",
                body.vm_id, body.os_account_id
            )
        else:
            os_acc = None
            arows = await conn.fetch(
                """SELECT va.vm_id, COALESCE(va.vm_name, v.vm_name) AS vm_name
                   FROM vm_assignments va
                   LEFT JOIN vms v ON v.vm_id = va.vm_id AND v.host_name = va.host_name
                   WHERE va.user_id = $1 AND va.host_name = $2 AND va.deleted_at IS NULL""",
                body.user_id, body.host_name
            )

    if urow:
        username = urow["username"]
        if os_acc and os_acc["vm_name"]:
            from services.ssh_client import decrypt_secret
            async def _grant_os_acc():
                creds = {
                    "os_type":       os_acc["os_type"],
                    "guac_protocol": os_acc["guac_protocol"],
                    "ssh_host":      os_acc["ssh_host"],
                    "ssh_port":      os_acc["ssh_port"],
                    "username":      os_acc["os_username"],
                    "password":      decrypt_secret(os_acc["password_enc"]) if os_acc["password_enc"] else "",
                    "pkey":          decrypt_secret(os_acc["pkey_enc"])     if os_acc["pkey_enc"]     else "",
                }
                conn_id = await guac_retry(
                    sync_os_account_connection,
                    body.host_name, os_acc["vm_name"], os_acc["os_username"], creds,
                    op_name="assign_vm:sync_os_conn",
                )
                if conn_id:
                    await guac_retry(grant_connection, username, conn_id,
                                     op_name="assign_vm:grant_conn")
                return bool(conn_id)
            asyncio.create_task(guac_retry(_grant_os_acc, op_name="assign_vm:os_account"))
        else:
            vm_names = [r["vm_name"] for r in arows if r["vm_name"]]
            asyncio.create_task(guac_retry(
                sync_vm_assignments, username, body.host_name, vm_names,
                op_name="assign_vm:sync_assignments",
            ))
    return {"status": "assigned"}


@router.get("/{user_id}/vm-assignments")
async def get_user_assignments(user_id: int, current: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT va.id, va.user_id, va.vm_id, va.host_name,
                      COALESCE(va.vm_name, v.vm_name) AS vm_name,
                      va.assigned_at, va.os_account_id,
                      voa.os_username, voa.label AS os_account_label
               FROM vm_assignments va
               LEFT JOIN vms v ON v.vm_id = va.vm_id AND v.host_name = va.host_name
               LEFT JOIN vm_os_accounts voa ON voa.id = va.os_account_id
               WHERE va.user_id = $1 AND va.deleted_at IS NULL""",
            user_id
        )
    return [dict(r) for r in rows]


@router.delete("/{user_id}/vm-assignments/{vm_id}")
async def remove_assignment(user_id: int, vm_id: str, request: Request, current: dict = Depends(require_sysadmin)):
    await log_activity(current, "RBAC_UNASSIGN_VM", "WARNING",
                       {"id": vm_id, "name": vm_id},
                       f"Unassign VM {vm_id} dari user #{user_id}", request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        urow = await conn.fetchrow(
            "SELECT username FROM users WHERE id = $1 AND deleted_at IS NULL", user_id
        )
        removed_row = await conn.fetchrow(
            """SELECT va.host_name, va.os_account_id, voa.os_username,
                      COALESCE(va.vm_name, v.vm_name) AS vm_name
               FROM vm_assignments va
               LEFT JOIN vms v ON v.vm_id = va.vm_id AND v.host_name = va.host_name
               LEFT JOIN vm_os_accounts voa ON voa.id = va.os_account_id
               WHERE va.user_id = $1 AND va.vm_id = $2 AND va.deleted_at IS NULL""",
            user_id, vm_id
        )
        await conn.execute(
            "UPDATE vm_assignments SET deleted_at = NOW() WHERE user_id = $1 AND vm_id = $2 AND deleted_at IS NULL",
            user_id, vm_id
        )
        arows = await conn.fetch(
            """SELECT va.vm_id, va.host_name, va.os_account_id, voa.os_username,
                      COALESCE(va.vm_name, v.vm_name) AS vm_name
               FROM vm_assignments va
               LEFT JOIN vms v ON v.vm_id = va.vm_id AND v.host_name = va.host_name
               LEFT JOIN vm_os_accounts voa ON voa.id = va.os_account_id
               WHERE va.user_id = $1 AND va.deleted_at IS NULL""",
            user_id
        )

    if urow and removed_row:
        username = urow["username"]
        if removed_row["os_account_id"] and removed_row["os_username"] and removed_row["vm_name"]:
            conn_name = _conn_name_os(removed_row["host_name"], removed_row["vm_name"], removed_row["os_username"])
            async def _revoke_os():
                cid = await _find_connection_id(conn_name)
                if not cid:
                    return True  # nothing to revoke
                return await guac_retry(revoke_connection, username, cid,
                                        op_name="remove_assign:revoke_conn")
            asyncio.create_task(guac_retry(_revoke_os, op_name="remove_assign:revoke_os"))
        else:
            by_host: dict[str, list[str]] = {}
            for r in arows:
                if r["vm_name"] and not r["os_account_id"]:
                    by_host.setdefault(r["host_name"], []).append(r["vm_name"])
            removed_host = removed_row["host_name"]
            if removed_host not in by_host:
                by_host[removed_host] = []
            for host, names in by_host.items():
                asyncio.create_task(guac_retry(
                    sync_vm_assignments, username, host, names,
                    op_name="remove_assign:sync_assignments",
                ))

    return {"status": "removed"}