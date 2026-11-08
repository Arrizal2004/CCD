"""
User management + auth endpoints.
"""
import re

from fastapi import APIRouter, HTTPException, Depends, status, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone, timedelta

from database import get_pool
from auth import (
    forget_account,
    hash_password, verify_password, create_token,
    get_current_user, require_superadmin, require_sysadmin, Role
)
from services.guac_sync import (
    sync_user, sync_user_disabled, delete_user as guac_delete_user,
    sync_vm_assignments, grant_all_connections_to_admin, ADMIN_ROLES,
    revoke_connection, _find_connection_id, _conn_name_os,
    with_retry as guac_retry,
    get_user_token,
)
from services.audit import both, log_activity, _client_ip
from services import scope, mfa, onetime
from services.login_rate_limit import (
    seconds_locked, record_failure, record_success, clear as clear_login_lock, allow as rate_allow,
)
import asyncio
from i18n import tr

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
    username:   str
    password:   str
    full_name:  str
    role:       str  # superadmin | admin | sysadmin | student
    email:      Optional[str] = None
    expires_at: Optional[datetime] = None

class UpdateUserRequest(BaseModel):
    full_name:   Optional[str]  = None
    role:        Optional[str]  = None
    is_active:   Optional[bool] = None
    is_verified: Optional[bool] = None
    email:       Optional[str]  = None
    password:    Optional[str]  = None
    expires_at:  Optional[datetime] = None   # kirim null untuk menghapus batas masa berlaku

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

class PasswordHelpRequest(BaseModel):
    username: str
    message:  str = ""

class AssignVmRequest(BaseModel):
    user_id:       int
    vm_id:         str
    host_name:     str
    vm_name:       Optional[str] = None
    os_account_id: Optional[int] = None
    access:        str = "full"         # "full" (Connect + Open Web) | "web" (hanya Open Web)


# ── Auth ──────────────────────────────────────────────────────
async def _guac_login(user, password: str, client_ip: str = "") -> dict:
    """Guacamole token for this user. Accounts whose sync failed earlier (or that were created by raw
    SQL, like the bootstrap admin) are (re)created here, since login is the only moment the plaintext
    password is available. client_ip is recorded by Guacamole as the client of every Remote session
    opened with this token."""
    from services.guac_sync import grant_all_connections_to_admin, ADMIN_ROLES as GUAC_ADMIN_ROLES
    auth = await get_user_token(user["username"], password, client_ip)
    if auth:
        return auth
    if not await sync_user(user["username"], password, user["full_name"] or ""):
        return {}
    if user["role"] in GUAC_ADMIN_ROLES:
        await grant_all_connections_to_admin(user["username"])
    return await get_user_token(user["username"], password, client_ip)


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    locked_for = await seconds_locked(body.username)
    if locked_for:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=tr(f"Terlalu banyak percobaan login gagal untuk akun ini. Coba lagi dalam {locked_for} detik.",
                      f"Too many failed sign-in attempts for this account. Try again in {locked_for} seconds.")
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
            None, both(lambda: tr(f"Login gagal untuk '{body.username}'", f"Sign-in failed for '{body.username}'")), request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr("Username atau password salah", "Wrong username or password")
        )

    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=tr("Akun belum diaktifkan. Silakan hubungi administrator.",
                      "This account is not active. Please contact an administrator.")
        )
    if user.get("expires_at") and user["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=tr("Masa berlaku akun sudah habis. Silakan hubungi administrator.",
                      "This account has expired. Please contact an administrator.")
        )

    # 2FA: password benar baru langkah pertama. Password disimpan terenkripsi bersama tantangan (5 menit)
    # karena akun Guacamole memakai password yang sama dan baru dibuka setelah kode benar.
    has_mfa = user["mfa_secret_enc"] is not None
    needs_second_step = has_mfa or mfa.required_for(await mfa.get_policy(), user["role"])
    if not needs_second_step:
        # Hitungan gagal baru dihapus saat login benar-benar selesai. Untuk akun dengan 2FA, password benar saja
        # belum cukup: kalau dihapus di sini, penyerang yang tahu password bisa login ulang tanpa batas untuk menebak kode.
        await record_success(body.username)
    if needs_second_step:
        challenge = onetime.new_id()
        await onetime.put("mfa", challenge, {"uid": user["id"], "pwv": user["password_version"], "pw": body.password,
                                              "setup": not has_mfa, "tries": 0}, MFA_TTL)
        return {"mfa_required": has_mfa, "mfa_setup_required": not has_mfa, "mfa_token": challenge,
                "expires_in": MFA_TTL}

    return await _start_session(user, body.password, request)


async def _start_session(user, password: str, request: Request, method: str = "password") -> dict:
    """Akhir login yang sukses: catat login, buat token, buka sesi Guacamole."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET last_login = NOW() WHERE id = $1", user["id"])

    token = create_token(user["id"], user["username"], user["role"], user["password_version"])
    await log_activity(
        {"sub": user["id"], "username": user["username"], "role": user["role"]},
        "AUTH_LOGIN", "INFO", None,
        both(lambda: tr("Login berhasil", "Signed in") if method == "password"
             else (tr("Login berhasil dengan kode pemulihan 2FA", "Signed in with a 2FA recovery code") if method == "recovery"
                   else tr("Login berhasil dengan 2FA", "Signed in with 2FA"))), request)

    # Login ke Guacamole sebagai user — non-blocking, gagal tidak menghentikan login. Dengan password
    # sementara dari admin belum ada Connect; token Guacamole diberikan setelah password diganti.
    guac_auth: dict = {}
    if not user["must_change_password"]:
        try:
            guac_auth = await asyncio.wait_for(_guac_login(user, password, _client_ip(request)), timeout=10.0)
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
            "must_change_password": user["must_change_password"],
        }
    }


# ── 2FA saat login ────────────────────────────────────────────
MFA_TTL = 300
MFA_MAX_TRIES = 5


class MfaLoginBody(BaseModel):
    mfa_token: str
    code:      str = ""


class MfaCodeBody(BaseModel):
    code: str


class MfaDisableBody(BaseModel):
    code:     str
    password: str = ""


def _challenge_expired() -> HTTPException:
    return HTTPException(401, tr("Waktu verifikasi habis. Silakan login lagi.", "The verification step has expired. Please sign in again."))


async def _challenge_user(body: MfaLoginBody, setup: bool):
    """Tantangan 2FA yang masih berlaku beserta akun pemiliknya (diperiksa ulang: aktif, belum kedaluwarsa, password sama)."""
    ch = await onetime.get("mfa", body.mfa_token)
    if not ch or bool(ch.get("setup")) != setup:
        raise _challenge_expired()
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE id = $1 AND deleted_at IS NULL", ch["uid"])
    if (not user or user["password_version"] != ch["pwv"] or not user["is_active"]
            or (user["expires_at"] and user["expires_at"] <= datetime.now(timezone.utc))):
        await onetime.delete("mfa", body.mfa_token)
        raise _challenge_expired()
    locked_for = await seconds_locked(user["username"])
    if locked_for:
        raise HTTPException(429, tr(f"Terlalu banyak percobaan gagal. Coba lagi dalam {locked_for} detik.",
                                    f"Too many failed attempts. Try again in {locked_for} seconds."))
    return ch, user


async def _mfa_failed(body: MfaLoginBody, ch: dict, user, request: Request) -> HTTPException:
    """Catat kode salah: hitungan per tantangan dan kunci akun yang sama dengan password salah."""
    ch["tries"] = int(ch.get("tries", 0)) + 1
    left = await onetime.ttl("mfa", body.mfa_token)
    if ch["tries"] >= MFA_MAX_TRIES or left <= 0:
        await onetime.delete("mfa", body.mfa_token)
    else:
        await onetime.put("mfa", body.mfa_token, ch, left)
    await record_failure(user["username"])
    await log_activity(
        {"sub": user["id"], "username": user["username"], "role": user["role"]},
        "AUTH_MFA_FAILED", "WARNING", None,
        both(lambda: tr(f"Kode 2FA salah untuk '{user['username']}'", f"Wrong 2FA code for '{user['username']}'")), request)
    if ch["tries"] >= MFA_MAX_TRIES:
        return HTTPException(401, tr("Terlalu banyak kode salah. Silakan login lagi.", "Too many wrong codes. Please sign in again."))
    return mfa.bad_code()


@router.post("/login/mfa")
async def login_mfa(body: MfaLoginBody, request: Request):
    """Langkah kedua login: kode dari aplikasi authenticator, atau salah satu kode pemulihan."""
    ch, user = await _challenge_user(body, setup=False)
    pool = await get_pool()
    async with pool.acquire() as conn:
        kind = await mfa.verify_user_code(conn, user["id"], body.code)
    if not kind:
        raise await _mfa_failed(body, ch, user, request)
    await onetime.delete("mfa", body.mfa_token)
    await record_success(user["username"])
    out = await _start_session(user, ch["pw"], request, method="recovery" if kind == "recovery" else "mfa")
    if kind == "recovery":
        async with pool.acquire() as conn:
            out["recovery_codes_left"] = (await mfa.status(conn, user["id"]))["recovery_codes_left"]
    return out


async def _issuer_name() -> str:
    from services import system_settings as ss
    return (await ss.get_settings()).get("name") or "CCD"


@router.post("/login/mfa/setup")
async def login_mfa_setup(body: MfaLoginBody):
    """Kebijakan mewajibkan 2FA tetapi akun belum memasangnya: buat rahasia baru untuk dipindai."""
    ch, user = await _challenge_user(body, setup=True)
    if not ch.get("secret"):
        ch["secret"] = mfa.new_secret()
        await onetime.put("mfa", body.mfa_token, ch, max(await onetime.ttl("mfa", body.mfa_token), 1))
    return mfa.enrollment(ch["secret"], user["username"], await _issuer_name())


@router.post("/login/mfa/enable")
async def login_mfa_enable(body: MfaLoginBody, request: Request):
    ch, user = await _challenge_user(body, setup=True)
    step = mfa.match_step(ch.get("secret") or "", body.code) if ch.get("secret") else None
    if step is None:
        raise await _mfa_failed(body, ch, user, request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            codes = await mfa.enable(conn, user["id"], ch["secret"], step)
    await onetime.delete("mfa", body.mfa_token)
    await record_success(user["username"])
    await log_activity(
        {"sub": user["id"], "username": user["username"], "role": user["role"]},
        "MFA_ENABLED", "INFO", None,
        both(lambda: tr(f"User '{user['username']}' memasang 2FA (wajib menurut kebijakan)",
                        f"User '{user['username']}' set up 2FA (required by policy)")), request)
    out = await _start_session(user, ch["pw"], request, method="mfa")
    out["recovery_codes"] = codes
    return out


_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def _clean_email(email: Optional[str]) -> Optional[str]:
    """Email dirapikan (huruf kecil, tanpa spasi). Kosong menjadi None."""
    email = (email or "").strip().lower()
    if not email:
        return None
    if len(email) > 254 or not _EMAIL.match(email):
        raise HTTPException(400, tr("Format email tidak valid", "Invalid email format"))
    return email


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Tanggal tanpa zona waktu dianggap UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _ensure_email_free(conn, email: Optional[str], exclude_id: Optional[int] = None) -> None:
    """Satu email untuk satu akun (akun yang sudah dihapus tidak dihitung)."""
    if email and await conn.fetchval(
            "SELECT 1 FROM users WHERE lower(email) = $1 AND deleted_at IS NULL AND id IS DISTINCT FROM $2",
            email, exclude_id):
        raise HTTPException(409, tr("Email sudah dipakai akun lain",
                                    "This email is already used by another account"))


@router.post("/register")
async def register_student(body: RegisterRequest, request: Request):
    """Public student self-registration.
    Creates account with is_active=False — admin must activate before first login.
    """
    if len(body.username.strip()) < 3:
        raise HTTPException(400, tr("Username minimal 3 karakter",
                                    "The username must be at least 3 characters"))
    if len(body.password) < 8:
        raise HTTPException(400, tr("Password minimal 8 karakter",
                                    "The password must be at least 8 characters"))
    if not body.full_name.strip():
        raise HTTPException(400, tr("Nama lengkap wajib diisi", "Full name is required"))

    # Aturan pendaftaran dari Pengaturan Sistem (superadmin).
    from services.system_settings import get_settings, email_allowed
    settings = await get_settings()
    if not settings["registration_open"]:
        raise HTTPException(403, tr("Pendaftaran mandiri sedang ditutup. Hubungi admin untuk dibuatkan akun.",
                                    "Self-registration is closed. Ask an administrator to create an account."))
    email = _clean_email(body.email)
    if settings["allowed_emails"] and not email:
        raise HTTPException(400, tr("Email wajib diisi", "Email is required"))
    if email and not email_allowed(email, settings["allowed_emails"]):
        raise HTTPException(400, tr("Email ini tidak diizinkan untuk mendaftar. Gunakan email institusi Anda.",
                                    "This email is not allowed to register. Use your institution email."))

    expires = (datetime.now(timezone.utc) + timedelta(days=settings["default_account_days"])
               if settings["default_account_days"] else None)

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM users WHERE username = $1", body.username.strip()
        )
        if existing:
            raise HTTPException(409, tr("Username sudah dipakai, silakan pilih yang lain",
                                        "This username is taken, please choose another"))
        await _ensure_email_free(conn, email)

        await conn.execute(
            """INSERT INTO users (username, password_hash, full_name, role, email, is_active, is_verified, expires_at)
               VALUES ($1, $2, $3, 'student', $4, true, false, $5)""",
            body.username.strip(),
            hash_password(body.password),
            body.full_name.strip(),
            email,
            expires,
        )

    await log_activity(
        {"username": body.username, "role": "student"},
        "AUTH_REGISTER", "INFO", None,
        both(lambda: tr(f"Registrasi baru: '{body.username}' ({body.full_name})", f"New registration: '{body.username}' ({body.full_name})")), request
    )

    asyncio.create_task(guac_retry(
        sync_user, body.username.strip(), body.password, body.full_name.strip(),
        op_name="register:sync_user",
    ))

    return {"message": tr("Registrasi berhasil! Silakan login sekarang.",
                          "Registration successful! You can sign in now.")}


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

    return {"message": tr("Logout berhasil", "Signed out")}


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Ambil info user yang sedang login."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, username, full_name, role, email, is_verified, last_login, created_at, must_change_password,
                      mfa_secret_enc IS NOT NULL AS mfa_enabled
               FROM users WHERE id = $1""",
            int(user["sub"])
        )
    if not row:
        raise HTTPException(status_code=404, detail=tr("User tidak ditemukan", "User not found"))
    return dict(row)


# ── 2FA: pengaturan akun sendiri ──────────────────────────────
@router.get("/me/mfa")
async def my_mfa(user: dict = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        out = await mfa.status(conn, int(user["sub"]))
    out["required"] = mfa.required_for(await mfa.get_policy(), user.get("role"))
    return out


@router.post("/me/mfa/setup")
async def my_mfa_setup(user: dict = Depends(get_current_user)):
    """Rahasia baru untuk dipindai. Baru berlaku setelah dikonfirmasi dengan satu kode (10 menit)."""
    uid = int(user["sub"])
    pool = await get_pool()
    async with pool.acquire() as conn:
        if (await mfa.status(conn, uid))["enabled"]:
            raise HTTPException(409, tr("2FA sudah aktif. Nonaktifkan dulu untuk memasang ulang.",
                                        "2FA is already on. Turn it off first to set it up again."))
    secret = mfa.new_secret()
    await onetime.put("mfa-enroll", str(uid), {"secret": secret}, 600)
    return mfa.enrollment(secret, user.get("username") or "", await _issuer_name())


@router.post("/me/mfa/enable")
async def my_mfa_enable(body: MfaCodeBody, request: Request, user: dict = Depends(get_current_user)):
    uid = int(user["sub"])
    pending = await onetime.get("mfa-enroll", str(uid))
    if not pending:
        raise HTTPException(400, tr("Waktu pemasangan habis. Mulai lagi.", "The setup has expired. Start again."))
    step = mfa.match_step(pending["secret"], body.code)
    if step is None:
        raise mfa.bad_code()
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if (await mfa.status(conn, uid))["enabled"]:
                raise HTTPException(409, tr("2FA sudah aktif", "2FA is already on"))
            codes = await mfa.enable(conn, uid, pending["secret"], step)
    await onetime.delete("mfa-enroll", str(uid))
    await log_activity(user, "MFA_ENABLED", "INFO", None,
                       both(lambda: tr(f"User '{user.get('username')}' mengaktifkan 2FA",
                                       f"User '{user.get('username')}' turned on 2FA")), request)
    return {"recovery_codes": codes}


@router.post("/me/mfa/disable")
async def my_mfa_disable(body: MfaDisableBody, request: Request, user: dict = Depends(get_current_user)):
    """Matikan 2FA sendiri: butuh kode 2FA dan password."""
    uid = int(user["sub"])
    if mfa.required_for(await mfa.get_policy(), user.get("role")):
        raise HTTPException(403, tr("Kebijakan sistem mewajibkan 2FA untuk akun Anda",
                                    "The system policy requires 2FA for your account"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        pw_hash = await conn.fetchval("SELECT password_hash FROM users WHERE id = $1", uid)
        if not pw_hash or not verify_password(body.password, pw_hash):
            raise HTTPException(400, tr("Password salah", "Wrong password"))
        if not await mfa.verify_user_code(conn, uid, body.code):
            raise mfa.bad_code()
        await mfa.disable(conn, uid)
    await log_activity(user, "MFA_DISABLED", "WARNING", None,
                       both(lambda: tr(f"User '{user.get('username')}' menonaktifkan 2FA",
                                       f"User '{user.get('username')}' turned off 2FA")), request)
    return {"ok": True}


@router.post("/me/mfa/recovery-codes")
async def my_mfa_recovery_codes(body: MfaCodeBody, request: Request, user: dict = Depends(get_current_user)):
    """Buat 10 kode pemulihan baru; kode lama tidak berlaku lagi."""
    uid = int(user["sub"])
    pool = await get_pool()
    async with pool.acquire() as conn:
        if await mfa.verify_user_code(conn, uid, body.code) != "totp":
            raise mfa.bad_code()
        async with conn.transaction():
            codes = await mfa.replace_recovery_codes(conn, uid)
    await log_activity(user, "MFA_RECOVERY_CODES", "INFO", None,
                       both(lambda: tr(f"User '{user.get('username')}' membuat kode pemulihan 2FA baru",
                                       f"User '{user.get('username')}' generated new 2FA recovery codes")), request)
    return {"recovery_codes": codes}


@router.post("/{user_id}/mfa/reset")
async def admin_mfa_reset(user_id: int, request: Request, current: dict = Depends(require_sysadmin)):
    """Admin mematikan 2FA pengguna yang kehilangan ponselnya dan kode pemulihannya. Sesi lamanya berakhir."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await _visible_target(conn, current, user_id)
            await mfa.disable(conn, user_id)
            await conn.execute("UPDATE users SET password_version = password_version + 1, updated_at = NOW() WHERE id = $1", user_id)
    forget_account(user_id)
    try:
        from routers.guac import disconnect_user
        await disconnect_user(target["username"])
    except Exception:
        pass
    await log_activity(current, "MFA_RESET", "WARNING", None,
                       both(lambda: tr(f"{current.get('username')} mereset 2FA akun '{target['username']}'",
                                       f"{current.get('username')} reset 2FA of the account '{target['username']}'")), request)
    return {"ok": True}


@router.get("/mfa-policy")
async def get_mfa_policy(user: dict = Depends(require_superadmin)):
    return await mfa.get_policy()


@router.put("/mfa-policy")
async def put_mfa_policy(body: dict, request: Request, user: dict = Depends(require_superadmin)):
    policy = await mfa.save_policy(body, user.get("username") or "")
    await log_activity(user, "MFA_POLICY", "WARNING", None,
                       both(lambda: tr(f"Kebijakan 2FA diubah menjadi '{policy['required']}'",
                                       f"The 2FA policy was changed to '{policy['required']}'")), request)
    return policy



@router.post("/me/change-password")
async def change_password(body: ChangePasswordRequest, request: Request, user: dict = Depends(get_current_user)):
    """Ganti password sendiri — wajib verifikasi password lama terlebih dahulu. Semua sesi lain ikut
    berakhir; sesi ini menerima token baru di respons."""
    if len(body.new_password) < 8:
        raise HTTPException(400, tr("Password baru minimal 8 karakter",
                                    "The new password must be at least 8 characters"))
    if len(body.new_password) > 128:
        raise HTTPException(400, tr("Password baru maksimal 128 karakter",
                                    "The new password may be at most 128 characters"))

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, full_name, role, password_hash, must_change_password FROM users WHERE id = $1",
            int(user["sub"])
        )
    if not row:
        raise HTTPException(404, tr("User tidak ditemukan", "User not found"))
    if not verify_password(body.old_password, row["password_hash"]):
        raise HTTPException(400, tr("Password lama tidak sesuai", "The current password is wrong"))
    if body.new_password == body.old_password:
        raise HTTPException(400, tr("Password baru harus berbeda dari password lama",
                                    "The new password must differ from the current one"))

    new_hash = hash_password(body.new_password)
    async with pool.acquire() as conn:
        version = await conn.fetchval(
            """UPDATE users SET password_hash = $1, password_version = password_version + 1,
                      must_change_password = FALSE, updated_at = NOW()
               WHERE id = $2 RETURNING password_version""",
            new_hash, row["id"]
        )
    forget_account(row["id"])

    # Token Guacamole dengan password baru (sekaligus menyinkronkan password-nya ke Guacamole).
    guac_auth: dict = {}
    try:
        guac_auth = await asyncio.wait_for(_guac_login(row, body.new_password, _client_ip(request)), timeout=10.0)
    except Exception:
        pass
    if not guac_auth:
        asyncio.create_task(guac_retry(
            sync_user, row["username"], body.new_password, row["full_name"] or "",
            op_name="change_password:sync_user",
        ))

    await log_activity(
        user, "AUTH_CHANGE_PASSWORD", "INFO", None,
        both(lambda: tr(f"User '{user.get('username')}' mengganti password", f"User '{user.get('username')}' changed their password")
             + (tr(" (password sementara dari admin)", " (temporary password from an admin)") if row["must_change_password"] else "")),
        request
    )
    return {
        "message":      tr("Password berhasil diubah", "Password changed"),
        "access_token": create_token(row["id"], row["username"], row["role"], version),
        "guac_auth":    guac_auth,
    }


async def _visible_target(conn, current: dict, user_id: int):
    """Akun yang boleh ditangani admin ini: superadmin semua akun, sysadmin hanya mahasiswa."""
    target = await conn.fetchrow(
        "SELECT id, username, full_name, role, is_active FROM users WHERE id = $1 AND deleted_at IS NULL",
        user_id)
    if not target:
        raise HTTPException(404, tr("User tidak ditemukan", "User not found"))
    if current["role"] != Role.SUPERADMIN and target["role"] != Role.STUDENT:
        raise HTTPException(403, tr("Sysadmin hanya bisa menangani akun mahasiswa",
                                    "Sysadmins can only manage student accounts"))
    return target


@router.post("/{user_id}/reset-password")
async def reset_password(user_id: int, request: Request, current: dict = Depends(require_sysadmin)):
    """Buat password sementara acak untuk pengguna yang lupa password. Password ditampilkan sekali ke
    admin, semua sesi lama pengguna berakhir, dan pengguna wajib menggantinya saat login berikutnya."""
    if user_id == int(current["sub"]):
        raise HTTPException(400, tr("Untuk akun Anda sendiri, gunakan Ganti Password di profil",
                                    "For your own account, use Change password in your profile"))
    from services.guest_accounts import generate_password
    from routers.guac import disconnect_user
    password = generate_password()
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await _visible_target(conn, current, user_id)
            await conn.execute(
                """UPDATE users SET password_hash = $1, password_version = password_version + 1,
                          must_change_password = TRUE, updated_at = NOW() WHERE id = $2""",
                hash_password(password), user_id)
            handled = await conn.execute(
                """UPDATE password_help_requests SET status = 'done', handled_by = $1, handled_at = NOW()
                   WHERE user_id = $2 AND status = 'open'""",
                current.get("username"), user_id)
    forget_account(user_id)
    await clear_login_lock(target["username"])
    try:
        await disconnect_user(target["username"])
    except Exception:
        pass

    async def _sync_guac():
        await guac_retry(sync_user, target["username"], password, target["full_name"] or "",
                         disabled=not target["is_active"], op_name="reset_password:sync_user")
        if target["role"] in ADMIN_ROLES:
            await guac_retry(grant_all_connections_to_admin, target["username"],
                             op_name="reset_password:grant_admin")
    asyncio.create_task(_sync_guac())

    await log_activity(current, "USER_PASSWORD_RESET", "WARNING", None,
                       both(lambda: tr(f"{current.get('username')} mereset password akun '{target['username']}'",
                                       f"{current.get('username')} reset the password of the account '{target['username']}'")
                            + (tr(" (permintaan Lupa password)", " (Forgot password request)") if handled != "UPDATE 0" else "")),
                       request)
    return {"username": target["username"], "password": password, "must_change_password": True}


# ── Lupa password (dari halaman login) ────────────────────────


async def _record_help(username: str, message: str, ip: str) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            user = await conn.fetchrow(
                """SELECT id, username, role FROM users WHERE lower(username) = lower($1) AND deleted_at IS NULL
                   ORDER BY id LIMIT 1""", username)
            if not user:
                return
            await conn.execute(
                """INSERT INTO password_help_requests (user_id, message, client_ip) VALUES ($1, $2, $3)
                   ON CONFLICT (user_id) WHERE status = 'open'
                   DO UPDATE SET message = EXCLUDED.message, client_ip = EXCLUDED.client_ip, created_at = NOW()""",
                user["id"], message, ip or None)
        await log_activity({"sub": user["id"], "username": user["username"], "role": user["role"]},
                           "AUTH_PASSWORD_HELP", "INFO", None,
                           both(lambda: tr(f"Permintaan reset password untuk '{user['username']}' dari halaman login",
                                           f"Password reset request for '{user['username']}' from the sign-in page")), ip=ip)
    except Exception:
        import logging
        logging.getLogger("users").exception("password-help: gagal mencatat permintaan")


@router.post("/password-help")
async def request_password_help(body: PasswordHelpRequest, request: Request):
    """Publik. Pengguna yang lupa password meminta bantuan admin. Jawabannya selalu sama, terdaftar atau
    tidak, dan dicatat di latar belakang supaya waktu respons juga tidak membedakan."""
    username = body.username.strip()
    if not username or len(username) > 64:
        raise HTTPException(400, tr("Username wajib diisi", "Username is required"))
    ip = _client_ip(request)
    if not await rate_allow(f"pwhelp:ip:{ip}", 10, 3600):
        raise HTTPException(429, tr("Terlalu banyak permintaan dari jaringan ini. Coba lagi nanti atau hubungi admin langsung.",
                                    "Too many requests from this network. Try again later or contact an administrator directly."))
    if await rate_allow(f"pwhelp:user:{username.lower()}", 3, 3600):
        asyncio.create_task(_record_help(username, body.message.strip()[:500], ip))
    return {"message": tr("Permintaan terkirim. Kalau username itu terdaftar, admin akan memberikan password "
                          "sementara kepada Anda secara langsung.",
                          "Request sent. If that username exists, an administrator will give you a temporary "
                          "password in person.")}


# ── Reset password lewat tautan email (publik) ────────────────
class ResetRequestBody(BaseModel):
    identifier: str


class ResetConfirmBody(BaseModel):
    token:    str
    password: str = ""


@router.get("/password-reset")
async def password_reset_available():
    """Publik: apakah halaman login menawarkan reset lewat email."""
    from services import password_reset
    return {"enabled": await password_reset.available()}


@router.post("/password-reset/request")
async def password_reset_request(body: ResetRequestBody, request: Request):
    """Jawaban selalu sama, dan pekerjaannya dijalankan di latar belakang, supaya tidak bisa dipakai menebak
    akun atau email yang terdaftar."""
    from services import password_reset
    ip = _client_ip(request)
    if not await rate_allow(f"pwreset:ip:{ip}", 20, 3600):
        raise HTTPException(429, tr("Terlalu banyak permintaan dari jaringan ini. Coba lagi nanti.",
                                    "Too many requests from this network. Try again later."))
    if await password_reset.available():
        asyncio.create_task(password_reset.request_reset(body.identifier[:254], ip))
    return {"message": tr("Kalau akun itu terdaftar dan punya email, tautan reset password sudah dikirim. Periksa kotak masuk dan folder spam.",
                          "If that account exists and has an email address, a password reset link has been sent. Check your inbox and spam folder.")}


@router.post("/password-reset/verify")
async def password_reset_verify(body: ResetConfirmBody):
    """Cek tautan sebelum menampilkan form password baru."""
    from services import password_reset
    user = await password_reset.account_for_token(body.token) if await password_reset.available() else None
    if not user:
        raise password_reset.invalid_link()
    return {"username": user["username"]}


@router.post("/password-reset/confirm")
async def password_reset_confirm(body: ResetConfirmBody, request: Request):
    from services import password_reset
    out = await password_reset.confirm(body.token, body.password, _client_ip(request))
    return {"message": tr("Password diganti. Silakan login dengan password baru.", "Password changed. Sign in with your new password."),
            "username": out["username"]}



@router.get("/password-help")
async def list_password_help(current: dict = Depends(require_sysadmin)):
    """Permintaan Lupa password yang belum ditangani. Sysadmin hanya melihat akun mahasiswa."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT r.id, r.user_id, r.message, r.client_ip, r.created_at,
                      u.username, u.full_name, u.role, u.is_active
               FROM password_help_requests r JOIN users u ON u.id = r.user_id
               WHERE r.status = 'open' AND u.deleted_at IS NULL AND ($1 OR u.role = 'student')
               ORDER BY r.created_at DESC LIMIT 200""",
            current["role"] == Role.SUPERADMIN)
    return [dict(r) for r in rows]


@router.post("/password-help/{req_id}/dismiss")
async def dismiss_password_help(req_id: int, request: Request, current: dict = Depends(require_sysadmin)):
    """Abaikan permintaan (mis. bukan dari pemilik akun, atau sudah ditangani dengan cara lain)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id FROM password_help_requests WHERE id = $1 AND status = 'open'", req_id)
        if not row:
            raise HTTPException(404, tr("Permintaan tidak ditemukan atau sudah ditangani",
                                        "Request not found or already handled"))
        target = await _visible_target(conn, current, row["user_id"])
        await conn.execute(
            """UPDATE password_help_requests SET status = 'dismissed', handled_by = $1, handled_at = NOW()
               WHERE id = $2""", current.get("username"), req_id)
    await log_activity(current, "USER_PASSWORD_HELP_DISMISS", "INFO", None,
                       both(lambda: tr(f"{current.get('username')} mengabaikan permintaan reset password '{target['username']}'",
                                       f"{current.get('username')} dismissed the password reset request of '{target['username']}'")),
                       request)
    return {"ok": True}


# ── User CRUD (superadmin only) ───────────────────────────────
@router.get("")
async def list_users(user: dict = Depends(require_sysadmin)):
    """List semua users — admin dan superadmin saja."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT u.id, u.username, u.full_name, u.role, u.email,
                      u.is_active, u.is_verified, u.last_login, u.created_at, u.expires_at,
                      u.must_change_password, u.mfa_secret_enc IS NOT NULL AS mfa_enabled,
                      ARRAY(SELECT gm.group_id FROM group_members gm WHERE gm.user_id = u.id) AS group_ids,
                      ARRAY(SELECT ui.instance FROM user_instances ui WHERE ui.user_id = u.id ORDER BY ui.instance) AS instances
               FROM users u WHERE u.deleted_at IS NULL ORDER BY u.role, u.username"""
        )
    out = [dict(r) for r in rows]
    if user["role"] != Role.SUPERADMIN:        # label Proxmox hanya untuk superadmin yang mengaturnya
        for r in out:
            r["instances"] = []
    return out


@router.post("")
async def create_user(body: CreateUserRequest, request: Request, user: dict = Depends(require_superadmin)):
    """Buat user baru — superadmin saja."""
    valid_roles = {Role.SUPERADMIN, Role.SYSADMIN, Role.STUDENT}
    if body.role not in valid_roles:
        raise HTTPException(status_code=400, detail=tr(f"Role tidak valid: {body.role}",
                                                       f"Invalid role: {body.role}"))
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail=tr("Password minimal 8 karakter",
                                                       "The password must be at least 8 characters"))

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM users WHERE username = $1", body.username
        )
        if existing:
            raise HTTPException(status_code=409, detail=tr("Username sudah dipakai",
                                                           "This username is taken"))
        email = _clean_email(body.email)
        await _ensure_email_free(conn, email)

        row = await conn.fetchrow(
            """INSERT INTO users (username, password_hash, full_name, role, email, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id, username, full_name, role, email, expires_at""",
            body.username, hash_password(body.password),
            body.full_name, body.role, email, _aware(body.expires_at)
        )
    async def _setup_guac_user():
        await guac_retry(sync_user, body.username, body.password, body.full_name or "",
                         op_name="create_user:sync_user")
        if body.role in ADMIN_ROLES:
            await guac_retry(grant_all_connections_to_admin, body.username,
                             op_name="create_user:grant_admin")
    asyncio.create_task(_setup_guac_user())
    await log_activity(user, "USER_CREATE", "CRITICAL" if body.role == Role.SUPERADMIN else "WARNING", None,
                       both(lambda: tr(f"{user.get('username')} membuat akun '{row['username']}' dengan peran {row['role']}",
                                       f"{user.get('username')} created the account '{row['username']}' with the role {row['role']}")),
                       request)
    return dict(row)


class BulkRequest(BaseModel):
    user_ids:   list[int]
    action:     str                       # activate | deactivate | set_expiry | clear_expiry
    expires_at: Optional[datetime] = None


@router.post("/bulk")
async def bulk_update(body: BulkRequest, request: Request, current: dict = Depends(require_superadmin)):
    """Aktifkan, nonaktifkan, atau atur masa berlaku banyak akun sekaligus. Akun sendiri dilewati."""
    if body.action not in ("activate", "deactivate", "set_expiry", "clear_expiry"):
        raise HTTPException(400, tr("Aksi tidak dikenal", "Unknown action"))
    if body.action == "set_expiry" and not body.expires_at:
        raise HTTPException(400, tr("Tanggal masa berlaku wajib diisi", "An expiry date is required"))
    ids = sorted({i for i in body.user_ids if i != int(current["sub"])})[:2000]
    if not ids:
        raise HTTPException(400, tr("Tidak ada akun yang dipilih (akun Anda sendiri tidak ikut diubah)",
                                    "No accounts selected (your own account is never changed)"))
    sets = {
        "activate":     ("is_active = true", []),
        "deactivate":   ("is_active = false", []),
        "set_expiry":   ("expires_at = $2, expiry_enforced_at = NULL", [_aware(body.expires_at)]),
        "clear_expiry": ("expires_at = NULL, expiry_enforced_at = NULL", []),
    }[body.action]
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""UPDATE users SET {sets[0]}, updated_at = NOW()
                WHERE id = ANY($1::int[]) AND deleted_at IS NULL
                RETURNING id, username, is_active, expires_at""", ids, *sets[1])
    now = datetime.now(timezone.utc)
    from routers.guac import disconnect_user
    for r in rows:
        forget_account(r["id"])
        usable = r["is_active"] and (r["expires_at"] is None or r["expires_at"] > now)
        if not usable:
            try:
                await disconnect_user(r["username"])
            except Exception:
                pass
        asyncio.create_task(guac_retry(sync_user_disabled, r["username"], disabled=not usable,
                                       op_name=f"bulk:{body.action}"))
    def _detail():
        label = {"activate": tr("mengaktifkan", "activated"), "deactivate": tr("menonaktifkan", "deactivated"),
                 "clear_expiry": tr("menghapus masa berlaku", "removed the expiry of"),
                 "set_expiry": (tr(f"mengatur masa berlaku sampai {body.expires_at:%Y-%m-%d}", f"set the expiry to {body.expires_at:%Y-%m-%d} for")
                                if body.expires_at else "")}[body.action]
        return tr(f"{current.get('username')} {label} {len(rows)} akun", f"{current.get('username')} {label} {len(rows)} accounts")
    await log_activity(current, "USER_BULK_UPDATE", "WARNING", None, both(_detail), request)
    return {"updated": len(rows)}


class ImportRow(BaseModel):
    username:   str = ""
    full_name:  str = ""
    email:      Optional[str] = None
    password:   Optional[str] = None
    role:       Optional[str] = None
    expires_at: Optional[str] = None      # YYYY-MM-DD
    group:      Optional[str] = None


class ImportRequest(BaseModel):
    rows:    list[ImportRow]
    dry_run: bool = True


_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


def _gen_password() -> str:
    import secrets, string
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


@router.post("/import")
async def import_users(body: ImportRequest, request: Request, current: dict = Depends(require_superadmin)):
    """Impor akun dari CSV (diurai di browser). Semua baris dicek dulu; kalau ada satu saja yang
    salah, tidak ada akun yang dibuat. Password kosong dibuatkan acak dan dikembalikan sekali."""
    if not body.rows:
        raise HTTPException(400, tr("File kosong", "The file is empty"))
    if len(body.rows) > 500:
        raise HTTPException(400, tr("Maksimal 500 baris per impor", "At most 500 rows per import"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        groups = {r["name"].lower(): r["id"] for r in await conn.fetch("SELECT id, name FROM groups")}
        taken_users = {r["username"] for r in await conn.fetch("SELECT username FROM users")}
        taken_emails = {r["e"] for r in await conn.fetch(
            "SELECT lower(email) AS e FROM users WHERE email <> '' AND email IS NOT NULL AND deleted_at IS NULL")}
    today = datetime.now(timezone.utc).date()
    from services.system_settings import get_settings
    default_days = (await get_settings())["default_account_days"]
    results, seen_u, seen_e = [], set(), set()
    for n, row in enumerate(body.rows, start=2):        # baris 1 = header CSV
        errs = []
        username = row.username.strip().lower()
        if not _USERNAME.match(username):
            errs.append(tr("username 3-32 karakter: huruf kecil, angka, titik, minus, garis bawah",
                           "username must be 3-32 characters: lowercase letters, digits, dot, hyphen, underscore"))
        elif username in taken_users or username in seen_u:
            errs.append(tr("username sudah dipakai", "username already taken"))
        seen_u.add(username)
        full_name = " ".join(row.full_name.split())
        if not full_name:
            errs.append(tr("nama lengkap kosong", "full name is empty"))
        email = None
        try:
            email = _clean_email(row.email)
        except HTTPException:
            errs.append(tr("format email tidak valid", "invalid email format"))
        if email and (email in taken_emails or email in seen_e):
            errs.append(tr("email sudah dipakai", "email already in use"))
        if email:
            seen_e.add(email)
        role = (row.role or "student").strip().lower()
        if role not in (Role.STUDENT, Role.SYSADMIN):
            errs.append(tr("role harus student atau sysadmin", "role must be student or sysadmin"))
        if row.password and len(row.password) < 8:
            errs.append(tr("password minimal 8 karakter", "password must be at least 8 characters"))
        expires = None
        if row.expires_at and row.expires_at.strip():
            try:
                d = datetime.strptime(row.expires_at.strip(), "%Y-%m-%d").date()
                if d <= today:
                    errs.append(tr("masa berlaku harus setelah hari ini",
                                   "the expiry date must be after today"))
                # Berlaku sampai akhir hari itu (WIB, UTC+7).
                expires = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone(timedelta(hours=7)))
            except ValueError:
                errs.append(tr("masa berlaku harus berformat YYYY-MM-DD",
                               "the expiry date must use the YYYY-MM-DD format"))
        if expires is None and default_days:              # kolom kosong: masa berlaku bawaan
            expires = datetime.now(timezone.utc) + timedelta(days=default_days)
        group_id = None
        if row.group and row.group.strip():
            group_id = groups.get(row.group.strip().lower())
            if group_id is None:
                errs.append(tr(f"grup '{row.group.strip()}' tidak ada",
                               f"group '{row.group.strip()}' does not exist"))
        results.append({"line": n, "username": username, "full_name": full_name, "email": email, "role": role,
                        "expires_at": expires, "group_id": group_id, "group": (row.group or "").strip(),
                        "password": row.password or None, "errors": errs})

    errors = [r for r in results if r["errors"]]
    summary = [{"line": r["line"], "username": r["username"], "full_name": r["full_name"], "role": r["role"],
                "email": r["email"], "group": r["group"], "expires_at": r["expires_at"], "errors": r["errors"]}
               for r in results]
    if body.dry_run or errors:
        return {"created": 0, "errors": len(errors), "rows": summary}

    created = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in results:
                password = r["password"] or _gen_password()
                uid = await conn.fetchval(
                    """INSERT INTO users (username, password_hash, full_name, role, email, is_active, is_verified, expires_at)
                       VALUES ($1, $2, $3, $4, $5, true, true, $6) RETURNING id""",
                    r["username"], hash_password(password), r["full_name"], r["role"], r["email"], r["expires_at"])
                if r["group_id"]:
                    await conn.execute("INSERT INTO group_members (group_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                                       r["group_id"], uid)
                created.append({"id": uid, "username": r["username"], "full_name": r["full_name"], "role": r["role"],
                                "password": password if not r["password"] else None})

    async def _sync_guac():
        for c, r in zip(created, results):
            await guac_retry(sync_user, c["username"], r["password"] or c["password"], c["full_name"],
                             op_name="import:sync_user")
            if c["role"] in ADMIN_ROLES:
                await guac_retry(grant_all_connections_to_admin, c["username"], op_name="import:grant_admin")
    asyncio.create_task(_sync_guac())
    groups_used = sorted({r["group"] for r in results if r["group"]})
    await log_activity(current, "USER_IMPORT", "WARNING", None,
                       both(lambda: tr(f"{current.get('username')} mengimpor {len(created)} akun", f"{current.get('username')} imported {len(created)} accounts")
                            + (tr(f" ke grup {', '.join(groups_used)}", f" into the group {', '.join(groups_used)}") if groups_used else "")),
                       request)
    return {"created": len(created), "errors": 0, "rows": summary,
            "credentials": [{"username": c["username"], "password": c["password"]} for c in created if c["password"]]}


def _fmt_expiry(value) -> str:
    return (value.astimezone(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M WIB") if value
            else tr("tanpa batas", "no limit"))


def _describe_changes(old, body) -> list:
    """Ringkasan perubahan untuk Activity Log, tiap butir dua bahasa. Password tidak disebut di sini
    (dicatat terpisah)."""
    out = []
    if body.full_name is not None and body.full_name != (old["full_name"] or ""):
        out.append(both(lambda: tr(f"nama '{old['full_name'] or ''}' → '{body.full_name}'", f"name '{old['full_name'] or ''}' → '{body.full_name}'")))
    if body.role is not None and body.role != old["role"]:
        out.append(both(lambda: tr(f"peran {old['role']} → {body.role}", f"role {old['role']} → {body.role}")))
    if body.is_active is not None and body.is_active != old["is_active"]:
        out.append(both(lambda: tr("diaktifkan", "activated") if body.is_active else tr("dinonaktifkan", "deactivated")))
    if body.is_verified is not None and body.is_verified != old["is_verified"]:
        out.append(both(lambda: tr("diverifikasi", "verified") if body.is_verified else tr("verifikasi dicabut", "verification revoked")))
    if body.email is not None and (_clean_email(body.email) or None) != (old["email"] or None):
        out.append(both(lambda: tr(f"email '{old['email'] or ''}' → '{_clean_email(body.email) or ''}'", f"email '{old['email'] or ''}' → '{_clean_email(body.email) or ''}'")))
    if "expires_at" in body.model_fields_set and _aware(body.expires_at) != old["expires_at"]:
        out.append(both(lambda: tr(f"masa berlaku {_fmt_expiry(old['expires_at'])} → {_fmt_expiry(_aware(body.expires_at))}",
                                   f"expiry {_fmt_expiry(old['expires_at'])} → {_fmt_expiry(_aware(body.expires_at))}")))
    return out


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    body: UpdateUserRequest,
    request: Request,
    current: dict = Depends(require_superadmin)
):
    if body.password is not None and len(body.password) < 8:
        raise HTTPException(status_code=400, detail=tr("Password minimal 8 karakter",
                                                       "The password must be at least 8 characters"))

    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if not user:
            raise HTTPException(status_code=404, detail=tr("User tidak ditemukan", "User not found"))

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
            email = _clean_email(body.email)
            await _ensure_email_free(conn, email, exclude_id=user_id)
            updates.append(f"email = ${idx}"); values.append(email); idx += 1
        if body.password is not None:
            updates.append(f"password_hash = ${idx}"); values.append(hash_password(body.password)); idx += 1
        if body.password is not None or (body.role is not None and body.role != user["role"]):
            # Token lama membawa password/peran lama: naikkan versinya supaya semua sesi login ulang.
            updates.append("password_version = password_version + 1")
        if "expires_at" in body.model_fields_set:
            updates.append(f"expires_at = ${idx}, expiry_enforced_at = NULL"); values.append(_aware(body.expires_at)); idx += 1

        if not updates:
            return dict(user)

        values.append(user_id)
        row = await conn.fetchrow(
            f"UPDATE users SET {', '.join(updates)}, updated_at = NOW() WHERE id = ${idx} "
            f"RETURNING id, username, full_name, role, is_active, expires_at",
            *values
        )
    result = dict(row)
    forget_account(user_id)
    changes = _describe_changes(user, body)
    if changes:
        role_change = body.role is not None and body.role != user["role"]
        severity = "CRITICAL" if role_change and Role.SUPERADMIN in (body.role, user["role"]) else "WARNING"
        await log_activity(current, "USER_UPDATE", severity, None,
                           both(lambda: tr(f"{current.get('username')} mengubah akun '{result['username']}': {'; '.join(c.t() for c in changes)}",
                                           f"{current.get('username')} changed the account '{result['username']}': {'; '.join(c.t() for c in changes)}")),
                           request)
    if body.password is not None:
        from routers.guac import disconnect_user
        try:
            await disconnect_user(result["username"])
        except Exception:
            pass
        await log_activity(current, "USER_PASSWORD_RESET", "WARNING", None,
                           both(lambda: tr(f"{current.get('username')} mengganti password akun '{result['username']}' lewat Edit",
                                           f"{current.get('username')} changed the password of the account '{result['username']}' through Edit")),
                           request)
    if "expires_at" in body.model_fields_set and body.password is None and body.is_active is None:
        # Masa berlaku diperpanjang/dihapus: aktifkan lagi akun Guacamole-nya kalau akunnya aktif.
        usable = result["is_active"] and (result["expires_at"] is None or result["expires_at"] > datetime.now(timezone.utc))
        asyncio.create_task(guac_retry(sync_user_disabled, result["username"], disabled=not usable,
                                       op_name="update_user:expiry"))
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
async def delete_user_endpoint(user_id: int, request: Request, current: dict = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT username FROM users WHERE id = $1 AND deleted_at IS NULL", user_id
        )
        if not row:
            raise HTTPException(status_code=404, detail=tr("User tidak ditemukan", "User not found"))
        await conn.execute(
            "UPDATE users SET deleted_at = NOW() WHERE id = $1", user_id
        )
        # Soft-delete semua assignment aktif milik user ini
        await conn.execute(
            "UPDATE vm_assignments SET deleted_at = NOW() WHERE user_id = $1 AND deleted_at IS NULL",
            user_id
        )
    forget_account(user_id)
    asyncio.create_task(guac_retry(guac_delete_user, row["username"], op_name="delete_user:guac"))
    await log_activity(current, "USER_DELETE", "WARNING", None,
                       both(lambda: tr(f"{current.get('username')} menghapus akun '{row['username']}'",
                                       f"{current.get('username')} deleted the account '{row['username']}'")), request)
    return {"status": "deleted", "user_id": user_id}


# ── Proxmox yang dikelola sysadmin (hanya superadmin yang mengatur) ──────────
class InstancesBody(BaseModel):
    instances: list[str]


async def _sysadmin_row(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, username, role FROM users WHERE id = $1 AND deleted_at IS NULL", user_id)
    if not row:
        raise HTTPException(404, tr("Pengguna tidak ditemukan", "User not found"))
    if row["role"] != Role.SYSADMIN:
        raise HTTPException(400, tr("Hanya akun sysadmin yang dibatasi per Proxmox. Superadmin mengelola semuanya",
                                    "Only sysadmin accounts are limited per Proxmox. Superadmins manage everything"))
    return row


@router.get("/{user_id}/instances")
async def get_user_instances(user_id: int, current: dict = Depends(require_superadmin)):
    await _sysadmin_row(user_id)
    return {"instances": sorted(await scope.labels_of(user_id))}


@router.put("/{user_id}/instances")
async def put_user_instances(user_id: int, body: InstancesBody, request: Request,
                             current: dict = Depends(require_superadmin)):
    row = await _sysadmin_row(user_id)
    added, removed = await scope.set_instances(user_id, body.instances, current.get("username") or "")
    # Akses Guacamole mengikuti: koneksi VM di Proxmox yang dicabut ikut dicabut, yang baru diberikan.
    asyncio.create_task(guac_retry(grant_all_connections_to_admin, row["username"], op_name="instances:guac"))
    names = lambda items: ", ".join(items) if items else "-"
    await log_activity(current, "USER_INSTANCES", "WARNING", {"id": str(user_id), "name": row["username"]},
                       both(lambda: tr(f"{current.get('username')} mengatur Proxmox untuk sysadmin '{row['username']}': "
                                       f"ditambah {names(added)}; dicabut {names(removed)}",
                                       f"{current.get('username')} set the Proxmox access of the sysadmin '{row['username']}': "
                                       f"added {names(added)}; removed {names(removed)}")), request)
    return {"instances": sorted(await scope.labels_of(user_id)), "added": added, "removed": removed}


# ── VM Assignment (untuk student role) ───────────────────────
@router.post("/vm-assignments")
async def assign_vm(body: AssignVmRequest, request: Request, current: dict = Depends(require_sysadmin)):
    """Assign VM ke student, opsional dengan OS account tertentu."""
    await scope.require_host(current, body.host_name)
    if body.access not in ("full", "web"):
        raise HTTPException(400, tr("access harus 'full' atau 'web'", "access must be 'full' or 'web'"))
    if body.access == "web" and body.os_account_id:
        raise HTTPException(400, tr("Akses 'Hanya Open Web' tidak bisa memakai OS account",
                                    "'Open Web only' access cannot use an OS account"))
    from services import quotas
    await quotas.check_assign(body.user_id, body.vm_id, body.host_name)     # sebelum dicatat sebagai berhasil
    detail = both(lambda: tr(f"Assign VM {body.vm_id} ke user #{body.user_id}", f"Assigned VM {body.vm_id} to user #{body.user_id}")
                  + (f" (OS account #{body.os_account_id})" if body.os_account_id else "")
                  + (tr(" (hanya Open Web)", " (Open Web only)") if body.access == "web" else ""))
    await log_activity(current, "RBAC_ASSIGN_VM", "WARNING",
                       {"id": body.vm_id, "name": body.host_name}, detail, request)
    from services.assignments import assign_vm as do_assign
    await do_assign(body.user_id, body.vm_id, body.host_name, body.vm_name, body.os_account_id, body.access)
    return {"status": "assigned"}


@router.get("/{user_id}/vm-assignments")
async def get_user_assignments(user_id: int, current: dict = Depends(require_sysadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT va.id, va.user_id, va.vm_id, va.host_name,
                      COALESCE(va.vm_name, v.vm_name) AS vm_name,
                      va.assigned_at, va.os_account_id, va.access,
                      voa.os_username, voa.label AS os_account_label
               FROM vm_assignments va
               LEFT JOIN vms v ON v.vm_id = va.vm_id AND v.host_name = va.host_name
               LEFT JOIN vm_os_accounts voa ON voa.id = va.os_account_id
               WHERE va.user_id = $1 AND va.deleted_at IS NULL""",
            user_id
        )
    allowed = await scope.allowed_labels(current)
    return [dict(r) for r in rows if allowed is None or scope.host_label(r["host_name"]) in allowed]


@router.delete("/{user_id}/vm-assignments/{vm_id}")
async def remove_assignment(user_id: int, vm_id: str, request: Request, current: dict = Depends(require_sysadmin)):
    if scope.is_scoped(current):
        pool = await get_pool()
        async with pool.acquire() as conn:
            hosts = await conn.fetch(
                "SELECT host_name FROM vm_assignments WHERE user_id = $1 AND vm_id = $2 AND deleted_at IS NULL", user_id, vm_id)
        for h in hosts:
            await scope.require_host(current, h["host_name"])
    await log_activity(current, "RBAC_UNASSIGN_VM", "WARNING",
                       {"id": vm_id, "name": vm_id},
                       both(lambda: tr(f"Unassign VM {vm_id} dari user #{user_id}", f"Unassigned VM {vm_id} from user #{user_id}")), request)
    await unassign_vm(user_id, vm_id)
    return {"status": "removed"}


async def unassign_vm(user_id: int, vm_id: str) -> bool:
    """Cabut penugasan langsung VM dari user beserta akses Guacamole-nya. False kalau tidak ada.
    Dipakai juga oleh Admin → Sesi Remote (putuskan sesi dan cabut akses VM)."""
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
               WHERE va.user_id = $1 AND va.deleted_at IS NULL AND va.access = 'full'""",
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

    return bool(removed_row)