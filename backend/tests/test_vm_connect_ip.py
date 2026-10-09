"""
Connect ke VM yang IP-nya belum pernah diisi (VM DHCP, mis. dari VM massal tanpa switch): IP diambil dari
QEMU Guest Agent saat Connect, disimpan, lalu koneksi Guacamole dibuat. Proxmox dan Guacamole diganti tiruan.
"""
import asyncpg
import pytest

from services import guac_sync, proxmox_instances
from tests.conftest import DATABASE_URL, _run, auth

LABEL, NODE, VMID, VM_NAME = "labConnIp", "pve", "9301", "lab-ip-vm"
HOST = f"{LABEL}__{NODE}"
URL = f"/api/v1/ssh-creds/guac-url/{HOST}/{VMID}"


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


class FakePve:
    def __init__(self):
        self.ip = None

    async def get_guest_ip(self, node, vmid):
        return self.ip


@pytest.fixture
def env(monkeypatch):
    fake, synced = FakePve(), []

    async def get_client(label):
        return fake

    async def find_connection_id(name):
        return None

    async def sync_vm_connection(host_name, vm_name, vm_id, creds):
        synced.append(creds)
        return "77"

    async def no_op(*args, **kwargs):
        return None

    async def connection_url(host_name, vm_name, guac_public):
        return "https://guac.invalid/#/client/77"

    monkeypatch.setattr(proxmox_instances, "get_client", get_client)
    monkeypatch.setattr(guac_sync, "_find_connection_id", find_connection_id)
    monkeypatch.setattr(guac_sync, "sync_vm_connection", sync_vm_connection)
    monkeypatch.setattr(guac_sync, "grant_vm_to_all_admins", no_op)
    monkeypatch.setattr(guac_sync, "get_connection_url", connection_url)

    _run(_sql("DELETE FROM vm_credentials WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vms WHERE host_name = $1", HOST))
    _run(_sql("INSERT INTO vms (vm_id, host_name, vm_name, state) VALUES ($1, $2, $3, 'running')", VMID, HOST, VM_NAME))
    _run(_sql("INSERT INTO vm_credentials (vm_id, host_name, username, ssh_host) VALUES ($1, $2, 'mhs', '')", VMID, HOST))
    yield fake, synced
    _run(_sql("DELETE FROM vm_credentials WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vms WHERE host_name = $1", HOST))


def _stored_ip():
    return _run(_sql("SELECT ssh_host FROM vm_credentials WHERE vm_id = $1 AND host_name = $2", VMID, HOST))[0]["ssh_host"]


def test_connect_fills_missing_ip_from_guest_agent(client, superadmin_token, env):
    fake, synced = env
    fake.ip = "172.16.5.20"
    r = client.get(URL, headers=auth(superadmin_token))
    assert r.status_code == 200, r.text
    assert synced and synced[0]["ssh_host"] == "172.16.5.20"
    assert _stored_ip() == "172.16.5.20"


def test_connect_explains_when_ip_is_still_unknown(client, superadmin_token, env):
    fake, synced = env
    fake.ip = None                                   # VM mati atau agent belum aktif
    r = client.get(URL, headers=auth(superadmin_token))
    assert r.status_code == 409
    assert "Guest Agent" in r.json()["detail"]
    assert not synced
    assert _stored_ip() == ""


def test_ip_filled_by_admin_is_not_overwritten(client, superadmin_token, env):
    fake, synced = env
    fake.ip = "172.16.5.99"
    _run(_sql("UPDATE vm_credentials SET ssh_host = '10.0.0.8' WHERE vm_id = $1 AND host_name = $2", VMID, HOST))
    r = client.get(URL, headers=auth(superadmin_token))
    assert r.status_code == 200, r.text
    assert synced[0]["ssh_host"] == "10.0.0.8"
    assert _stored_ip() == "10.0.0.8"
