"""
Pembuatan VM sebagai proses latar belakang: antrean, langkah, hasil, gagal, ulangi, dan riwayat.
create_vm_core diganti tiruan supaya tidak butuh Proxmox.
"""
import time
import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from tests.conftest import DATABASE_URL, _run, auth

J = "/api/v1/vm-jobs"


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def _body(name=None, **kw):
    return {"instance": "labJobs", "node": "pve", "template_vmid": 9000, "name": name or f"job-{uuid.uuid4().hex[:6]}",
            "username": "mhs", "password": "Rahasia123!", "ip_mode": "dhcp", **kw}


def _wait(client, token, job_id, want=("done", "failed")):
    for _ in range(100):
        j = client.get(f"{J}/{job_id}", headers=auth(token)).json()
        if j["status"] in want:
            return j
        time.sleep(0.1)
    raise AssertionError(f"proses #{job_id} tidak selesai: {j}")


@pytest.fixture
def fake_core(monkeypatch):
    calls = {"n": 0, "fail": None, "seen": []}

    async def fake_create_vm_core(label, node, body, user, request=None, wait_for_agent=True, progress=None):
        calls["n"] += 1
        calls["seen"].append((label, node, body.name, body.password, user.get("username")))
        await progress("clone_wait")
        await progress("configure")
        if calls["fail"]:
            raise HTTPException(502, calls["fail"])
        await progress("credentials")
        return {"vmid": 9100 + calls["n"], "name": body.name, "static_ip": None, "agent_ip": "10.0.0.5", "connect_ready": True,
                "clone": "linked", "switch": None, "os_type": "linux", "started": True}

    from routers import proxmox
    monkeypatch.setattr(proxmox, "create_vm_core", fake_create_vm_core)
    yield calls
    _run(_sql("DELETE FROM vm_jobs WHERE instance = 'labJobs'"))


def test_job_runs_in_background_and_clears_password(client, sysadmin_token, fake_core):
    r = client.post(J, headers=auth(sysadmin_token), json=_body("job-ok"))
    assert r.status_code == 201, r.text
    assert "password" not in str(r.json()["request"])
    done = _wait(client, sysadmin_token, r.json()["id"])
    assert done["status"] == "done" and done["vmid"] == 9101 and done["result"]["agent_ip"] == "10.0.0.5"
    assert done["can_retry"] is False                                             # password sudah dikosongkan
    assert fake_core["seen"][0][3] == "Rahasia123!" and fake_core["seen"][0][4] == "tst_sysadmin"
    stored = _run(_sql("SELECT password_enc, request::text AS req FROM vm_jobs WHERE id = $1", done["id"]))[0]
    assert stored["password_enc"] is None and "Rahasia123" not in stored["req"]


def test_failure_keeps_password_for_retry_then_succeeds(client, sysadmin_token, fake_core):
    fake_core["fail"] = "Proxmox menolak (tiruan)"
    job = client.post(J, headers=auth(sysadmin_token), json=_body("job-gagal")).json()
    failed = _wait(client, sysadmin_token, job["id"])
    assert failed["status"] == "failed" and "tiruan" in failed["error"] and failed["can_retry"] is True
    stored = _run(_sql("SELECT password_enc FROM vm_jobs WHERE id = $1", job["id"]))[0]["password_enc"]
    assert stored and "Rahasia123" not in stored                                   # terenkripsi

    fake_core["fail"] = None
    assert client.post(f"{J}/{job['id']}/retry", headers=auth(sysadmin_token)).status_code == 200
    assert _wait(client, sysadmin_token, job["id"])["status"] == "done"
    assert client.post(f"{J}/{job['id']}/retry", headers=auth(sysadmin_token)).status_code == 409   # hanya yang gagal


def test_validation_rejects_before_queueing(client, sysadmin_token, fake_core):
    post = lambda **kw: client.post(J, headers=auth(sysadmin_token), json=_body(**kw))
    assert post(name="-salah").status_code == 400
    assert post(username="root").status_code == 400
    assert post(password="").status_code == 400
    assert post(ip_mode="static", ip_cidr="bukan-ip").status_code == 400
    assert fake_core["n"] == 0


def test_duplicate_name_and_ip_in_flight_are_refused(client, sysadmin_token, fake_core):
    _run(_sql("""INSERT INTO vm_jobs (instance, node, name, template_vmid, request, actor, status)
                 VALUES ('labJobs', 'pve', 'sedang-jalan', 1, '{"ip_mode":"static","ip_cidr":"10.9.9.9/24"}'::jsonb, '{}'::jsonb, 'running')"""))
    assert client.post(J, headers=auth(sysadmin_token), json=_body("SEDANG-JALAN")).status_code == 409
    r = client.post(J, headers=auth(sysadmin_token), json=_body(ip_mode="static", ip_cidr="10.9.9.9/24", gateway="10.9.9.1"))
    assert r.status_code == 409 and "10.9.9.9" in r.json()["detail"]


def test_history_and_delete(client, sysadmin_token, fake_core):
    job = client.post(J, headers=auth(sysadmin_token), json=_body("job-riwayat")).json()
    _wait(client, sysadmin_token, job["id"])
    assert job["id"] in [j["id"] for j in client.get(J, headers=auth(sysadmin_token)).json()]
    running = _run(_sql("""INSERT INTO vm_jobs (instance, node, name, template_vmid, request, actor, status)
                           VALUES ('labJobs', 'pve', 'x', 1, '{}'::jsonb, '{}'::jsonb, 'running') RETURNING id"""))[0]["id"]
    assert client.delete(f"{J}/{running}", headers=auth(sysadmin_token)).status_code == 409
    assert client.delete(f"{J}/{job['id']}", headers=auth(sysadmin_token)).status_code == 200
    assert client.get(f"{J}/{job['id']}", headers=auth(sysadmin_token)).status_code == 404


def test_recover_interrupted(client, sysadmin_token, fake_core):
    from services import vm_jobs
    jid = _run(_sql("""INSERT INTO vm_jobs (instance, node, name, template_vmid, request, actor, status)
                       VALUES ('labJobs', 'pve', 'putus', 1, '{}'::jsonb, '{}'::jsonb, 'running') RETURNING id"""))[0]["id"]
    client.portal.call(vm_jobs.recover_interrupted)
    j = client.get(f"{J}/{jid}", headers=auth(sysadmin_token)).json()
    assert j["status"] == "interrupted" and "Backend berhenti" in j["error"]


def test_only_admins(client, student_token):
    assert client.post(J, headers=auth(student_token), json=_body()).status_code == 403
    assert client.get(J, headers=auth(student_token)).status_code == 403
    assert client.get(f"{J}/1", headers=auth(student_token)).status_code == 403
