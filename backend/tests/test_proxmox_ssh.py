"""
SSH ke host Proxmox lewat Guacamole (sysadmin dan superadmin). Koneksinya tidak menyimpan kredensial:
Guacamole meminta username dan password setiap kali tersambung.
"""
import pytest

from services import guac_sync as gs
from tests.conftest import auth

P = "/api/v1/proxmox/instances"


@pytest.mark.parametrize("address,expected", [
    ("192.168.1.10:8006", "192.168.1.10"),
    ("192.168.1.10", "192.168.1.10"),
    ("pve.kampus.example:8006", "pve.kampus.example"),
    ("https://pve.kampus.example:8006/", "pve.kampus.example"),
    ("[fd00::1]:8006", "fd00::1"),
    ("fd00::1", "fd00::1"),
    ("  10.0.0.5:8006  ", "10.0.0.5"),
])
def test_host_only(address, expected):
    assert gs.host_only(address) == expected


def test_connection_params_never_carry_credentials():
    params = gs.build_host_ssh_params("10.0.0.5", 2222, "Asia/Makassar")
    assert params["hostname"] == "10.0.0.5" and params["port"] == "2222" and params["timezone"] == "Asia/Makassar"
    assert not {"username", "password", "private-key", "passphrase"} & set(params)      # Guacamole yang meminta
    # Namanya tidak bentrok dengan sinkronisasi VM mahasiswa ("HV/{instance}__{node}/...").
    assert gs.proxmox_host_connection_name("kampus") == "HV/kampus/PROXMOX-HOST"
    assert not gs.proxmox_host_connection_name("kampus").startswith("HV/kampus__")


@pytest.fixture
def fake_guac(monkeypatch):
    calls = {"sync": [], "grant_all": [], "grant": [], "delete": []}

    async def sync(label, hostname, port=22, timezone="Asia/Jakarta"):
        calls["sync"].append((label, hostname, port, timezone))
        return "77"

    async def grant_all(conn_id):
        calls["grant_all"].append(conn_id)

    async def grant(username, conn_id):
        calls["grant"].append((username, conn_id))
        return True

    async def url(name, base=""):
        return f"{base or '/guacamole'}/#/client/{name}"

    async def delete(name):
        calls["delete"].append(name)
        return True

    monkeypatch.setattr(gs, "sync_proxmox_host_connection", sync)
    monkeypatch.setattr(gs, "grant_vm_to_all_admins", grant_all)
    monkeypatch.setattr(gs, "grant_connection", grant)
    monkeypatch.setattr(gs, "get_connection_url_by_name", url)
    monkeypatch.setattr(gs, "delete_named_connection", delete)
    return calls


@pytest.fixture
def instance(client, superadmin_token):
    label = "tst_pve_ssh"
    client.delete(f"{P}/{label}", headers=auth(superadmin_token))
    r = client.post(P, headers=auth(superadmin_token), json={
        "label": label, "host": "10.9.9.9:8006", "token_id": "root@pam!ccd", "token_secret": "rahasia-uji"})
    assert r.status_code == 200, r.text
    yield label
    client.delete(f"{P}/{label}", headers=auth(superadmin_token))


def test_admins_get_a_url_and_it_is_audited(client, sysadmin_token, superadmin_token, instance, fake_guac):
    r = client.get(f"{P}/{instance}/ssh-url", headers=auth(sysadmin_token))
    assert r.status_code == 200, r.text
    assert r.json()["hostname"] == "10.9.9.9" and r.json()["port"] == 22
    assert r.json()["url"].endswith(f"#/client/HV/{instance}/PROXMOX-HOST")
    assert fake_guac["sync"][0][:3] == (instance, "10.9.9.9", 22)                  # port API 8006 tidak ikut
    assert fake_guac["grant_all"] == ["77"] and fake_guac["grant"] == [("tst_sysadmin", "77")]

    assert client.get(f"{P}/{instance}/ssh-url", params={"port": 2222}, headers=auth(superadmin_token)).json()["port"] == 2222
    assert fake_guac["sync"][1][2] == 2222
    log = client.get("/api/admin/audit-logs", params={"action": "PVE_HOST_SSH", "search": instance, "page_size": 5},
                     headers=auth(superadmin_token)).json()["items"]
    assert any("tst_sysadmin membuka SSH ke host Proxmox" in i["detail"] and "10.9.9.9:22" in i["detail"] for i in log)


def test_students_and_bad_requests_are_refused(client, student_token, sysadmin_token, instance, fake_guac):
    assert client.get(f"{P}/{instance}/ssh-url", headers=auth(student_token)).status_code == 403
    assert client.get(f"{P}/tidak_ada/ssh-url", headers=auth(sysadmin_token)).status_code == 404
    assert client.get(f"{P}/{instance}/ssh-url", params={"port": 70000}, headers=auth(sysadmin_token)).status_code == 422
    assert client.get(f"{P}/{instance}/ssh-url").status_code in (401, 403)
    assert fake_guac["sync"] == []                                                  # tidak ada koneksi dibuat untuk yang ditolak


def test_deleting_the_instance_removes_its_ssh_connection(client, superadmin_token, fake_guac):
    label = "tst_pve_ssh_del"
    client.delete(f"{P}/{label}", headers=auth(superadmin_token))
    client.post(P, headers=auth(superadmin_token), json={"label": label, "host": "10.9.9.6:8006", "token_id": "t", "token_secret": "s"})
    fake_guac["delete"].clear()
    assert client.delete(f"{P}/{label}", headers=auth(superadmin_token)).status_code == 200
    assert fake_guac["delete"] == [f"HV/{label}/PROXMOX-HOST"]
