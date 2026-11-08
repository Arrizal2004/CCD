"""
Fitur kelas: reset lab ke kondisi awal, mode ujian, dan laporan pemakaian per kelas. Proxmox dan riwayat Guacamole tiruan.
"""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from services import exam_mode, lab_reset, remote_history, usage_report
from tests.conftest import DATABASE_URL, _run, auth

L = "/api/v1/lab"
U = "/api/v1/users"
LABEL, NODE = "labClass", "pve"
HOST = f"{LABEL}__{NODE}"
PX = f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/vms"
VM_A, VM_B, VM_X = 6101, 6102, 6103


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def _fresh_state():
    exam_mode.forget()
    lab_reset._recent.clear()


@pytest.fixture
def klass(client, pve, superadmin_token, sysadmin_token, student_token):
    """Satu kelas dengan mahasiswa tst_student; VM_A dan VM_B di-assign ke dia, VM_X dipegang mahasiswa di luar kelas."""
    _fresh_state()
    me = client.get(f"{U}/me", headers=auth(student_token)).json()
    other = client.post(U, headers=auth(superadmin_token), json={"username": f"tst_cl_{uuid.uuid4().hex[:5]}", "password": "Rahasia123!",
                                                                 "full_name": "Luar Kelas", "role": "student"}).json()
    g = client.post("/api/v1/groups", headers=auth(sysadmin_token), json={"name": f"tst_kelas_{uuid.uuid4().hex[:5]}"}).json()
    assert client.post(f"/api/v1/groups/{g['id']}/members", headers=auth(sysadmin_token), json={"user_id": me["id"]}).status_code == 201
    for vmid, name in ((VM_A, "lab-a"), (VM_B, "lab-b"), (VM_X, "lab-x")):
        pve.add(vmid, name, "running")
        _run(_sql("INSERT INTO vms (vm_id, host_name, vm_name, state) VALUES ($1, $2, $3, 'running') ON CONFLICT (vm_id, host_name) DO UPDATE SET vm_name = $3", str(vmid), HOST, name))
    for uid, vmid in ((me["id"], VM_A), (me["id"], VM_B), (other["id"], VM_X)):
        _run(_sql("INSERT INTO vm_assignments (user_id, vm_id, host_name, vm_name, access) VALUES ($1, $2, $3, 'x', 'full')", uid, str(vmid), HOST))
    yield {"gid": g["id"], "uid": me["id"], "other": other["id"]}
    client.delete(f"/api/v1/groups/{g['id']}", headers=auth(sysadmin_token))
    _run(_sql("DELETE FROM vm_assignments WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vm_baselines WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vms WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM lab_resets"))
    client.delete(f"{U}/{other['id']}", headers=auth(superadmin_token))
    client.put(f"{L}/settings", headers=auth(superadmin_token), json={"students_can_reset": False})
    _fresh_state()


# ══ reset lab ═════════════════════════════════════════════════════════════════

def test_baseline_needs_a_stopped_vm_and_replaces_the_old_one(client, klass, pve, sysadmin_token, student_token):
    url = f"{PX}/{VM_A}/baseline"
    assert client.put(url, headers=auth(student_token)).status_code == 403
    assert client.put(url, headers=auth(sysadmin_token)).status_code == 409           # masih menyala
    pve.vms[VM_A]["status"] = "stopped"
    assert client.put(url, headers=auth(sysadmin_token)).status_code == 200
    assert pve.vms[VM_A]["snaps"] == ["ccd-baseline"]
    assert client.put(url, headers=auth(sysadmin_token)).status_code == 200            # diganti, tidak menumpuk
    assert pve.vms[VM_A]["snaps"] == ["ccd-baseline"] and ("delsnap", VM_A, "ccd-baseline") in pve.calls
    listed = client.get(PX, headers=auth(sysadmin_token)).json()
    assert {v["vmid"]: v["baseline"] for v in listed}[VM_A] is True
    assert client.delete(url, headers=auth(sysadmin_token)).json() == {"removed": True}
    assert pve.vms[VM_A]["snaps"] == []


def _baseline(client, pve, token, vmid):
    pve.vms[vmid]["status"] = "stopped"
    assert client.put(f"{PX}/{vmid}/baseline", headers=auth(token)).status_code == 200
    pve.vms[vmid]["status"] = "running"


def test_admin_resets_a_vm_and_it_is_started_again(client, klass, pve, sysadmin_token):
    url = f"{PX}/{VM_A}/reset-lab"
    nb = client.post(url, headers=auth(sysadmin_token), json={})
    assert nb.status_code == 409 and "baseline" in nb.json()["detail"]
    _baseline(client, pve, sysadmin_token, VM_A)
    r = client.post(url, headers=auth(sysadmin_token), json={})
    assert r.status_code == 200 and r.json()["started"] is True
    assert ("rollback", VM_A, "ccd-baseline") in pve.calls and pve.vms[VM_A]["status"] == "running"
    off = client.post(url, headers=auth(sysadmin_token), json={"start": False}).json()
    assert off["started"] is False and pve.vms[VM_A]["status"] == "stopped"
    pve.fail_on.add("rollback")
    assert client.post(url, headers=auth(sysadmin_token), json={}).status_code == 502


def test_student_reset_is_off_by_default_and_has_rules(client, klass, pve, sysadmin_token, superadmin_token, student_token):
    _baseline(client, pve, sysadmin_token, VM_A)
    url = f"{PX}/{VM_A}/reset-lab"
    assert client.post(url, headers=auth(student_token), json={}).status_code == 403          # belum diizinkan
    assert client.put(f"{L}/settings", headers=auth(sysadmin_token), json={"students_can_reset": True}).status_code == 403
    assert client.put(f"{L}/settings", headers=auth(superadmin_token), json={"students_can_reset": True}).json() == {"students_can_reset": True}
    assert client.post(url, headers=auth(student_token), json={}).status_code == 200
    assert client.post(url, headers=auth(student_token), json={}).status_code == 429          # jeda antar-reset
    assert client.post(f"{PX}/{VM_X}/reset-lab", headers=auth(student_token), json={}).status_code == 403   # bukan VM miliknya


def test_group_reset_reports_each_vm(client, klass, pve, sysadmin_token):
    _baseline(client, pve, sysadmin_token, VM_A)                                              # VM_B sengaja tanpa baseline
    r = client.post(f"{L}/groups/{klass['gid']}/reset", headers=auth(sysadmin_token), json={})
    assert r.status_code == 202 and r.json()["total"] == 2
    run = None
    for _ in range(100):
        run = client.get(f"{L}/resets/{r.json()['id']}", headers=auth(sysadmin_token)).json()
        if run["status"] != "running":
            break
        time.sleep(0.05)
    assert run["status"] == "done" and run["total"] == 2 and run["ok"] == 1
    by_vm = {i["vm_id"]: i for i in run["items"]}
    assert by_vm[str(VM_A)]["ok"] and by_vm[str(VM_A)]["started"]
    assert by_vm[str(VM_B)]["ok"] is False and "baseline" in by_vm[str(VM_B)]["reason_text"]
    assert any(x["id"] == r.json()["id"] for x in client.get(f"{L}/resets", headers=auth(sysadmin_token)).json())


def test_lab_reset_endpoints_need_admin(client, klass, student_token):
    assert client.post(f"{L}/groups/{klass['gid']}/reset", headers=auth(student_token), json={}).status_code == 403
    assert client.get(f"{L}/resets", headers=auth(student_token)).status_code == 403
    assert client.post(f"{L}/groups/999999/reset", headers=auth(student_token), json={}).status_code == 403


# ══ mode ujian ════════════════════════════════════════════════════════════════

def _exam(client, token, gid, start_in=1, minutes=60, **kw):
    now = datetime.now(timezone.utc)
    return client.post(f"{L}/exams", headers=auth(token), json={
        "name": kw.pop("name", "Ujian Jaringan"), "group_id": gid, "starts_at": (now + timedelta(minutes=start_in)).isoformat(),
        "ends_at": (now + timedelta(minutes=start_in + minutes)).isoformat(), **kw})


def _make_live(exam_id):
    """Majukan jendela ujian ke 'sudah mulai' tanpa menunggu jam."""
    _run(_sql("UPDATE exams SET starts_at = NOW() - interval '1 minute', ends_at = NOW() + interval '1 hour' WHERE id = $1", exam_id))
    exam_mode.forget()


def test_exam_validation(client, klass, sysadmin_token, student_token):
    gid = klass["gid"]
    assert _exam(client, student_token, gid).status_code == 403
    assert _exam(client, sysadmin_token, 999999).status_code == 404
    assert _exam(client, sysadmin_token, gid, start_in=10, minutes=-5).status_code in (400, 422)
    assert _exam(client, sysadmin_token, gid, minutes=60 * 25).status_code == 400            # > 24 jam
    assert _exam(client, sysadmin_token, gid, start_in=-120, minutes=30).status_code == 400  # sudah lewat
    ok = _exam(client, sysadmin_token, gid, name="  Ujian 1  ")
    assert ok.status_code == 201 and ok.json()["name"] == "Ujian 1" and ok.json()["status"] == "scheduled" and ok.json()["vm_count"] == 2
    assert ok.json()["live"] is False
    clash = _exam(client, sysadmin_token, gid, start_in=30, minutes=60)
    assert clash.status_code == 409 and "Bentrok" in clash.json()["detail"]
    assert _exam(client, sysadmin_token, gid, start_in=200, minutes=30).status_code == 201     # jendela lain boleh


def test_a_live_exam_limits_the_class_to_exam_vms(client, klass, pve, sysadmin_token, student_token):
    from database import get_student_vm_access
    # mahasiswa kelas ini juga anggota grup lain yang punya VM sendiri (VM_Y)
    _run(_sql("INSERT INTO vms (vm_id, host_name, vm_name) VALUES ('6199', $1, 'lab-y') ON CONFLICT DO NOTHING", HOST))
    g2 = client.post("/api/v1/groups", headers=auth(sysadmin_token), json={"name": f"tst_k2_{uuid.uuid4().hex[:4]}"}).json()
    _run(_sql("INSERT INTO group_members (group_id, user_id) VALUES ($1, $2)", g2["id"], klass["uid"]))
    _run(_sql("INSERT INTO group_vm_access (group_id, vm_id, host_name, access) VALUES ($1, '6199', $2, 'full')", g2["id"], HOST))
    try:
        before = client.portal.call(get_student_vm_access, klass["uid"])
        assert {k[0] for k in before} == {"6101", "6102", "6199"}
        exam = _exam(client, sysadmin_token, klass["gid"]).json()
        assert {k[0] for k in client.portal.call(get_student_vm_access, klass["uid"])} == {"6101", "6102", "6199"}   # belum mulai
        _make_live(exam["id"])
        during = client.portal.call(get_student_vm_access, klass["uid"])
        assert {k[0] for k in during} == {"6101", "6102"}          # VM grup lain (6199) tidak terlihat selama ujian kelas ini
        # selesai/batal: kembali normal
        assert client.post(f"{L}/exams/{exam['id']}/cancel", headers=auth(sysadmin_token)).status_code == 200
        assert {k[0] for k in client.portal.call(get_student_vm_access, klass["uid"])} == {"6101", "6102", "6199"}
    finally:
        client.delete(f"/api/v1/groups/{g2['id']}", headers=auth(sysadmin_token))
        _run(_sql("DELETE FROM vms WHERE vm_id = '6199'"))


def test_live_exam_blocks_changes_and_open_web_for_students_only(client, klass, pve, sysadmin_token, student_token):
    exam = _exam(client, sysadmin_token, klass["gid"]).json()
    power = lambda tok: client.post(f"{PX}/{VM_A}/action", headers=auth(tok), json={"action": "stop"})
    snap = lambda tok: client.post(f"{PX}/{VM_A}/snapshots", headers=auth(tok), json={"snapname": "uji"})
    assert power(student_token).status_code == 200                                      # sebelum ujian: boleh
    pve.vms[VM_A]["status"] = "running"
    _make_live(exam["id"])
    r = power(student_token)
    assert r.status_code == 403 and "ujian" in r.json()["detail"].lower()
    assert snap(student_token).status_code == 403
    assert client.post(f"{PX}/{VM_A}/reset-lab", headers=auth(student_token), json={}).status_code == 403
    ow = client.post("/api/v1/openweb/ticket", headers=auth(student_token), json={"url": "http://10.1.2.3/"})
    assert ow.status_code == 403 and "ujian" in ow.json()["detail"].lower()
    assert power(sysadmin_token).status_code == 200                                     # admin tidak terpengaruh
    # tanpa block_open_web, pesan penolakan bukan soal ujian
    _run(_sql("UPDATE exams SET block_open_web = FALSE WHERE id = $1", exam["id"]))
    exam_mode.forget()
    ow = client.post("/api/v1/openweb/ticket", headers=auth(student_token), json={"url": "http://10.1.2.3/"})
    assert ow.status_code == 403 and "ujian" not in ow.json()["detail"].lower()


def test_other_classes_are_not_affected(client, klass, sysadmin_token, superadmin_token):
    other_token = client.post(f"{U}/login", json={"username": _run(_sql("SELECT username FROM users WHERE id = $1", klass["other"]))[0]["username"],
                                                  "password": "Rahasia123!"}).json()["access_token"]
    exam = _exam(client, sysadmin_token, klass["gid"]).json()
    _make_live(exam["id"])
    r = client.post(f"{PX}/{VM_X}/action", headers=auth(other_token), json={"action": "stop"})
    assert r.status_code == 200


def test_tick_runs_start_and_end_actions_once(client, klass, pve, sysadmin_token):
    _baseline(client, pve, sysadmin_token, VM_A)
    _baseline(client, pve, sysadmin_token, VM_B)
    pve.vms[VM_A]["status"] = "stopped"
    exam = _exam(client, sysadmin_token, klass["gid"]).json()
    assert client.portal.call(exam_mode.tick) == 0                                      # belum waktunya
    _make_live(exam["id"])
    assert client.portal.call(exam_mode.tick) == 1
    e = client.get(f"{L}/exams/{exam['id']}", headers=auth(sysadmin_token)).json()
    assert e["status"] == "running" and e["start_result"]["reset"] == {"total": 2, "ok": 2, "failed": []}
    assert pve.vms[VM_A]["status"] == "running" and pve.vms[VM_B]["status"] == "running"      # di-reset lalu dinyalakan
    assert [c for c in pve.calls if c[0] == "rollback" and c[1] == VM_A]
    assert client.portal.call(exam_mode.tick) == 0                                      # tidak diulang

    _run(_sql("UPDATE exams SET ends_at = NOW() - interval '1 second' WHERE id = $1", exam["id"]))
    exam_mode.forget()
    assert client.portal.call(exam_mode.tick) == 1
    e = client.get(f"{L}/exams/{exam['id']}", headers=auth(sysadmin_token)).json()
    assert e["status"] == "finished" and e["end_result"]["shutdown"] == {"total": 2, "ok": 2, "failed": []}
    assert pve.vms[VM_A]["status"] == "stopped" and pve.vms[VM_B]["status"] == "stopped"
    assert pve.vms[VM_X]["status"] == "running"                                         # VM di luar kelas tidak disentuh


def test_exam_without_baseline_reports_the_gap(client, klass, pve, sysadmin_token):
    exam = _exam(client, sysadmin_token, klass["gid"]).json()
    _make_live(exam["id"])
    client.portal.call(exam_mode.tick)
    e = client.get(f"{L}/exams/{exam['id']}", headers=auth(sysadmin_token)).json()
    assert e["start_result"]["reset"]["ok"] == 0 and len(e["start_result"]["reset"]["failed"]) == 2
    assert e["start_result"]["reset"]["failed"][0]["reason"] == "no_baseline"


def test_missed_exam_is_skipped_without_side_effects(client, klass, pve, sysadmin_token):
    exam = _exam(client, sysadmin_token, klass["gid"]).json()
    _run(_sql("UPDATE exams SET starts_at = NOW() - interval '3 hours', ends_at = NOW() - interval '2 hours' WHERE id = $1", exam["id"]))
    client.portal.call(exam_mode.tick)
    assert client.get(f"{L}/exams/{exam['id']}", headers=auth(sysadmin_token)).json()["status"] == "finished"
    assert not [c for c in pve.calls if c[0] in ("rollback", "action", "shutdown")]


def test_start_now_end_now_cancel_update_delete(client, klass, pve, sysadmin_token):
    exam = _exam(client, sysadmin_token, klass["gid"], start_in=120).json()
    eid = exam["id"]
    now = datetime.now(timezone.utc)
    upd = client.put(f"{L}/exams/{eid}", headers=auth(sysadmin_token), json={
        "name": "Ujian Revisi", "group_id": klass["gid"], "starts_at": (now + timedelta(minutes=180)).isoformat(),
        "ends_at": (now + timedelta(minutes=240)).isoformat(), "reset_at_start": False})
    assert upd.status_code == 200 and upd.json()["name"] == "Ujian Revisi" and upd.json()["reset_at_start"] is False
    assert client.post(f"{L}/exams/{eid}/end-now", headers=auth(sysadmin_token)).status_code == 409    # belum jalan
    started = client.post(f"{L}/exams/{eid}/start-now", headers=auth(sysadmin_token))
    assert started.status_code == 200 and started.json()["status"] == "running" and started.json()["live"] is True
    for _ in range(60):                                                                  # aksi mulai berjalan di latar belakang
        if pve.vms[VM_A]["status"] == "running" and client.get(f"{L}/exams/{eid}", headers=auth(sysadmin_token)).json()["start_result"]:
            break
        time.sleep(0.05)
    assert client.put(f"{L}/exams/{eid}", headers=auth(sysadmin_token), json={"name": "x", "group_id": klass["gid"],
        "starts_at": (now + timedelta(minutes=300)).isoformat(), "ends_at": (now + timedelta(minutes=320)).isoformat()}).status_code == 409
    assert client.delete(f"{L}/exams/{eid}", headers=auth(sysadmin_token)).status_code == 409        # sedang berjalan
    assert client.post(f"{L}/exams/{eid}/end-now", headers=auth(sysadmin_token)).json()["status"] == "finished"
    assert client.post(f"{L}/exams/{eid}/cancel", headers=auth(sysadmin_token)).status_code == 409
    assert client.delete(f"{L}/exams/{eid}", headers=auth(sysadmin_token)).status_code == 200
    assert client.get(f"{L}/exams/{eid}", headers=auth(sysadmin_token)).status_code == 404


def test_group_reset_is_refused_during_a_live_exam(client, klass, sysadmin_token):
    exam = _exam(client, sysadmin_token, klass["gid"]).json()
    _make_live(exam["id"])
    r = client.post(f"{L}/groups/{klass['gid']}/reset", headers=auth(sysadmin_token), json={})
    assert r.status_code == 409 and "ujian" in r.json()["detail"]


# ══ laporan pemakaian ═════════════════════════════════════════════════════════

def test_clip_minutes():
    t0 = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
    lo, hi, now = t0, t0 + timedelta(hours=10), t0 + timedelta(hours=5)
    assert usage_report.clip_minutes(t0 + timedelta(hours=1), t0 + timedelta(hours=2), lo, hi, now) == 60
    assert usage_report.clip_minutes(t0 - timedelta(hours=1), t0 + timedelta(hours=1), lo, hi, now) == 60       # dipotong awal rentang
    assert usage_report.clip_minutes(t0 + timedelta(hours=9), t0 + timedelta(hours=12), lo, hi, now) == 60      # dipotong akhir rentang
    assert usage_report.clip_minutes(t0 + timedelta(hours=4), None, lo, hi, now) == 60                          # masih berjalan -> sampai sekarang
    assert usage_report.clip_minutes(None, None, lo, hi, now) == 0 and usage_report.clip_minutes(t0 + timedelta(hours=20), None, lo, hi, now) == 0


def test_parse_range():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Jakarta")
    lo, hi = usage_report.parse_range("2026-11-01", "2026-11-03", tz)
    assert lo == datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc) and hi == datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc)
    lo, hi = usage_report.parse_range(None, None, tz, today=datetime(2026, 11, 30).date())
    assert (hi - lo).days == 30
    for bad in (("2026-13-01", None), ("2026-11-05", "2026-11-01"), ("2024-01-01", "2026-01-01"), ("x", "y")):
        with pytest.raises(ValueError):
            usage_report.parse_range(*bad, tz)


@pytest.fixture
def activity(klass, monkeypatch, client):
    """Sesi tiruan: 2 sesi Remote, 1 Open Web, 1 SSH, 3 login untuk mahasiswa kelas; satu mahasiswa lain di luar kelas."""
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=1)
    name = _run(_sql("SELECT username FROM users WHERE id = $1", klass["uid"]))[0]["username"]
    outsider = _run(_sql("SELECT username FROM users WHERE id = $1", klass["other"]))[0]["username"]

    async def fake_sessions(usernames, start, end):
        rows = [{"username": name, "connection": f"HV/{HOST}/lab-a", "start": base, "end": base + timedelta(minutes=45)},
                {"username": name, "connection": f"HV/{HOST}/lab-b", "start": base + timedelta(hours=2), "end": base + timedelta(hours=2, minutes=15)},
                {"username": outsider, "connection": f"HV/{HOST}/lab-x", "start": base, "end": base + timedelta(minutes=99)}]
        now = datetime.now(timezone.utc)
        return [r for r in rows if r["username"].lower() in usernames and r["start"] < end and (r["end"] or now) > start]

    monkeypatch.setattr(remote_history, "sessions_between", fake_sessions)
    _run(_sql("INSERT INTO openweb_sessions (id, user_id, username, role, target_ip, created_at, expires_at, last_seen) "
              "VALUES ($1, $2, $3, 'student', '10.0.0.5', $4, $5, $6)", uuid.uuid4().hex, klass["uid"], name, base, base + timedelta(hours=1), base + timedelta(minutes=20)))
    _run(_sql("INSERT INTO ssh_sessions (user_id, username, role, fingerprint, client_ip, client_port, started_at, ended_at) "
              "VALUES ($1, $2, 'student', 'fp', '203.0.113.5', 40000, $3, $4)", klass["uid"], name, base, base + timedelta(minutes=10)))
    _run(_sql("DELETE FROM audit_logs WHERE user_id = $1 AND action_type = 'AUTH_LOGIN'", klass["uid"]))      # login dari fixture token
    for i in range(3):
        _run(_sql("INSERT INTO audit_logs (user_id, username, user_role, action_type, created_at) VALUES ($1, $2, 'student', 'AUTH_LOGIN', $3)",
                  klass["uid"], name, base + timedelta(hours=i)))
    yield {"name": name}
    _run(_sql("DELETE FROM openweb_sessions WHERE user_id = $1", klass["uid"]))
    _run(_sql("DELETE FROM ssh_sessions WHERE user_id = $1", klass["uid"]))
    _run(_sql("DELETE FROM audit_logs WHERE user_id = $1 AND action_type = 'AUTH_LOGIN'", klass["uid"]))


def test_usage_report_totals_per_student(client, klass, activity, sysadmin_token):
    r = client.get(f"{L}/groups/{klass['gid']}/usage-report", headers=auth(sysadmin_token))
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["remote_available"] is True and rep["group"]["id"] == klass["gid"]
    assert len(rep["students"]) == 1                                                     # mahasiswa di luar kelas tidak ikut
    s = rep["students"][0]
    assert s["username"] == activity["name"]
    assert (s["remote_sessions"], s["remote_minutes"]) == (2, 60)
    assert (s["web_sessions"], s["web_minutes"]) == (1, 20)
    assert (s["ssh_sessions"], s["ssh_minutes"]) == (1, 10)
    assert s["total_sessions"] == 4 and s["total_minutes"] == 90 and s["logins"] == 3
    assert s["vms"] == ["lab-a", "lab-b"] and s["active_days"] >= 1 and s["last_active"]
    assert rep["summary"] == {"students": 1, "active_students": 1, "participation_pct": 100, "total_minutes": 90,
                              "avg_minutes_active": 90, "total_sessions": 4}


def test_usage_report_counts_inactive_students_and_respects_range(client, klass, activity, sysadmin_token):
    far = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%d")
    far_to = (datetime.now(timezone.utc) - timedelta(days=190)).strftime("%Y-%m-%d")
    rep = client.get(f"{L}/groups/{klass['gid']}/usage-report", params={"from": far, "to": far_to}, headers=auth(sysadmin_token)).json()
    assert rep["summary"]["active_students"] == 0 and rep["summary"]["participation_pct"] == 0
    assert rep["students"][0]["total_minutes"] == 0 and rep["students"][0]["active"] is False


def test_usage_report_csv_and_validation(client, klass, activity, sysadmin_token, student_token):
    gid = klass["gid"]
    csv_r = client.get(f"{L}/groups/{gid}/usage-report", params={"format": "csv"}, headers=auth(sysadmin_token))
    assert csv_r.status_code == 200 and csv_r.headers["content-type"].startswith("text/csv")
    body = csv_r.content.decode("utf-8-sig").splitlines()
    assert body[0].startswith("Username,Nama,Login") and activity["name"] in body[1] and ",90," in body[1]
    assert "attachment" in csv_r.headers["content-disposition"] and f"kelas-{gid}" in csv_r.headers["content-disposition"]
    bad = lambda **p: client.get(f"{L}/groups/{gid}/usage-report", params=p, headers=auth(sysadmin_token)).status_code
    assert bad(**{"from": "kemarin"}) == 400 and bad(**{"from": "2026-11-05", "to": "2026-11-01"}) == 400 and bad(format="xml") == 400
    assert client.get(f"{L}/groups/{gid}/usage-report", headers=auth(student_token)).status_code == 403
    assert client.get(f"{L}/groups/999999/usage-report", headers=auth(sysadmin_token)).status_code == 404


def test_usage_report_when_guacamole_history_is_unavailable(client, klass, monkeypatch, sysadmin_token):
    async def none(*a, **k):
        return None
    monkeypatch.setattr(remote_history, "sessions_between", none)
    rep = client.get(f"{L}/groups/{klass['gid']}/usage-report", headers=auth(sysadmin_token)).json()
    assert rep["remote_available"] is False and rep["students"][0]["remote_sessions"] == 0


# ══ pendukung antarmuka ═══════════════════════════════════════════════════════

def test_group_vm_list_shows_baseline_status(client, klass, pve, sysadmin_token, student_token):
    _baseline(client, pve, sysadmin_token, VM_A)
    r = client.get(f"{L}/groups/{klass['gid']}/vms", headers=auth(sysadmin_token))
    assert r.status_code == 200
    assert {v["vm_id"]: v["baseline"] for v in r.json()} == {str(VM_A): True, str(VM_B): False}
    assert client.get(f"{L}/groups/{klass['gid']}/vms", headers=auth(student_token)).status_code == 403


def test_my_exam_tells_students_what_is_going_on(client, klass, sysadmin_token, student_token):
    assert client.get(f"{L}/my-exam", headers=auth(student_token)).json() == {"exam": None}
    exam = _exam(client, sysadmin_token, klass["gid"], name="Ujian Akhir").json()
    soon = client.get(f"{L}/my-exam", headers=auth(student_token)).json()["exam"]
    assert soon["name"] == "Ujian Akhir" and soon["live"] is False                      # akan datang (belum membatasi)
    _make_live(exam["id"])
    live = client.get(f"{L}/my-exam", headers=auth(student_token)).json()["exam"]
    assert live["live"] is True and live["block_open_web"] is True and live["ends_at"]
    assert "group_id" not in live                                                       # tidak membocorkan data internal


def test_system_config_exposes_student_reset_flag(client, superadmin_token, student_token):
    client.put(f"{L}/settings", headers=auth(superadmin_token), json={"students_can_reset": True})
    assert client.get("/api/v1/system/config", headers=auth(student_token)).json()["lab_reset_students"] is True
    client.put(f"{L}/settings", headers=auth(superadmin_token), json={"students_can_reset": False})
    assert client.get("/api/v1/system/config", headers=auth(student_token)).json()["lab_reset_students"] is False
