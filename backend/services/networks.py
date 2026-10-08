"""
Switch (jaringan) yang dibuat dari dashboard: satu VNet + subnet di SDN zone tipe Simple pada Proxmox.
Host Proxmox menjadi gateway setiap switch dan memberi akses internet lewat NAT.

Supaya VPS bisa menjangkau VM di switch tanpa mengiklankan subnet satu per satu, setiap Proxmox punya
satu atau beberapa blok alamat (net_pools) yang diiklankan lewat Tailscale; setiap switch berada di
salah satu blok. Switch baru di blok yang sudah ada langsung terjangkau. Blok baru (mis. switch dengan
subnet di luar blok yang ada) butuh ccd-net-setup.sh dijalankan ulang di host dan route-nya disetujui
di Tailscale. Isolasi antar-switch, ke jaringan kampus, dan ke host Proxmox dipasang oleh skrip itu
(nftables di host), jadi dashboard tidak butuh akses root ke host.
"""
import asyncio
import ipaddress
import os
import re
import secrets
import string
from pathlib import Path

from fastapi import HTTPException

from database import get_pool
from services import proxmox_instances
from services.proxmox_client import ProxmoxError
from i18n import tr

SETUP_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ccd-net-setup.sh"
VNET_PREFIX = "ccd"                     # nama bridge switch; aturan nftables di host memakai awalan ini
DEFAULT_PREFIX = 24
PREFIX_RANGE = (20, 29)                 # ukuran switch
POOL_RANGE = (8, 29)                    # ukuran blok; switch yang dijadikan blok sendiri bisa sekecil /29
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,39}$")
_PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
# Docker di VPS memakai 172.17.0.0/16 s.d. 172.31.0.0/16 untuk jaringan bridge-nya.
_DOCKER = [ipaddress.ip_network(f"172.{n}.0.0/16") for n in range(17, 32)]

_locks: dict[str, asyncio.Lock] = {}


def lock(label: str) -> asyncio.Lock:
    """Perubahan SDN satu Proxmox dijalankan bergantian: menerapkan SDN memuat ulang jaringan host."""
    return _locks.setdefault(label, asyncio.Lock())


# ── Validasi alamat ───────────────────────────────────────────────────────────

def _network(text: str, what: str) -> ipaddress.IPv4Network:
    try:
        net = ipaddress.ip_network(str(text or "").strip(), strict=True)
    except ValueError:
        raise HTTPException(400, tr(f"{what} harus berformat jaringan IPv4, mis. 10.111.1.0/24 (bagian host harus 0)",
                                    f"{what} must be an IPv4 network, e.g. 10.111.1.0/24 (the host part must be 0)"))
    if net.version != 4:
        raise HTTPException(400, tr(f"{what} harus IPv4", f"{what} must be IPv4"))
    return net


def host_networks() -> list[tuple[str, ipaddress.IPv4Network]]:
    """Jaringan VPS dari HOST_NETWORKS ("iface=cidr,..."), diisi setup.sh karena container tidak bisa melihatnya."""
    out = []
    for item in os.getenv("HOST_NETWORKS", "").split(","):
        iface, _, cidr = item.strip().rpartition("=")
        try:
            net = ipaddress.ip_interface(cidr).network
        except ValueError:
            continue
        if net.version == 4 and not net.is_loopback and (iface, net) not in out:
            out.append((iface or "?", net))
    return out


def parse_pool(text: str) -> ipaddress.IPv4Network:
    pool = _network(text, tr("Blok alamat", "Address block"))
    if not any(pool.subnet_of(p) for p in _PRIVATE):
        raise HTTPException(400, tr("Blok alamat harus alamat privat: 10.x, 172.16-31.x, atau 192.168.x",
                                    "The address block must be private: 10.x, 172.16-31.x or 192.168.x"))
    if not POOL_RANGE[0] <= pool.prefixlen <= POOL_RANGE[1]:
        raise HTTPException(400, tr(f"Ukuran blok alamat antara /{POOL_RANGE[0]} dan /{POOL_RANGE[1]}",
                                    f"The address block size must be between /{POOL_RANGE[0]} and /{POOL_RANGE[1]}"))
    if any(pool.overlaps(d) for d in _DOCKER):
        raise HTTPException(400, tr(f"Blok {pool} bertabrakan dengan rentang jaringan Docker di VPS (172.17.0.0 s.d. 172.31.255.255)",
                                    f"Block {pool} overlaps the Docker network range on the VPS (172.17.0.0 to 172.31.255.255)"))
    clash = next(((iface, net) for iface, net in host_networks() if pool.overlaps(net)), None)
    if clash:
        raise HTTPException(409, tr(f"Blok {pool} bertabrakan dengan jaringan VPS {clash[1]} ({clash[0]})",
                                    f"Block {pool} overlaps the VPS network {clash[1]} ({clash[0]})"))
    return pool


def parse_switch(text: str, taken: list) -> ipaddress.IPv4Network:
    net = _network(text, tr("Subnet switch", "Switch subnet"))
    if not PREFIX_RANGE[0] <= net.prefixlen <= PREFIX_RANGE[1]:
        raise HTTPException(400, tr(f"Ukuran switch antara /{PREFIX_RANGE[0]} dan /{PREFIX_RANGE[1]}",
                                    f"The switch size must be between /{PREFIX_RANGE[0]} and /{PREFIX_RANGE[1]}"))
    clash = next((t for t in taken if net.overlaps(t)), None)
    if clash:
        raise HTTPException(409, tr(f"Subnet {net} bertabrakan dengan switch lain ({clash})",
                                    f"Subnet {net} overlaps another switch ({clash})"))
    return net


def pools_of(inst: dict) -> list[ipaddress.IPv4Network]:
    return [ipaddress.ip_network(p) for p in inst.get("net_pools") or []]


def block_of(net, pools: list):
    """Blok tempat subnet ini berada, atau None."""
    return next((p for p in pools if net.subnet_of(p)), None)


def next_free_subnet(pools, taken: list, prefix: int = DEFAULT_PREFIX):
    """/24 kosong berikutnya, dicari dari blok pertama. Di blok yang lebih besar dari /24, subnet x.x.0.0
    dilewati supaya penomoran mulai dari x.x.1.0. Blok yang lebih kecil dipakai utuh."""
    for pool in ([pools] if isinstance(pools, ipaddress.IPv4Network) else pools):
        size = max(prefix, pool.prefixlen)
        for i, sub in enumerate(pool.subnets(new_prefix=size)):
            if i == 0 and size > pool.prefixlen:
                continue
            if not any(sub.overlaps(t) for t in taken):
                return sub
    return None


def gateway_of(net: ipaddress.IPv4Network) -> ipaddress.IPv4Address:
    return next(net.hosts())


def check_name(name: str) -> str:
    name = " ".join(str(name or "").split())
    if not NAME_RE.match(name):
        raise HTTPException(400, tr("Nama switch 1-40 karakter: huruf, angka, spasi, titik, garis bawah, tanda hubung, atau kurung",
                                    "Switch names are 1-40 characters: letters, digits, spaces, dots, underscores, hyphens or parentheses"))
    return name


def new_vnet_id(existing: set) -> str:
    alphabet = string.ascii_lowercase + string.digits
    while True:
        vnet = VNET_PREFIX + "".join(secrets.choice(alphabet) for _ in range(5))
        if vnet not in existing:
            return vnet


def subnet_id(zone: str, net) -> str:
    """ID subnet SDN di Proxmox, mis. 'ccd-10.111.1.0-24'."""
    return f"{zone}-{net.network_address}-{net.prefixlen}"


# ── Data ──────────────────────────────────────────────────────────────────────

async def get_instance(label: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT label, token_id, net_pools, sdn_zone FROM proxmox_instances WHERE label = $1", label)
    if not row:
        raise HTTPException(404, tr(f"Proxmox '{label}' tidak terdaftar",
                                    f"Proxmox '{label}' is not registered"))
    return dict(row)


async def instance_networks(label: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM networks WHERE instance = $1 ORDER BY id", label)
    return [dict(r) for r in rows]


async def get_network(network_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM networks WHERE id = $1", network_id)
    if not row:
        raise HTTPException(404, tr("Switch tidak ditemukan", "Switch not found"))
    return dict(row)


async def client_for(label: str):
    try:
        return await proxmox_instances.get_client(label)
    except ValueError as e:
        raise HTTPException(404, str(e))


def _sdn_error(e: ProxmoxError, zone: str) -> HTTPException:
    detail = e.detail or ""
    if e.status_code == 403 or "Permission check failed" in detail:
        return HTTPException(403, tr("Token CCD belum punya izin SDN di Proxmox ini. Jalankan ccd-net-setup.sh di host Proxmox (lihat Topology → Switch)",
                                     "The CCD token has no SDN permissions on this Proxmox yet. Run ccd-net-setup.sh on the Proxmox host (see Topology → Switch)"))
    if "zone" in detail.lower() and ("not exist" in detail or "does not exist" in detail):
        return HTTPException(409, tr(f"SDN zone '{zone}' belum ada di Proxmox ini. Jalankan ccd-net-setup.sh di host Proxmox",
                                     f"SDN zone '{zone}' does not exist on this Proxmox yet. Run ccd-net-setup.sh on the Proxmox host"))
    return HTTPException(502, tr(f"Proxmox menolak perubahan jaringan: {detail[:200]}",
                                 f"Proxmox refused the network change: {detail[:200]}"))


async def _all_vms(client) -> list[tuple[str, dict, dict]]:
    """(node, vm, config) untuk semua VM yang terlihat token, termasuk template."""
    out = []
    for n in await client.list_nodes():
        for vm in await client.list_vms(n["node"]):
            out.append((n["node"], vm, await client.get_vm_config(n["node"], vm["vmid"])))
    return out


async def attached_vms(client, vnet: str) -> list[dict]:
    """VM yang punya kartu jaringan di switch ini."""
    pattern = re.compile(rf"(^|,)bridge={re.escape(vnet)}(,|$)")
    return [{"vmid": vm["vmid"], "name": cfg.get("name") or vm.get("name") or str(vm["vmid"]), "node": node}
            for node, vm, cfg in await _all_vms(client)
            if any(re.fullmatch(r"net\d+", k) and pattern.search(str(v)) for k, v in cfg.items())]


async def used_ips(client, label: str, net) -> set:
    """IP di subnet ini yang sudah dipakai: ipconfig cloud-init VM dan IP Login Connect yang tersimpan."""
    used = set()
    for _, _, cfg in await _all_vms(client):
        for key, value in cfg.items():
            m = re.fullmatch(r"ipconfig\d+", key) and re.search(r"(?:^|,)ip=([\d.]+)/", str(value))
            if m and ipaddress.ip_address(m.group(1)) in net:
                used.add(ipaddress.ip_address(m.group(1)))
    pool = await get_pool()
    async with pool.acquire() as conn:
        hosts = await conn.fetch(
            "SELECT ssh_host FROM vm_credentials WHERE split_part(host_name, '__', 1) = $1", label)
    for h in hosts:
        try:
            ip = ipaddress.ip_address((h["ssh_host"] or "").strip())
        except ValueError:
            continue
        if ip in net:
            used.add(ip)
    return used


def free_ip(net, used: set):
    gateway = gateway_of(net)
    return next((ip for ip in net.hosts() if ip != gateway and ip not in used), None)


async def lan_networks(client) -> list:
    """Subnet yang sudah dipakai interface host Proxmox (mis. vmbr0), kecuali bridge switch CCD."""
    out = []
    for n in await client.list_nodes():
        try:
            ifaces = await client.node_networks(n["node"])
        except ProxmoxError:
            continue
        for i in ifaces:
            if not i.get("cidr") or str(i.get("iface", "")).startswith(VNET_PREFIX):
                continue
            try:
                out.append(ipaddress.ip_interface(i["cidr"]).network)
            except ValueError:
                pass
    return out


async def reachable(ip: str, port: int = 8006, timeout: float = 3.0) -> bool:
    """Bisakah dashboard menjangkau alamat ini? Dipakai untuk gateway switch (web UI Proxmox :8006)."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
        writer.close()
        return True
    except Exception:
        return False


async def readiness(client, inst: dict) -> dict:
    zone = inst["sdn_zone"]
    out = {"zone": zone, "zone_ok": False, "zone_type": None, "perm_zone": False, "perm_apply": False,
           "pools": list(inst["net_pools"] or []), "error": None}
    try:
        z = next((z for z in await client.sdn_zones() if z.get("zone") == zone), None)
        out["zone_type"] = z.get("type") if z else None
        out["zone_ok"] = out["zone_type"] == "simple"
        privs = await client.permissions(f"/sdn/zones/{zone}")
        out["perm_zone"] = "SDN.Allocate" in privs and "SDN.Use" in privs
        out["perm_apply"] = "SDN.Allocate" in await client.permissions("/sdn")
    except ProxmoxError as e:
        if e.status_code != 403:
            out["error"] = f"Proxmox: {(e.detail or '')[:150]}"
    except Exception as e:
        out["error"] = tr(f"Proxmox tidak terjangkau: {type(e).__name__}",
                          f"Proxmox is unreachable: {type(e).__name__}")
    out["ready"] = bool(out["zone_ok"] and out["perm_zone"] and out["perm_apply"] and inst["net_pools"])
    return out


# ── Perubahan ─────────────────────────────────────────────────────────────────

async def _check_block(label: str, block, own: list, absorb: bool) -> list:
    """Pastikan blok baru tidak bertabrakan dengan blok Proxmox lain, LAN Proxmox mana pun, atau blok
    Proxmox ini sendiri. Kalau absorb, blok sendiri yang seluruhnya berada di dalam blok baru boleh
    digantikan olehnya (mis. 192.168.111.0/24 diperbesar menjadi 192.168.96.0/19); dikembalikan."""
    absorbed = []
    for p in own:
        if p == block or block.subnet_of(p):
            raise HTTPException(409, tr(f"Blok {block} sudah termasuk di blok {p}",
                                        f"Block {block} is already inside block {p}"))
        if p.subnet_of(block) and absorb:
            absorbed.append(p)
        elif p.overlaps(block):
            raise HTTPException(409, tr(f"Blok {block} bertabrakan dengan blok {p} di Proxmox ini",
                                        f"Block {block} overlaps block {p} on this Proxmox"))
    db = await get_pool()
    async with db.acquire() as conn:
        others = await conn.fetch(
            "SELECT label, net_pools FROM proxmox_instances WHERE label <> $1 AND cardinality(net_pools) > 0", label)
        labels = [r["label"] for r in await conn.fetch("SELECT label FROM proxmox_instances")]
    for o in others:
        for p in o["net_pools"]:
            if block.overlaps(ipaddress.ip_network(p)):
                raise HTTPException(409, tr(f"Blok {block} bertabrakan dengan blok Proxmox '{o['label']}' ({p})",
                                            f"Block {block} overlaps the block of Proxmox '{o['label']}' ({p})"))
    # Bertabrakan dengan LAN Proxmox mana pun membuat route di VPS rancu.
    for other in labels:
        try:
            lans = await lan_networks(await client_for(other))
        except Exception:              # Proxmox yang sedang tidak terjangkau dilewati
            continue
        clash = next((n for n in lans if n.overlaps(block)), None)
        if clash:
            raise HTTPException(409, tr(f"Blok {block} bertabrakan dengan jaringan {clash} di Proxmox '{other}'",
                                        f"Block {block} overlaps the network {clash} on Proxmox '{other}'"))
    return absorbed


async def _save_pools(conn, label: str, pools: list) -> None:
    await conn.execute("UPDATE proxmox_instances SET net_pools = $2, updated_at = NOW() WHERE label = $1",
                       label, [str(p) for p in pools])


async def add_pool(label: str, text: str) -> dict:
    """Tambah blok alamat. Blok yang memuat blok lama Proxmox ini menggantikannya."""
    block = parse_pool(text)
    async with lock(label):
        inst = await get_instance(label)
        own = pools_of(inst)
        absorbed = await _check_block(label, block, own, absorb=True)
        pools = [p for p in own if p not in absorbed] + [block]
        db = await get_pool()
        async with db.acquire() as conn:
            await _save_pools(conn, label, pools)
    return {"pools": [str(p) for p in pools], "added": str(block), "replaced": [str(p) for p in absorbed]}


async def remove_pool(label: str, text: str) -> dict:
    block = _network(text, tr("Blok alamat", "Address block"))
    async with lock(label):
        inst = await get_instance(label)
        own = pools_of(inst)
        if block not in own:
            raise HTTPException(404, tr(f"Blok {block} tidak ada di Proxmox ini",
                                        f"Block {block} does not exist on this Proxmox"))
        inside = [n for n in await instance_networks(label) if ipaddress.ip_network(n["cidr"]).subnet_of(block)]
        if inside:
            names = ", ".join(f"'{n['name']}'" for n in inside[:3]) + (" …" if len(inside) > 3 else "")
            raise HTTPException(409, tr(f"Blok {block} masih dipakai switch {names}. Hapus switch-nya dulu",
                                        f"Block {block} is still used by switch {names}. Delete the switch first"))
        pools = [p for p in own if p != block]
        db = await get_pool()
        async with db.acquire() as conn:
            await _save_pools(conn, label, pools)
    return {"pools": [str(p) for p in pools], "removed": str(block)}


async def create(label: str, name: str, cidr: str | None, snat: bool, username: str,
                 add_pool: bool = False) -> dict:
    """Buat switch. Subnet kosong = /24 kosong berikutnya di blok yang ada. Subnet di luar semua blok
    hanya diterima dengan add_pool: subnet itu sekaligus menjadi blok baru Proxmox ini."""
    name = check_name(name)
    async with lock(label):
        inst = await get_instance(label)
        pools = pools_of(inst)
        nets = await instance_networks(label)
        if any(n["name"].lower() == name.lower() for n in nets):
            raise HTTPException(409, tr(f"Nama switch '{name}' sudah dipakai di Proxmox ini",
                                        f"The switch name '{name}' is already used on this Proxmox"))
        taken = [ipaddress.ip_network(n["cidr"]) for n in nets]
        new_block = None
        if (cidr or "").strip():
            net = parse_switch(cidr, taken)
            if block_of(net, pools) is None:
                if not add_pool:
                    listed = ", ".join(map(str, pools))
                    if pools:
                        where = tr(f"di luar blok alamat Proxmox ini ({listed})", f"is outside this Proxmox's address blocks ({listed})")
                    else:
                        where = tr("belum termasuk blok alamat mana pun", "is not inside any address block yet")
                    raise HTTPException(400, tr(f"Subnet {net} {where}. Pilih 'Tambahkan sebagai blok baru' "
                                                "untuk memakainya; host Proxmox perlu disiapkan ulang untuk blok itu",
                                                f"Subnet {net} {where}. Choose 'Add as a new address block' "
                                                "to use it; the Proxmox host must be prepared again for that block"))
                new_block = parse_pool(str(net))
                await _check_block(label, new_block, pools, absorb=False)
        else:
            if not pools:
                raise HTTPException(400, tr("Proxmox ini belum punya blok alamat. Isi subnet switch atau tambah blok alamat dulu",
                                            "This Proxmox has no address blocks yet. Enter a switch subnet or add an address block first"))
            net = next_free_subnet(pools, taken)
            if net is None:
                raise HTTPException(409, tr(f"Semua blok alamat sudah penuh ({', '.join(map(str, pools))}). "
                                            "Isi subnet di luar blok untuk menambah blok baru",
                                            f"Every address block is full ({', '.join(map(str, pools))}). "
                                            "Enter a subnet outside the blocks to add a new block"))
        gateway, zone = gateway_of(net), inst["sdn_zone"]
        client = await client_for(label)
        try:
            vnet = new_vnet_id({v.get("vnet") for v in await client.sdn_vnets()})
        except ProxmoxError as e:
            raise _sdn_error(e, zone)
        done = []
        try:
            await client.create_vnet(vnet, zone, name)
            done.append("vnet")
            await client.create_subnet(vnet, str(net), str(gateway), snat)
            done.append("subnet")
            await client.sdn_apply()
        except ProxmoxError as e:
            await _undo(client, vnet, subnet_id(zone, net), done)
            raise _sdn_error(e, zone)
        db = await get_pool()
        async with db.acquire() as conn:
            async with conn.transaction():
                if new_block:
                    await _save_pools(conn, label, pools + [new_block])
                row = await conn.fetchrow(
                    """INSERT INTO networks (instance, vnet, name, cidr, gateway, snat, created_by)
                       VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *""",
                    label, vnet, name, str(net), str(gateway), snat, username)
    return {**dict(row), "pool_added": str(new_block) if new_block else None}


async def _undo(client, vnet: str, sid: str, done: list) -> None:
    """Batalkan VNet/subnet yang sempat dibuat supaya tidak tertinggal sebagai perubahan pending."""
    if not done:
        return
    for step, call in (("subnet", lambda: client.delete_subnet(vnet, sid)), ("vnet", lambda: client.delete_vnet(vnet))):
        if step in done:
            try:
                await call()
            except ProxmoxError:
                pass
    try:
        await client.sdn_apply()
    except ProxmoxError:
        pass


async def update(network_id: int, name: str | None, snat: bool | None) -> dict:
    net = await get_network(network_id)
    async with lock(net["instance"]):
        inst = await get_instance(net["instance"])
        new_name = check_name(name) if name is not None else net["name"]
        if new_name.lower() != net["name"].lower():
            if any(n["name"].lower() == new_name.lower() for n in await instance_networks(net["instance"])):
                raise HTTPException(409, tr(f"Nama switch '{new_name}' sudah dipakai di Proxmox ini",
                                            f"The switch name '{new_name}' is already used on this Proxmox"))
        new_snat = net["snat"] if snat is None else bool(snat)
        client = await client_for(net["instance"])
        try:
            if new_name != net["name"]:
                await client.update_vnet(net["vnet"], new_name)
            if new_snat != net["snat"]:
                await client.update_subnet(net["vnet"], subnet_id(inst["sdn_zone"], ipaddress.ip_network(net["cidr"])), new_snat)
            if new_name != net["name"] or new_snat != net["snat"]:
                await client.sdn_apply()
        except ProxmoxError as e:
            raise _sdn_error(e, inst["sdn_zone"])
        db = await get_pool()
        async with db.acquire() as conn:
            row = await conn.fetchrow("UPDATE networks SET name = $2, snat = $3 WHERE id = $1 RETURNING *",
                                      network_id, new_name, new_snat)
    return dict(row)


async def delete(network_id: int) -> dict:
    net = await get_network(network_id)
    async with lock(net["instance"]):
        inst = await get_instance(net["instance"])
        client = await client_for(net["instance"])
        try:
            vms = await attached_vms(client, net["vnet"])
        except ProxmoxError as e:
            raise HTTPException(502, tr(f"Gagal memeriksa VM di switch ini: {e.detail[:150]}",
                                        f"Could not check the VMs on this switch: {e.detail[:150]}"))
        if vms:
            names = ", ".join(f"{v['name']} ({v['vmid']})" for v in vms[:5]) + (" …" if len(vms) > 5 else "")
            raise HTTPException(409, tr(f"Switch masih dipakai {len(vms)} VM: {names}. Hapus VM-nya atau pindahkan kartu jaringannya dulu",
                                        f"The switch is still used by {len(vms)} VMs: {names}. Delete the VMs or move their network cards first"))
        sid = subnet_id(inst["sdn_zone"], ipaddress.ip_network(net["cidr"]))
        try:
            for call in (lambda: client.delete_subnet(net["vnet"], sid), lambda: client.delete_vnet(net["vnet"])):
                try:
                    await call()
                except ProxmoxError as e:
                    if "does not exist" not in (e.detail or "") and "no such" not in (e.detail or "").lower():
                        raise
            await client.sdn_apply()
        except ProxmoxError as e:
            raise _sdn_error(e, inst["sdn_zone"])
        db = await get_pool()
        async with db.acquire() as conn:
            await conn.execute("DELETE FROM networks WHERE id = $1", network_id)
    return net


async def vm_settings(label: str, network_id: int, ip_cidr: str | None, dns: str | None) -> dict:
    """Bridge, IP, gateway, dan DNS untuk VM baru di switch ini. IP kosong = dibagikan otomatis. DNS bawaan
    adalah gateway switch: host Proxmox menjalankan penerus DNS di sana (ccd-net-setup.sh), karena jaringan
    kampus sering memblokir DNS publik dan DNS kampus berada di jaringan privat yang ditutup untuk switch."""
    net_row = await get_network(network_id)
    if net_row["instance"] != label:
        raise HTTPException(400, tr("Switch itu milik Proxmox lain",
                                    "That switch belongs to another Proxmox"))
    net = ipaddress.ip_network(net_row["cidr"])
    client = await client_for(label)
    used = await used_ips(client, label, net)
    if (ip_cidr or "").strip():
        text = ip_cidr.strip()
        try:
            ip = ipaddress.ip_interface(text if "/" in text else f"{text}/{net.prefixlen}").ip
        except ValueError:
            raise HTTPException(400, tr(f"IP {text} tidak valid", f"IP {text} is invalid"))
        if ip not in net or ip in (net.network_address, net.broadcast_address):
            raise HTTPException(400, tr(f"IP {ip} harus berada di {net}", f"IP {ip} must be inside {net}"))
        if str(ip) == net_row["gateway"]:
            raise HTTPException(400, tr(f"{ip} adalah gateway switch (host Proxmox)",
                                        f"{ip} is the switch gateway (the Proxmox host)"))
        if ip in used:
            raise HTTPException(409, tr(f"IP {ip} sudah dipakai VM lain",
                                        f"IP {ip} is already used by another VM"))
    else:
        ip = free_ip(net, used)
        if ip is None:
            raise HTTPException(409, tr(f"Switch '{net_row['name']}' sudah penuh",
                                        f"Switch '{net_row['name']}' is full"))
    return {"bridge": net_row["vnet"], "ip_cidr": f"{ip}/{net.prefixlen}", "gateway": net_row["gateway"],
            "dns": (dns or "").strip() or net_row["gateway"], "name": net_row["name"]}
