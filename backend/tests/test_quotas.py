"""
Kuota per mahasiswa: bawaan, grup, dan pengguna; dicek saat VM di-assign.
"""
import uuid

import asyncpg
import pytest

from services import quotas
from tests.conftest import DATABASE_URL, _run, auth

Q = "/api/v1/quotas"
U = "/api/v1/users"
HOST = "labQuota__pve"


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


@pytest.fixture
def lab(client, superadmin_token):
    """Satu mahasiswa dan tiga VM (2 vCPU / 2048 MB / 20 GB)."""
    name = f"tst_qt_{uuid.uuid4().hex[:6]}"
    r = client.post(U, headers=auth(superadmin_token), json={"username": name, "password": "Rahasia123!", "full_name": "Uji Kuota", "role": "student"})
    uid = r.json()["id"]
    vms = [str(8100 + i) for i in range(3)]
    for v in vms:
        _run(_sql("INSERT INTO vms (vm_id, host_name, vm_name, cores, memory_mb, disk_gb) VALUES ($1, $2, $3, 2, 2048, 20) ON CONFLICT (vm_id, host_name) DO UPDATE SET cores = 2, memory_mb = 2048, disk_gb = 20",
                  v, HOST, f"qvm-{v}"))
    yield {"uid": uid, "name": name, "vms": vms}
    client.put(f"{Q}/defaults", headers=auth(superadmin_token), json={})
    _run(_sql("DELETE FROM vm_assignments WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vms WHERE host_name = $1", HOST))
    client.delete(f"{U}/{uid}", headers=auth(superadmin_token))


def _assign(client, token, uid, vm):
    return client.post(f"{U}/vm-assignments", headers=auth(token), json={"user_id": uid, "vm_id": vm, "host_name": HOST})


def test_no_quota_means_unlimited(client, sysadmin_token, lab):
    for v in lab["vms"]:
        assert _assign(client, sysadmin_token, lab["uid"], v).status_code == 200


def test_vm_count_limit_from_defaults(client, superadmin_token, sysadmin_token, lab):
    assert client.put(f"{Q}/defaults", headers=auth(superadmin_token), json={"max_vms": 2}).json()["max_vms"] == 2
    assert _assign(client, sysadmin_token, lab["uid"], lab["vms"][0]).status_code == 200
    assert _assign(client, sysadmin_token, lab["uid"], lab["vms"][1]).status_code == 200
    r = _assign(client, sysadmin_token, lab["uid"], lab["vms"][2])
    assert r.status_code == 409 and "jumlah VM" in r.json()["detail"]
    # meng-assign ulang VM yang sudah dimiliki tidak dihitung dua kali
    assert _assign(client, sysadmin_token, lab["uid"], lab["vms"][0]).status_code == 200


def test_resource_limits(client, sysadmin_token, lab):
    client.put(f"{Q}/users/{lab['uid']}", headers=auth(sysadmin_token), json={"max_vcpu": 4})
    assert _assign(client, sysadmin_token, lab["uid"], lab["vms"][0]).status_code == 200
    assert _assign(client, sysadmin_token, lab["uid"], lab["vms"][1]).status_code == 200
    r = _assign(client, sysadmin_token, lab["uid"], lab["vms"][2])
    assert r.status_code == 409 and "vCPU" in r.json()["detail"]
    client.put(f"{Q}/users/{lab['uid']}", headers=auth(sysadmin_token), json={"max_vcpu": 0, "max_ram_mb": 4096})
    r = _assign(client, sysadmin_token, lab["uid"], lab["vms"][2])
    assert r.status_code == 409 and "RAM" in r.json()["detail"]
    client.put(f"{Q}/users/{lab['uid']}", headers=auth(sysadmin_token), json={"max_ram_mb": 0, "max_disk_gb": 40})
    assert _assign(client, sysadmin_token, lab["uid"], lab["vms"][2]).status_code == 409


def test_layer_precedence(client, superadmin_token, sysadmin_token, lab):
    client.put(f"{Q}/defaults", headers=auth(superadmin_token), json={"max_vms": 1})
    assert quotas_eff(client, lab["uid"])["limits"]["max_vms"] == 1 and quotas_eff(client, lab["uid"])["source"]["max_vms"] == "default"

    g = client.post("/api/v1/groups", headers=auth(sysadmin_token), json={"name": f"tst_qg_{uuid.uuid4().hex[:4]}"})
    gid = g.json()["id"]
    try:
        assert client.post(f"/api/v1/groups/{gid}/members", headers=auth(sysadmin_token), json={"user_id": lab["uid"]}).status_code == 201
        client.put(f"{Q}/groups/{gid}", headers=auth(sysadmin_token), json={"max_vms": 3})
        eff = quotas_eff(client, lab["uid"])
        assert eff["limits"]["max_vms"] == 3 and eff["source"]["max_vms"] == "group"
        # kuota pengguna menimpa grup; 0 = tanpa batas
        client.put(f"{Q}/users/{lab['uid']}", headers=auth(sysadmin_token), json={"max_vms": 0})
        eff = quotas_eff(client, lab["uid"])
        assert eff["limits"]["max_vms"] == 0 and eff["source"]["max_vms"] == "user"
        # mengosongkan semua = hapus penimpaan
        client.put(f"{Q}/users/{lab['uid']}", headers=auth(sysadmin_token), json={})
        assert quotas_eff(client, lab["uid"])["source"]["max_vms"] == "group"
    finally:
        client.delete(f"/api/v1/groups/{gid}", headers=auth(sysadmin_token))


def quotas_eff(client, uid):
    return client.portal.call(quotas.effective, uid)


def test_usage_and_me(client, sysadmin_token, lab):
    _assign(client, sysadmin_token, lab["uid"], lab["vms"][0])
    _assign(client, sysadmin_token, lab["uid"], lab["vms"][1])
    r = client.get(f"{Q}/users/{lab['uid']}", headers=auth(sysadmin_token)).json()
    assert r["usage"] == {"vms": 2, "vcpu": 4, "ram_mb": 4096, "disk_gb": 40}
    login = client.post(f"{U}/login", json={"username": lab["name"], "password": "Rahasia123!"}).json()["access_token"]
    me = client.get(f"{Q}/me", headers=auth(login)).json()
    assert me["usage"]["vms"] == 2 and "limits" in me


def test_admins_are_not_limited(client, superadmin_token, sysadmin_token, lab):
    client.put(f"{Q}/defaults", headers=auth(superadmin_token), json={"max_vms": 1})
    admin = client.post(U, headers=auth(superadmin_token), json={"username": f"tst_qa_{uuid.uuid4().hex[:5]}", "password": "Rahasia123!", "full_name": "Admin", "role": "sysadmin"}).json()
    try:
        for v in lab["vms"][:2]:
            assert _assign(client, sysadmin_token, admin["id"], v).status_code == 200
    finally:
        client.delete(f"{U}/{admin['id']}", headers=auth(superadmin_token))


def test_validation_and_permissions(client, superadmin_token, sysadmin_token, student_token, lab):
    assert client.put(f"{Q}/defaults", headers=auth(sysadmin_token), json={}).status_code == 403
    assert client.get(f"{Q}/defaults", headers=auth(student_token)).status_code == 403
    assert client.get(f"{Q}/users/{lab['uid']}", headers=auth(student_token)).status_code == 403
    assert client.put(f"{Q}/defaults", headers=auth(superadmin_token), json={"max_vms": -1}).status_code == 400
    assert client.put(f"{Q}/defaults", headers=auth(superadmin_token), json={"max_vms": "abc"}).status_code == 400
    assert client.put(f"{Q}/users/999999", headers=auth(sysadmin_token), json={"max_vms": 1}).status_code == 404
    assert client.put(f"{Q}/groups/999999", headers=auth(sysadmin_token), json={"max_vms": 1}).status_code == 404
