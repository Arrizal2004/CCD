"""
Async client for the Tailscale API, authenticated via API access token.

Role in the "Clientless Campus Cloud" architecture: Tailscale is the network-level access
layer — devices/users only reach campus infra (Guacamole gateway, Proxmox host) if the
tailnet ACL grants them a path. This client lets the dashboard read device/ACL state so
that layer is visible and auditable alongside the app-level RBAC (auth.py roles + groups).

Credentials are per deployment: set from the dashboard (`tailscale_config` table, api_key
Fernet-encrypted at rest), falling back to TAILSCALE_TAILNET / TAILSCALE_API_KEY env vars
for headless deploys. The dashboard config wins when both exist.
"""
import os
import logging

import httpx

from database import get_pool
from services.ssh_client import encrypt_secret, decrypt_secret

log = logging.getLogger("tailscale_client")

BASE_URL = "https://api.tailscale.com/api/v2"

_creds_cache: tuple[str, str] | None = None  # (tailnet, api_key)


class TailscaleError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Tailscale API error {status_code}: {detail}")


class TailscaleNotConfigured(Exception):
    pass


async def _request_resp(method: str, path: str, api_key: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.request(method, f"{BASE_URL}{path}", auth=(api_key, ""), **kwargs)
    if resp.status_code >= 400:
        raise TailscaleError(resp.status_code, resp.text)
    return resp


async def _request(method: str, path: str, api_key: str, **kwargs):
    resp = await _request_resp(method, path, api_key, **kwargs)
    if resp.headers.get("content-type", "").startswith("application/json"):
        return resp.json()
    return resp.text


async def _load_db_config():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT tailnet, api_key_enc, updated_at FROM tailscale_config WHERE id = 1"
        )


async def _creds() -> tuple[str, str]:
    global _creds_cache
    if _creds_cache:
        return _creds_cache
    row = await _load_db_config()
    if row:
        _creds_cache = (row["tailnet"], decrypt_secret(row["api_key_enc"]))
    else:
        key = os.getenv("TAILSCALE_API_KEY", "")
        if not key:
            raise TailscaleNotConfigured("Tailscale belum dikonfigurasi")
        _creds_cache = (os.getenv("TAILSCALE_TAILNET", "-"), key)
    return _creds_cache


async def _list_devices(tailnet: str, api_key: str) -> list[dict]:
    data = await _request("GET", f"/tailnet/{tailnet}/devices", api_key)
    return data.get("devices", [])


async def list_devices() -> list[dict]:
    tailnet, key = await _creds()
    return await _list_devices(tailnet, key)


async def get_acl() -> str:
    """Raw ACL policy (HuJSON text)."""
    tailnet, key = await _creds()
    return await _request("GET", f"/tailnet/{tailnet}/acl", key)


async def get_acl_json() -> tuple[dict, str]:
    """Policy as plain JSON (comments stripped by Tailscale) plus its ETag, for If-Match on write."""
    tailnet, key = await _creds()
    resp = await _request_resp("GET", f"/tailnet/{tailnet}/acl", key, headers={"Accept": "application/json"})
    return resp.json(), resp.headers.get("etag", "")


async def validate_acl(policy: dict) -> str | None:
    """None when Tailscale accepts the policy, otherwise its error message. Changes nothing."""
    tailnet, key = await _creds()
    resp = await _request_resp("POST", f"/tailnet/{tailnet}/acl/validate", key, json=policy)
    data = resp.json() if resp.content else {}
    if isinstance(data, dict) and data.get("message"):
        extra = "; ".join(str(d) for d in data.get("data") or [])
        return f"{data['message']}{' — ' + extra if extra else ''}"
    return None


async def set_acl(policy: dict, etag: str) -> None:
    tailnet, key = await _creds()
    await _request_resp("POST", f"/tailnet/{tailnet}/acl", key, json=policy, headers={"If-Match": etag})


async def get_device(device_id: str) -> dict:
    _, key = await _creds()
    return await _request("GET", f"/device/{device_id}", key)


async def set_device_tags(device_id: str, tags: list[str]) -> None:
    _, key = await _creds()
    await _request("POST", f"/device/{device_id}/tags", key, json={"tags": tags})


# ── Per-deployment configuration ──────────────────────────────────────────────

def _key_hint(key: str) -> str:
    return f"…{key[-4:]}" if len(key) > 8 else "…"


async def get_config() -> dict:
    """Current config for the admin UI — never returns the API key itself."""
    row = await _load_db_config()
    if row:
        return {"configured": True, "source": "dashboard", "tailnet": row["tailnet"],
                "api_key_hint": _key_hint(decrypt_secret(row["api_key_enc"])),
                "updated_at": row["updated_at"]}
    key = os.getenv("TAILSCALE_API_KEY", "")
    if key:
        return {"configured": True, "source": "env", "tailnet": os.getenv("TAILSCALE_TAILNET", "-"),
                "api_key_hint": _key_hint(key), "updated_at": None}
    return {"configured": False, "source": None, "tailnet": None, "api_key_hint": None, "updated_at": None}


async def save_config(tailnet: str, api_key: str | None) -> dict:
    """Validate against the live API before storing, so a typo'd key never replaces a working one.
    api_key=None keeps the key currently in use (dashboard or env) and only changes the tailnet."""
    global _creds_cache
    if not api_key:
        _, api_key = await _creds()
    devices = await _list_devices(tailnet, api_key)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO tailscale_config (id, tailnet, api_key_enc, updated_at)
            VALUES (1, $1, $2, NOW())
            ON CONFLICT (id) DO UPDATE SET
                tailnet     = EXCLUDED.tailnet,
                api_key_enc = EXCLUDED.api_key_enc,
                updated_at  = NOW()
        """, tailnet, encrypt_secret(api_key))
    _creds_cache = None
    return {**await get_config(), "devices_total": len(devices)}


async def delete_config() -> bool:
    global _creds_cache
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM tailscale_config WHERE id = 1")
    _creds_cache = None
    return result != "DELETE 0"
