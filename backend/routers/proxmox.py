"""
Proxmox VE integration — primary VM management surface, with multi-instance support
(multiple Proxmox clusters/hosts, each configured via the dashboard rather than a
single fixed PROXMOX_HOST/TOKEN_ID/TOKEN_SECRET env var). See ADAPTATION_NOTES.md.

  GET    /api/v1/proxmox/host-status                                   → live CPU/RAM/disk/network semua node (panel dashboard utama)
  GET/POST              /api/v1/proxmox/instances                     → list/create Proxmox instances (admin)
  PUT/DELETE            /api/v1/proxmox/instances/{label}             → update/delete an instance (admin)
  GET    /api/v1/proxmox/instances/{label}/nodes                                  → daftar node
  GET    /api/v1/proxmox/instances/{label}/nodes/{node}/vms                       → daftar VM (juga sync ke tabel `vms`)
  GET    /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}                → status + config VM
  POST   /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}/action          → start/stop/shutdown/reboot/suspend/resume (admin, atau student pada VM yang di-assign)
  GET    /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}/snapshots       → daftar snapshot
  POST   /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}/snapshots       → buat snapshot (admin, atau student pada VM yang di-assign)
  DELETE /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}/snapshots/{name} → hapus snapshot (admin-only)
  POST   /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}/snapshots/{name}/rollback → restore snapshot (admin, atau student pada VM yang di-assign)
  GET    /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}/ip             → guest IP (QEMU Guest Agent)
  GET    /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}/rrddata        → histori metrik (Proxmox RRD)
  GET    /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}/iops           → histori disk IOPS (disampling dashboard)
  POST   /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}/guest-agent    → aktifkan opsi QEMU Guest Agent (admin)
  GET/PUT /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}/resources     → baca / ubah RAM, CPU, storage (admin; VM harus mati, storage hanya bisa naik)
  GET    /api/v1/proxmox/instances/{label}/nodes/{node}/templates                 → template yang bisa di-clone (admin)
  POST   /api/v1/proxmox/instances/{label}/nodes/{node}/vms                       → create VM: clone template + cloud-init (admin)
  DELETE /api/v1/proxmox/instances/{label}/nodes/{node}/vms/{vmid}?confirm_name= → hapus VM + semua jejaknya (admin)
  GET    /api/v1/proxmox/instances/{label}/nodes/{node}/my-assigned-vmids         → VM yang boleh dikontrol user ini

`vms` (tabel entity generik, awalnya untuk Hyper-V) di-upsert tiap kali daftar VM diambil, dengan
vm_id = str(vmid) dan host_name = "{instance_label}__{node}" — komposit ini menghindari tabrakan nama
node antar-instance Proxmox berbeda (mis. dua cluster yang sama-sama punya node "pve"). Skema komposit
ini membuat fitur generik yang sudah ada (ssh_creds, vm_metadata, guac_sync, groups/ReBAC) langsung bisa
dipakai untuk VM Proxmox multi-instance tanpa perubahan lebih lanjut di sana.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import re
import time

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user, Role
from database import get_pool, get_student_vm_ids
from services import proxmox_instances as pve_instances
from services.proxmox_client import ProxmoxError
from services.audit import log_activity
from services import proxmox_provision as provision
from services.vm_credentials import save_vm_credentials
from services import vm_cleanup
from services import networks
from services import proxmox_iops_poller as iops_poller
from i18n import tr

router = APIRouter()
log = logging.getLogger("proxmox")

_ADMIN_ROLES = (Role.SUPERADMIN, Role.SYSADMIN)


def _require_admin(user: dict):
    if user.get("role") not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail=tr("Aksi ini hanya untuk admin/sysadmin/superadmin",
                                                       "Only admins/sysadmins/superadmins can do this"))


async def _require_admin_or_assigned(user: dict, host_key: str, vmid: int):
    """Admin/sysadmin selalu boleh. Student boleh kalau VM ini di-assign ke mereka
    (langsung via vm_assignments, atau lewat grup via group_vm_access)."""
    if user.get("role") in _ADMIN_ROLES:
        return
    allowed = await get_student_vm_ids(int(user["sub"]), host_key)
    if (str(vmid), host_key) not in allowed:
        raise HTTPException(status_code=403, detail=tr("Anda tidak punya akses ke VM ini",
                                                       "You do not have access to this VM"))


def _host_key(label: str, node: str) -> str:
    return f"{label}__{node}"


async def _get_client(label: str):
    try:
        return await pve_instances.get_client(label)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class VmActionRequest(BaseModel):
    action: str  # start | stop | shutdown | reboot | suspend | resume


class SnapshotCreateRequest(BaseModel):
    snapname: str
    description: str = ""


class InstanceCreateRequest(BaseModel):
    label: str
    host: str
    token_id: str
    token_secret: str
    verify_ssl: bool = False


class InstanceUpdateRequest(BaseModel):
    host: str | None = None
    token_id: str | None = None
    token_secret: str | None = None
    verify_ssl: bool | None = None


async def _sync_vms_table(host_key: str, vms: list[dict]):
    """Upsert daftar VM Proxmox ke tabel `vms` generik agar fitur ssh_creds/guac_sync/vm_metadata
    yang sudah ada (dibangun untuk Hyper-V) bisa dipakai tanpa perubahan."""
    if not vms:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        for vm in vms:
            args = (str(vm["vmid"]), host_key, vm.get("name") or str(vm["vmid"]), vm.get("status"))
            # UPDATE dulu, INSERT hanya untuk VM baru. INSERT ... ON CONFLICT tetap mengambil nomor
            # dari sequence CCDID setiap dipanggil, sehingga nomor VM baru akan melompat jauh.
            done = await conn.execute(
                "UPDATE vms SET vm_name = $3, state = $4, updated_at = NOW() WHERE vm_id = $1 AND host_name = $2",
                *args)
            if done == "UPDATE 0":
                await conn.execute(
                    """INSERT INTO vms (vm_id, host_name, vm_name, state, updated_at)
                       VALUES ($1, $2, $3, $4, NOW()) ON CONFLICT (vm_id, host_name) DO NOTHING""", *args)


@router.get("/all-vms")
async def get_all_vms(user: dict = Depends(get_current_user)):
    """Flat list semua VM lintas semua instance/node Proxmox yang terkonfigurasi —
    dipakai picker VM generik (mis. link request VPS ke VM Proxmox yang sudah ada)."""
    _require_admin(user)
    result = []
    pool = await get_pool()
    async with pool.acquire() as conn:
        ccd_ids = {(r["vm_id"], r["host_name"]): r["ccd_id"] for r in await conn.fetch("SELECT vm_id, host_name, ccd_id FROM vms")}
    for inst in await pve_instances.list_instances():
        label = inst["label"]
        try:
            client = await pve_instances.get_client(label)
            nodes = await client.list_nodes()
        except (ProxmoxError, ValueError):
            continue
        for node in nodes:
            node_name = node.get("node")
            if not node_name:
                continue
            try:
                vms = await client.list_vms(node_name)
            except ProxmoxError:
                continue
            for vm in vms:
                if vm.get("template"):
                    continue
                host_key = _host_key(label, node_name)
                result.append({
                    "vm_id":     str(vm["vmid"]),
                    "vm_name":   vm.get("name") or str(vm["vmid"]),
                    "host_name": host_key,
                    "ccd_id":    ccd_ids.get((str(vm["vmid"]), host_key)),
                    "status":    vm.get("status"),
                })
    return result


# label → (fetched_at, result). Dashboard utama bisa dibuka banyak user sekaligus dan auto-refresh
# tiap beberapa detik — cache pendek supaya tidak membanjiri Proxmox dengan 2 request/node/poll.
_HOST_STATUS_TTL = 4
_host_status_cache: dict[str, tuple[float, dict]] = {}


async def _node_host_status(client, label: str, node_name: str) -> dict | None:
    key = f"{label}__{node_name}"
    hit = _host_status_cache.get(key)
    if hit and time.monotonic() - hit[0] < _HOST_STATUS_TTL:
        return hit[1]
    try:
        status = await client.get_node_status(node_name)
        rrd = await client.get_node_rrddata(node_name, "hour")
    except ProxmoxError:
        return None
    last = rrd[-1] if rrd else {}
    mem, rootfs, swap = status.get("memory", {}), status.get("rootfs", {}), status.get("swap", {})
    result = {
        "instance": label, "node": node_name,
        "cpu_pct":  round((status.get("cpu") or 0) * 100, 1),
        "cpus":     status.get("cpuinfo", {}).get("cpus"),
        "mem_used": mem.get("used"), "mem_total": mem.get("total"),
        "disk_used": rootfs.get("used"), "disk_total": rootfs.get("total"),
        "swap_used": swap.get("used"), "swap_total": swap.get("total"),
        "net_in_bps": last.get("netin"), "net_out_bps": last.get("netout"),
        "uptime_s": status.get("uptime"), "loadavg": status.get("loadavg"),
    }
    _host_status_cache[key] = (time.monotonic(), result)
    return result


@router.get("/host-status")
async def get_host_status(user: dict = Depends(get_current_user)):
    """Live CPU/RAM/disk/swap/network semua node Proxmox yang online — panel Host Performance di
    dashboard utama. Butuh token dengan Sys.Audit di /nodes/{node} (PVEAuditor) — read-only, tidak
    bisa kontrol VM/node. Hanya admin — student tidak boleh melihat performa/nama host."""
    _require_admin(user)
    result = []
    for inst in await pve_instances.list_instances():
        label = inst["label"]
        try:
            client = await pve_instances.get_client(label)
            nodes = await client.list_nodes()
        except (ProxmoxError, ValueError):
            continue
        for node in nodes:
            node_name = node.get("node")
            if not node_name or node.get("status") != "online":
                continue
            st = await _node_host_status(client, label, node_name)
            if st:
                result.append(st)
    return result


# ── Instance management ────────────────────────────────────────────────────────

@router.get("/instances")
async def get_instances(user: dict = Depends(get_current_user)):
    # Berisi alamat host Proxmox — hanya admin. Student memakai GET /my-vms (tanpa nama host).
    _require_admin(user)
    return await pve_instances.list_instances()


@router.post("/instances")
async def post_instance(body: InstanceCreateRequest, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    if not body.label or not body.label.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail=tr("Label hanya boleh huruf/angka/underscore/dash",
                                                       "The label may only contain letters, digits, underscores and dashes"))
    existing = await pve_instances.get_instance(body.label)
    if existing:
        raise HTTPException(status_code=409, detail=tr("Label instance sudah dipakai",
                                                       "This instance label is already used"))
    created = await pve_instances.create_instance(
        body.label, body.host, body.token_id, body.token_secret, body.verify_ssl
    )
    await log_activity(user, "PVE_INSTANCE_ADD", "WARNING", {"id": body.label, "name": body.label},
                       f"{user.get('username')} menambahkan instance Proxmox '{body.label}' ({body.host}, "
                       f"token {body.token_id}, verifikasi SSL {'aktif' if body.verify_ssl else 'mati'})", request)
    return created


@router.put("/instances/{label}")
async def put_instance(label: str, body: InstanceUpdateRequest, request: Request,
                       user: dict = Depends(get_current_user)):
    _require_admin(user)
    old = await pve_instances.get_instance(label)
    updated = await pve_instances.update_instance(
        label, body.host, body.token_id, body.token_secret, body.verify_ssl
    )
    if not updated:
        raise HTTPException(status_code=404, detail=tr("Instance tidak ditemukan", "Instance not found"))
    changes = []
    if body.host is not None and body.host != (old or {}).get("host"):
        changes.append(f"alamat {(old or {}).get('host')} → {body.host}")
    if body.token_id is not None and body.token_id != (old or {}).get("token_id"):
        changes.append(f"token {(old or {}).get('token_id')} → {body.token_id}")
    if body.token_secret:
        changes.append("secret token diganti")
    if body.verify_ssl is not None and body.verify_ssl != (old or {}).get("verify_ssl"):
        changes.append(f"verifikasi SSL {'aktif' if body.verify_ssl else 'mati'}")
    if changes:
        await log_activity(user, "PVE_INSTANCE_UPDATE", "WARNING", {"id": label, "name": label},
                           f"{user.get('username')} mengubah instance Proxmox '{label}': {'; '.join(changes)}", request)
    return updated


@router.delete("/instances/{label}")
async def delete_instance(label: str, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    old = await pve_instances.get_instance(label)
    ok = await pve_instances.delete_instance(label)
    if not ok:
        raise HTTPException(status_code=404, detail=tr("Instance tidak ditemukan", "Instance not found"))
    await log_activity(user, "PVE_INSTANCE_DELETE", "CRITICAL", {"id": label, "name": label},
                       f"{user.get('username')} menghapus instance Proxmox '{label}' ({(old or {}).get('host', '')})",
                       request)
    return {"status": "deleted"}


# ── Nodes / VMs ──────────────────────────────────────────────────────────────

@router.get("/instances/{label}/nodes")
async def get_nodes(label: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    client = await _get_client(label)
    try:
        return await client.list_nodes()
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)


# (label, node, vmid) → (fetched_at, ip). The VM table polls every 10 s; asking every guest agent on
# every poll would be slow, so answers — misses included, so dead agents stay cheap — are cached.
_AGENT_IP_TTL = 60
_agent_ip_cache: dict[tuple[str, str, int], tuple[float, str | None]] = {}


async def _agent_ips(client, label: str, node: str, vmids: list[int]) -> dict[str, str | None]:
    sem = asyncio.Semaphore(8)

    async def one(vmid: int):
        hit = _agent_ip_cache.get((label, node, vmid))
        if hit and time.monotonic() - hit[0] < _AGENT_IP_TTL:
            return vmid, hit[1]
        async with sem:
            try:
                ip = await asyncio.wait_for(client.get_guest_ip(node, vmid), timeout=3)
            except Exception:
                ip = None
        _agent_ip_cache[(label, node, vmid)] = (time.monotonic(), ip)
        return vmid, ip

    return {str(v): ip for v, ip in await asyncio.gather(*(one(v) for v in vmids))}


async def _node_vms(label: str, node: str, user: dict) -> list[dict]:
    """VM satu node. Admin: semua VM. Student: HANYA VM yang di-assign ke mereka (langsung/grup)."""
    client = await _get_client(label)
    try:
        vms = await client.list_vms(node)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)
    # Templates are provisioning material (cloned in Proxmox), never assignable/startable VMs.
    vms = [vm for vm in vms if not vm.get("template")]
    try:
        await _sync_vms_table(_host_key(label, node), vms)
    except Exception:
        log.warning("Gagal sync tabel vms untuk %s/%s", label, node, exc_info=True)

    # IPs only for VMs this user may see details of — same RBAC as GET .../vms/{vmid}/ip.
    host_key = _host_key(label, node)
    if user.get("role") in _ADMIN_ROLES:
        visible = {str(vm["vmid"]) for vm in vms}
    else:
        visible = {vid for vid, _ in await get_student_vm_ids(int(user["sub"]), host_key)}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT vm_id, ssh_host, ssh_port FROM vm_credentials WHERE host_name = $1", host_key)
        vm_rows = {r["vm_id"]: r for r in await conn.fetch(
            "SELECT vm_id, ccd_id, lease_until FROM vms WHERE host_name = $1", host_key)}
    manual = {r["vm_id"]: r["ssh_host"] for r in rows if r["ssh_host"]}
    ssh_ports = {r["vm_id"]: r["ssh_port"] for r in rows if r["ssh_host"]}
    if user.get("role") not in _ADMIN_ROLES:
        vms = [vm for vm in vms if str(vm["vmid"]) in visible]
    running = [vm["vmid"] for vm in vms if vm.get("status") == "running" and str(vm["vmid"]) in visible]
    agent = await _agent_ips(client, label, node, running)
    for vm in vms:
        vid = str(vm["vmid"])
        meta = vm_rows.get(vid)
        vm["ccd_id"] = meta["ccd_id"] if meta else None   # nomor unik lintas Proxmox (migrations/V006)
        vm["lease_until"] = meta["lease_until"].isoformat() if meta and meta["lease_until"] else None
        if vid in visible:
            vm["ip"] = agent.get(vid)
            vm["manual_ip"] = manual.get(vid)
            vm["ssh_port"] = ssh_ports.get(vid)
    return vms


@router.get("/instances/{label}/nodes/{node}/vms")
async def get_vms(label: str, node: str, user: dict = Depends(get_current_user)):
    return await _node_vms(label, node, user)


@router.get("/my-vms")
async def get_my_vms(user: dict = Depends(get_current_user)):
    """VM milik user ini lintas instance/node (untuk student: tampilan Servers/Topology tanpa perlu
    memilih host). Tiap item diberi `instance` dan `node` hanya untuk dipakai klien saat memanggil aksi."""
    allowed = await get_student_vm_ids(int(user["sub"]))
    host_keys = {host_key for _, host_key in allowed if "__" in host_key}
    result = []
    for host_key in sorted(host_keys):
        label, node = host_key.rsplit("__", 1)
        try:
            vms = await _node_vms(label, node, user)
        except HTTPException:
            continue  # instance sudah dihapus / tidak terjangkau
        for vm in vms:
            result.append({**vm, "instance": label, "node": node})
    return result


@router.get("/instances/{label}/nodes/{node}/vms/{vmid}")
async def get_vm_detail(label: str, node: str, vmid: int, user: dict = Depends(get_current_user)):
    await _require_admin_or_assigned(user, _host_key(label, node), vmid)
    client = await _get_client(label)
    try:
        status = await client.get_vm_status(node, vmid)
        config = await client.get_vm_config(node, vmid)
        return {"status": status, "config": config}
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)


@router.post("/instances/{label}/nodes/{node}/vms/{vmid}/action")
async def post_vm_action(label: str, node: str, vmid: int, body: VmActionRequest, user: dict = Depends(get_current_user)):
    await _require_admin_or_assigned(user, _host_key(label, node), vmid)
    if user.get("role") not in _ADMIN_ROLES and body.action in ("start", "resume", "reboot"):
        pool = await get_pool()
        async with pool.acquire() as conn:
            expired = await conn.fetchval(
                "SELECT lease_until <= NOW() FROM vms WHERE vm_id = $1 AND host_name = $2",
                str(vmid), _host_key(label, node))
        if expired:
            raise HTTPException(status_code=403, detail=tr("Masa sewa VM ini sudah habis. Ajukan perpanjangan lewat Helpdesk.",
                                                           "This VM's lease has expired. Request an extension through the Helpdesk."))
    client = await _get_client(label)
    try:
        upid = await client.vm_action(node, vmid, body.action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)

    try:
        await log_activity(
            user, "VM_ACTION", "INFO", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
            f"{user.get('username')} menjalankan aksi '{body.action}' pada VM {vmid} di {label}/{node}", None)
    except Exception:
        pass

    return {"upid": upid}


_DISK_KEY_RE = re.compile(r"^(scsi|virtio|sata|ide)\d+$")
_MAX_CORES, _MAX_MEMORY_MB = 128, 1024 * 1024
_SIZE_UNIT_GB = {"K": 1 / (1024 * 1024), "M": 1 / 1024, "G": 1, "T": 1024}


def _vm_disks(config: dict) -> list[dict]:
    """Disk VM (bukan CD-ROM / cloud-init) dari config Proxmox: [{key, storage, size_gb}]."""
    disks = []
    for key, value in config.items():
        if not _DISK_KEY_RE.match(key) or not isinstance(value, str):
            continue
        if "media=cdrom" in value or "cloudinit" in value:
            continue
        m = re.search(r"size=(\d+(?:\.\d+)?)([KMGT])", value)
        if not m:
            continue
        disks.append({"key": key, "storage": value.split(":", 1)[0],
                      "size_gb": round(float(m.group(1)) * _SIZE_UNIT_GB[m.group(2)], 2)})
    return sorted(disks, key=lambda d: d["key"])


class VmResourcesRequest(BaseModel):
    memory_mb: int | None = None
    cores: int | None = None
    disk_key: str | None = None
    disk_size_gb: int | None = None


@router.get("/instances/{label}/nodes/{node}/vms/{vmid}/resources")
async def get_vm_resources(label: str, node: str, vmid: int, user: dict = Depends(get_current_user)):
    """RAM/CPU/disk saat ini + status VM, untuk form resize (admin)."""
    _require_admin(user)
    client = await _get_client(label)
    try:
        status = await client.get_vm_status(node, vmid)
        config = await client.get_vm_config(node, vmid)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)
    return {
        "status": status.get("status"),
        "memory_mb": int(config.get("memory") or 0),
        "cores": int(config.get("cores") or 1),
        "sockets": int(config.get("sockets") or 1),
        "disks": _vm_disks(config),
    }


@router.put("/instances/{label}/nodes/{node}/vms/{vmid}/resources")
async def put_vm_resources(label: str, node: str, vmid: int, body: VmResourcesRequest,
                           request: Request, user: dict = Depends(get_current_user)):
    """Ubah RAM/CPU/storage VM (superadmin/sysadmin). Aturan: VM harus mati (stopped);
    storage hanya boleh diperbesar, tidak pernah diperkecil."""
    _require_admin(user)
    if body.memory_mb is None and body.cores is None and body.disk_size_gb is None:
        raise HTTPException(status_code=400, detail=tr("Tidak ada perubahan yang diminta",
                                                       "No changes were requested"))
    client = await _get_client(label)
    try:
        status = await client.get_vm_status(node, vmid)
        config = await client.get_vm_config(node, vmid)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)

    if status.get("status") != "stopped":
        raise HTTPException(status_code=409, detail=tr("VM harus dalam kondisi mati (stopped) sebelum diubah",
                                                       "The VM must be stopped before it can be changed"))
    if config.get("lock"):
        raise HTTPException(status_code=409, detail=tr(f"VM sedang terkunci oleh operasi lain ({config['lock']})",
                                                       f"The VM is locked by another operation ({config['lock']})"))

    changes, config_update = [], {}
    cur_mem, cur_cores = int(config.get("memory") or 0), int(config.get("cores") or 1)

    if body.memory_mb is not None and body.memory_mb != cur_mem:
        if not 256 <= body.memory_mb <= _MAX_MEMORY_MB:
            raise HTTPException(status_code=400, detail=tr(f"RAM harus 256 - {_MAX_MEMORY_MB} MB",
                                                           f"RAM must be 256 - {_MAX_MEMORY_MB} MB"))
        config_update["memory"] = body.memory_mb
        balloon = int(config.get("balloon") or 0)
        if balloon > body.memory_mb:
            config_update["balloon"] = body.memory_mb  # Proxmox menolak balloon > memory
        changes.append(f"RAM {cur_mem}->{body.memory_mb}MB")

    if body.cores is not None and body.cores != cur_cores:
        if not 1 <= body.cores <= _MAX_CORES:
            raise HTTPException(status_code=400, detail=tr(f"CPU harus 1 - {_MAX_CORES} core",
                                                           f"CPU must be 1 - {_MAX_CORES} cores"))
        config_update["cores"] = body.cores
        changes.append(f"CPU {cur_cores}->{body.cores}core")

    # Batas kapasitas node: jangan izinkan melebihi total fisik (best-effort, abaikan bila node status gagal)
    if config_update:
        try:
            ns = await client.get_node_status(node)
            sockets = int(config.get("sockets") or 1)
            max_cpu = int((ns.get("cpuinfo") or {}).get("cpus") or 0)
            max_mem = int((ns.get("memory") or {}).get("total") or 0) // (1024 * 1024)
            if "cores" in config_update and max_cpu and config_update["cores"] * sockets > max_cpu:
                raise HTTPException(status_code=400, detail=tr(f"CPU melebihi kapasitas node ({max_cpu} thread)",
                                                               f"CPU exceeds the node capacity ({max_cpu} threads)"))
            if "memory" in config_update and max_mem and config_update["memory"] > max_mem:
                raise HTTPException(status_code=400, detail=tr(f"RAM melebihi kapasitas node ({max_mem} MB)",
                                                               f"RAM exceeds the node capacity ({max_mem} MB)"))
        except HTTPException:
            raise
        except Exception:
            pass

    disk_resize = None
    if body.disk_size_gb is not None:
        if not body.disk_key or not _DISK_KEY_RE.match(body.disk_key):
            raise HTTPException(status_code=400, detail=tr("disk_key tidak valid", "Invalid disk_key"))
        disk = next((d for d in _vm_disks(config) if d["key"] == body.disk_key), None)
        if not disk:
            raise HTTPException(status_code=400, detail=tr(f"Disk {body.disk_key} tidak ditemukan pada VM ini",
                                                           f"Disk {body.disk_key} not found on this VM"))
        if body.disk_size_gb < disk["size_gb"]:
            raise HTTPException(status_code=400, detail=tr(f"Storage hanya bisa diperbesar, tidak bisa diperkecil (sekarang {disk['size_gb']:g} GB)",
                                                           f"Storage can only grow, never shrink (currently {disk['size_gb']:g} GB)"))
        if body.disk_size_gb > disk["size_gb"]:
            disk_resize = (body.disk_key, body.disk_size_gb)
            changes.append(f"disk {body.disk_key} {disk['size_gb']:g}->{body.disk_size_gb}GB")

    if not changes:
        raise HTTPException(status_code=400, detail=tr("Nilai sama dengan yang sekarang, tidak ada perubahan",
                                                       "The values are the same as now; nothing changed"))

    applied = []
    try:
        if config_update:
            await client.update_vm_config(node, vmid, config_update)
            applied += [c for c in changes if not c.startswith("disk")]
        if disk_resize:
            await client.resize_disk(node, vmid, disk_resize[0], f"{disk_resize[1]}G")
            applied.append(next(c for c in changes if c.startswith("disk")))
    except ProxmoxError as e:
        done = tr(f" Sudah diterapkan: {', '.join(applied)}.",
                  f" Already applied: {', '.join(applied)}.") if applied else ""
        raise HTTPException(status_code=502, detail=tr(f"Proxmox menolak perubahan: {e.detail[:300]}.{done}",
                                                       f"Proxmox refused the change: {e.detail[:300]}.{done}"))

    try:
        await log_activity(
            user, "VM_RESIZE", "WARNING", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
            f"{user.get('username')} mengubah resource VM {vmid} di {label}/{node}: {', '.join(changes)}", request)
    except Exception:
        pass
    return {"status": "updated", "changes": changes}


@router.get("/instances/{label}/nodes/{node}/vms/{vmid}/ip")
async def get_vm_ip(label: str, node: str, vmid: int, user: dict = Depends(get_current_user)):
    """{ip, agent_enabled, reason, manual_ip}: ip dari QEMU Guest Agent (reason menjelaskan kenapa null),
    manual_ip = SSH Host yang diisi admin di kredensial VM — itulah yang dipakai tombol Connect."""
    await _require_admin_or_assigned(user, _host_key(label, node), vmid)
    client = await _get_client(label)
    try:
        info = await client.guest_ip_status(node, vmid)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)
    pool = await get_pool()
    async with pool.acquire() as conn:
        manual = await conn.fetchval(
            "SELECT ssh_host FROM vm_credentials WHERE vm_id = $1 AND host_name = $2",
            str(vmid), _host_key(label, node))
    info["manual_ip"] = manual or None
    return info


@router.post("/instances/{label}/nodes/{node}/vms/{vmid}/guest-agent")
async def enable_guest_agent(label: str, node: str, vmid: int, user: dict = Depends(get_current_user)):
    _require_admin(user)
    client = await _get_client(label)
    try:
        await client.enable_guest_agent(node, vmid)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)
    try:
        await log_activity(
            user, "VM_CONFIG", "INFO", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
            f"{user.get('username')} mengaktifkan opsi QEMU Guest Agent pada VM {vmid} di {label}/{node}", None)
    except Exception:
        pass
    return {"agent_enabled": True, "restart_required": True}


# timeframe → (lookback s, bucket s); buckets mirror the Proxmox RRD step of each timeframe.
_IOPS_WINDOWS = {
    # id → (lookback detik, ukuran bucket detik). Data sendiri (poller sampling tiap 15 detik,
    # retensi 14 hari) — bukan RRD Proxmox, jadi jendela pendek benar-benar dapat resolusi lebih
    # halus, bukan sekadar crop ulang. Bucket <=15 detik pada jendela pendek = mendekati data mentah.
    "1m":  (60, 15),
    "5m":  (300, 15),
    "10m": (600, 15),
    "30m": (1800, 30),
    "1h":  (3600, 60),
    "6h":  (21600, 300),
    "24h": (86400, 1800),
    "7d":  (604800, 10800),
}


@router.get("/instances/{label}/nodes/{node}/vms/{vmid}/iops")
async def get_iops(label: str, node: str, vmid: int, timeframe: str = "1h", user: dict = Depends(get_current_user)):
    if timeframe not in _IOPS_WINDOWS:
        raise HTTPException(status_code=400, detail=tr(f"timeframe harus salah satu dari {', '.join(_IOPS_WINDOWS)}",
                                                       f"timeframe must be one of {', '.join(_IOPS_WINDOWS)}"))
    await _require_admin_or_assigned(user, _host_key(label, node), vmid)
    lookback, bucket = _IOPS_WINDOWS[timeframe]
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT (floor(extract(epoch FROM recorded_at) / $3::int) * $3::int)::bigint AS time,
                   round(avg(read_iops)::numeric, 1)::float  AS read_iops,
                   round(avg(write_iops)::numeric, 1)::float AS write_iops
            FROM vm_iops_history
            WHERE host_name = $1 AND vm_id = $2 AND recorded_at > NOW() - make_interval(secs => $4::int)
            GROUP BY 1 ORDER BY 1
        """, _host_key(label, node), str(vmid), bucket, lookback)
    return [dict(r) for r in rows]


@router.get("/instances/{label}/nodes/{node}/vms/{vmid}/rrddata")
async def get_rrddata(label: str, node: str, vmid: int, timeframe: str = "hour", user: dict = Depends(get_current_user)):
    if timeframe not in ("hour", "day", "week", "month", "year"):
        raise HTTPException(status_code=400, detail=tr("timeframe harus salah satu dari hour/day/week/month/year",
                                                       "timeframe must be one of hour/day/week/month/year"))
    await _require_admin_or_assigned(user, _host_key(label, node), vmid)
    client = await _get_client(label)
    try:
        return await client.get_rrddata(node, vmid, timeframe)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)


@router.get("/instances/{label}/nodes/{node}/vms/{vmid}/snapshots")
async def get_snapshots(label: str, node: str, vmid: int, user: dict = Depends(get_current_user)):
    await _require_admin_or_assigned(user, _host_key(label, node), vmid)
    client = await _get_client(label)
    try:
        snaps = await client.list_snapshots(node, vmid)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)
    # 'current' adalah penanda posisi live VM, bukan snapshot sungguhan — sembunyikan dari UI.
    return [s for s in snaps if s.get("name") != "current"]


@router.post("/instances/{label}/nodes/{node}/vms/{vmid}/snapshots")
async def post_snapshot(label: str, node: str, vmid: int, body: SnapshotCreateRequest, user: dict = Depends(get_current_user)):
    await _require_admin_or_assigned(user, _host_key(label, node), vmid)
    if not body.snapname or not body.snapname.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail=tr("Nama snapshot hanya boleh huruf/angka/underscore/dash",
                                                       "Snapshot names may only contain letters, digits, underscores and dashes"))
    client = await _get_client(label)
    try:
        upid = await client.create_snapshot(node, vmid, body.snapname, body.description)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)

    try:
        await log_activity(
            user, "VM_SNAPSHOT", "INFO", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
            f"{user.get('username')} membuat snapshot '{body.snapname}' pada VM {vmid} di {label}/{node}", None)
    except Exception:
        pass
    return {"upid": upid}


@router.delete("/instances/{label}/nodes/{node}/vms/{vmid}/snapshots/{snapname}")
async def delete_snapshot(label: str, node: str, vmid: int, snapname: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    client = await _get_client(label)
    try:
        upid = await client.delete_snapshot(node, vmid, snapname)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)

    try:
        await log_activity(
            user, "VM_SNAPSHOT", "WARNING", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
            f"{user.get('username')} menghapus snapshot '{snapname}' pada VM {vmid} di {label}/{node}", None)
    except Exception:
        pass
    return {"upid": upid}


@router.post("/instances/{label}/nodes/{node}/vms/{vmid}/snapshots/{snapname}/rollback")
async def rollback_snapshot(label: str, node: str, vmid: int, snapname: str, user: dict = Depends(get_current_user)):
    await _require_admin_or_assigned(user, _host_key(label, node), vmid)
    client = await _get_client(label)
    try:
        upid = await client.rollback_snapshot(node, vmid, snapname)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)

    try:
        await log_activity(
            user, "VM_SNAPSHOT", "WARNING", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
            f"{user.get('username')} rollback ke snapshot '{snapname}' pada VM {vmid} di {label}/{node}", None)
    except Exception:
        pass
    return {"upid": upid}


@router.get("/instances/{label}/nodes/{node}/my-assigned-vmids")
async def get_my_assigned_vmids(label: str, node: str, user: dict = Depends(get_current_user)):
    """VM ID yang boleh dikontrol user ini di node ini (student: assignment langsung + grup;
    admin/sysadmin: semua, jadi dikembalikan kosong — frontend admin tidak perlu daftar ini)."""
    if user.get("role") in _ADMIN_ROLES:
        return {"vmids": [], "all": True}
    allowed = await get_student_vm_ids(int(user["sub"]), _host_key(label, node))
    return {"vmids": [vm_id for vm_id, _ in allowed], "all": False}


# ── Create VM from template (clone + cloud-init) ─────────────────────────────

class CreateVmRequest(BaseModel):
    template_vmid: int
    name: str
    username: str
    password: str
    ip_mode: Literal["static", "dhcp"] = "static"
    ip_cidr: str | None = None
    gateway: str | None = None
    dns: str | None = None
    cores: int | None = Field(None, ge=1, le=64)
    memory_mb: int | None = Field(None, ge=256, le=262144)
    disk_gb: int | None = Field(None, ge=1, le=4096)
    bridge: str | None = None
    full_clone: bool = False
    start: bool = True
    lease_days: int | None = Field(None, ge=1, le=3650)   # masa sewa; kosong = tanpa batas
    network_id: int | None = None   # switch CCD: bridge, gateway, DNS dari switch; IP kosong = otomatis


@router.get("/instances/{label}/nodes/{node}/templates")
async def get_templates(label: str, node: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    client = await _get_client(label)
    try:
        return await provision.list_templates(client, node)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)


@router.post("/instances/{label}/nodes/{node}/vms")
async def create_vm(label: str, node: str, body: CreateVmRequest, request: Request, user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await create_vm_core(label, node, body, user, request)


async def create_vm_core(label: str, node: str, body: CreateVmRequest, user: dict, request: Request | None = None,
                         wait_for_agent: bool = True) -> dict:
    """Clone template + cloud-init, catat VM di dashboard, dan simpan kredensial Connect. Dipakai form
    Create VM dan pembuatan VM massal (services/vm_batches.py). Kesalahan dikembalikan sebagai HTTPException.
    wait_for_agent=False melewati penantian IP dari guest agent kalau IP-nya statis (VM massal)."""
    client = await _get_client(label)
    host_key = _host_key(label, node)
    ip_cidr, gateway, dns, bridge = body.ip_cidr, body.gateway, body.dns, (body.bridge or "").strip() or None
    switch = None
    if body.network_id is not None:
        if body.ip_mode != "static":
            raise HTTPException(400, tr("Switch CCD tidak menyediakan DHCP. Pakai IP statis; kosongkan IP supaya dibagikan otomatis",
                                        "CCD switches have no DHCP. Use a static IP; leave the IP empty to have one assigned automatically"))
        switch = await networks.vm_settings(label, body.network_id, body.ip_cidr, body.dns)
        ip_cidr, gateway, dns, bridge = switch["ip_cidr"], switch["gateway"], switch["dns"], switch["bridge"]
    if body.ip_mode == "static":
        ip = (ip_cidr or "").split("/")[0].strip()
        pool = await get_pool()
        async with pool.acquire() as conn:
            clash = await conn.fetchrow("SELECT vm_id, host_name FROM vm_credentials WHERE ssh_host = $1", ip)
        if clash:
            raise HTTPException(status_code=409, detail=tr(f"IP {ip} sudah dipakai VM {clash['vm_id']} ({clash['host_name']}) di dashboard",
                                                           f"IP {ip} is already used by VM {clash['vm_id']} ({clash['host_name']}) in the dashboard"))
    try:
        vm = await provision.create_from_template(
            client, node, template_vmid=body.template_vmid, name=body.name.strip(),
            username=body.username.strip(), password=body.password, ip_mode=body.ip_mode,
            ip_cidr=ip_cidr, gateway=gateway, dns=dns, cores=body.cores,
            memory_mb=body.memory_mb, disk_gb=body.disk_gb, bridge=bridge,
            full_clone=body.full_clone, start=body.start)
    except provision.ProvisionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=e.detail)

    # Proxmox reuses freed VMIDs and names can repeat: drop whatever an earlier VM left behind (e.g. one
    # deleted directly in Proxmox) so the new VM never inherits its assignments, credentials or grants.
    await vm_cleanup.purge_vm_records(host_key, str(vm["vmid"]))
    try:
        await vm_cleanup.remove_guac_connections(host_key, {vm["name"]})
    except Exception:
        log.warning("Pre-clean of Guacamole connections for new VM %s failed", vm["vmid"], exc_info=True)
    await _sync_vms_table(host_key, [{"vmid": vm["vmid"], "name": vm["name"],
                                      "status": "running" if vm["started"] else "stopped"}])
    if body.lease_days:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE vms SET lease_until = NOW() + make_interval(days => $3) WHERE vm_id = $1 AND host_name = $2",
                str(vm["vmid"]), host_key, body.lease_days)
    need_agent = vm["started"] and (wait_for_agent or not vm["static_ip"])
    agent_ip = await provision.wait_for_agent_ip(client, node, vm["vmid"]) if need_agent else None
    ssh_host = vm["static_ip"] or agent_ip or ""
    linux = vm["os_type"] == "linux"
    await save_vm_credentials(
        host_key, str(vm["vmid"]), os_type=vm["os_type"], cred_type="ssh",
        guac_protocol="ssh" if linux else "rdp", ssh_host=ssh_host, ssh_port=22 if linux else 3389,
        username=body.username.strip(), password=body.password, wait_for_guac=True)
    try:
        await log_activity(
            user, "VM_CREATE", "WARNING", {"id": str(vm["vmid"]), "name": f"{label}/{node}/{vm['vmid']}"},
            f"{user.get('username')} membuat VM {vm['name']} ({vm['vmid']}, {vm['clone']} clone) dari template "
            f"{body.template_vmid} di {label}/{node}" + (f", switch '{switch['name']}' {ip_cidr}" if switch else ""), request)
    except Exception:
        pass
    return {**vm, "agent_ip": agent_ip, "connect_ready": bool(ssh_host), "switch": switch["name"] if switch else None}


class LeaseRequest(BaseModel):
    until: datetime | None = None        # tanggal baru; null bersama clear=true = tanpa batas
    add_days: int | None = Field(None, ge=-3650, le=3650)
    clear: bool = False


@router.put("/instances/{label}/nodes/{node}/vms/{vmid}/lease")
async def put_vm_lease(label: str, node: str, vmid: int, body: LeaseRequest, request: Request,
                       user: dict = Depends(get_current_user)):
    """Atur masa sewa VM (admin). add_days menambah dari batas sekarang, atau dari hari ini kalau
    masa sewanya sudah habis; angka negatif mengurangi. Kalau batas baru masih di depan, VM boleh
    dinyalakan lagi oleh mahasiswa."""
    _require_admin(user)
    host_key = _host_key(label, node)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT vm_name, ccd_id, lease_until FROM vms WHERE vm_id = $1 AND host_name = $2",
                                  str(vmid), host_key)
        if not row:
            raise HTTPException(404, tr("VM belum tercatat di dashboard. Buka daftar VM-nya dulu.",
                                        "The VM is not recorded in the dashboard yet. Open its VM list first."))
        now = datetime.now(timezone.utc)
        if body.clear:
            new = None
        elif body.until is not None:
            new = body.until if body.until.tzinfo else body.until.replace(tzinfo=timezone.utc)
        elif body.add_days:
            cur = row["lease_until"]
            base = max(cur, now) if (cur and body.add_days > 0) else (cur or now)
            new = base + timedelta(days=body.add_days)
        else:
            raise HTTPException(400, tr("Isi tanggal, jumlah hari, atau pilih hapus batas",
                                        "Enter a date or a number of days, or choose to remove the limit"))
        await conn.execute(
            """UPDATE vms SET lease_until = $3,
                   lease_enforced_at = CASE WHEN $3::timestamptz IS NULL OR $3 > NOW() THEN NULL ELSE lease_enforced_at END
               WHERE vm_id = $1 AND host_name = $2""", str(vmid), host_key, new)
    before = row["lease_until"].strftime("%Y-%m-%d %H:%M") if row["lease_until"] else "tanpa batas"
    after = new.strftime("%Y-%m-%d %H:%M") if new else "tanpa batas"
    await log_activity(user, "VM_LEASE_UPDATE", "INFO", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
                       f"{user.get('username')} mengubah masa sewa VM {row['vm_name']} (CCD-{row['ccd_id']:04d}): "
                       f"{before} -> {after} UTC", request)
    return {"lease_until": new.isoformat() if new else None}


@router.delete("/instances/{label}/nodes/{node}/vms/{vmid}")
async def delete_vm(label: str, node: str, vmid: int, confirm_name: str, request: Request,
                    user: dict = Depends(get_current_user)):
    _require_admin(user)
    client = await _get_client(label)
    host_key = _host_key(label, node)
    pool = await get_pool()
    async with pool.acquire() as conn:
        known_name = await conn.fetchval("SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2", str(vmid), host_key)
    try:
        result = await provision.destroy_vm_fully(client, node, vmid, confirm_name)
    except provision.ProvisionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=tr(f"Proxmox menolak menghapus VM: {e.detail[:300]}",
                                                       f"Proxmox refused to delete the VM: {e.detail[:300]}"))

    # The VM is gone from Proxmox from here on, so the dashboard cleanup always runs.
    guac_removed, guac_error = 0, None
    try:
        guac_removed = await vm_cleanup.remove_guac_connections(host_key, {result["name"], known_name})
    except Exception as e:
        guac_error = str(e)
    rows = await vm_cleanup.purge_vm_records(host_key, str(vmid))
    _agent_ip_cache.pop((label, node, vmid), None)
    iops_poller.forget(host_key, vmid)
    try:
        await log_activity(
            user, "VM_DELETE", "CRITICAL", {"id": str(vmid), "name": f"{label}/{node}/{vmid}"},
            f"{user.get('username')} menghapus VM {result['name']} ({vmid}) di {label}/{node}", request)
    except Exception:
        pass
    return {**result, "guacamole_connections_removed": guac_removed, "guacamole_error": guac_error,
            "dashboard_rows_removed": rows}
