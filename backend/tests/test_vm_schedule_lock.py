"""
Jadwal VM (nyala, mati, snapshot otomatis) dan kunci VM (Protection Proxmox). Proxmox diganti tiruan.
"""
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from services import vm_schedule
from tests.conftest import DATABASE_URL, _run, auth

LABEL, NODE, VMID = "labSched", "pve", 7301
HOST = f"{LABEL}__{NODE}"
BASE = f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/vms/{VMID}"
JKT = ZoneInfo("Asia/Jakarta")
MON_10 = datetime(2026, 11, 9, 10, 0, tzinfo=JKT)          # Senin 2026-11-09 10:00 WIB


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


@pytest.fixture
def vm(pve):
    pve.add(VMID, "vm-jadwal", "stopped")
    _run(_sql("DELETE FROM vm_schedules WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vms WHERE host_name = $1", HOST))
    _run(_sql("INSERT INTO vms (vm_id, host_name, vm_name, state) VALUES ($1, $2, 'vm-jadwal', 'stopped')", str(VMID), HOST))
    yield pve
    _run(_sql("DELETE FROM vm_schedules WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vms WHERE host_name = $1", HOST))


def _add(action, at, days=(1,), keep=3, enabled=True):
    _run(_sql("INSERT INTO vm_schedules (host_name, vm_id, action, days, at_time, keep, enabled) VALUES ($1,$2,$3,$4,$5,$6,$7)",
              HOST, str(VMID), action, list(days), at, keep, enabled))


def _run_due(client, now):
    return client.portal.call(vm_schedule.run_due, now.astimezone(timezone.utc))


# ── unit ──────────────────────────────────────────────────────────────────────

def test_due_moment():
    assert vm_schedule.due_moment([1], dtime(9, 55), MON_10) == MON_10.replace(hour=9, minute=55)
    assert vm_schedule.due_moment([1], dtime(10, 5), MON_10) is None            # jamnya belum tiba
    assert vm_schedule.due_moment([2, 3], dtime(9, 0), MON_10) is None          # bukan hari terjadwal


def test_result_text_is_bilingual():
    from i18n import lang_as
    with lang_as("en"):
        assert vm_schedule.result_text("started") == "VM started"
        assert vm_schedule.result_text("failed: x") == "failed (x)"
    with lang_as("id"):
        assert vm_schedule.result_text("started") == "VM dinyalakan"


# ── eksekusi ──────────────────────────────────────────────────────────────────

def test_start_runs_once_per_scheduled_time(client, vm):
    _add("start", dtime(9, 55))
    assert _run_due(client, MON_10) == 1
    assert vm.vms[VMID]["status"] == "running"
    assert _run_due(client, MON_10 + timedelta(minutes=1)) == 0                 # sudah dijalankan untuk waktu itu
    row = _run(_sql("SELECT last_result FROM vm_schedules"))[0]
    assert row["last_result"] == "started"


def test_start_skipped_when_already_running_or_lease_over(client, vm):
    vm.vms[VMID]["status"] = "running"
    _add("start", dtime(9, 55))
    _run_due(client, MON_10)
    assert _run(_sql("SELECT last_result FROM vm_schedules"))[0]["last_result"] == "running"
    _run(_sql("DELETE FROM vm_schedules"))
    vm.vms[VMID]["status"] = "stopped"
    _run(_sql("UPDATE vms SET lease_until = NOW() - interval '1 day' WHERE host_name = $1", HOST))
    _add("start", dtime(9, 55))
    _run_due(client, MON_10)
    assert _run(_sql("SELECT last_result FROM vm_schedules"))[0]["last_result"] == "lease"
    assert vm.vms[VMID]["status"] == "stopped"


def test_shutdown_and_off(client, vm):
    vm.vms[VMID]["status"] = "running"
    _add("shutdown", dtime(9, 55))
    _run_due(client, MON_10)
    assert vm.vms[VMID]["status"] == "stopped"
    assert _run(_sql("SELECT last_result FROM vm_schedules"))[0]["last_result"] == "shutdown"
    _run(_sql("UPDATE vm_schedules SET last_run_at = NULL"))
    _run_due(client, MON_10)
    assert _run(_sql("SELECT last_result FROM vm_schedules"))[0]["last_result"] == "off"


def test_missed_window_is_not_caught_up(client, vm):
    _add("start", dtime(9, 0))                                                  # 60 menit lalu, lewat toleransi 15 menit
    assert _run_due(client, MON_10) == 0 and vm.vms[VMID]["status"] == "stopped"


def test_disabled_and_other_day_do_nothing(client, vm):
    _add("start", dtime(9, 55), enabled=False)
    _add("start", dtime(9, 55), days=(2, 3))
    assert _run_due(client, MON_10) == 0


def test_snapshot_keeps_only_newest_auto_snapshots(client, vm):
    vm.vms[VMID]["snaps"] = ["ccd-auto-20260101-0000", "ccd-auto-20260102-0000", "ccd-auto-20260103-0000", "manual-penting"]
    _add("snapshot", dtime(9, 55), keep=2)
    _run_due(client, MON_10)
    names = vm.vms[VMID]["snaps"]
    assert "manual-penting" in names                                            # snapshot buatan tangan tidak disentuh
    autos = [n for n in names if n.startswith("ccd-auto-")]
    assert len(autos) == 2 and "ccd-auto-20260101-0000" not in autos
    assert _run(_sql("SELECT last_result FROM vm_schedules"))[0]["last_result"] == "snapshot"


def test_proxmox_failure_is_recorded_not_raised(client, vm):
    vm.fail_on.add("action")
    _add("start", dtime(9, 55))
    assert _run_due(client, MON_10) == 1
    assert _run(_sql("SELECT last_result FROM vm_schedules"))[0]["last_result"].startswith("failed:")


# ── API jadwal ────────────────────────────────────────────────────────────────

def test_schedule_crud(client, vm, sysadmin_token, student_token):
    body = {"action": "start", "days": [1, 2, 3, 4, 5], "time": "07:30", "note": "jam kerja"}
    assert client.post(f"{BASE}/schedules", headers=auth(student_token), json=body).status_code == 403
    r = client.post(f"{BASE}/schedules", headers=auth(sysadmin_token), json=body)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["time"] == "07:30" and r.json()["days"] == [1, 2, 3, 4, 5]

    listing = client.get(f"{BASE}/schedules", headers=auth(sysadmin_token)).json()
    assert listing["timezone"] and [s["id"] for s in listing["schedules"]] == [sid]
    assert client.get(f"{BASE}/schedules", headers=auth(student_token)).status_code == 403

    upd = client.put(f"{BASE}/schedules/{sid}", headers=auth(sysadmin_token), json={**body, "action": "shutdown", "time": "17:00", "enabled": False})
    assert upd.json()["action"] == "shutdown" and upd.json()["enabled"] is False and upd.json()["time"] == "17:00"
    assert client.delete(f"{BASE}/schedules/{sid}", headers=auth(sysadmin_token)).status_code == 204
    assert client.delete(f"{BASE}/schedules/{sid}", headers=auth(sysadmin_token)).status_code == 404


def test_schedule_validation(client, vm, sysadmin_token):
    post = lambda **kw: client.post(f"{BASE}/schedules", headers=auth(sysadmin_token), json={"action": "start", "days": [1], "time": "08:00", **kw})
    assert post(time="25:00").status_code == 400
    assert post(time="8am").status_code == 400
    assert post(days=[0]).status_code == 400 and post(days=[8]).status_code == 400
    assert post(days=[]).status_code == 422 and post(action="reboot").status_code == 422
    unknown = client.post(f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/vms/1/schedules", headers=auth(sysadmin_token),
                          json={"action": "start", "days": [1], "time": "08:00"})
    assert unknown.status_code == 404                                           # VM belum tercatat


def test_schedule_flag_in_vm_list(client, vm, sysadmin_token, student_token):
    _add("start", dtime(8, 0))
    admin_list = client.get(f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/vms", headers=auth(sysadmin_token)).json()
    assert admin_list[0]["scheduled"] is True and "locked" in admin_list[0]


# ── kunci VM ──────────────────────────────────────────────────────────────────

def test_lock_unlock_and_delete_guard(client, vm, sysadmin_token, student_token):
    url = f"{BASE}/protection"
    assert client.put(url, headers=auth(student_token), json={"locked": True}).status_code == 403
    r = client.put(url, headers=auth(sysadmin_token), json={"locked": True})
    assert r.status_code == 200 and r.json() == {"vmid": VMID, "locked": True}
    assert vm.vms[VMID]["config"]["protection"] == "1"

    listed = client.get(f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/vms", headers=auth(sysadmin_token)).json()
    assert listed[0]["locked"] is True

    gone = client.delete(f"{BASE}?confirm_name=vm-jadwal", headers=auth(sysadmin_token))
    assert gone.status_code == 409 and "terkunci" in gone.json()["detail"]
    assert VMID in vm.vms                                                       # tidak dihapus

    assert client.put(url, headers=auth(sysadmin_token), json={"locked": False}).json()["locked"] is False
    assert vm.vms[VMID]["config"]["protection"] == "0"


def test_lock_unknown_vm_is_404(client, vm, sysadmin_token):
    r = client.put(f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/vms/424242/protection", headers=auth(sysadmin_token), json={"locked": True})
    assert r.status_code == 404
