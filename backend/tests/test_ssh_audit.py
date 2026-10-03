"""
Test audit SSH bastion: baris log sshd disusun menjadi sesi dan Activity Log.
Contoh baris diambil dari log asli bastion (busybox syslogd + OpenSSH 10).
"""
import random

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from services.ssh_audit import parse_line
from tests.conftest import auth

TOKEN = "token-bastion-uji"
HDR = {"X-Bastion-Token": TOKEN}


def _line(pid: int, msg: str) -> str:
    return f"Oct  3 18:12:34 779afdec36ab auth.info sshd-session[{pid}]: {msg}"


def _post(client, *lines):
    r = client.post("/api/v1/ssh-keys/events", content="\n".join(lines) + "\n", headers=HDR)
    assert r.status_code == 200, r.text
    return r.json()["handled"]


def _history(client, token):
    r = client.get("/api/admin/ssh/history", params={"page_size": 200}, headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _audit(client, token, action):
    r = client.get("/api/admin/audit-logs", params={"search": action, "page_size": 200}, headers=auth(token))
    return [i for i in r.json()["items"] if i["action_type"] == action]


@pytest.fixture
def bastion_env(monkeypatch):
    monkeypatch.setenv("BASTION_TOKEN", TOKEN)


@pytest.fixture
def student_key(client, student_token):
    pub = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    r = client.post("/api/v1/ssh-keys", json={"name": "laptop audit", "public_key": pub}, headers=auth(student_token))
    assert r.status_code == 200, r.text
    key = r.json()
    yield key
    client.delete(f"/api/v1/ssh-keys/{key['id']}", headers=auth(student_token))


def _conn():
    """PID dan port acak supaya tidak bentrok dengan sesi dari test lain."""
    return random.randint(1000, 30000), random.randint(30001, 60000), random.randint(1024, 65000)


def test_parse_line():
    assert parse_line(_line(10, "User child is on pid 12")) == (10, "User child is on pid 12")
    assert parse_line("Oct  3 18:12:31 779afdec36ab auth.info sshd[1]: Server listening on 0.0.0.0 port 2222.") \
        == (1, "Server listening on 0.0.0.0 port 2222.")
    assert parse_line("Oct  3 18:12:30 779afdec36ab syslog.info syslogd started: BusyBox v1.37.0") is None
    assert parse_line("Accepted publickey for tunnel from 1.2.3.4 port 1 ssh2: ED25519 SHA256:x") is None


def test_events_require_bastion_token(client, bastion_env):
    assert client.post("/api/v1/ssh-keys/events", content="x").status_code == 403
    assert client.post("/api/v1/ssh-keys/events", content="x", headers={"X-Bastion-Token": "tebakan"}).status_code == 403


def test_full_session_is_recorded(client, bastion_env, student_key, sysadmin_token):
    mon, child, port = _conn()
    fp = student_key["fingerprint"]
    handled = _post(client,
        _line(mon, f'Connection from 203.0.113.7 port {port} on 172.18.0.8 port 2222 rdomain ""'),
        _line(mon, f"Accepted publickey for tunnel from 203.0.113.7 port {port} ssh2: ED25519 {fp}"),
        _line(mon, f"User child is on pid {child}"),
        _line(child, f"debug1: serverloop.c:server_request_direct_tcpip():417 (bin=sshd-session, pid={child}): "
                     "originator 127.0.0.1 port 65535, target 172.16.111.21 port 22"),
    )
    assert handled == 3

    active = client.get("/api/admin/ssh/sessions", headers=auth(sysadmin_token)).json()["sessions"]
    s = next(x for x in active if x["fingerprint"] == fp)
    assert s["username"] == "tst_student" and s["client_ip"] == "203.0.113.7" and s["status"] == "active"
    assert [t["target"] for t in s["targets"]] == ["172.16.111.21:22"]

    _post(client,
        _line(child, f"Connection closed by 203.0.113.7 port {port}"),
        _line(child, "Transferred: sent 3260, received 3448 bytes"),
        _line(child, f"Closing connection to 203.0.113.7 port {port}"),
    )
    s = next(x for x in _history(client, sysadmin_token) if x["id"] == s["id"])
    assert s["status"] == "closed" and s["ended_at"] and s["bytes_sent"] == 3260 and s["bytes_received"] == 3448

    login = [a for a in _audit(client, sysadmin_token, "SSH_LOGIN") if a["target_id"] == fp]
    logout = [a for a in _audit(client, sysadmin_token, "SSH_LOGOUT") if a["target_id"] == fp]
    assert login and login[0]["username"] == "tst_student" and login[0]["client_ip"] == "203.0.113.7"
    assert logout and "172.16.111.21:22" in logout[0]["detail"]


def test_denied_target_is_recorded(client, bastion_env, student_key, sysadmin_token):
    mon, child, port = _conn()
    fp = student_key["fingerprint"]
    _post(client,
        _line(mon, f"Accepted publickey for tunnel from 198.51.100.4 port {port} ssh2: ED25519 {fp}"),
        _line(mon, f"User child is on pid {child}"),
        _line(child, f"debug1: serverloop.c:server_request_direct_tcpip():417 (bin=sshd-session, pid={child}): "
                     "originator 127.0.0.1 port 65535, target 172.16.111.99 port 22"),
        _line(child, f"Received request from 198.51.100.4 port {port} to connect to host 172.16.111.99 port 22, "
                     "but the request was denied."),
        _line(child, f"Timeout, client not responding from user tunnel 198.51.100.4 port {port}"),
    )
    s = next(x for x in _history(client, sysadmin_token) if x["fingerprint"] == fp)
    assert s["targets"] == [] and s["denied_targets"] == ["172.16.111.99:22"] and s["status"] == "timeout"
    denied = [a for a in _audit(client, sysadmin_token, "SSH_DENIED") if a["target_id"] == "172.16.111.99:22"]
    assert denied and denied[0]["username"] == "tst_student" and denied[0]["severity"] == "WARNING"


def test_client_text_cannot_fake_events(client, bastion_env, student_key, sysadmin_token):
    _, _, port = _conn()
    fp = student_key["fingerprint"]
    fake = f"Accepted publickey for tunnel from 192.0.2.1 port {port} ssh2: ED25519 {fp}"
    handled = _post(client,
        _line(77, f"Invalid user {fake} from 192.0.2.66 port 4444"),
        _line(77, f"Connection closed by invalid user x: {fake} 192.0.2.66 port 4444 [preauth]"),
        f"Oct  3 18:12:34 779afdec36ab auth.info su[5]: {fake}",
    )
    assert handled == 0
    assert not [x for x in _history(client, sysadmin_token) if x["client_ip"] == "192.0.2.1"]


def test_bastion_restart_closes_open_sessions(client, bastion_env, student_key, sysadmin_token):
    mon, _, port = _conn()
    _post(client, _line(mon, f"Accepted publickey for tunnel from 192.0.2.9 port {port} ssh2: ED25519 {student_key['fingerprint']}"))
    _post(client, "Oct  3 18:20:00 779afdec36ab auth.info sshd[1]: Server listening on 0.0.0.0 port 2222.")
    active = client.get("/api/admin/ssh/sessions", headers=auth(sysadmin_token)).json()["sessions"]
    assert not [x for x in active if x["client_ip"] == "192.0.2.9"]
    s = next(x for x in _history(client, sysadmin_token) if x["client_ip"] == "192.0.2.9")
    assert s["status"] == "restart"


def test_key_of_user_without_vm_is_denied_and_logged(client, bastion_env, student_key, sysadmin_token):
    r = client.get("/api/v1/ssh-keys/authorized", headers=HDR,
                   params={"fingerprint": student_key["fingerprint"], "client": "203.0.113.50"})
    assert r.status_code == 200 and r.text == ""
    denied = [a for a in _audit(client, sysadmin_token, "SSH_DENIED") if a["target_id"] == student_key["fingerprint"]]
    assert denied and denied[0]["client_ip"] == "203.0.113.50" and "tidak ada VM" in denied[0]["detail"]


def test_ssh_sessions_admin_only(client, student_token):
    assert client.get("/api/admin/ssh/sessions", headers=auth(student_token)).status_code == 403
    assert client.get("/api/admin/ssh/history", headers=auth(student_token)).status_code == 403
