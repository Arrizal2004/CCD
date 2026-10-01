"""
Auth utilities — JWT + password hashing + role checks.
"""
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import uuid4

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
import bcrypt

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
def create_token(user_id: int, username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=EXPIRE_HOURS)
    payload = {
        "sub":      str(user_id),
        "username": username,
        "role":     role,
        "exp":      expire,
        "iat":      datetime.now(timezone.utc),
        "jti":      str(uuid4()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

# ── Dependency ────────────────────────────────────────────────
bearer_scheme = HTTPBearer(auto_error=False)

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> dict:
    """FastAPI dependency — extract user dari JWT token."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    jti = payload.get("jti")
    if jti:
        import token_blocklist
        if await token_blocklist.is_revoked(jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token telah direvoke. Silakan login ulang.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return payload

def require_role(minimum_role: str):
    """
    Dependency factory — pastikan user punya role yang cukup.
    Contoh: Depends(require_role(Role.SYSADMIN))
    """
    async def checker(user: dict = Depends(get_current_user)) -> dict:
        if not Role.has_permission(user["role"], minimum_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user['role']}' tidak cukup. Diperlukan: {minimum_role}+"
            )
        return user
    return checker

# Shorthand dependencies
require_superadmin = require_role(Role.SUPERADMIN)
require_sysadmin   = require_role(Role.SYSADMIN)
require_student    = require_role(Role.STUDENT)   # semua role