"""
Switch (jaringan) CCD: blok alamat per Proxmox (bisa lebih dari satu), membuat/mengubah/menghapus switch
lewat SDN, dan VM baru di switch. Proxmox diganti tiruan yang meniru VNet, subnet, apply, dan pemeriksaan izinnya.
"""
import re
from pathlib import Path

import asyncpg
import pytest

from routers import proxmox as proxmox_router
from services import networks as nw
from services import proxmox_instances, proxmox_provision, vm_cleanup
from services.proxmox_client import ProxmoxError
from services.ssh_client import encrypt_secret
from tests.conftest import DATABASE_URL, _run, auth

LABEL, LABEL2, NODE = "labNet", "labNet2", "pve"
API = "/api/v1/networks"


class FakePve:
    def __init__(self, lan="172.16.111.2/24"):
        self.zones = [{"zone": "ccd", "type": "simple"}]
        self.privs = {"/sdn/zones/ccd": {"SDN.Allocate": 1, "SDN.Audit": 1, "SDN.Use": 1}, "/sdn": {"SDN.Allocate": 0}}
        self.vnets, self.vms, self.calls = {}, {}, []
        self.lans = [{"iface": "vmbr0", "type": "bridge", "cidr": lan}]
        self.fail_apply = False

    async def list_nodes(self):
        return [{"node": NODE}]

    async def node_networks(self, node):
        return self.lans

    async def list_vms(self, node):
        return [{"vmid": k, "name": v.get("name")} for k, v in self.vms.items()]

    async def get_vm_config(self, node, vmid):
        return self.vms[vmid]

    async def permissions(self, path):
        return self.privs.get(path, {})

    async def sdn_zones(self):
        return self.zones

    async def sdn_vnets(self):
        return [{"vnet": k, "zone": v["zone"]} for k, v in self.vnets.items()]

    async def create_vnet(self, vnet, zone, alias):
        if f"/sdn/zones/{zone}" not in self.privs:
            raise ProxmoxError(403, "Permission check failed (/sdn/zones/ccd, SDN.Allocate)")
        if zone not in {z["zone"] for z in self.zones}:
            raise ProxmoxError(400, f"zone '{zone}' does not exist")
        self.vnets[vnet] = {"zone": zone, "alias": alias, "subnets": {}}
        self.calls.append(("create_vnet", vnet, zone, alias))

    async def update_vnet(self, vnet, alias):
        self.vnets[vnet]["alias"] = alias
        self.calls.append(("update_vnet", vnet, alias))

    async def delete_vnet(self, vnet):
        self.vnets.pop(vnet, None)
        self.calls.append(("delete_vnet", vnet))

    async def create_subnet(self, vnet, cidr, gateway, snat):
        sid = f"{self.vnets[vnet]['zone']}-{cidr.replace('/', '-')}"
        self.vnets[vnet]["subnets"][sid] = {"gateway": gateway, "snat": snat}
        self.calls.append(("create_subnet", vnet, cidr, gateway, snat))

    async def update_subnet(self, vnet, sid, snat):
        self.vnets[vnet]["subnets"][sid]["snat"] = snat
        self.calls.append(("update_subnet", vnet, sid, snat))

    async def delete_subnet(self, vnet, sid):
        self.vnets[vnet]["subnets"].pop(sid)
        self.calls.append(("delete_subnet", vnet, sid))

    async def sdn_apply(self):
        self.calls.append(("apply",))
        if self.fail_apply:
            raise ProxmoxError(500, "ifreload failed")


async def _exec(*statements):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for sql, *args in statements:
            await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _val(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


def _reset():
    _run(_exec(
        ("DELETE FROM proxmox_instances WHERE label IN ($1, $2)", LABEL, LABEL2),     # switch ikut terhapus
        ("DELETE FROM vm_credentials WHERE host_name = $1", f"{LABEL}__{NODE}"),
        ("DELETE FROM vms WHERE host_name = $1", f"{LABEL}__{NODE}"),
    ))


@pytest.fixture
def pve(monkeypatch):
    fakes = {LABEL: FakePve(), LABEL2: FakePve(lan="172.16.222.2/24")}

    async def get_client(label):
        if label not in fakes:
            raise ValueError(f"Proxmox instance '{label}' tidak ditemukan")
        return fakes[label]

    async def reachable(ip, port=8006, timeout=3.0):
        return ip.startswith("10.111.")

    monkeypatch.setattr(proxmox_instances, "get_client", get_client)
    monkeypatch.setattr(nw, "reachable", reachable)
    _reset()
    for label in (LABEL, LABEL2):
        _run(_exec(("INSERT INTO proxmox_instances (label, host, token_id, token_secret_enc) VALUES ($1, 'fake:8006', 'root@pam!ccd', $2)",
                    label, encrypt_secret("rahasia"))))
    yield fakes[LABEL], fakes[LABEL2]
    _reset()


def _pool(client, token, pool, label=LABEL):
    return client.post(f"{API}/instances/{label}/pools", headers=auth(token), json={"cidr": pool})


def _unpool(client, token, pool, label=LABEL):
    return client.delete(f"{API}/instances/{label}/pools", headers=auth(token), params={"cidr": pool})


def _create(client, token, name, **extra):
    return client.post(API, headers=auth(token), json={"instance": LABEL, "name": name, **extra})


def _instance(client, token, label=LABEL):
    return next(i for i in client.get(API, headers=auth(token)).json()["instances"] if i["label"] == label)


# ── Blok alamat ──────────────────────────────────────────────────────────────

def test_only_admin(client, student_token, pve):
    assert client.get(API, headers=auth(student_token)).status_code == 403
    assert _pool(client, student_token, "10.111.0.0/16").status_code == 403
    assert _unpool(client, student_token, "10.111.0.0/16").status_code == 403
    assert _create(client, student_token, "x").status_code == 403


@pytest.mark.parametrize("pool,code", [
    ("8.8.8.0/24", 400),         # bukan alamat privat
    ("10.111.5.0/16", 400),      # bagian host tidak nol
    ("10.111.0.0/30", 400),      # terlalu kecil untuk blok
    ("172.20.0.0/16", 400),      # rentang Docker di VPS
    ("172.16.0.0/16", 409),      # memuat LAN vmbr0 Proxmox ini (172.16.111.0/24)
    ("10.111.0.0/16", 200),
])
def test_pool_validation(client, sysadmin_token, pve, pool, code):
    assert _pool(client, sysadmin_token, pool).status_code == code


def test_pool_cannot_overlap_vps_networks(client, sysadmin_token, pve, monkeypatch):
    """Jaringan VPS dicatat setup.sh di HOST_NETWORKS; blok yang bentrok membuat VPS kehilangan jalur."""
    monkeypatch.setenv("HOST_NETWORKS", "eth1=10.10.30.18/20,eth1=10.10.16.0/20,docker0=172.17.0.1/16,rusak")
    r = _pool(client, sysadmin_token, "10.10.0.0/16")
    assert r.status_code == 409 and "10.10.16.0/20" in r.json()["detail"] and "eth1" in r.json()["detail"]
    assert _pool(client, sysadmin_token, "10.111.0.0/16").status_code == 200


def test_pool_cannot_overlap_other_proxmox(client, sysadmin_token, pve):
    assert _pool(client, sysadmin_token, "10.111.0.0/16").status_code == 200
    r = _pool(client, sysadmin_token, "10.111.128.0/17", label=LABEL2)
    assert r.status_code == 409 and LABEL in r.json()["detail"]
    assert _pool(client, sysadmin_token, "10.222.0.0/16", label=LABEL2).status_code == 200
    inst = _instance(client, sysadmin_token)
    assert inst["pools"] == [{"cidr": "10.111.0.0/16", "switches": 0, "full": False}]
    assert inst["suggested_cidr"] == "10.111.1.0/24" and inst["token_id"] == "root@pam!ccd"


def test_several_blocks_added_manually(client, sysadmin_token, pve):
    """Blok kecil ditambah satu per satu, hanya rentang yang memang dipakai (mis. 192.168.111.0/24 lalu .112)."""
    fake, _ = pve
    assert _create(client, sysadmin_token, "Kelas A").status_code == 400            # belum ada blok sama sekali
    assert _pool(client, sysadmin_token, "192.168.111.0/24").status_code == 200
    a = _create(client, sysadmin_token, "Kelas A").json()
    assert a["cidr"] == "192.168.111.0/24" and a["pool_added"] is None              # blok /24 dipakai utuh
    r = _create(client, sysadmin_token, "Kelas B")
    assert r.status_code == 409 and "penuh" in r.json()["detail"]

    # Subnet di luar blok ditolak, kecuali admin memilih menjadikannya blok baru.
    calls = len(fake.calls)
    r = _create(client, sysadmin_token, "Kelas B", cidr="192.168.112.0/24")
    assert r.status_code == 400 and "blok baru" in r.json()["detail"] and len(fake.calls) == calls
    r = _create(client, sysadmin_token, "Kelas B", cidr="192.168.112.0/24", add_pool=True)
    assert r.status_code == 200, r.text
    assert r.json()["pool_added"] == "192.168.112.0/24" and r.json()["gateway"] == "192.168.112.1"
    inst = _instance(client, sysadmin_token)
    assert inst["pools"] == [{"cidr": "192.168.111.0/24", "switches": 1, "full": True},
                             {"cidr": "192.168.112.0/24", "switches": 1, "full": True}]
    assert inst["suggested_cidr"] is None
    check = client.get(f"{API}/instances/{LABEL}/check", headers=auth(sysadmin_token)).json()
    assert check["pools"] == ["192.168.111.0/24", "192.168.112.0/24"]

    # Blok yang tumpang tindih dengan blok sendiri ditolak; blok yang memuatnya menggantikannya.
    assert _pool(client, sysadmin_token, "192.168.111.0/24").status_code == 409
    r = _pool(client, sysadmin_token, "192.168.111.128/25")
    assert r.status_code == 409 and "sudah termasuk" in r.json()["detail"]
    r = _pool(client, sysadmin_token, "192.168.96.0/20")
    assert r.status_code == 200 and r.json()["replaced"] == ["192.168.111.0/24"]
    assert r.json()["pools"] == ["192.168.112.0/24", "192.168.96.0/20"]
    assert _create(client, sysadmin_token, "Kelas C").json()["cidr"] == "192.168.97.0/24"

    # Blok hanya bisa dihapus kalau tidak ada switch di dalamnya.
    r = _unpool(client, sysadmin_token, "192.168.112.0/24")
    assert r.status_code == 409 and "Kelas B" in r.json()["detail"]
    assert _unpool(client, sysadmin_token, "192.168.113.0/24").status_code == 404
    b = next(n for n in inst["networks"] if n["name"] == "Kelas B")
    assert client.delete(f"{API}/{b['id']}", headers=auth(sysadmin_token)).status_code == 200
    r = _unpool(client, sysadmin_token, "192.168.112.0/24")
    assert r.status_code == 200 and r.json()["pools"] == ["192.168.96.0/20"]


def test_switch_as_new_block_is_validated(client, sysadmin_token, pve):
    fake, _ = pve
    assert _pool(client, sysadmin_token, "10.222.0.0/16", label=LABEL2).status_code == 200
    calls = len(fake.calls)
    for cidr, code in (("172.16.111.0/24", 409),      # LAN vmbr0 Proxmox ini
                       ("10.222.5.0/24", 409),        # blok Proxmox lain
                       ("172.20.1.0/24", 400),        # rentang Docker di VPS
                       ("8.8.8.0/24", 400)):          # bukan alamat privat
        r = _create(client, sysadmin_token, "Lab", cidr=cidr, add_pool=True)
        assert r.status_code == code, (cidr, r.text)
    assert len(fake.calls) == calls                                                   # Proxmox tidak disentuh
    r = _create(client, sysadmin_token, "Lab", cidr="10.50.7.0/26", add_pool=True)    # switch kecil jadi blok sendiri
    assert r.status_code == 200 and r.json()["pool_added"] == "10.50.7.0/26"
    # Subnet yang memuat switch yang sudah ada ditolak.
    r = _create(client, sysadmin_token, "Lab 2", cidr="10.50.0.0/20", add_pool=True)
    assert r.status_code == 409


def test_old_single_block_is_migrated(pve):
    """V011 memindahkan net_pool (satu blok) ke net_pools."""
    sql = (Path(__file__).resolve().parent.parent / "migrations" / "V011__network_pools.sql").read_text()
    _run(_exec(("UPDATE proxmox_instances SET net_pool = '10.111.0.0/16', net_pools = '{}' WHERE label = $1", LABEL)))
    _run(_exec((sql,)))
    assert _run(_val("SELECT net_pools FROM proxmox_instances WHERE label = $1", LABEL)) == ["10.111.0.0/16"]
    assert _run(_val("SELECT cardinality(net_pools) FROM proxmox_instances WHERE label = $1", LABEL2)) == 0


# ── Membuat, mengubah, menghapus switch ──────────────────────────────────────

def test_create_switches(client, sysadmin_token, pve):
    fake, _ = pve
    assert _create(client, sysadmin_token, "Kelas A").status_code == 400            # belum ada blok alamat
    _pool(client, sysadmin_token, "10.111.0.0/16")

    r = _create(client, sysadmin_token, "Kelas A")
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["cidr"], a["gateway"], a["snat"]) == ("10.111.1.0/24", "10.111.1.1", True)
    assert re.fullmatch(r"ccd[a-z0-9]{5}", a["vnet"])
    assert fake.calls == [("create_vnet", a["vnet"], "ccd", "Kelas A"),
                          ("create_subnet", a["vnet"], "10.111.1.0/24", "10.111.1.1", True), ("apply",)]

    assert _create(client, sysadmin_token, "Kelas B").json()["cidr"] == "10.111.2.0/24"
    assert _create(client, sysadmin_token, "kelas a").status_code == 409                       # nama kembar
    assert _create(client, sysadmin_token, "Lab", cidr="10.111.2.128/25").status_code == 409   # bertabrakan
    assert _create(client, sysadmin_token, "Lab", cidr="10.112.0.0/24").status_code == 400     # di luar blok
    assert _create(client, sysadmin_token, "Lab", cidr="10.111.0.0/19").status_code == 400     # switch terlalu besar
    assert _create(client, sysadmin_token, "Lab; rm -rf /").status_code == 400                 # nama tidak valid
    r = _create(client, sysadmin_token, "Lab Tertutup", cidr="10.111.10.0/24", snat=False)
    assert r.status_code == 200 and r.json()["snat"] is False

    inst = _instance(client, sysadmin_token)
    assert [n["name"] for n in inst["networks"]] == ["Kelas A", "Kelas B", "Lab Tertutup"]
    assert inst["suggested_cidr"] == "10.111.3.0/24"


def test_setup_problems_are_explained(client, sysadmin_token, pve):
    fake, _ = pve
    _pool(client, sysadmin_token, "10.111.0.0/16")
    fake.zones = []
    r = _create(client, sysadmin_token, "Kelas A")
    assert r.status_code == 409 and "ccd-net-setup.sh" in r.json()["detail"]
    fake.zones, fake.privs = [{"zone": "ccd", "type": "simple"}], {}
    r = _create(client, sysadmin_token, "Kelas A")
    assert r.status_code == 403 and "izin SDN" in r.json()["detail"]
    check = client.get(f"{API}/instances/{LABEL}/check", headers=auth(sysadmin_token)).json()
    assert check["zone_ok"] and not check["perm_zone"] and not check["perm_apply"] and not check["ready"]
    assert _run(_val("SELECT COUNT(*) FROM networks WHERE instance = $1", LABEL)) == 0


def test_failed_apply_is_rolled_back(client, sysadmin_token, pve):
    fake, _ = pve
    _pool(client, sysadmin_token, "10.111.0.0/16")
    fake.fail_apply = True
    r = _create(client, sysadmin_token, "Kelas A")
    assert r.status_code == 502
    assert fake.vnets == {}                                         # VNet dan subnet pending dibatalkan
    assert [c[0] for c in fake.calls][-3:] == ["delete_subnet", "delete_vnet", "apply"]
    assert _run(_val("SELECT COUNT(*) FROM networks WHERE instance = $1", LABEL)) == 0


def test_update_and_delete(client, sysadmin_token, pve):
    fake, _ = pve
    _pool(client, sysadmin_token, "10.111.0.0/16")
    a = _create(client, sysadmin_token, "Kelas A").json()
    _create(client, sysadmin_token, "Kelas B")

    assert client.patch(f"{API}/{a['id']}", headers=auth(sysadmin_token), json={"name": "Kelas B"}).status_code == 409
    r = client.patch(f"{API}/{a['id']}", headers=auth(sysadmin_token), json={"name": "Kelas A Pagi", "snat": False})
    assert r.status_code == 200 and (r.json()["name"], r.json()["snat"]) == ("Kelas A Pagi", False)
    assert ("update_vnet", a["vnet"], "Kelas A Pagi") in fake.calls
    assert ("update_subnet", a["vnet"], "ccd-10.111.1.0-24", False) in fake.calls

    fake.vms[701] = {"name": "router-a", "net0": f"virtio=AA:BB:CC:00:00:01,bridge={a['vnet']},firewall=1"}
    assert client.get(f"{API}/{a['id']}/vms", headers=auth(sysadmin_token)).json()[0]["name"] == "router-a"
    r = client.delete(f"{API}/{a['id']}", headers=auth(sysadmin_token))
    assert r.status_code == 409 and "router-a" in r.json()["detail"]

    del fake.vms[701]
    assert client.delete(f"{API}/{a['id']}", headers=auth(sysadmin_token)).status_code == 200
    assert a["vnet"] not in fake.vnets and fake.calls[-1] == ("apply",)
    assert [n["name"] for n in _instance(client, sysadmin_token)["networks"]] == ["Kelas B"]


def test_check_reports_ready_and_reachability(client, sysadmin_token, pve):
    _pool(client, sysadmin_token, "10.111.0.0/16")
    a = _create(client, sysadmin_token, "Kelas A").json()
    check = client.get(f"{API}/instances/{LABEL}/check", headers=auth(sysadmin_token)).json()
    assert check["ready"] and check["zone_ok"] and check["perm_zone"] and check["perm_apply"]
    assert check["reachable"] == {str(a["id"]): True}


def test_setup_script_is_public(client):
    r = client.get(f"{API}/setup-script")
    assert r.status_code == 200 and "fib daddr . iif oif missing" in r.text and "--advertise-routes" in r.text
    assert "# ccd-pools: $POOL" in r.text and "elements = { ${POOL//,/, } }" in r.text   # beberapa blok
    assert "interface=${VNET_PREFIX}*" in r.text and "dport 53 accept" in r.text      # penerus DNS hanya di bridge switch


# ── VM di switch ─────────────────────────────────────────────────────────────

def test_free_ip_skips_gateway_and_used(client, sysadmin_token, pve):
    fake, _ = pve
    _pool(client, sysadmin_token, "10.111.0.0/16")
    a = _create(client, sysadmin_token, "Kelas A").json()
    fake.vms[702] = {"name": "pc-1", "net0": f"virtio,bridge={a['vnet']}", "ipconfig0": "ip=10.111.1.2/24,gw=10.111.1.1"}
    _run(_exec(("INSERT INTO vm_credentials (vm_id, host_name, ssh_host, username) VALUES ('703', $1, '10.111.1.3', 'u')",
                f"{LABEL}__{NODE}")))
    r = client.get(f"{API}/{a['id']}/free-ip", headers=auth(sysadmin_token)).json()
    assert (r["ip_cidr"], r["gateway"], r["dns"], r["bridge"]) == ("10.111.1.4/24", "10.111.1.1", "10.111.1.1", a["vnet"])


def test_create_vm_on_switch(client, sysadmin_token, pve, monkeypatch):
    _pool(client, sysadmin_token, "10.111.0.0/16")
    a = _create(client, sysadmin_token, "Kelas A").json()
    captured = {}

    async def create_from_template(client_, node, **kw):
        captured.update(kw)
        return {"vmid": 990, "name": kw["name"], "static_ip": kw["ip_cidr"].split("/")[0], "os_type": "linux",
                "started": False, "clone": "linked"}

    async def no_op(*args, **kwargs):
        return None

    monkeypatch.setattr(proxmox_provision, "create_from_template", create_from_template)
    monkeypatch.setattr(proxmox_router, "save_vm_credentials", no_op)
    monkeypatch.setattr(vm_cleanup, "remove_guac_connections", no_op)
    url = f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/vms"
    body = {"template_vmid": 100, "name": "pc-kelas-a", "username": "siswa", "password": "Rahasia-123", "network_id": a["id"]}

    assert client.post(url, headers=auth(sysadmin_token), json={**body, "ip_mode": "dhcp"}).status_code == 400
    r = client.post(url, headers=auth(sysadmin_token), json=body)
    assert r.status_code == 200, r.text
    assert r.json()["switch"] == "Kelas A"
    assert (captured["bridge"], captured["ip_cidr"], captured["gateway"], captured["dns"]) == \
        (a["vnet"], "10.111.1.2/24", "10.111.1.1", "10.111.1.1")              # DNS = gateway (penerus di host)

    r = client.post(url, headers=auth(sysadmin_token), json={**body, "ip_cidr": "10.111.1.1"})
    assert r.status_code == 400 and "gateway" in r.json()["detail"]
    assert client.post(url, headers=auth(sysadmin_token), json={**body, "ip_cidr": "10.111.7.5"}).status_code == 400
