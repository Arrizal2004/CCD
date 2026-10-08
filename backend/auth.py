"""
Auth utilities — JWT + password hashing + role checks.
"""
import time
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import uuid4

from fastapi import Depends, HTTPException, Request, status
from fastapi.requests import HTTPConnection
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
import bcrypt
from i18n import tr

# ── Config ────────────────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET environment variable is not set")
ALGORITHM   = "HS256"
EXPIRE_HOURS = 8

# ── Roles ─────────────────────────────────────────────────────
class Role:
    SUPERADMIN = "superadmin"
    SYSADMIN   = "sysadmin"
    STUDENT    = "student"

    HIERARCHY = [SUPERADMIN, SYSADMIN, STUDENT]

    @staticmethod
    def has_permission(user_role: str, required_role: str) -> bool:
        """Return True jika user_role >= required_role dalam hierarki."""
        try:
            return Role.HIERARCHY.index(user_role) <= Role.HIERARCHY.index(required_role)
        except ValueError:
            return False

# ── Password ──────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))

# ── JWT ───────────────────────────────────────────────────────
def create_token(user_id: int, username: str, role: str, password_version: int = 0) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=EXPIRE_HOURS)
    payload = {
        "sub":      str(user_id),
        "username": username,
        "role":     role,
        "exp":      expire,
        "iat":      datetime.now(timezone.utc),
        "jti":      str(uuid4()),
        "pwv":      password_version,   # versi password; token versi lama ditolak setelah password diganti
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr(f"Token tidak valid atau kedaluwarsa: {e}", f"Invalid or expired token: {e}"),
            headers={"WWW-Authenticate": "Bearer"},
        )

# ── Dependency ────────────────────────────────────────────────
bearer_scheme = HTTPBearer(auto_error=False)

# Selama wajib ganti password (setelah direset admin), hanya endpoint ini yang boleh dipakai.
_ALLOWED_WHILE_MUST_CHANGE = {
    "/api/v1/users/me", "/api/v1/users/me/change-password", "/api/v1/users/logout", "/api/v1/system/config",
}


async def verify_token(raw: str, allow_must_change: bool = False) -> dict:
    """Decode JWT lalu pastikan belum direvoke, akunnya masih bisa dipakai, password-nya belum diganti
    sejak token dibuat, dan (kecuali allow_must_change) pengguna tidak sedang wajib mengganti password.
    Dipakai get_current_user dan endpoint WebSocket/unduhan yang membawa token."""
    payload = decode_token(raw)
    jti = payload.get("jti")
    if jti:
        import token_blocklist
        if await token_blocklist.is_revoked(jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=tr("Token telah direvoke. Silakan login ulang.",
                          "This session has been revoked. Please sign in again."),
                headers={"WWW-Authenticate": "Bearer"},
            )
    state = await account_state(payload.get("sub"))
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr("Akun tidak aktif atau masa berlakunya sudah habis. Hubungi admin.",
                      "This account is inactive or has expired. Contact an administrator."),
            headers={"WWW-Authenticate": "Bearer"},
        )
    if int(payload.get("pwv") or 0) != state["password_version"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr("Password akun ini sudah diganti. Silakan login lagi.",
                      "This account's password has changed. Please sign in again."),
            headers={"WWW-Authenticate": "Bearer"},
        )
    if state["must_change_password"] and not allow_must_change:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=tr("Anda wajib mengganti password terlebih dahulu.",
                      "You must change your password first."),
            headers={"X-Password-Change-Required": "1"},
        )
    payload["must_change_password"] = state["must_change_password"]
    return payload


async def get_current_user(
    connection: HTTPConnection,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> dict:
    """FastAPI dependency — extract user dari JWT token."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr("Belum login", "Not authenticated"),
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await verify_token(credentials.credentials,
                              allow_must_change=connection.url.path in _ALLOWED_WHILE_MUST_CHANGE)


# Token JWT berlaku 8 jam. Supaya menonaktifkan akun, masa berlaku yang habis, atau password yang
# diganti langsung berlaku, status akun dicek ke database, disimpan sementara 30 detik per user.
_ACCOUNT_TTL = 30.0
_account_cache: dict[int, tuple[float, dict | None]] = {}


async def account_state(sub) -> dict | None:
    """{password_version, must_change_password}, atau None kalau akun tidak bisa dipakai."""
    try:
        uid = int(sub)
    except (TypeError, ValueError):
        return None
    now = time.monotonic()
    hit = _account_cache.get(uid)
    if hit and now - hit[0] < _ACCOUNT_TTL:
        return hit[1]
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT password_version, must_change_password FROM users
               WHERE id = $1 AND is_active AND deleted_at IS NULL
               AND (expires_at IS NULL OR expires_at > NOW())""", uid)
    state = dict(row) if row else None
    if len(_account_cache) > 5000:
        _account_cache.clear()
    _account_cache[uid] = (now, state)
    return state


def forget_account(*uids: int) -> None:
    """Panggil setelah status akun berubah supaya pemeriksaan berikutnya membaca database."""
    for uid in uids:
        _account_cache.pop(int(uid), None)

def require_role(minimum_role: str):
    """
    Dependency factory — pastikan user punya role yang cukup.
    Contoh: Depends(require_role(Role.SYSADMIN))
    """
    async def checker(user: dict = Depends(get_current_user)) -> dict:
        if not Role.has_permission(user["role"], minimum_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(tr("Forbidden: fitur ini khusus superadmin.",
                           "Forbidden: this feature is for superadmins only.")
                        if minimum_role == Role.SUPERADMIN
                        else tr("Forbidden: fitur ini khusus admin.",
                                "Forbidden: this feature is for admins only.")),
            )
        return user
    return checker

# Shorthand dependencies
require_superadmin = require_role(Role.SUPERADMIN)
require_sysadmin   = require_role(Role.SYSADMIN)
require_student    = require_role(Role.STUDENT)   # semua role