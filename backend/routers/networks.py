"""
Switch (jaringan) per Proxmox: jaringan terisolasi dengan subnet sendiri yang dibuat dari dashboard.

  GET    /api/v1/networks                          daftar Proxmox beserta blok alamat dan switch-nya (admin)
  GET    /api/v1/networks/instances/{label}/check  kesiapan host Proxmox dan jalur dari dashboard ke tiap switch
  POST   /api/v1/networks/instances/{label}/pools  tambah blok alamat (blok lama di dalamnya digantikan)
  DELETE /api/v1/networks/instances/{label}/pools?cidr=…  hapus blok alamat yang tidak dipakai switch
  POST   /api/v1/networks                          buat switch (subnet di luar blok: add_pool menjadikannya blok baru)
  PATCH  /api/v1/networks/{id}                     ganti nama atau nyala/matikan internet (NAT)
  DELETE /api/v1/networks/{id}                     hapus switch (ditolak kalau masih dipakai VM)
  GET    /api/v1/networks/{id}/vms                 VM yang tersambung ke switch
  GET    /api/v1/networks/{id}/free-ip             IP kosong berikutnya untuk VM baru
  GET    /api/v1/networks/setup-script             skrip ccd-net-setup.sh untuk host Proxmox (tanpa login)
"""
import asyncio
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from auth import Role, get_current_user
from database import get_pool
from services import networks as nw
from services.audit import both, log_activity
from services.proxmox_client import ProxmoxError
from i18n import tr

router = APIRouter()


def _require_admin(user: dict):
    if user["role"] not in (Role.SUPERADMIN, Role.SYSADMIN):
        raise HTTPException(403, tr("Aksi ini hanya untuk admin/sysadmin",
                                    "Only admins/sysadmins can do this"))


class PoolBody(BaseModel):
    cidr: str


class NetworkCreate(BaseModel):
    instance: str
    name: str
    cidr: str | None = None      # kosong = /24 kosong berikutnya di blok yang ada
    snat: bool = True
    add_pool: bool = False       # subnet di luar semua blok dijadikan blok baru


class NetworkUpdate(BaseModel):
    name: str | None = None
    snat: bool | None = None


def _view(n: dict) -> dict:
    return {k: n[k] for k in ("id", "instance", "vnet", "name", "cidr", "gateway", "snat", "created_by", "created_at")}


@router.get("/setup-script", response_class=PlainTextResponse)
async def setup_script():
    """Tidak berisi rahasia; disajikan tanpa login supaya bisa diunduh langsung dari host Proxmox."""
    return PlainTextResponse(nw.SETUP_SCRIPT.read_text(encoding="utf-8"), media_type="text/x-shellscript")


@router.get("")
async def list_networks(user: dict = Depends(get_current_user)):
    _require_admin(user)
    db = await get_pool()
    async with db.acquire() as conn:
        insts = await conn.fetch("SELECT label, token_id, net_pools, sdn_zone FROM proxmox_instances ORDER BY label")
        rows = await conn.fetch("SELECT * FROM networks ORDER BY instance, id")
    out = []
    for inst in insts:
        nets = [_view(dict(r)) for r in rows if r["instance"] == inst["label"]]
        taken = [ipaddress.ip_network(n["cidr"]) for n in nets]
        pools = []
        for p in nw.pools_of(dict(inst)):
            free = nw.next_free_subnet([p], taken)
            pools.append({"cidr": str(p), "switches": sum(1 for t in taken if t.subnet_of(p)),
                          "full": free is None})
        sub = nw.next_free_subnet(nw.pools_of(dict(inst)), taken)
        out.append({"label": inst["label"], "token_id": inst["token_id"], "pools": pools,
                    "zone": inst["sdn_zone"], "suggested_cidr": str(sub) if sub else None, "networks": nets})
    return {"instances": out}


@router.get("/instances/{label}/check")
async def check_instance(label: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    inst = await nw.get_instance(label)
    result = await nw.readiness(await nw.client_for(label), inst)
    nets = await nw.instance_networks(label)
    reach = await asyncio.gather(*(nw.reachable(n["gateway"]) for n in nets))
    result["reachable"] = {n["id"]: ok for n, ok in zip(nets, reach)}
    return result


@router.post("/instances/{label}/pools")
async def add_pool(label: str, body: PoolBody, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    res = await nw.add_pool(label, body.cidr)
    def _detail():
        replaced = (tr(f" (menggantikan {', '.join(res['replaced'])})", f" (replacing {', '.join(res['replaced'])})")
                    if res["replaced"] else "")
        return tr(f"{user.get('username')} menambah blok alamat switch {res['added']}{replaced} di Proxmox {label}",
                  f"{user.get('username')} added the switch address block {res['added']}{replaced} on Proxmox {label}")
    await log_activity(user, "NETWORK_POOL_ADD", "WARNING", {"id": label, "name": label}, both(_detail), request)
    return {"label": label, **res}


@router.delete("/instances/{label}/pools")
async def remove_pool(label: str, cidr: str, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    res = await nw.remove_pool(label, cidr)
    await log_activity(user, "NETWORK_POOL_REMOVE", "WARNING", {"id": label, "name": label},
                       both(lambda: tr(f"{user.get('username')} menghapus blok alamat switch {res['removed']} dari Proxmox {label}",
                       f"{user.get('username')} removed the switch address block {res['removed']} from Proxmox {label}")), request)
    return {"label": label, **res}


@router.post("")
async def create_network(body: NetworkCreate, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    net = await nw.create(body.instance, body.name, body.cidr, body.snat, user.get("username"), body.add_pool)
    await log_activity(user, "NETWORK_CREATE", "WARNING", {"id": net["vnet"], "name": body.instance},
                       both(lambda: tr(f"{user.get('username')} membuat switch '{net['name']}' {net['cidr']} di {body.instance} "
                                       f"(VNet {net['vnet']}, internet {'NAT' if net['snat'] else 'mati'}"
                                       f"{', blok alamat baru' if net['pool_added'] else ''})",
                                       f"{user.get('username')} created the switch '{net['name']}' {net['cidr']} on {body.instance} "
                                       f"(VNet {net['vnet']}, internet {'NAT' if net['snat'] else 'off'}"
                                       f"{', new address block' if net['pool_added'] else ''})")), request)
    return {**_view(net), "pool_added": net["pool_added"]}


@router.patch("/{network_id}")
async def update_network(network_id: int, body: NetworkUpdate, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    before = await nw.get_network(network_id)
    net = await nw.update(network_id, body.name, body.snat)
    changes = []
    if net["name"] != before["name"]:
        changes.append(both(lambda: tr(f"nama '{before['name']}' -> '{net['name']}'", f"name '{before['name']}' -> '{net['name']}'")))
    if net["snat"] != before["snat"]:
        changes.append(both(lambda: tr(f"internet {'NAT' if net['snat'] else 'mati'}", f"internet {'NAT' if net['snat'] else 'off'}")))
    if changes:
        await log_activity(user, "NETWORK_UPDATE", "WARNING", {"id": net["vnet"], "name": net["instance"]},
                           both(lambda: tr(f"{user.get('username')} mengubah switch {net['cidr']} di {net['instance']}: {', '.join(c.t() for c in changes)}",
                                           f"{user.get('username')} changed the switch {net['cidr']} on {net['instance']}: {', '.join(c.t() for c in changes)}")),
                           request)
    return _view(net)


@router.delete("/{network_id}")
async def delete_network(network_id: int, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    net = await nw.delete(network_id)
    await log_activity(user, "NETWORK_DELETE", "WARNING", {"id": net["vnet"], "name": net["instance"]},
                       both(lambda: tr(f"{user.get('username')} menghapus switch '{net['name']}' {net['cidr']} di {net['instance']}",
                       f"{user.get('username')} deleted the switch '{net['name']}' {net['cidr']} on {net['instance']}")), request)
    return {"status": "deleted"}


@router.get("/{network_id}/vms")
async def network_vms(network_id: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    net = await nw.get_network(network_id)
    try:
        return await nw.attached_vms(await nw.client_for(net["instance"]), net["vnet"])
    except ProxmoxError as e:
        raise HTTPException(502, f"Proxmox: {(e.detail or '')[:150]}")


@router.get("/{network_id}/free-ip")
async def network_free_ip(network_id: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    net = await nw.get_network(network_id)
    try:
        settings = await nw.vm_settings(net["instance"], network_id, None, None)
    except ProxmoxError as e:
        raise HTTPException(502, f"Proxmox: {(e.detail or '')[:150]}")
    return {**settings, "cidr": net["cidr"]}
