"""
Audit & Remote: kelengkapan Activity Log, filter/ekspor, rekap login gagal, riwayat Remote dari
database Guacamole, dan memutus sesi (Remote, Web, SSH) dengan pilihan pemblokiran.
"""
import csv
import io
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from tests.conftest import DATABASE_URL, _run, auth

U = "/api/v1/users"
A = "/api/admin"
PW = "Rahasia123!"
BASTION = {"X-Bastion-Token": "token-bastion-uji"}


async def _sql(sql, *args, url=DATABASE_URL):
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def _audit(client, token, **params):
    r = client.get(f"{A}/audit-logs", params={"page_size": 200, **params}, headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _details(client, token, action, needle):
    return [i["detail"] for i in _audit(client, token, action=action, search=needle)]


def _uid():
    return uuid.uuid4().hex[:6]


@pytest.fixture
def make_user(client, superadmin_token):
    created = []

    def make(role="student"):
        name = f"tst_ar_{role[:3]}_{_uid()}"
        r = client.post(U, headers=auth(superadmin_token),
                        json={"username": name, "password": PW, "full_name": name.title(), "role": role})
        assert r.status_code == 200, r.text
        created.append(r.json()["id"])
        return r.json()["id"], name

    yield make
    for uid in created:
        client.delete(f"{U}/{uid}", headers=auth(superadmin_token))


def _login(client, name):
    r = client.post(f"{U}/login", json={"username": name, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ── Activity Log mencatat perubahan akun, grup, dan instance ──────────────────

def test_user_lifecycle_is_audited(client, superadmin_token):
    name = f"tst_ar_life_{_uid()}"
    r = client.post(U, headers=auth(superadmin_token),
                    json={"username": name, "password": PW, "full_name": "Awal", "role": "student"})
    uid = r.json()["id"]
    assert any(f"'{name}'" in d for d in _details(client, superadmin_token, "USER_CREATE", name))

    client.put(f"{U}/{uid}", headers=auth(superadmin_token),
               json={"full_name": "Awal", "role": "sysadmin", "is_active": False, "email": f"{name}@kampus.test"})
    update = _details(client, superadmin_token, "USER_UPDATE", name)
    assert len(update) == 1
    assert "peran student → sysadmin" in update[0] and "dinonaktifkan" in update[0] and "email" in update[0]
    assert "nama" not in update[0]                                    # nama tidak berubah

    client.put(f"{U}/{uid}", headers=auth(superadmin_token), json={"full_name": "Awal"})
    assert len(_details(client, superadmin_token, "USER_UPDATE", name)) == 1   # tanpa perubahan: tidak dicatat
    client.put(f"{U}/{uid}", headers=auth(superadmin_token), json={"role": "superadmin"})
    crit = _audit(client, superadmin_token, action="USER_UPDATE", search=name, severity="CRITICAL")
    assert len(crit) == 1 and "sysadmin → superadmin" in crit[0]["detail"]

    assert client.delete(f"{U}/{uid}", headers=auth(superadmin_token)).status_code == 200
    assert _details(client, superadmin_token, "USER_DELETE", name)


def test_group_changes_are_audited(client, sysadmin_token, make_user):
    _, member = make_user()
    uid = client.get(U, headers=auth(sysadmin_token)).json()
    uid = next(u["id"] for u in uid if u["username"] == member)
    gname = f"tst-grup-{_uid()}"
    g = client.post("/api/v1/groups", json={"name": gname}, headers=auth(sysadmin_token)).json()
    G = f"/api/v1/groups/{g['id']}"
    client.put(G, json={"name": gname + "-b", "description": "kelas pagi"}, headers=auth(sysadmin_token))
    client.post(f"{G}/members", json={"user_id": uid}, headers=auth(sysadmin_token))
    client.delete(f"{G}/members/{uid}", headers=auth(sysadmin_token))
    vm = {"vm_id": "9901", "host_name": "lab__node1"}
    assert client.post(f"{G}/vms", json=vm, headers=auth(sysadmin_token)).status_code == 201
    client.put(f"{G}/vms", json={**vm, "auth_mode": "credentials", "os_username": "siswa", "os_password": "x1234567"},
               headers=auth(sysadmin_token))
    client.request("DELETE", f"{G}/vms", json=vm, headers=auth(sysadmin_token))
    assert client.delete(G, headers=auth(sysadmin_token)).status_code == 204

    rows = _audit(client, sysadmin_token, search=gname)
    acts = {r["action_type"] for r in rows}
    assert {"GROUP_CREATE", "GROUP_UPDATE", "GROUP_MEMBER_ADD", "GROUP_MEMBER_REMOVE",
            "GROUP_VM_ADD", "GROUP_VM_UPDATE", "GROUP_VM_REMOVE", "GROUP_DELETE"} <= acts
    upd = next(r["detail"] for r in rows if r["action_type"] == "GROUP_VM_UPDATE")
    assert "akun OS 'siswa'" in upd and "password diganti" in upd and "x1234567" not in upd


def test_proxmox_instance_changes_are_audited(client, superadmin_token):
    label = f"tst_pve_{_uid()}"
    P = "/api/v1/proxmox/instances"
    r = client.post(P, json={"label": label, "host": "10.9.9.9", "token_id": "root@pam!ccd",
                             "token_secret": "rahasia-token-uji"}, headers=auth(superadmin_token))
    assert r.status_code == 200, r.text
    client.put(f"{P}/{label}", json={"host": "10.9.9.10", "token_secret": "rahasia-token-baru"},
               headers=auth(superadmin_token))
    client.delete(f"{P}/{label}", headers=auth(superadmin_token))
    rows = _audit(client, superadmin_token, search=label)
    acts = {r["action_type"]: r["detail"] for r in rows}
    assert set(acts) >= {"PVE_INSTANCE_ADD", "PVE_INSTANCE_UPDATE", "PVE_INSTANCE_DELETE"}
    assert "10.9.9.9 → 10.9.9.10" in acts["PVE_INSTANCE_UPDATE"] and "secret token diganti" in acts["PVE_INSTANCE_UPDATE"]
    assert not any("rahasia-token" in d for d in acts.values())


# ── Filter, ekspor CSV, rekap login gagal ─────────────────────────────────────

def test_audit_filters_and_actions(client, sysadmin_token, student_token, make_user):
    _, name = make_user()
    _login(client, name)
    rows = _audit(client, sysadmin_token, username=name.upper())          # tanpa beda huruf besar/kecil
    assert rows and {r["username"] for r in rows} == {name}
    actions = client.get(f"{A}/audit-logs/actions", headers=auth(sysadmin_token)).json()["actions"]
    assert "USER_CREATE" in actions and actions == sorted(actions)
    assert all(r["action_type"] == "USER_CREATE" for r in _audit(client, sysadmin_token, action="USER_CREATE"))
    for path in ("/audit-logs/actions", "/audit-logs/failed-logins", "/audit-logs/export", "/remote/history/export",
                 "/openweb/history/export", "/ssh/history/export"):
        assert client.get(A + path, headers=auth(student_token)).status_code == 403, path


def test_failed_logins_summary(client, sysadmin_token, make_user):
    _, known = make_user()
    ghost = f"tst_ar_ghost_{_uid()}"
    ip = f"198.51.100.{int(_uid(), 16) % 250 + 1}"
    for name in (known, known, ghost):
        r = client.post(f"{U}/login", json={"username": name, "password": "salah-salah"}, headers={"X-Real-IP": ip})
        assert r.status_code == 401
    data = client.get(f"{A}/audit-logs/failed-logins", params={"days": 1}, headers=auth(sysadmin_token)).json()
    users = {u["username"]: u for u in data["by_user"]}
    assert users[known]["attempts"] == 2 and users[known]["known"] and users[known]["ips"] == [ip]
    assert users[ghost]["attempts"] == 1 and not users[ghost]["known"]
    by_ip = next(i for i in data["by_ip"] if i["ip"] == ip)
    assert by_ip["attempts"] == 3 and by_ip["accounts"] == 2 and set(by_ip["usernames"]) == {known, ghost}
    assert client.get(f"{A}/audit-logs/failed-logins", params={"days": 91}, headers=auth(sysadmin_token)).status_code == 422


def test_audit_export_csv(client, sysadmin_token):
    evil = f"=HYPERLINK(tst{_uid()})"
    client.post(f"{U}/login", json={"username": evil, "password": "x"})
    r = client.get(f"{A}/audit-logs/export", params={"username": evil}, headers={**auth(sysadmin_token), "Accept-Language": "en"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="ccd-activity-log-' in r.headers["content-disposition"]
    assert r.content.startswith("﻿".encode())
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][:4] == ["Time (Asia/Jakarta)", "User", "Role", "Action"]
    assert len(rows) == 2 and rows[1][1] == "'" + evil and rows[1][3] == "AUTH_LOGIN_FAILED"   # tidak jadi rumus
    assert datetime.strptime(rows[1][0], "%Y-%m-%d %H:%M:%S")
    assert _audit(client, sysadmin_token, action="AUDIT_EXPORT")


# ── Riwayat Remote dari database Guacamole ────────────────────────────────────

GUAC_SCHEMA = """
CREATE TABLE guacamole_connection (connection_id SERIAL PRIMARY KEY, connection_name TEXT, protocol TEXT);
CREATE TABLE guacamole_connection_history (
    history_id SERIAL PRIMARY KEY, user_id INTEGER, username TEXT NOT NULL, remote_host TEXT,
    connection_id INTEGER, connection_name TEXT NOT NULL, sharing_profile_id INTEGER,
    sharing_profile_name TEXT, start_date TIMESTAMPTZ NOT NULL, end_date TIMESTAMPTZ);
"""


@pytest.fixture
def guac_db(client, monkeypatch):
    from services import remote_history
    name = f"tst_guac_{_uid()}"
    _run(_sql(f"CREATE DATABASE {name}"))
    url = DATABASE_URL.rsplit("/", 1)[0] + "/" + name
    async def setup():
        conn = await asyncpg.connect(url)
        try:
            await conn.execute(GUAC_SCHEMA)
        finally:
            await conn.close()
    _run(setup())
    monkeypatch.setenv("GUAC_DATABASE_URL", url)
    remote_history.reset()
    yield url
    client.portal.call(remote_history.close)
    remote_history.reset()
    _run(_sql(f"DROP DATABASE {name} WITH (FORCE)"))


def _guac_rows(url, rows):
    async def go():
        conn = await asyncpg.connect(url)
        try:
            ids = {}
            for proto, cname in {(r[3], r[1]) for r in rows}:
                ids[cname] = await conn.fetchval(
                    "INSERT INTO guacamole_connection (connection_name, protocol) VALUES ($1, $2) RETURNING connection_id",
                    cname, proto)
            for user, cname, ip, _, start, end in rows:
                await conn.execute(
                    """INSERT INTO guacamole_connection_history
                       (username, remote_host, connection_id, connection_name, start_date, end_date)
                       VALUES ($1, $2, $3, $4, $5, $6)""", user, ip, ids[cname], cname, start, end)
        finally:
            await conn.close()
    _run(go())


def test_remote_history_from_guacamole_db(client, sysadmin_token, guac_db):
    from services.remote_history import _own_ips
    now = datetime.now(timezone.utc)
    own = next(iter(_own_ips()), "172.31.255.254")
    rows = [("budi", "HV/lab__pve1/tkj-budi", "100.101.1.5", "ssh", now - timedelta(hours=3), now - timedelta(hours=2))]
    rows += [("budi", "HV/lab__pve1/win-lab@siswa", own, "rdp", now - timedelta(minutes=30), None)]
    rows += [(f"mhs{i:03d}", f"HV/lab__pve1/vm-{i:03d}", "10.0.0.9", "ssh", now - timedelta(days=1, minutes=i),
              now - timedelta(days=1)) for i in range(600)]
    rows += [("lama", "HV/lab__pve1/vm-lama", "10.0.0.8", "ssh", now - timedelta(days=400), now - timedelta(days=400))]
    _guac_rows(guac_db, rows)
    # Koneksi yang dibuat ulang (id lama hilang): protokol tetap ketemu lewat nama koneksinya.
    _run(_sql("UPDATE guacamole_connection_history SET connection_id = 99999 WHERE username = 'budi' AND end_date IS NOT NULL",
              url=guac_db))

    r = client.get(f"{A}/remote/history", params={"page_size": 10, "page": 3}, headers=auth(sysadmin_token)).json()
    assert r["total"] == 603 and len(r["items"]) == 10                       # tidak berhenti di 500
    budi = client.get(f"{A}/remote/history", params={"username": "BUDI"}, headers=auth(sysadmin_token)).json()
    assert budi["total"] == 2
    live, old = budi["items"]
    assert live["active"] and live["vm"] == "win-lab" and live["os_account"] == "siswa" and live["protocol"] == "RDP"
    assert live["remote_host"] == ""                                         # IP backend tidak ditampilkan
    assert old["remote_host"] == "100.101.1.5" and old["duration_s"] == 3600 and old["protocol"] == "SSH"
    found = client.get(f"{A}/remote/history", params={"search": "vm-05"}, headers=auth(sysadmin_token)).json()
    assert found["total"] == 10 and all("vm-05" in h["vm"] for h in found["items"])

    csv_rows = list(csv.reader(io.StringIO(client.get(f"{A}/remote/history/export", params={"username": "budi"},
                                                      headers=auth(sysadmin_token)).content.decode("utf-8-sig"))))
    assert len(csv_rows) == 3 and csv_rows[2][:6] == ["budi", "tkj-budi", "", "lab__pve1", "SSH", "100.101.1.5"]

    from services import remote_history
    deleted = client.portal.call(remote_history.purge, now - timedelta(days=180))
    assert deleted == 1                                                      # hanya yang lewat retensi
    assert client.get(f"{A}/remote/history", headers=auth(sysadmin_token)).json()["total"] == 602


def test_guacamole_token_carries_client_ip(client, monkeypatch):
    from services import guac_sync
    seen = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, headers=None):
            seen.update(headers or {})

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"authToken": "t"}
            return R()

    monkeypatch.setattr(guac_sync.httpx, "AsyncClient", FakeClient)
    assert client.portal.call(guac_sync.get_user_token, "budi", "pw", "203.0.113.9") == {"authToken": "t"}
    assert seen["X-Forwarded-For"] == "203.0.113.9"


# ── Memutus sesi Remote dengan pilihan pemblokiran ────────────────────────────

@pytest.fixture
def fake_guac(monkeypatch):
    from services import guac_sync
    state = {"sessions": [], "killed": []}

    async def active():
        return [s for s in state["sessions"] if s["active_id"] not in state["killed"]]

    async def kill(active_id):
        state["killed"].append(active_id)
        return True

    monkeypatch.setattr(guac_sync, "get_active_sessions", active)
    monkeypatch.setattr(guac_sync, "kill_session", kill)

    def add(username, vm, host="lab__pve1"):
        sid = uuid.uuid4().hex
        state["sessions"].append({"active_id": sid, "username": username, "vm": vm, "host": host, "os_account": "",
                                  "connection": f"HV/{host}/{vm}", "protocol": "SSH", "remote_host": "", "start_date": 0})
        return sid
    state["add"] = add
    return state


def _kill(client, token, active_id, block="none"):
    return client.post(f"{A}/remote/kill-session", json={"active_id": active_id, "block": block}, headers=auth(token))


def test_remote_kill_records_who_and_where(client, sysadmin_token, fake_guac, make_user):
    _, name = make_user()
    sid = fake_guac["add"](name, "tkj-12a-budi")
    r = _kill(client, sysadmin_token, sid)
    assert r.status_code == 200 and fake_guac["killed"] == [sid]
    detail = _details(client, sysadmin_token, "REMOTE_KILL", name)[0]
    assert f"'{name}'" in detail and "VM tkj-12a-budi di lab__pve1" in detail and sid not in detail
    assert _kill(client, sysadmin_token, sid).status_code == 404               # sudah berakhir


def test_remote_kill_and_deactivate_account(client, sysadmin_token, superadmin_token, fake_guac, make_user):
    uid, name = make_user()
    tok = _login(client, name)
    other = fake_guac["add"](name, "vm-lain")
    r = _kill(client, sysadmin_token, fake_guac["add"](name, "vm-a"), "account")
    assert r.status_code == 200, r.text
    assert r.json()["sessions"]["remote"] == 1 and other in fake_guac["killed"]   # sesi lain ikut diputus
    assert client.get(f"{U}/me", headers=auth(tok)).status_code in (401, 403)
    assert client.post(f"{U}/login", json={"username": name, "password": PW}).status_code == 403
    assert _details(client, sysadmin_token, "USER_LOCKOUT", name)

    # Sysadmin tidak boleh menonaktifkan sysadmin lain; sesi tidak diputus kalau blokir ditolak.
    _, admin2 = make_user("sysadmin")
    sid = fake_guac["add"](admin2, "vm-b")
    assert _kill(client, sysadmin_token, sid, "account").status_code == 403
    assert sid not in fake_guac["killed"]
    assert _kill(client, superadmin_token, sid, "account").status_code == 200


def test_remote_kill_and_revoke_vm(client, sysadmin_token, fake_guac, make_user):
    uid, name = make_user()
    host = "lab__pve1"
    client.post(f"{U}/vm-assignments", json={"user_id": uid, "vm_id": "4242", "host_name": host, "vm_name": "tkj-x"},
                headers=auth(sysadmin_token))
    r = _kill(client, sysadmin_token, fake_guac["add"](name, "tkj-x", host), "vm")
    assert r.status_code == 200, r.text
    assert client.get(f"{U}/{uid}/vm-assignments", headers=auth(sysadmin_token)).json() == []
    assert "mencabut penugasan" in _details(client, sysadmin_token, "REMOTE_KILL", name)[0]

    sid = fake_guac["add"](name, "tkj-x", host)                                # sudah tidak ditugaskan
    r = _kill(client, sysadmin_token, sid, "vm")
    assert r.status_code == 409 and sid not in fake_guac["killed"]

    g = client.post("/api/v1/groups", json={"name": f"tst-kelas-{_uid()}"}, headers=auth(sysadmin_token)).json()
    client.post(f"/api/v1/groups/{g['id']}/members", json={"user_id": uid}, headers=auth(sysadmin_token))
    client.post(f"/api/v1/groups/{g['id']}/vms", json={"vm_id": "tkj-x", "host_name": host}, headers=auth(sysadmin_token))
    r = _kill(client, sysadmin_token, sid, "vm")
    assert r.status_code == 409 and g["name"] in r.json()["detail"]
    client.delete(f"/api/v1/groups/{g['id']}", headers=auth(sysadmin_token))


# ── Open Web: cabut link dan nonaktifkan akun ─────────────────────────────────

def test_openweb_kill_and_deactivate(client, sysadmin_token, make_user):
    uid, name = make_user()
    sids = [uuid.uuid4().hex, uuid.uuid4().hex]
    for sid in sids:
        _run(_sql("""INSERT INTO openweb_sessions (id, user_id, username, role, target_ip, expires_at)
                     VALUES ($1, $2, $3, 'student', '10.0.0.7', NOW() + interval '1 hour')""", sid, uid, name))
    r = client.post(f"{A}/openweb/kill", json={"session_id": sids[0], "block": "account"}, headers=auth(sysadmin_token))
    assert r.status_code == 200, r.text
    assert r.json()["sessions"]["web"] == 1                                     # link kedua ikut dicabut
    hist = client.get(f"{A}/openweb/history", params={"username": name}, headers=auth(sysadmin_token)).json()
    assert hist["total"] == 2 and {h["status"] for h in hist["items"]} == {"revoked"}
    assert not _run(_sql("SELECT is_active FROM users WHERE id = $1", uid))[0]["is_active"]


# ── SSH: perintah lewat bastion (ccd-kill) ────────────────────────────────────

@pytest.fixture
def ssh_session(monkeypatch, make_user):
    monkeypatch.setenv("BASTION_TOKEN", BASTION["X-Bastion-Token"])
    uid, name = make_user()

    def make():
        return _run(_sql(
            """INSERT INTO ssh_sessions (user_id, username, role, key_name, fingerprint, client_ip, client_port,
                                         monitor_pid, child_pid)
               VALUES ($1, $2, 'student', 'laptop', 'SHA256:uji', '203.0.113.50', $3, $4, $5) RETURNING id""",
            uid, name, int(_uid(), 16) % 30000 + 1024, 5000 + int(_uid(), 16) % 1000, 7000))[0]["id"]
    return name, make


def _bastion(client, seen):
    """Meniru ccd-kill: ambil satu batch perintah lalu laporkan id sesinya."""
    r = client.get("/api/v1/ssh-keys/kill-wait", params={"wait": 5}, headers=BASTION)
    seen.extend(r.text.splitlines())
    ids = "\n".join(line.split()[0] for line in r.text.splitlines())
    if ids:
        client.post("/api/v1/ssh-keys/killed", content=ids, headers=BASTION)


def test_ssh_kill_through_bastion(client, sysadmin_token, ssh_session):
    name, make = ssh_session
    sid = make()
    seen: list = []
    t = threading.Thread(target=_bastion, args=(client, seen))
    t.start()
    time.sleep(0.3)
    r = client.post(f"{A}/ssh/kill", json={"session_id": sid}, headers=auth(sysadmin_token))
    t.join(10)
    assert r.status_code == 200, r.text
    assert len(seen) == 1 and seen[0].split()[0] == str(sid) and seen[0].split()[2] == "7000"
    item = next(s for s in client.get(f"{A}/ssh/history", params={"username": name},
                                      headers=auth(sysadmin_token)).json()["items"] if s["id"] == sid)
    assert item["status"] == "killed" and item["killed_by"] == "tst_sysadmin" and item["ended_at"]
    assert _details(client, sysadmin_token, "SSH_KILL", name)
    assert client.post(f"{A}/ssh/kill", json={"session_id": sid}, headers=auth(sysadmin_token)).status_code == 404
    assert client.get("/api/v1/ssh-keys/kill-wait", params={"wait": 1}).status_code == 403


def test_ssh_kill_without_bastion_answer(client, sysadmin_token, ssh_session, monkeypatch):
    from services import ssh_kill
    monkeypatch.setattr(ssh_kill, "WAIT_SECONDS", 0.5)
    _, make = ssh_session
    sid = make()
    r = client.post(f"{A}/ssh/kill", json={"session_id": sid}, headers=auth(sysadmin_token))
    assert r.status_code == 504
    # Perintah ditarik lagi dan sesi tetap aktif tanpa tanda diputus.
    assert client.get("/api/v1/ssh-keys/kill-wait", params={"wait": 1}, headers=BASTION).text == ""
    row = _run(_sql("SELECT ended_at, killed_by FROM ssh_sessions WHERE id = $1", sid))[0]
    assert row["ended_at"] is None and row["killed_by"] is None


def test_lockout_queues_ssh_sessions(client, sysadmin_token, ssh_session, fake_guac):
    name, make = ssh_session
    sids = {make(), make()}
    sid = fake_guac["add"](name, "vm-z")
    r = _kill(client, sysadmin_token, sid, "account")
    assert r.status_code == 200 and r.json()["sessions"]["ssh"] == 2
    seen: list = []
    _bastion(client, seen)
    assert {int(line.split()[0]) for line in seen} == sids
    rows = _run(_sql("SELECT ended_at FROM ssh_sessions WHERE id = ANY($1::int[])", list(sids)))
    assert all(r["ended_at"] for r in rows)


def test_csv_cells_cannot_become_formulas():
    from services.csv_export import _cell, fmt_time
    assert _cell("=1+1") == "'=1+1" and _cell("@cmd") == "'@cmd" and _cell("-2") == "'-2" and _cell("+x") == "'+x"
    assert _cell("budi") == "budi" and _cell(None) == "" and _cell(["a", "b"]) == "a, b" and _cell(5) == "5"
    assert fmt_time(datetime(2026, 10, 7, 0, 30, tzinfo=timezone.utc)) == "2026-10-07 07:30:00"
    assert fmt_time(1_759_797_000_000) == "2025-10-07 07:30:00"


# ── Detail Activity Log mengikuti bahasa admin ────────────────────────────────

def _detail_in(client, token, lang, action, needle):
    r = client.get(f"{A}/audit-logs", params={"action": action, "search": needle, "page_size": 20},
                   headers={**auth(token), "Accept-Language": lang})
    return [i["detail"] for i in r.json()["items"]]


def test_audit_detail_follows_the_viewer_language(client, superadmin_token):
    name = f"tst-bahasa-{_uid()}"
    g = client.post("/api/v1/groups", json={"name": name}, headers=auth(superadmin_token)).json()
    client.put(f"/api/v1/groups/{g['id']}", json={"name": name + "-b", "description": "x"}, headers=auth(superadmin_token))
    assert client.delete(f"/api/v1/groups/{g['id']}", headers=auth(superadmin_token)).status_code == 204

    assert _detail_in(client, superadmin_token, "id", "GROUP_CREATE", name)[0].endswith(f"membuat grup '{name}'")
    assert _detail_in(client, superadmin_token, "en", "GROUP_CREATE", name)[0].endswith(f"created the group '{name}'")
    # Daftar perubahan di dalam kalimat ikut berganti bahasa, nama yang diketik pengguna tidak.
    en = _detail_in(client, superadmin_token, "en", "GROUP_UPDATE", name)[0]
    idn = _detail_in(client, superadmin_token, "id", "GROUP_UPDATE", name)[0]
    assert "changed the group" in en and f"name '{name}' → '{name}-b'" in en and "description changed" in en
    assert "mengubah grup" in idn and f"nama '{name}' → '{name}-b'" in idn and "deskripsi diubah" in idn
    # Pencarian menemukan kata dari kedua bahasa.
    r = client.get(f"{A}/audit-logs", params={"search": "deleted the group", "page_size": 50}, headers=auth(superadmin_token))
    assert any(name in i["detail"] for i in r.json()["items"])
    assert any(name in i["detail"] for i in client.get(f"{A}/audit-logs", params={"search": "menghapus grup", "page_size": 50},
                                                         headers=auth(superadmin_token)).json()["items"])


def test_old_rows_without_english_fall_back_to_indonesian(client, superadmin_token):
    marker = f"catatan-lama-{_uid()}"
    _run(_sql("INSERT INTO audit_logs (username, action_type, severity_level, detail_message) VALUES ('tst_lama', 'TEST_OLD', 'INFO', $1)", marker))
    assert _detail_in(client, superadmin_token, "en", "TEST_OLD", marker) == [marker]
    assert _detail_in(client, superadmin_token, "id", "TEST_OLD", marker) == [marker]
    _run(_sql("UPDATE audit_logs SET detail_en = $2 WHERE detail_message = $1", marker, "old row in English"))
    assert _detail_in(client, superadmin_token, "en", "TEST_OLD", marker) == ["old row in English"]
    assert _detail_in(client, superadmin_token, "id", "TEST_OLD", marker) == [marker]


def test_audit_csv_uses_the_viewer_language(client, superadmin_token):
    name = f"tst-csv-{_uid()}"
    client.post("/api/v1/groups", json={"name": name}, headers=auth(superadmin_token))
    def export(lang):
        r = client.get(f"{A}/audit-logs/export", params={"search": name}, headers={**auth(superadmin_token), "Accept-Language": lang})
        return list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))[1][5]
    assert export("en").endswith(f"created the group '{name}'") and export("id").endswith(f"membuat grup '{name}'")


def test_ticket_system_message_has_both_languages(client, student_token, sysadmin_token):
    t = client.post("/api/tickets", headers=auth(student_token), json={"title": "uji pesan sistem"}).json()
    assert client.patch(f"/api/tickets/{t['id']}/status", headers=auth(sysadmin_token), json={"status": "IN_PROGRESS"}).status_code == 200
    msgs = client.get(f"/api/tickets/{t['id']}", headers=auth(student_token)).json()["messages"]
    system = next(m for m in msgs if m["sender_role"] == "system")
    assert system["message"] == "Status diubah ke IN_PROGRESS oleh tst_sysadmin (sysadmin)"
    assert system["message_en"] == "Status changed to IN_PROGRESS by tst_sysadmin (sysadmin)"
    human = client.post(f"/api/tickets/{t['id']}/messages", headers=auth(student_token), json={"message": "halo"})
    assert human.status_code == 200
    again = client.get(f"/api/tickets/{t['id']}", headers=auth(student_token)).json()["messages"]
    assert [m["message_en"] for m in again if m["sender_role"] != "system"] == [None]       # pesan pengguna tidak diterjemahkan
