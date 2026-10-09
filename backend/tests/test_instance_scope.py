"""
Sysadmin hanya mengelola Proxmox yang ditugaskan kepadanya (services/scope.py); superadmin mengelola semuanya.
Sysadmin tanpa penugasan tidak melihat apa pun. Yang diuji: daftar instance, jalur per instance dan per host,
jaringan, grup, penugasan VM, tiket, Infra Request, Activity Log, sesi Remote/Web/SSH, Open Web, bastion SSH,
dan akses koneksi Guacamole. Proxmox dan Guacamole diganti tiruan.
"""
import asyncpg
import pytest

from services import guac_sync, proxmox_instances, remote_history, scope
from tests.conftest import DATABASE_URL, _run, auth
from tests.test_ssh_keys import _ed25519_pub

A, B = "scopeA", "scopeB"
HA, HB = f"{A}__pve", f"{B}__pve"
IP_A, IP_B, IP_X = "10.61.0.5", "10.62.0.5", "10.99.99.99"
SYSADMIN = "tst_sysadmin"
MARK = "SCOPE-MARK"


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def _uid(name):
    return _run(_sql("SELECT id FROM users WHERE username = $1", name))[0]["id"]


class FakePve:
    async def list_nodes(self):
        return []

    async def list_vms(self, node):
        return []

    async def guest_ip_status(self, node, vmid):
        return {"ip": None, "agent_enabled": True, "reason": None}


def _clean():
    uid = _uid(SYSADMIN)
    _run(_sql("DELETE FROM user_instances WHERE user_id = $1", uid))
    _run(_sql("DELETE FROM user_ssh_keys WHERE user_id = $1", uid))
    _run(_sql("DELETE FROM tickets WHERE title LIKE $1", f"{MARK}%"))
    _run(_sql("DELETE FROM infrastructure_requests WHERE notes LIKE $1", f"{MARK}%"))
    _run(_sql("DELETE FROM audit_logs WHERE detail_message LIKE $1", f"{MARK}%"))
    _run(_sql("DELETE FROM openweb_sessions WHERE target_ip = ANY($1)", [IP_A, IP_B]))
    _run(_sql("DELETE FROM ssh_sessions WHERE client_ip = '203.0.113.77'"))
    _run(_sql("DELETE FROM group_vm_access WHERE host_name = ANY($1)", [HA, HB]))
    _run(_sql("DELETE FROM groups WHERE name LIKE 'tst_scope_%'"))
    _run(_sql("DELETE FROM vm_assignments WHERE host_name = ANY($1)", [HA, HB]))
    _run(_sql("DELETE FROM vm_credentials WHERE host_name = ANY($1)", [HA, HB]))
    _run(_sql("DELETE FROM vms WHERE host_name = ANY($1)", [HA, HB]))
    _run(_sql("DELETE FROM proxmox_instances WHERE label LIKE 'scope%'"))


@pytest.fixture
def env(client, superadmin_token, sysadmin_token, student_token, monkeypatch):
    async def get_client(label):
        return FakePve()

    async def no_op(*args, **kwargs):
        return True

    monkeypatch.setattr(proxmox_instances, "get_client", get_client)
    import routers.users as users_router
    monkeypatch.setattr(users_router, "guac_retry", no_op)
    import services.assignments as assignments
    monkeypatch.setattr(assignments, "guac_retry", no_op)
    _clean()
    for label in (A, B):
        _run(_sql("INSERT INTO proxmox_instances (label, host, token_id, token_secret_enc) VALUES ($1, $2, 'u@pam!t', 'x')",
                  label, f"10.0.0.{1 if label == A else 2}:8006"))
    for host, ip, vmid in ((HA, IP_A, "101"), (HB, IP_B, "201")):
        _run(_sql("INSERT INTO vms (vm_id, host_name, vm_name) VALUES ($1, $2, $3)", vmid, host, f"vm-{vmid}"))
        _run(_sql("INSERT INTO vm_credentials (vm_id, host_name, username, ssh_host, guac_protocol) VALUES ($1, $2, 'mhs', $3, 'ssh')",
                  vmid, host, ip))
    yield
    _clean()


def _grant(*labels):
    uid = _uid(SYSADMIN)
    for l in labels:
        _run(_sql("INSERT INTO user_instances (user_id, instance, assigned_by) VALUES ($1, $2, 'uji')", uid, l))


def _labels(resp):
    return sorted(i["label"] for i in resp.json())


# ── Daftar dan jalur per instance ─────────────────────────────────────────────

def test_sysadmin_without_assignment_sees_nothing(client, sysadmin_token, env):
    h = auth(sysadmin_token)
    assert client.get("/api/v1/proxmox/instances", headers=h).json() == []
    assert client.get("/api/v1/proxmox/all-vms", headers=h).json() == []
    assert client.get("/api/v1/proxmox/host-status", headers=h).json() == []
    assert client.get("/api/v1/networks", headers=h).json() == {"instances": []}


def test_sysadmin_sees_only_assigned_instances_and_superadmin_all(client, sysadmin_token, superadmin_token, env):
    _grant(A)
    assert _labels(client.get("/api/v1/proxmox/instances", headers=auth(sysadmin_token))) == [A]
    assert {A, B} <= set(_labels(client.get("/api/v1/proxmox/instances", headers=auth(superadmin_token))))
    nets = client.get("/api/v1/networks", headers=auth(sysadmin_token)).json()["instances"]
    assert [n["label"] for n in nets] == [A]


@pytest.mark.parametrize("path", [
    "/api/v1/proxmox/instances/{L}/nodes",
    "/api/v1/proxmox/instances/{L}/nodes/pve/vms/101/ip",
    "/api/v1/proxmox/instances/{L}/ssh-url",
    "/api/v1/ssh-creds/vm/{L}__pve/101",
    "/api/v1/linux-vm/{L}__pve/101/disk-info",
    "/api/v1/vm-metadata/{L}__pve",
    "/api/v1/networks/instances/{L}/check",
])
def test_paths_with_a_proxmox_are_refused_for_other_proxmox(client, sysadmin_token, superadmin_token, env, path):
    _grant(A)
    h = auth(sysadmin_token)
    denied = client.get(path.format(L=B), headers=h)
    assert denied.status_code == 403 and "ditugaskan" in denied.json()["detail"]
    assert client.get(path.format(L=A), headers=h).status_code != 403          # miliknya: lolos pembatasan
    assert client.get(path.format(L=B), headers=auth(superadmin_token)).status_code != 403


def test_sysadmin_cannot_create_or_use_switch_elsewhere(client, sysadmin_token, env):
    _grant(A)
    r = client.post("/api/v1/networks", headers=auth(sysadmin_token), json={"instance": B, "name": "kelas"})
    assert r.status_code == 403
    r = client.post(f"/api/v1/networks/instances/{B}/pools", headers=auth(sysadmin_token), json={"cidr": "10.130.0.0/16"})
    assert r.status_code == 403


def test_sysadmin_cannot_start_bulk_vms_elsewhere(client, sysadmin_token, env):
    _grant(A)
    body = {"instance": B, "node": "pve", "group_id": 1, "template_vmid": 100}
    assert client.post("/api/v1/vm-batches/preview", headers=auth(sysadmin_token), json=body).status_code == 403
    assert client.post("/api/v1/vm-batches", headers=auth(sysadmin_token), json=body).status_code == 403


def test_instance_added_by_sysadmin_is_assigned_to_them(client, sysadmin_token, env, monkeypatch):
    created = {}

    async def create_instance(label, host, token_id, token_secret, verify_ssl):
        await _insert_instance(label, host, token_id)
        created["label"] = label
        return {"label": label, "host": host, "token_id": token_id}

    monkeypatch.setattr(proxmox_instances, "create_instance", create_instance)
    r = client.post("/api/v1/proxmox/instances", headers=auth(sysadmin_token),
                    json={"label": "scopeNew", "host": "10.0.0.9:8006", "token_id": "u@pam!t", "token_secret": "s"})
    assert r.status_code == 200, r.text
    assert _labels(client.get("/api/v1/proxmox/instances", headers=auth(sysadmin_token))) == ["scopeNew"]


async def _insert_instance(label, host, token_id):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("INSERT INTO proxmox_instances (label, host, token_id, token_secret_enc) VALUES ($1, $2, $3, 'x')",
                           label, host, token_id)
    finally:
        await conn.close()


# ── Superadmin mengatur penugasan ─────────────────────────────────────────────

def test_only_superadmin_sets_instances_of_a_sysadmin(client, sysadmin_token, superadmin_token, env):
    uid = _uid(SYSADMIN)
    url = f"/api/v1/users/{uid}/instances"
    assert client.put(url, headers=auth(sysadmin_token), json={"instances": [A]}).status_code == 403
    assert client.get(url, headers=auth(sysadmin_token)).status_code == 403
    r = client.put(url, headers=auth(superadmin_token), json={"instances": [A, B, "tidak-ada"]})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == [A, B] and r.json()["instances"] == [A, B]                # label tak dikenal diabaikan
    r = client.put(url, headers=auth(superadmin_token), json={"instances": [B]})
    assert r.json()["added"] == [] and r.json()["removed"] == [A] and r.json()["instances"] == [B]
    assert client.get(url, headers=auth(superadmin_token)).json() == {"instances": [B]}
    logs = client.get("/api/admin/audit-logs", params={"action": "USER_INSTANCES"}, headers=auth(superadmin_token)).json()["items"]
    assert logs and "tst_sysadmin" in logs[0]["detail"]


def test_only_sysadmins_are_limited(client, superadmin_token, student_token, env):
    sid = _uid("tst_student")
    assert client.put(f"/api/v1/users/{sid}/instances", headers=auth(superadmin_token), json={"instances": [A]}).status_code == 400
    assert client.get("/api/v1/users/999999/instances", headers=auth(superadmin_token)).status_code == 404


def test_user_list_shows_instances_to_superadmin_only(client, sysadmin_token, superadmin_token, env):
    _grant(A)
    row = lambda tok: next(u for u in client.get("/api/v1/users", headers=auth(tok)).json() if u["username"] == SYSADMIN)
    assert row(superadmin_token)["instances"] == [A]
    assert row(sysadmin_token)["instances"] == []


# ── Penugasan VM dan grup ─────────────────────────────────────────────────────

def test_assign_vm_only_on_own_proxmox(client, sysadmin_token, student_token, env):
    _grant(A)
    sid = _uid("tst_student")
    h = auth(sysadmin_token)
    assert client.post("/api/v1/users/vm-assignments", headers=h,
                       json={"user_id": sid, "vm_id": "201", "host_name": HB, "vm_name": "vm-201"}).status_code == 403
    assert client.post("/api/v1/users/vm-assignments", headers=h,
                       json={"user_id": sid, "vm_id": "101", "host_name": HA, "vm_name": "vm-101"}).status_code == 200
    _run(_sql("INSERT INTO vm_assignments (user_id, vm_id, host_name, vm_name) VALUES ($1, '201', $2, 'vm-201')", sid, HB))
    shown = client.get(f"/api/v1/users/{sid}/vm-assignments", headers=h).json()
    assert [a["host_name"] for a in shown] == [HA]                                  # penugasan di Proxmox lain tak terlihat
    assert client.delete(f"/api/v1/users/{sid}/vm-assignments/201", headers=h).status_code == 403
    assert client.delete(f"/api/v1/users/{sid}/vm-assignments/101", headers=h).status_code == 200


def test_group_vm_access_only_on_own_proxmox(client, sysadmin_token, superadmin_token, env):
    _grant(A)
    gid = client.post("/api/v1/groups", headers=auth(superadmin_token), json={"name": "tst_scope_kelas"}).json()["id"]
    _run(_sql("INSERT INTO group_vm_access (group_id, vm_id, host_name) VALUES ($1, '201', $2)", gid, HB))
    h = auth(sysadmin_token)
    assert client.post(f"/api/v1/groups/{gid}/vms", headers=h, json={"vm_id": "201", "host_name": HB}).status_code == 403
    assert client.put(f"/api/v1/groups/{gid}/vms", headers=h, json={"vm_id": "201", "host_name": HB}).status_code == 403
    assert client.request("DELETE", f"/api/v1/groups/{gid}/vms", headers=h, json={"vm_id": "201", "host_name": HB}).status_code == 403
    assert client.post(f"/api/v1/groups/{gid}/vms", headers=h, json={"vm_id": "101", "host_name": HA}).status_code == 201
    assert [v["host_name"] for v in client.get(f"/api/v1/groups/{gid}/vms", headers=h).json()] == [HA]
    assert len(client.get(f"/api/v1/groups/{gid}/vms", headers=auth(superadmin_token)).json()) == 2


# ── Tiket dan Infra Request ───────────────────────────────────────────────────

def _ticket(client, tok, title, **vm):
    r = client.post("/api/tickets", headers=auth(tok), json={"title": f"{MARK} {title}", "category": "OTHERS", **vm})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_tickets_follow_the_proxmox_of_their_vm(client, sysadmin_token, superadmin_token, env):
    _grant(A)
    none = _ticket(client, superadmin_token, "tanpa vm")
    on_a = _ticket(client, superadmin_token, "di A", vm_id="101", host_name=HA)
    on_b = _ticket(client, superadmin_token, "di B", vm_id="201", host_name=HB)
    h = auth(sysadmin_token)
    titles = {t["title"] for t in client.get("/api/tickets", params={"search": MARK}, headers=h).json()["items"]}
    assert titles == {f"{MARK} tanpa vm", f"{MARK} di A"}
    assert len(client.get("/api/tickets", params={"search": MARK}, headers=auth(superadmin_token)).json()["items"]) == 3
    assert client.get(f"/api/tickets/{on_a}", headers=h).status_code == 200
    assert client.get(f"/api/tickets/{none}", headers=h).status_code == 200
    assert client.get(f"/api/tickets/{on_b}", headers=h).status_code == 403
    assert client.patch(f"/api/tickets/{on_b}/status", headers=h, json={"status": "IN_PROGRESS"}).status_code == 403
    assert client.post(f"/api/tickets/{on_b}/messages", headers=h, json={"message": "halo"}).status_code == 403
    assert client.patch(f"/api/tickets/{on_a}/status", headers=h, json={"status": "IN_PROGRESS"}).status_code == 200
    assert client.post("/api/tickets", headers=h, json={"title": f"{MARK} x", "vm_id": "201", "host_name": HB}).status_code == 403


def test_infra_requests_follow_the_linked_vm(client, sysadmin_token, superadmin_token, student_token, env):
    _grant(A)
    ids = {}
    for key, host in (("none", None), ("a", HA), ("b", HB)):
        r = client.post("/api/v1/infra-requests", headers=auth(student_token),
                        json={"request_type": "VPS", "specs": {"cpu": 1, "ram_gb": 1, "disk_gb": 10}, "notes": f"{MARK} {key}"})
        ids[key] = r.json()["id"]
        if host:
            _run(_sql("UPDATE infrastructure_requests SET linked_host_name = $2 WHERE id = $1", ids[key], host))
    h = auth(sysadmin_token)
    seen = {r["id"] for r in client.get("/api/v1/infra-requests", params={"page_size": 100}, headers=h).json()["items"]}
    assert ids["none"] in seen and ids["a"] in seen and ids["b"] not in seen
    assert client.get(f"/api/v1/infra-requests/{ids['b']}", headers=h).status_code == 403
    assert client.get(f"/api/v1/infra-requests/{ids['a']}", headers=h).status_code == 200
    r = client.patch(f"/api/v1/infra-requests/{ids['none']}/status", headers=h,
                     json={"status": "ON_PROGRESS", "linked_vm_id": "201", "linked_host_name": HB})
    assert r.status_code == 403
    seen = {r["id"] for r in client.get("/api/v1/infra-requests", params={"page_size": 100}, headers=auth(superadmin_token)).json()["items"]}
    assert set(ids.values()) <= seen


# ── Audit dan sesi ────────────────────────────────────────────────────────────

def _audit(target, text):
    _run(_sql("INSERT INTO audit_logs (username, user_role, action_type, severity_level, target_server_name, detail_message) "
              "VALUES ('x', 'sysadmin', 'VM_ACTION', 'INFO', $1, $2)", target, f"{MARK} {text}"))


def test_activity_log_hides_other_proxmox(client, sysadmin_token, superadmin_token, env):
    _grant(A)
    for target, text in ((f"{A}/pve/101", "a-slash"), (f"{B}/pve/201", "b-slash"), (HB, "b-host"), (B, "b-label"),
                         (HA, "a-host"), (None, "tanpa-target"), ("scopeBX/pve/1", "mirip-b")):
        _audit(target, text)
    seen = lambda tok: sorted(i["detail"].replace(f"{MARK} ", "") for i in client.get(
        "/api/admin/audit-logs", params={"search": MARK, "action": "VM_ACTION", "page_size": 100}, headers=auth(tok)).json()["items"])
    assert seen(sysadmin_token) == ["a-host", "a-slash", "mirip-b", "tanpa-target"]       # 'scopeBX' bukan 'scopeB'
    assert seen(superadmin_token) == sorted(["a-host", "a-slash", "b-host", "b-label", "b-slash", "mirip-b", "tanpa-target"])
    csv = client.get("/api/admin/audit-logs/export", params={"search": MARK, "action": "VM_ACTION"}, headers=auth(sysadmin_token)).text
    assert "b-slash" not in csv and "a-slash" in csv


def test_web_sessions_listed_and_killed_only_for_own_proxmox(client, sysadmin_token, superadmin_token, env):
    _grant(A)
    for sid, ip in (("a" * 32, IP_A), ("b" * 32, IP_B)):
        _run(_sql("INSERT INTO openweb_sessions (id, username, role, target_ip, expires_at) VALUES ($1, 'mhs', 'student', $2, NOW() + interval '1 hour')",
                  sid, ip))
    h = auth(sysadmin_token)
    hist = lambda tok: sorted(s["target_ip"] for s in client.get("/api/admin/openweb/history", headers=auth(tok)).json()["items"]
                              if s["target_ip"] in (IP_A, IP_B))
    assert hist(sysadmin_token) == [IP_A] and hist(superadmin_token) == [IP_A, IP_B]
    active = client.get("/api/admin/openweb/sessions", headers=h).json()["sessions"]
    assert {s["target_ip"] for s in active} & {IP_A, IP_B} == {IP_A}
    assert client.post("/api/admin/openweb/kill", headers=h, json={"session_id": "b" * 32}).status_code == 403
    assert client.post("/api/admin/openweb/kill", headers=h, json={"session_id": "a" * 32}).status_code == 200


def test_ssh_sessions_listed_and_killed_only_for_own_proxmox(client, sysadmin_token, superadmin_token, env):
    _grant(A)
    ids = {}
    for key, ip in (("a", IP_A), ("b", IP_B)):
        ids[key] = _run(_sql("INSERT INTO ssh_sessions (username, role, fingerprint, client_ip, client_port, targets) "
                             "VALUES ('mhs', 'student', 'SHA256:x', '203.0.113.77', 5000, $1) RETURNING id", [f"{ip}:22"]))[0]["id"]
    h = auth(sysadmin_token)
    ips = lambda tok: sorted(t["target"].split(":")[0] for s in client.get("/api/admin/ssh/history", headers=auth(tok)).json()["items"]
                             if s["client_ip"] == "203.0.113.77" for t in s["targets"])
    assert ips(sysadmin_token) == [IP_A] and ips(superadmin_token) == [IP_A, IP_B]
    assert client.post("/api/admin/ssh/kill", headers=h, json={"session_id": ids["b"]}).status_code == 403


def test_remote_sessions_and_history_only_for_own_proxmox(client, sysadmin_token, superadmin_token, env, monkeypatch):
    _grant(A)
    sessions = [{"active_id": "1", "username": "mhs", "host": HA, "vm": "vm-101", "connection": f"HV/{HA}/vm-101"},
                {"active_id": "2", "username": "mhs", "host": HB, "vm": "vm-201", "connection": f"HV/{HB}/vm-201"}]

    async def active():
        return sessions

    async def hist(limit=100):
        return [{"username": "mhs", "connection": s["connection"], "vm": s["vm"], "host": s["host"]} for s in sessions]

    killed = []

    async def kill(active_id):
        killed.append(active_id)
        return True

    monkeypatch.setattr(guac_sync, "get_active_sessions", active)
    monkeypatch.setattr(guac_sync, "kill_session", kill)
    monkeypatch.setattr(guac_sync, "get_connection_history", hist)
    async def no_pool():
        return None
    monkeypatch.setattr(remote_history, "_get_pool", no_pool)
    h = auth(sysadmin_token)
    assert [s["host"] for s in client.get("/api/admin/remote/sessions", headers=h).json()["sessions"]] == [HA]
    assert len(client.get("/api/admin/remote/sessions", headers=auth(superadmin_token)).json()["sessions"]) == 2
    assert [i["host"] for i in client.get("/api/admin/remote/history", headers=h).json()["items"]] == [HA]
    assert client.post("/api/admin/remote/kill-session", headers=h, json={"active_id": "2"}).status_code == 403
    assert killed == []
    assert client.post("/api/admin/remote/kill-session", headers=h, json={"active_id": "1"}).status_code == 200
    assert killed == ["1"]


# ── Open Web dan bastion ──────────────────────────────────────────────────────

def test_open_web_for_sysadmin_only_reaches_vms_of_own_proxmox(client, sysadmin_token, superadmin_token, env):
    _grant(A)
    post = lambda tok, ip: client.post("/api/v1/openweb/ticket", headers=auth(tok), json={"url": f"http://{ip}/"})
    assert post(sysadmin_token, IP_A).status_code == 200
    assert post(sysadmin_token, IP_B).status_code == 403
    assert post(sysadmin_token, IP_X).status_code == 403            # IP yang bukan VM mana pun
    assert post(superadmin_token, IP_X).status_code == 200


def test_bastion_targets_for_sysadmin_follow_own_proxmox(client, sysadmin_token, env, monkeypatch):
    monkeypatch.setenv("BASTION_TOKEN", "uji-token-bastion")
    _grant(A)
    pub = _ed25519_pub()
    key = client.post("/api/v1/ssh-keys", headers=auth(sysadmin_token), json={"name": "uji", "public_key": pub}).json()
    ask = lambda: client.get("/api/v1/ssh-keys/authorized", params={"fingerprint": key["fingerprint"]},
                             headers={"X-Bastion-Token": "uji-token-bastion"}).text
    out = ask()
    assert f'permitopen="{IP_A}:22"' in out and IP_B not in out
    _run(_sql("DELETE FROM user_instances WHERE user_id = $1", _uid(SYSADMIN)))
    assert ask() == ""                                               # tanpa Proxmox: tidak ada VM yang bisa dituju


# ── Guacamole ─────────────────────────────────────────────────────────────────

def test_connection_label_from_name():
    assert guac_sync._conn_label(f"HV/{HA}/vm-1") == A
    assert guac_sync._conn_label(f"HV/{HA}/vm-1@akun") == A
    assert guac_sync._conn_label("HV/lab-proxmox1/PROXMOX-HOST") == "lab-proxmox1"
    assert guac_sync._conn_label("lain/nama") == "" and guac_sync._conn_label("") == ""


def test_new_vm_connection_goes_to_superadmins_and_owners_of_that_proxmox(client, sysadmin_token, superadmin_token, env, monkeypatch):
    _grant(A)
    granted = []
    name = {"v": f"HV/{HA}/vm-101"}

    async def fetch(method, path, body=None):
        return {"name": name["v"]}, 200

    async def grant(user, conn_id):
        granted.append(user)
        return True

    monkeypatch.setattr(guac_sync, "_fetch", fetch)
    monkeypatch.setattr(guac_sync, "grant_connection", grant)
    client.portal.call(guac_sync.grant_vm_to_all_admins, "5")
    assert SYSADMIN in granted and "tst_superadmin" in granted
    granted.clear(); name["v"] = f"HV/{HB}/vm-201"
    client.portal.call(guac_sync.grant_vm_to_all_admins, "6")
    assert SYSADMIN not in granted and "tst_superadmin" in granted
    granted.clear(); name["v"] = ""
    client.portal.call(guac_sync.grant_vm_to_all_admins, "7")                       # nama tak terbaca: hanya superadmin
    assert SYSADMIN not in granted and "tst_superadmin" in granted


def test_admin_connections_follow_assignment(client, sysadmin_token, superadmin_token, env, monkeypatch):
    _grant(A)
    conns = {"1": {"identifier": "1", "name": f"HV/{HA}/vm-101"}, "2": {"identifier": "2", "name": f"HV/{HB}/vm-201"},
             "3": {"identifier": "3", "name": f"HV/{A}/PROXMOX-HOST"}}
    have = {"2"}                                                       # sudah memegang koneksi di Proxmox B
    log = []

    async def fetch(method, path, body=None):
        return conns, 200

    async def current(user):
        return list(have)

    async def grant(user, conn_id):
        log.append(("grant", conn_id)); return True

    async def revoke(user, conn_id):
        log.append(("revoke", conn_id)); return True

    monkeypatch.setattr(guac_sync, "_fetch", fetch)
    monkeypatch.setattr(guac_sync, "get_user_connections", current)
    monkeypatch.setattr(guac_sync, "grant_connection", grant)
    monkeypatch.setattr(guac_sync, "revoke_connection", revoke)
    client.portal.call(guac_sync.grant_all_connections_to_admin, SYSADMIN)
    assert sorted(log) == [("grant", "1"), ("grant", "3"), ("revoke", "2")]
    log.clear(); have.clear()
    client.portal.call(guac_sync.grant_all_connections_to_admin, "tst_superadmin")
    assert sorted(log) == [("grant", "1"), ("grant", "2"), ("grant", "3")]


# ── Migrasi ───────────────────────────────────────────────────────────────────

def test_migration_gives_existing_sysadmins_every_current_proxmox(client, sysadmin_token, env):
    import pathlib
    sql = (pathlib.Path(__file__).parent.parent / "migrations" / "V019__sysadmin_instances.sql").read_text()
    insert = sql[sql.index("INSERT INTO user_instances"):]
    _run(_sql("DELETE FROM user_instances WHERE user_id = $1", _uid(SYSADMIN)))
    _run(_sql(insert.rstrip().rstrip(";")))
    got = sorted(r["instance"] for r in _run(_sql("SELECT instance FROM user_instances WHERE user_id = $1", _uid(SYSADMIN))))
    assert {A, B} <= set(got)
