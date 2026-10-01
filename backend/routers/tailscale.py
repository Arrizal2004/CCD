"""
Tailscale integration — per-deployment API key config + read-only view (device list + ACL policy)
untuk memperlihatkan lapisan network-level RBAC (tag/grants) yang melengkapi RBAC/ReBAC aplikasi.

  GET    /api/v1/tailscale/config  → status konfigurasi (tanpa API key; hanya 4 karakter terakhir)
  PUT    /api/v1/tailscale/config  → simpan tailnet + API key (divalidasi ke Tailscale API dulu)
  DELETE /api/v1/tailscale/config  → hapus konfigurasi dashboard (kembali ke env var bila ada)
  GET    /api/v1/tailscale/devices → daftar device di tailnet (nama, IP, tag, online/offline)
  GET    /api/v1/tailscale/acl     → ACL policy mentah (HuJSON) yang sedang berlaku

Network policy terkelola (lihat services/tailscale_policy.py):
  GET    /api/v1/tailscale/policy                     → status (allow-all, terpasang, sinkron dgn RBAC)
  POST   /api/v1/tailscale/policy/preview             → susun + validasi policy, TANPA menerapkan
  POST   /api/v1/tailscale/policy/apply               → terapkan (If-Match ETag dari preview)
  PUT    /api/v1/tailscale/devices/{id}/gateway       → pasang/lepas tag:ccd-gateway
  PUT    /api/v1/tailscale/admin-logins/{user_id}     → petakan admin dashboard → login Tailscale
"""
import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth import get_current_user, Role
from services import tailscale_client as ts
from services import tailscale_policy as policy
from services.tailscale_client import TailscaleError, TailscaleNotConfigured
from services.audit import log_activity

router = APIRouter()
log = logging.getLogger("tailscale")

_ADMIN_ROLES = (Role.SUPERADMIN, Role.SYSADMIN)

# Interpolated into the Tailscale API URL path — keep it to tailnet-name characters only.
_TAILNET_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,253}$")

_NOT_CONFIGURED = "Tailscale belum dikonfigurasi — atur API key di tab Integrations"


def _require_admin(user: dict):
    if user.get("role") not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Aksi ini hanya untuk admin/sysadmin/superadmin")


class TailscaleConfigRequest(BaseModel):
    tailnet: str = "-"
    api_key: str | None = None  # kosong = pakai key yang sedang aktif, hanya ganti tailnet


@router.get("/config")
async def get_config(user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await ts.get_config()


@router.put("/config")
async def put_config(body: TailscaleConfigRequest, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    tailnet = (body.tailnet or "").strip() or "-"
    if not _TAILNET_RE.match(tailnet):
        raise HTTPException(status_code=400, detail="Nama tailnet tidak valid")
    api_key = (body.api_key or "").strip() or None

    try:
        result = await ts.save_config(tailnet, api_key)
    except TailscaleNotConfigured:
        raise HTTPException(status_code=400, detail="API key wajib diisi")
    except TailscaleError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Tailscale menolak kredensial (HTTP {e.status_code}) — periksa API key & nama tailnet",
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Tidak bisa menghubungi Tailscale API: {e}")

    await log_activity(
        user, "TAILSCALE_CONFIG_UPDATE", "WARNING", {"id": tailnet, "name": "tailscale"},
        f"{user.get('username')} memperbarui konfigurasi Tailscale (tailnet {tailnet})", request)
    return result


@router.delete("/config")
async def delete_config(request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    if not await ts.delete_config():
        raise HTTPException(status_code=404, detail="Tidak ada konfigurasi Tailscale di dashboard")
    await log_activity(
        user, "TAILSCALE_CONFIG_DELETE", "WARNING", {"id": "tailscale", "name": "tailscale"},
        f"{user.get('username')} menghapus konfigurasi Tailscale", request)
    return await ts.get_config()


async def _call(coro):
    try:
        return await coro
    except TailscaleNotConfigured:
        raise HTTPException(status_code=409, detail=_NOT_CONFIGURED)
    except TailscaleError as e:
        raise HTTPException(status_code=502, detail=f"Tailscale API error (HTTP {e.status_code}): {e.detail[:300]}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Tidak bisa menghubungi Tailscale API: {e}")


@router.get("/devices")
async def get_devices(user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await _call(ts.list_devices())


@router.get("/acl")
async def get_acl(user: dict = Depends(get_current_user)):
    _require_admin(user)
    return {"policy": await _call(ts.get_acl())}


# ── Managed network policy ────────────────────────────────────────────────────

_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")


class PolicyRequest(BaseModel):
    member_ports: list[int] = policy.DEFAULT_MEMBER_PORTS
    remove_allow_all: bool = False


class PolicyApplyRequest(PolicyRequest):
    etag: str


class GatewayRequest(BaseModel):
    enabled: bool


class AdminLoginRequest(BaseModel):
    tailscale_login: str | None = None


def _ports(ports: list[int]) -> list[int]:
    ports = sorted(set(ports))
    if len(ports) > 10 or any(p < 1 or p > 65535 for p in ports):
        raise HTTPException(status_code=400, detail="Port member harus 1–65535, maksimal 10 port")
    return ports


@router.get("/policy")
async def get_policy(user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await _call(policy.status())


@router.post("/policy/preview")
async def preview_policy(body: PolicyRequest, user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await _call(policy.preview(_ports(body.member_ports), body.remove_allow_all))


@router.post("/policy/apply")
async def apply_policy(body: PolicyApplyRequest, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    try:
        changes = await _call(policy.apply(_ports(body.member_ports), body.remove_allow_all, body.etag))
    except policy.PolicyChanged:
        raise HTTPException(status_code=409, detail="Policy di Tailscale berubah sejak preview — jalankan preview ulang")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Policy ditolak Tailscale: {e}")
    if changes:
        await log_activity(
            user, "TAILSCALE_POLICY_APPLY", "CRITICAL", {"id": "tailnet-policy", "name": "tailscale"},
            f"{user.get('username')} menerapkan policy Tailscale: {'; '.join(changes)}", request)
    return {"changes": changes, "status": await _call(policy.status())}


@router.put("/devices/{device_id}/gateway")
async def set_gateway(device_id: str, body: GatewayRequest, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    if not _DEVICE_ID_RE.match(device_id):
        raise HTTPException(status_code=400, detail="Device ID tidak valid")
    try:
        tags = await _call(policy.set_gateway(device_id, body.enabled))
    except policy.PolicyNotInstalled:
        raise HTTPException(status_code=409, detail=f"Terapkan policy dulu — {policy.GATEWAY_TAG} belum punya tagOwners")
    await log_activity(
        user, "TAILSCALE_GATEWAY_TAG", "WARNING", {"id": device_id, "name": device_id},
        f"{user.get('username')} {'memasang' if body.enabled else 'melepas'} {policy.GATEWAY_TAG} pada device {device_id}",
        request)
    return {"device_id": device_id, "tags": tags}


@router.put("/admin-logins/{user_id}")
async def set_admin_login(user_id: int, body: AdminLoginRequest, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    login = (body.tailscale_login or "").strip() or None
    if login and not policy.LOGIN_RE.match(login):
        raise HTTPException(status_code=400, detail="Login Tailscale tidak valid (contoh: nama@gmail.com atau nama@github)")
    updated = await policy.set_admin_login(user_id, login)
    if not updated:
        raise HTTPException(status_code=404, detail="User admin tidak ditemukan")
    await log_activity(
        user, "TAILSCALE_ADMIN_MAP", "INFO", {"id": str(user_id), "name": updated["username"]},
        f"{user.get('username')} memetakan {updated['username']} ke login Tailscale {login or '(kosong)'}", request)
    return updated
