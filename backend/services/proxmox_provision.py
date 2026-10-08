"""
Create a VM by cloning a Proxmox template and injecting cloud-init settings — the same flow as
Proxmox's own "Clone → Cloud-Init" UI (user, password, static IP or DHCP, DNS, CPU/RAM/disk, bridge),
driven through the dashboard's scoped API token. The clone joins the template's pool, so the token —
and therefore the dashboard — keeps seeing it. Templates must be generalized and carry a CloudInit drive.
"""
import asyncio
import ipaddress
import logging
import re

from services.proxmox_client import ProxmoxError
from i18n import tr

log = logging.getLogger("proxmox_provision")

NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
BRIDGE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
_DISK_KEYS = ("scsi0", "virtio0", "sata0")


class ProvisionError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def _disk(config: dict) -> tuple[str, int] | None:
    """(key, size in GB) of the template's boot disk."""
    for key in _DISK_KEYS:
        value = config.get(key) or ""
        if value and "media=cdrom" not in value:
            m = re.search(r"size=(\d+)([KMGT])", value)
            if not m:
                return key, 0
            n, unit = int(m.group(1)), m.group(2)
            return key, {"K": n // (1024 * 1024), "M": n // 1024, "G": n, "T": n * 1024}[unit]
    return None


def _bridge(config: dict) -> str | None:
    m = re.search(r"bridge=([^,]+)", config.get("net0") or "")
    return m.group(1) if m else None


async def list_templates(client, node: str) -> list[dict]:
    templates = []
    for vm in await client.list_vms(node):
        if not vm.get("template"):
            continue
        cfg = await client.get_vm_config(node, vm["vmid"])
        disk = _disk(cfg)
        templates.append({
            "vmid":      vm["vmid"],
            "name":      cfg.get("name") or vm.get("name") or str(vm["vmid"]),
            "ostype":    cfg.get("ostype"),
            "cores":     int(cfg.get("cores") or 1),
            "memory_mb": int(cfg.get("memory") or 512),
            "disk_key":  disk[0] if disk else None,
            "disk_gb":   disk[1] if disk else None,
            "bridge":    _bridge(cfg),
            "cloudinit": any("cloudinit" in str(v) for v in cfg.values()),
        })
    return sorted(templates, key=lambda t: t["name"].lower())


def build_ipconfig(ip_mode: str, ip_cidr: str | None, gateway: str | None) -> tuple[str, str | None]:
    """(Proxmox ipconfig0 value, static IP or None)."""
    if ip_mode == "dhcp":
        return "ip=dhcp", None
    try:
        iface = ipaddress.ip_interface((ip_cidr or "").strip())
        gw = ipaddress.ip_address((gateway or "").strip())
    except ValueError:
        raise ProvisionError(tr("IP statis harus berformat CIDR (mis. 192.168.1.50/24) dan gateway harus alamat IPv4",
                                "A static IP must use CIDR format (e.g. 192.168.1.50/24) and the gateway must be an IPv4 address"))
    if iface.version != 4 or gw.version != 4:
        raise ProvisionError(tr("Hanya IPv4 yang didukung", "Only IPv4 is supported"))
    if iface.network.prefixlen >= 31:
        raise ProvisionError(tr("Prefix jaringan terlalu kecil — pakai mis. /24",
                                "The network prefix is too small — use e.g. /24"))
    if iface.ip in (iface.network.network_address, iface.network.broadcast_address):
        raise ProvisionError(tr("IP tidak boleh alamat network/broadcast",
                                "The IP may not be the network or broadcast address"))
    if gw not in iface.network or gw == iface.ip:
        raise ProvisionError(tr(f"Gateway {gw} harus berada di subnet {iface.network} dan berbeda dari IP VM",
                                f"Gateway {gw} must be inside subnet {iface.network} and differ from the VM IP"))
    return f"ip={iface.with_prefixlen},gw={gw}", str(iface.ip)


def parse_dns(dns: str | None) -> str | None:
    servers = [s for s in re.split(r"[\s,]+", dns or "") if s]
    try:
        return " ".join(str(ipaddress.ip_address(s)) for s in servers) or None
    except ValueError:
        raise ProvisionError(tr("DNS harus berupa alamat IP (pisahkan dengan spasi atau koma)",
                                "DNS must be IP addresses (separated by spaces or commas)"))


async def create_from_template(client, node: str, *, template_vmid: int, name: str, username: str,
                               password: str, ip_mode: str, ip_cidr: str | None, gateway: str | None,
                               dns: str | None, cores: int | None, memory_mb: int | None,
                               disk_gb: int | None, bridge: str | None, full_clone: bool, start: bool) -> dict:
    if not NAME_RE.match(name):
        raise ProvisionError(tr("Nama VM/hostname hanya huruf, angka, dan '-' (maks. 63, tidak diawali/diakhiri '-')",
                                "The VM name/hostname may only contain letters, digits and '-' (max 63, not starting or ending with '-')"))
    if not USER_RE.match(username) or username == "root":
        raise ProvisionError(tr("Username Linux tidak valid (huruf kecil/angka/_/-, diawali huruf, bukan 'root')",
                                "Invalid Linux username (lowercase letters/digits/_/-, starting with a letter, not 'root')"))
    if not password or len(password) > 128:
        raise ProvisionError(tr("Password wajib diisi (maks. 128 karakter)",
                                "A password is required (max 128 characters)"))
    if bridge and not BRIDGE_RE.match(bridge):
        raise ProvisionError(tr("Nama bridge tidak valid", "Invalid bridge name"))
    ipconfig, static_ip = build_ipconfig(ip_mode, ip_cidr, gateway)
    nameserver = parse_dns(dns)

    tpl = next((t for t in await list_templates(client, node) if t["vmid"] == template_vmid), None)
    if not tpl:
        raise ProvisionError(tr("Template tidak ditemukan (atau di luar pool dashboard)",
                                "Template not found (or outside the dashboard pool)"), 404)
    if not tpl["cloudinit"]:
        raise ProvisionError(tr("Template belum punya CloudInit drive (Hardware → Add → CloudInit Drive)",
                                "The template has no CloudInit drive (Hardware → Add → CloudInit Drive)"))
    if disk_gb and tpl["disk_gb"] and disk_gb < tpl["disk_gb"]:
        raise ProvisionError(tr(f"Disk tidak bisa lebih kecil dari template ({tpl['disk_gb']} GB)",
                                f"The disk cannot be smaller than the template ({tpl['disk_gb']} GB)"))
    existing = await client.list_vms(node)
    # Guacamole connections are keyed by VM name, so names must be unique per node.
    if any((vm.get("name") or "").lower() == name.lower() for vm in existing):
        raise ProvisionError(tr(f"Nama '{name}' sudah dipakai VM lain di node ini",
                                f"The name '{name}' is already used by another VM on this node"), 409)
    if static_ip:
        for vm in existing:
            other = (await client.get_vm_config(node, vm["vmid"])).get("ipconfig0") or ""
            if f"ip={static_ip}/" in other:
                raise ProvisionError(tr(f"IP {static_ip} sudah dipakai cloud-init VM {vm['vmid']} ({vm.get('name')})",
                                        f"IP {static_ip} is already used by the cloud-init of VM {vm['vmid']} ({vm.get('name')})"), 409)
    pool = next((r.get("pool") for r in await client.cluster_vm_resources() if r.get("vmid") == template_vmid), None)
    if not pool:
        raise ProvisionError(tr("Template harus berada di sebuah pool Proxmox agar VM baru tetap terlihat oleh dashboard",
                                "The template must be in a Proxmox pool so the new VM stays visible to the dashboard"))

    for attempt in range(3):
        vmid = await client.next_vmid()
        try:
            upid = await client.clone_vm(node, template_vmid, vmid, name, full=full_clone, pool=pool)
            break
        except ProxmoxError as e:
            if "already exists" in e.detail and attempt < 2:
                continue
            raise
    await client.wait_task(node, upid, timeout=900)
    log.info("Cloned template %s → VM %s (%s, %s clone)", template_vmid, vmid, name, "full" if full_clone else "linked")

    try:
        config = {"ciuser": username, "cipassword": password, "ipconfig0": ipconfig, "ciupgrade": 0}
        if nameserver:
            config["nameserver"] = nameserver
        if cores and cores != tpl["cores"]:
            config["cores"] = cores
        if memory_mb and memory_mb != tpl["memory_mb"]:
            config["memory"] = memory_mb
        if bridge and bridge != tpl["bridge"]:
            # Rewrite the clone's own net0 — it already carries a fresh MAC, the template's would duplicate it.
            net0 = (await client.get_vm_config(node, vmid)).get("net0") or ""
            config["net0"] = re.sub(r"bridge=[^,]+", f"bridge={bridge}", net0)
        await client.update_vm_config(node, vmid, config)
        if disk_gb and tpl["disk_key"] and tpl["disk_gb"] and disk_gb > tpl["disk_gb"]:
            await client.resize_disk(node, vmid, tpl["disk_key"], f"{disk_gb}G")
        if start:
            await client.wait_task(node, await client.vm_action(node, vmid, "start"), timeout=120)
    except Exception as e:
        detail = e.detail if isinstance(e, ProxmoxError) else str(e)
        try:
            await client.wait_task(node, await client.destroy_vm(node, vmid), timeout=180)
            undo = tr(f"VM {vmid} dihapus kembali", f"VM {vmid} was deleted again")
        except Exception:
            undo = tr(f"VM {vmid} gagal dihapus otomatis — hapus manual di Proxmox",
                      f"VM {vmid} could not be deleted automatically — delete it manually in Proxmox")
        raise ProvisionError(tr(f"Konfigurasi VM gagal: {detail[:300]} ({undo})",
                                f"VM configuration failed: {detail[:300]} ({undo})"), 502)

    return {
        "vmid":      vmid,
        "name":      name,
        "static_ip": static_ip,
        "os_type":   "windows" if (tpl["ostype"] or "").startswith("w") else "linux",
        "started":   start,
        "clone":     "full" if full_clone else "linked",
    }


async def wait_for_agent_ip(client, node: str, vmid: int, timeout: int = 90) -> str | None:
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        ip = await client.get_guest_ip(node, vmid)
        if ip:
            return ip
    return None


async def destroy_vm_fully(client, node: str, vmid: int, confirm_name: str) -> dict:
    """Stop (if needed) and destroy a VM together with its disks, snapshots and cloud-init drive
    (purge also drops it from backup/replication/HA jobs), then check every storage that held one of
    its volumes for anything named vm-<id>-* left behind. Templates and protected VMs are refused."""
    try:
        cfg = await client.get_vm_config(node, vmid)
    except ProxmoxError as e:
        # The scoped token gets 403 for VMs it cannot see (outside its pool) — same answer as "missing".
        if "does not exist" in e.detail or e.status_code == 403:
            raise ProvisionError(tr(f"VM {vmid} tidak ditemukan di node {node} (atau di luar pool dashboard)",
                                    f"VM {vmid} not found on node {node} (or outside the dashboard pool)"), 404)
        raise
    name = cfg.get("name") or str(vmid)
    if cfg.get("template"):
        raise ProvisionError(tr("Template tidak bisa dihapus dari dashboard — kelola template langsung di Proxmox",
                                "Templates cannot be deleted from the dashboard — manage templates in Proxmox directly"))
    if (confirm_name or "") != name:
        raise ProvisionError(tr("Konfirmasi tidak cocok — ketik nama VM persis seperti yang tertera",
                                "The confirmation does not match — type the VM name exactly as shown"))
    if str(cfg.get("protection", "0")) == "1":
        raise ProvisionError(tr("VM dilindungi (Protection aktif) — matikan dulu di Proxmox: Options → Protection",
                                "The VM is protected (Protection is on) — turn it off in Proxmox first: Options → Protection"), 409)
    if cfg.get("lock"):
        raise ProvisionError(tr(f"VM sedang terkunci ({cfg['lock']}) — tunggu operasi di Proxmox selesai",
                                f"The VM is locked ({cfg['lock']}) — wait for the Proxmox operation to finish"), 409)

    owned = re.compile(rf"([\w.-]+):((?:vm|base)-{vmid}-[\w.-]+)")
    storages = sorted({m.group(1) for v in cfg.values() if isinstance(v, str) for m in owned.finditer(v)})
    snapshots = [s["name"] for s in await client.list_snapshots(node, vmid) if s.get("name") != "current"]

    stopped_first = (await client.get_vm_status(node, vmid)).get("status") != "stopped"
    if stopped_first:
        await client.wait_task(node, await client.vm_action(node, vmid, "stop"), timeout=120)
    await client.wait_task(node, await client.destroy_vm(node, vmid), timeout=600)

    if any(v["vmid"] == vmid for v in await client.list_vms(node)):
        raise ProvisionError(tr(f"VM {vmid} masih terdaftar di Proxmox setelah dihapus",
                                f"VM {vmid} is still registered in Proxmox after deletion"), 500)
    left = []
    for storage in storages:
        try:
            left += [c["volid"] for c in await client.storage_content(node, storage, vmid)]
        except ProxmoxError as e:
            left.append(tr(f"{storage}: tidak bisa diverifikasi (HTTP {e.status_code})",
                           f"{storage}: could not be verified (HTTP {e.status_code})"))
    return {"vmid": vmid, "name": name, "stopped_first": stopped_first, "snapshots_removed": snapshots,
            "storages_checked": storages, "disks_left": left}
