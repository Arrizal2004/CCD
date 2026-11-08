"""
Data sementara di Redis untuk alur login dan reset password: tantangan MFA dan token reset password.
Isinya dienkripsi (Fernet dari JWT_SECRET) karena tantangan MFA memuat password login sementara.

Pola yang sama dengan token_blocklist.py: set_redis() dipanggil sekali dari lifespan di main.py.
"""
import json
import secrets

import redis.asyncio as aioredis
from fastapi import HTTPException

from i18n import tr
from services.ssh_client import decrypt_secret, encrypt_secret

_redis: aioredis.Redis | None = None


def set_redis(r: aioredis.Redis) -> None:
    global _redis
    _redis = r


def _client() -> aioredis.Redis:
    if _redis is None:
        raise HTTPException(503, tr("Penyimpanan sesi (Redis) belum siap", "Session storage (Redis) is not ready"))
    return _redis


def new_id() -> str:
    return secrets.token_urlsafe(32)


async def put(kind: str, key: str, data: dict, ttl: int) -> None:
    await _client().set(f"ccd:{kind}:{key}", encrypt_secret(json.dumps(data)), ex=ttl)


async def get(kind: str, key: str) -> dict | None:
    raw = await _client().get(f"ccd:{kind}:{key}") if key else None
    return json.loads(decrypt_secret(raw)) if raw else None


async def pop(kind: str, key: str) -> dict | None:
    """Ambil sekaligus hapus, supaya nilai sekali pakai tidak bisa dipakai ulang."""
    raw = await _client().getdel(f"ccd:{kind}:{key}") if key else None
    return json.loads(decrypt_secret(raw)) if raw else None


async def delete(kind: str, key: str) -> None:
    await _client().delete(f"ccd:{kind}:{key}")


async def ttl(kind: str, key: str) -> int:
    return max(int(await _client().ttl(f"ccd:{kind}:{key}")), 0)
