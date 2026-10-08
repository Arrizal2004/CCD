"""
CRUD for `proxmox_instances` (multi-Proxmox support) + a cached ProxmoxClient factory.

token_secret is Fernet-encrypted at rest (same scheme as vm_credentials.password_enc in
ssh_creds.py) — decrypted only in-memory when building a client.
"""
import logging

from database import get_pool
from services.ssh_client import encrypt_secret, decrypt_secret
from services.proxmox_client import ProxmoxClient
from i18n import tr

log = logging.getLogger("proxmox_instances")

_client_cache: dict[str, ProxmoxClient] = {}


def _invalidate(label: str):
    _client_cache.pop(label, None)


async def list_instances() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, label, host, token_id, verify_ssl, created_at, updated_at "
            "FROM proxmox_instances ORDER BY label"
        )
    return [dict(r) for r in rows]


async def get_instance(label: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, label, host, token_id, verify_ssl, created_at, updated_at "
            "FROM proxmox_instances WHERE label = $1", label
        )
    return dict(row) if row else None


async def create_instance(label: str, host: str, token_id: str, token_secret: str, verify_ssl: bool) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO proxmox_instances (label, host, token_id, token_secret_enc, verify_ssl)
               VALUES ($1, $2, $3, $4, $5)
               RETURNING id, label, host, token_id, verify_ssl, created_at, updated_at""",
            label, host, token_id, encrypt_secret(token_secret), verify_ssl,
        )
    return dict(row)


async def update_instance(label: str, host: str | None, token_id: str | None,
                           token_secret: str | None, verify_ssl: bool | None) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        updates, values, idx = [], [], 1
        if host is not None:
            updates.append(f"host = ${idx}"); values.append(host); idx += 1
        if token_id is not None:
            updates.append(f"token_id = ${idx}"); values.append(token_id); idx += 1
        if token_secret is not None:
            updates.append(f"token_secret_enc = ${idx}"); values.append(encrypt_secret(token_secret)); idx += 1
        if verify_ssl is not None:
            updates.append(f"verify_ssl = ${idx}"); values.append(verify_ssl); idx += 1
        if not updates:
            return await get_instance(label)
        values.append(label)
        row = await conn.fetchrow(
            f"UPDATE proxmox_instances SET {', '.join(updates)}, updated_at = NOW() "
            f"WHERE label = ${idx} "
            f"RETURNING id, label, host, token_id, verify_ssl, created_at, updated_at",
            *values,
        )
    _invalidate(label)
    return dict(row) if row else None


async def delete_instance(label: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM proxmox_instances WHERE label = $1", label)
    _invalidate(label)
    return result != "DELETE 0"


async def get_client(label: str) -> ProxmoxClient:
    """Return a cached ProxmoxClient for this instance label, building+decrypting on first use."""
    cached = _client_cache.get(label)
    if cached:
        return cached

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT host, token_id, token_secret_enc, verify_ssl FROM proxmox_instances WHERE label = $1",
            label,
        )
    if not row:
        raise ValueError(tr(f"Proxmox instance '{label}' tidak ditemukan",
                            f"Proxmox instance '{label}' not found"))

    client = ProxmoxClient(
        host=row["host"],
        token_id=row["token_id"],
        token_secret=decrypt_secret(row["token_secret_enc"]),
        verify_ssl=row["verify_ssl"],
    )
    _client_cache[label] = client
    return client
