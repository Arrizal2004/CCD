"""
VM massal per kelas dan request VPS yang otomatis meng-assign VM-nya. Proxmox dan pembuatan VM diganti
tiruan; yang diuji adalah rencana, proses latar belakang, assign, ulangi, CSV, dan pemulihan.
"""
import time
import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from routers import proxmox as proxmox_router
from services import assignments, proxmox_instances, proxmox_provision, vm_batches
from tests.conftest import DATABASE_URL, _run, auth

LABEL, NODE = "labBatch", "pve"
HOST = f"{LABEL}__{NODE}"
API = "/api/v1/vm-batches"
TEMPLATE = {"vmid": 100, "name": "Template-Ubuntu", "cores": 2, "memory_mb": 2048, "disk_gb": 10,
            "cloudinit": True, "ostype": "l26", "bridge": "vmbr0", "disk_key": "scsi0"}


class FakePve:
    def __init__(self):
        self.names, self.free_mb = ["sudah-ada"], 64000

    async def list_vms(self, node):
        return [{"vmid": 100 + i, "name": n} for i, n in enumerate(self.names)]

    async def get_node_status(self, node):
        return {"memory": {"available": self.free_mb * 1048576}}


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


@pytest.fixture
def env(client, superadmin_token, monkeypatch):
    fake, calls, fail = FakePve(), [], set()

    async def get_client(label):
        return fake

    async def list_templates(client_, node):
        return [TEMPLATE]

    async def create_vm_core(label, node, req, user, request=None, wait_for_agent=True):
        calls.append(req)
        if req.name in fail:
            raise HTTPException(502, "Proxmox menolak clone")
        vmid = 800 + len(calls)
        return {"vmid": vmid, "name": req.name, "static_ip": None, "agent_ip": f"172.16.111.{vmid - 700}"}

    async def no_op(*args, **kwargs):
        return None

    monkeypatch.setattr(proxmox_instances, "get_client", get_client)
    monkeypatch.setattr(proxmox_provision, "list_templates", list_templates)
    monkeypatch.setattr(proxmox_router, "create_vm_core", create_vm_core)
    monkeypatch.setattr(assignments, "sync_vm_assignments", no_op)

    tag = uuid.uuid4().hex[:5]
    gid = client.post("/api/v1/groups", headers=auth(superadmin_token), json={"name": f"TKJ 12A {tag}"}).json()["id"]
    users = []
    for name in (f"tst_bt_ani_{tag}", f"tst_bt_budi_{tag}", f"tst_bt_cici_{tag}"):
        r = client.post("/api/v1/users", headers=auth(superadmin_token),
                        json={"username": name, "password": "Rahasia123!", "full_name": name.title(), "role": "student"})
        users.append(r.json()["id"])
        client.post(f"/api/v1/groups/{gid}/members", headers=auth(superadmin_token), json={"user_id": r.json()["id"]})
    yield {"fake": fake, "calls": calls, "fail": fail, "group": gid, "users": users, "tag": tag}
    client.delete(f"/api/v1/groups/{gid}", headers=auth(superadmin_token))
    for uid in users:
        client.delete(f"/api/v1/users/{uid}", headers=auth(superadmin_token))
    _run(_sql("DELETE FROM vm_assignments WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vm_batches WHERE instance = $1", LABEL))


def _body(env, **extra):
    return {"instance": LABEL, "node": NODE, "group_id": env["group"], "template_vmid": 100, **extra}


def _wait(client, token, batch_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        b = client.get(f"{API}/{batch_id}", headers=auth(token)).json()
        if b["status"] != "running":
            return b
        time.sleep(0.2)
    raise AssertionError("batch tidak selesai")


def test_only_admin(client, student_token, env):
    assert client.post(f"{API}/preview", headers=auth(student_token), json=_body(env)).status_code == 403
    assert client.get(API, headers=auth(student_token)).status_code == 403


def test_preview_names_users_and_warnings(client, sysadmin_token, env):
    tag = env["tag"]
    r = client.post(f"{API}/preview", headers=auth(sysadmin_token), json=_body(env, start=True, memory_mb=30000))
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["prefix"] == f"tkj-12a-{tag}" and p["count"] == 3
    first = p["items"][0]
    assert first["vm_name"] == f"tkj-12a-{tag}-tst-bt-ani-{tag}" and first["os_username"] == f"tst_bt_ani_{tag}"
    assert any("RAM" in w for w in p["warnings"])                              # 3 x 30000 MB > 64000 MB

    env["fake"].names.append(f"lab-tst-bt-budi-{tag}")
    p = client.post(f"{API}/preview", headers=auth(sysadmin_token), json=_body(env, prefix="Lab", os_username="siswa")).json()
    assert [i["conflict"] is not None for i in p["items"]] == [False, True, False]
    assert {i["os_username"] for i in p["items"]} == {"siswa"}
    assert client.post(API, headers=auth(sysadmin_token), json=_body(env, prefix="Lab")).status_code == 409

    bad = client.post(f"{API}/preview", headers=auth(sysadmin_token), json=_body(env, os_username="root"))
    assert bad.status_code == 400


def test_preview_lists_inactive_members(client, sysadmin_token, superadmin_token, env):
    """Anggota nonaktif tetap terlihat di rencana (ditandai), bukan hilang diam-diam."""
    client.post("/api/v1/users/bulk", headers=auth(superadmin_token), json={"user_ids": env["users"][:1], "action": "deactivate"})
    p = client.post(f"{API}/preview", headers=auth(sysadmin_token), json=_body(env)).json()
    assert p["count"] == 3 and [i["inactive"] for i in p["items"]] == [True, False, False]
    assert any("nonaktif" in w for w in p["warnings"])
    # Admin tetap boleh memilihnya.
    p = client.post(f"{API}/preview", headers=auth(sysadmin_token), json=_body(env, user_ids=env["users"][:1])).json()
    assert p["count"] == 1 and not p["warnings"]


def test_batch_creates_and_assigns(client, sysadmin_token, env):
    r = client.post(API, headers=auth(sysadmin_token), json=_body(env, cores=2, memory_mb=1024, lease_days=30,
                                                                   user_ids=env["users"][:2]))
    assert r.status_code == 200, r.text
    b = _wait(client, sysadmin_token, r.json()["id"])
    assert b["status"] == "done" and b["counts"]["done"] == 2 and len(b["items"]) == 2
    assert all(i["vmid"] and i["ip"] for i in b["items"])
    req = env["calls"][0]
    assert (req.ip_mode, req.cores, req.memory_mb, req.lease_days, req.template_vmid) == ("dhcp", 2, 1024, 30, 100)
    assert req.username == b["items"][0]["os_username"] and len(req.password) == 14
    rows = _run(_sql("SELECT user_id, vm_id FROM vm_assignments WHERE host_name = $1 AND deleted_at IS NULL", HOST))
    assert {r["user_id"] for r in rows} == set(env["users"][:2])                 # langsung milik mahasiswanya
    assert all("password_enc" not in i for i in b["items"])                       # password hanya lewat CSV


def test_failed_vm_does_not_stop_batch_and_can_retry(client, sysadmin_token, env):
    tag = env["tag"]
    env["fail"].add(f"kelas-tst-bt-budi-{tag}")
    r = client.post(API, headers=auth(sysadmin_token), json=_body(env, prefix="kelas"))
    b = _wait(client, sysadmin_token, r.json()["id"])
    assert b["counts"] == {"pending": 0, "creating": 0, "done": 2, "failed": 1}
    failed = next(i for i in b["items"] if i["status"] == "failed")
    assert "Proxmox menolak clone" in failed["error"]

    env["fail"].clear()
    r = client.post(f"{API}/{b['id']}/retry", headers=auth(sysadmin_token))
    assert r.status_code == 200
    b = _wait(client, sysadmin_token, b["id"])
    assert b["counts"]["done"] == 3
    assert client.post(f"{API}/{b['id']}/retry", headers=auth(sysadmin_token)).status_code == 400   # tidak ada yang gagal

    csv = client.get(f"{API}/{b['id']}/credentials.csv", headers=auth(sysadmin_token))
    assert csv.status_code == 200 and csv.text.startswith("﻿vm_name,username")
    assert len(csv.text.strip().splitlines()) == 4 and f"kelas-tst-bt-budi-{tag}" in csv.text


def test_recover_interrupted(client, sysadmin_token, env):
    r = client.post(API, headers=auth(sysadmin_token), json=_body(env, prefix="putus"))
    b = _wait(client, sysadmin_token, r.json()["id"])
    _run(_sql("UPDATE vm_batches SET status = 'running' WHERE id = $1", b["id"]))
    _run(_sql("UPDATE vm_batch_items SET status = 'creating' WHERE id = $1", b["items"][0]["id"]))
    client.portal.call(vm_batches.recover_interrupted)
    b = client.get(f"{API}/{b['id']}", headers=auth(sysadmin_token)).json()
    assert b["status"] == "interrupted" and b["items"][0]["status"] == "failed" and "Backend berhenti" in b["items"][0]["error"]


def test_done_vps_request_assigns_linked_vm(client, student_token, sysadmin_token):
    r = client.post("/api/v1/infra-requests", headers=auth(student_token),
                    json={"request_type": "VPS", "notes": "uji", "specs": {"cpu": 2, "ram_gb": 4, "storage_gb": 40}})
    req_id = r.json()["id"]
    r = client.patch(f"/api/v1/infra-requests/{req_id}/status", headers=auth(sysadmin_token),
                     json={"status": "DONE", "linked_vm_id": "905", "linked_vm_name": "vps-uji", "linked_host_name": HOST})
    assert r.status_code == 200, r.text
    rows = _run(_sql("""SELECT u.username FROM vm_assignments va JOIN users u ON u.id = va.user_id
                        WHERE va.vm_id = '905' AND va.host_name = $1 AND va.deleted_at IS NULL""", HOST))
    assert [r["username"] for r in rows] == ["tst_student"]
    _run(_sql("DELETE FROM vm_assignments WHERE vm_id = '905' AND host_name = $1", HOST))
