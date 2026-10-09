"""
Tingkat akses VM untuk student: 'full' (Connect + Open Web) dan 'web' (hanya lihat status, Open Web, dan
Helpdesk). Yang diuji: 'web' ditolak di semua jalur Connect, kredensial, power, dan snapshot, tetap boleh
Open Web dan Helpdesk, jalur 'full' menang bila VM terdaftar lewat dua cara, dan koneksi Guacamole tidak
diberikan untuk VM 'Hanya Open Web'. Proxmox dan Guacamole diganti tiruan.
"""
import time

import asyncpg
import pytest

from services import assignments, guac_sync
from tests.conftest import DATABASE_URL, _run, auth

LABEL, NODE, VMID, VM_NAME, IP = "labLevel", "pve", "9501", "lab-level-vm", "10.9.9.9"
HOST = f"{LABEL}__{NODE}"
STUDENT = "tst_student"


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def _uid():
    return _run(_sql("SELECT id FROM users WHERE username = $1", STUDENT))[0]["id"]


def _clean():
    uid = _uid()
    _run(_sql("DELETE FROM vm_assignments WHERE user_id = $1", uid))
    _run(_sql("DELETE FROM group_members WHERE user_id = $1", uid))
    _run(_sql("DELETE FROM group_vm_access WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM groups WHERE name LIKE 'tst_lvl_%'"))
    _run(_sql("DELETE FROM vm_metadata WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vm_credentials WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vms WHERE host_name = $1", HOST))


@pytest.fixture
def env(student_token, monkeypatch):
    synced = []

    async def fake_retry(fn, *args, **kwargs):
        synced.append((getattr(fn, "__name__", ""), args))
        return None

    async def find_connection_id(name):
        return "77"

    async def connection_url(host_name, vm_name, guac_public):
        return "https://guac.invalid/#/client/77"

    async def no_op(*args, **kwargs):
        return True

    monkeypatch.setattr(assignments, "guac_retry", fake_retry)
    monkeypatch.setattr(guac_sync, "_find_connection_id", find_connection_id)
    monkeypatch.setattr(guac_sync, "get_connection_url", connection_url)
    monkeypatch.setattr(guac_sync, "grant_connection", no_op)
    _clean()
    _run(_sql("INSERT INTO vms (vm_id, host_name, vm_name) VALUES ($1, $2, $3)", VMID, HOST, VM_NAME))
    _run(_sql("INSERT INTO vm_credentials (vm_id, host_name, username, ssh_host) VALUES ($1, $2, 'mhs', $3)",
              VMID, HOST, IP))
    yield synced
    _clean()


def _sync_names(synced, count):
    """Nama VM pada sinkronisasi Guacamole ke-`count`. Sinkronisasi berjalan sebagai tugas latar belakang
    setelah permintaan selesai, jadi ditunggu dulu (di mesin yang lambat ia bisa terlambat)."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        calls = [a for fn, a in synced if fn == "sync_vm_assignments"]
        if len(calls) >= count:
            return calls[count - 1][2]
        time.sleep(0.05)
    raise AssertionError(f"sinkronisasi Guacamole ke-{count} tidak pernah berjalan")


def _direct(access):
    _run(_sql("INSERT INTO vm_assignments (user_id, vm_id, host_name, vm_name, access) VALUES ($1, $2, $3, $4, $5)",
              _uid(), VMID, HOST, VM_NAME, access))


def _group(access):
    gid = _run(_sql("INSERT INTO groups (name) VALUES ('tst_lvl_kelas') RETURNING id"))[0]["id"]
    _run(_sql("INSERT INTO group_members (group_id, user_id) VALUES ($1, $2)", gid, _uid()))
    _run(_sql("INSERT INTO group_vm_access (group_id, vm_id, host_name, access) VALUES ($1, $2, $3, $4)",
              gid, VMID, HOST, access))
    return gid


GUAC = f"/api/v1/ssh-creds/guac-url/{HOST}/{VMID}"
CRED = f"/api/v1/ssh-creds/my-vm-cred/{HOST}/{VMID}"
VM = f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/vms/{VMID}"


def test_web_only_student_cannot_connect_or_get_credentials(client, student_token, env):
    _direct("web")
    for url in (GUAC, CRED):
        r = client.get(url, headers=auth(student_token))
        assert r.status_code == 403, url
        assert "Open Web" in r.json()["detail"]


def test_web_only_student_cannot_power_or_snapshot(client, student_token, env):
    _direct("web")
    h = auth(student_token)
    assert client.post(f"{VM}/action", headers=h, json={"action": "stop"}).status_code == 403
    assert client.get(f"{VM}/snapshots", headers=h).status_code == 403
    assert client.post(f"{VM}/snapshots", headers=h, json={"snapname": "a"}).status_code == 403
    assert client.post(f"{VM}/snapshots/a/rollback", headers=h).status_code == 403


def test_web_only_student_can_still_open_web_and_report(client, student_token, env):
    _direct("web")
    h = auth(student_token)
    assert client.post("/api/v1/openweb/ticket", headers=h, json={"url": f"http://{IP}/"}).status_code == 200
    r = client.post("/api/tickets", headers=h, json={
        "title": "Web simulasi tidak muncul", "category": "REMOTE_ISSUE", "description": "uji",
        "vm_id": VMID, "host_name": HOST, "vm_snapshot": {"vm_name": VM_NAME, "state": "running"}})
    assert r.status_code == 200, r.text
    ids = client.get(f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/my-assigned-vmids", headers=h).json()
    assert VMID in ids["vmids"] and ids["web_only"] == [VMID]


def test_full_student_still_connects(client, student_token, env):
    _direct("full")
    assert client.get(CRED, headers=auth(student_token)).status_code == 200
    r = client.get(GUAC, headers=auth(student_token))
    assert r.status_code == 200, r.text
    ids = client.get(f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/my-assigned-vmids", headers=auth(student_token)).json()
    assert ids["web_only"] == []


def test_full_access_wins_when_two_paths_disagree(client, student_token, env):
    _direct("web")
    _group("full")
    assert client.get(CRED, headers=auth(student_token)).status_code == 200


def test_group_web_access_is_web_only(client, student_token, env):
    _group("web")
    assert client.get(GUAC, headers=auth(student_token)).status_code == 403
    assert client.post("/api/v1/openweb/ticket", headers=auth(student_token),
                       json={"url": f"http://{IP}/"}).status_code == 200


def test_student_without_assignment_is_refused(client, student_token, env):
    assert client.get(GUAC, headers=auth(student_token)).status_code == 403
    assert client.post("/api/v1/openweb/ticket", headers=auth(student_token),
                       json={"url": f"http://{IP}/"}).status_code == 403


def test_assign_endpoint_stores_level_and_skips_guacamole_grant(client, superadmin_token, student_token, env):
    synced = env
    h = auth(superadmin_token)
    body = {"user_id": _uid(), "vm_id": VMID, "host_name": HOST, "vm_name": VM_NAME}
    assert client.post("/api/v1/users/vm-assignments", headers=h, json={**body, "access": "bogus"}).status_code == 400
    assert client.post("/api/v1/users/vm-assignments", headers=h,
                       json={**body, "access": "web", "os_account_id": 1}).status_code == 400
    assert client.post("/api/v1/users/vm-assignments", headers=h, json={**body, "access": "web"}).status_code == 200
    row = client.get(f"/api/v1/users/{_uid()}/vm-assignments", headers=h).json()[0]
    assert row["access"] == "web"
    assert VM_NAME not in _sync_names(synced, 1)
    # kembali ke akses penuh: VM masuk lagi ke koneksi yang diberikan
    assert client.post("/api/v1/users/vm-assignments", headers=h, json=body).status_code == 200
    assert VM_NAME in _sync_names(synced, 2)


def test_group_endpoint_web_ignores_credentials(client, superadmin_token, student_token, env):
    h = auth(superadmin_token)
    gid = client.post("/api/v1/groups", headers=h, json={"name": "tst_lvl_kelas"}).json()["id"]
    r = client.post(f"/api/v1/groups/{gid}/vms", headers=h, json={
        "vm_id": VMID, "host_name": HOST, "access": "web", "auth_mode": "credentials",
        "os_username": "mhs", "os_password": "rahasia-123"})
    assert r.status_code == 201, r.text
    assert r.json()["access"] == "web" and r.json()["auth_mode"] == "mandiri" and not r.json()["os_username"]
    listed = client.get(f"/api/v1/groups/{gid}/vms", headers=h).json()[0]
    assert listed["access"] == "web" and listed["has_password"] is False
    assert client.put(f"/api/v1/groups/{gid}/vms", headers=h, json={
        "vm_id": VMID, "host_name": HOST, "access": "oops"}).status_code == 400
    r = client.put(f"/api/v1/groups/{gid}/vms", headers=h, json={
        "vm_id": VMID, "host_name": HOST, "access": "full"})
    assert r.status_code == 200 and r.json()["access"] == "full"


def test_vm_login_in_metadata_is_hidden_from_students(client, superadmin_token, student_token, env):
    h = auth(superadmin_token)
    r = client.put(f"/api/v1/vm-metadata/{HOST}/{VMID}", headers=h,
                   json={"description": "simulasi", "vm_username": "root", "vm_password": "rahasia-123"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/v1/vm-metadata/{HOST}/{VMID}", headers=h).json()["vm_password"] == "rahasia-123"
    for url in (f"/api/v1/vm-metadata/{HOST}/{VMID}", f"/api/v1/vm-metadata/{HOST}"):
        data = client.get(url, headers=auth(student_token)).json()
        row = data if isinstance(data, dict) else data[0]
        assert row["vm_password"] == "" and row["vm_username"] == ""
        assert row["description"] == "simulasi"
