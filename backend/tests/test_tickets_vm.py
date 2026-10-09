"""
Tiket dari Detail VM: mahasiswa hanya boleh melaporkan VM miliknya, dicek per host + VMID
karena VMID yang sama bisa ada di dua Proxmox berbeda.
"""
import asyncpg
import pytest

from tests.conftest import DATABASE_URL, _run, auth

MY_HOST = "labB__pve"
OTHER_HOST = "labA__pve"
VMID = "101"


async def _assign(username: str, vm_id: str, host: str, remove: bool = False) -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        uid = await conn.fetchval("SELECT id FROM users WHERE username = $1", username)
        await conn.execute("DELETE FROM vm_assignments WHERE user_id = $1 AND vm_id = $2 AND host_name = $3",
                           uid, vm_id, host)
        if not remove:
            await conn.execute("INSERT INTO vm_assignments (user_id, vm_id, host_name) VALUES ($1, $2, $3)",
                               uid, vm_id, host)
    finally:
        await conn.close()


async def _vm_row(vm_id: str, host: str, name: str) -> int:
    """Catat VM di tabel vms (seperti saat daftar VM dibuka) dan kembalikan CCDID-nya."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("INSERT INTO vms (vm_id, host_name, vm_name) VALUES ($1, $2, $3) "
                           "ON CONFLICT (vm_id, host_name) DO NOTHING", vm_id, host, name)
        return await conn.fetchval("SELECT ccd_id FROM vms WHERE vm_id = $1 AND host_name = $2", vm_id, host)
    finally:
        await conn.close()


@pytest.fixture
def assigned_vm(student_token):
    _run(_assign("tst_student", VMID, MY_HOST))
    yield
    _run(_assign("tst_student", VMID, MY_HOST, remove=True))


def _ticket(client, token, **vm):
    return client.post("/api/tickets", headers=auth(token), json={
        "title": "VM tidak bisa diakses", "category": "REMOTE_ISSUE", "description": "uji",
        "vm_snapshot": {"vm_name": "vm-uji", "state": "running"}, **vm})


def test_student_can_report_own_vm(client, student_token, assigned_vm):
    r = _ticket(client, student_token, vm_id=VMID, host_name=MY_HOST)
    assert r.status_code == 200, r.text
    t = client.get(f"/api/tickets/{r.json()['id']}", headers=auth(student_token)).json()["ticket"]
    assert t["vm_id"] == VMID and t["host_name"] == MY_HOST and t["vm_snapshot"]["vm_name"] == "vm-uji"


def test_same_vmid_on_other_host_is_rejected(client, student_token, assigned_vm):
    r = _ticket(client, student_token, vm_id=VMID, host_name=OTHER_HOST)
    assert r.status_code == 403


def test_ticket_without_host_still_allowed(client, student_token):
    assert _ticket(client, student_token, vm_id="999").status_code == 200


def test_admin_can_report_any_vm(client, sysadmin_token):
    assert _ticket(client, sysadmin_token, vm_id=VMID, host_name=OTHER_HOST).status_code == 200


def test_same_vmid_gets_different_ccd_id():
    mine, other = _run(_vm_row(VMID, MY_HOST, "vm-saya")), _run(_vm_row(VMID, OTHER_HOST, "vm-lain"))
    assert mine and other and mine != other


def test_ticket_by_ccd_id(client, student_token, assigned_vm):
    ccd = _run(_vm_row(VMID, MY_HOST, "vm-saya"))
    r = client.post("/api/tickets", headers=auth(student_token),
                    json={"title": "lewat CCDID", "ccd_id": f"CCD-{ccd:04d}"})
    assert r.status_code == 200, r.text
    t = client.get(f"/api/tickets/{r.json()['id']}", headers=auth(student_token)).json()["ticket"]
    assert t["ccd_id"] == ccd and t["vm_id"] == VMID and t["host_name"] == MY_HOST
    assert t["vm_snapshot"]["vm_name"] == "vm-saya"


def test_ticket_from_detail_gets_ccd_id(client, student_token, assigned_vm):
    ccd = _run(_vm_row(VMID, MY_HOST, "vm-saya"))
    r = _ticket(client, student_token, vm_id=VMID, host_name=MY_HOST)
    t = client.get(f"/api/tickets/{r.json()['id']}", headers=auth(student_token)).json()["ticket"]
    assert t["ccd_id"] == ccd


def test_ccd_id_of_other_vm_looks_like_unknown(client, student_token, assigned_vm):
    other = _run(_vm_row(VMID, OTHER_HOST, "vm-lain"))
    r1 = client.post("/api/tickets", headers=auth(student_token), json={"title": "x", "ccd_id": str(other)})
    r2 = client.post("/api/tickets", headers=auth(student_token), json={"title": "x", "ccd_id": "CCD-99999"})
    assert r1.status_code == r2.status_code == 400
    assert "tidak ditemukan" in r1.json()["detail"]


def test_admin_ticket_detail_tells_if_vm_exists_and_its_lease(client, student_token, sysadmin_token, assigned_vm):
    _run(_vm_row(VMID, MY_HOST, "vm-saya"))
    _run(_set_lease(VMID, MY_HOST, "2030-01-02T03:04:05+00:00"))
    tid = _ticket(client, student_token, vm_id=VMID, host_name=MY_HOST).json()["id"]
    admin = client.get(f"/api/tickets/{tid}", headers=auth(sysadmin_token)).json()["ticket"]
    assert admin["vm_live"] is True and admin["vm_lease_until"].startswith("2030-01-02")
    student = client.get(f"/api/tickets/{tid}", headers=auth(student_token)).json()["ticket"]
    assert "vm_live" not in student and "vm_lease_until" not in student


def test_admin_ticket_detail_marks_deleted_vm(client, student_token, sysadmin_token, assigned_vm):
    _run(_vm_row(VMID, MY_HOST, "vm-saya"))
    tid = _ticket(client, student_token, vm_id=VMID, host_name=MY_HOST).json()["id"]
    _run(_drop_vm(VMID, MY_HOST))
    admin = client.get(f"/api/tickets/{tid}", headers=auth(sysadmin_token)).json()["ticket"]
    assert admin["vm_live"] is False and admin["vm_lease_until"] is None


async def _set_lease(vm_id: str, host: str, until: str) -> None:
    from datetime import datetime
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("UPDATE vms SET lease_until = $3 WHERE vm_id = $1 AND host_name = $2",
                           vm_id, host, datetime.fromisoformat(until))
    finally:
        await conn.close()


async def _drop_vm(vm_id: str, host: str) -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("DELETE FROM vms WHERE vm_id = $1 AND host_name = $2", vm_id, host)
    finally:
        await conn.close()
