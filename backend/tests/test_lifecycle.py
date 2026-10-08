"""
Masa berlaku akun, aksi massal, impor CSV, dan masa sewa VM.
"""
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from tests.conftest import DATABASE_URL, _run, auth

PASSWORD = "Rahasia123!"


def _name(prefix="tst_lc"):
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


def _create(client, token, **extra):
    name = _name()
    r = client.post("/api/v1/users", headers=auth(token),
                    json={"username": name, "password": PASSWORD, "full_name": "Uji Siklus", "role": "student", **extra})
    assert r.status_code == 200, r.text
    return r.json()["id"], name


def _login(client, name, password=PASSWORD):
    return client.post("/api/v1/users/login", json={"username": name, "password": password})


async def _sql(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchval(query, *args)
    finally:
        await conn.close()


# ── Masa berlaku akun ────────────────────────────────────────────────────────

def test_expired_account_cannot_login(client, superadmin_token):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _, name = _create(client, superadmin_token, expires_at=past)
    r = _login(client, name)
    assert r.status_code == 403 and "Masa berlaku" in r.json()["detail"]


def test_expiry_revokes_existing_token_immediately(client, superadmin_token):
    uid, name = _create(client, superadmin_token)
    token = _login(client, name).json()["access_token"]
    assert client.get("/api/v1/users/me", headers=auth(token)).status_code == 200
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert client.put(f"/api/v1/users/{uid}", json={"expires_at": past}, headers=auth(superadmin_token)).status_code == 200
    assert client.get("/api/v1/users/me", headers=auth(token)).status_code == 401
    # diperpanjang: token yang sama berlaku lagi
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    client.put(f"/api/v1/users/{uid}", json={"expires_at": future}, headers=auth(superadmin_token))
    assert client.get("/api/v1/users/me", headers=auth(token)).status_code == 200


def test_expire_job_runs_once(client, superadmin_token):
    from services.lifecycle import expire_accounts
    uid, _ = _create(client, superadmin_token)
    _run(_sql("UPDATE users SET expires_at = NOW() - INTERVAL '1 minute' WHERE id = $1", uid))
    assert client.portal.call(expire_accounts) >= 1
    assert _run(_sql("SELECT expiry_enforced_at IS NOT NULL FROM users WHERE id = $1", uid))
    assert client.portal.call(expire_accounts) == 0


# ── Aksi massal ──────────────────────────────────────────────────────────────

def test_bulk_deactivate_activate_and_expiry(client, superadmin_token):
    a, na = _create(client, superadmin_token)
    b, nb = _create(client, superadmin_token)
    tok_a = _login(client, na).json()["access_token"]
    me = client.get("/api/v1/users/me", headers=auth(superadmin_token)).json()["id"]

    r = client.post("/api/v1/users/bulk", headers=auth(superadmin_token),
                    json={"user_ids": [a, b, me], "action": "deactivate"})
    assert r.status_code == 200 and r.json()["updated"] == 2          # akun sendiri dilewati
    assert client.get("/api/v1/users/me", headers=auth(tok_a)).status_code == 401
    assert client.get("/api/v1/users/me", headers=auth(superadmin_token)).status_code == 200

    client.post("/api/v1/users/bulk", headers=auth(superadmin_token), json={"user_ids": [a, b], "action": "activate"})
    assert client.get("/api/v1/users/me", headers=auth(tok_a)).status_code == 200

    until = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    client.post("/api/v1/users/bulk", headers=auth(superadmin_token),
                json={"user_ids": [a, b], "action": "set_expiry", "expires_at": until})
    users = {u["id"]: u for u in client.get("/api/v1/users", headers=auth(superadmin_token)).json()}
    assert users[a]["expires_at"] and users[b]["expires_at"]
    client.post("/api/v1/users/bulk", headers=auth(superadmin_token), json={"user_ids": [a, b], "action": "clear_expiry"})
    users = {u["id"]: u for u in client.get("/api/v1/users", headers=auth(superadmin_token)).json()}
    assert users[a]["expires_at"] is None


def test_bulk_superadmin_only(client, sysadmin_token):
    r = client.post("/api/v1/users/bulk", headers=auth(sysadmin_token), json={"user_ids": [1], "action": "deactivate"})
    assert r.status_code == 403


# ── Impor CSV ────────────────────────────────────────────────────────────────

def test_import_validates_all_rows_before_creating(client, superadmin_token):
    good = _name()
    rows = [
        {"username": good, "full_name": "Siswa Satu"},
        {"username": good, "full_name": "Ganda"},                          # duplikat di file
        {"username": "X Y", "full_name": "Spasi"},                         # username tidak valid
        {"username": _name(), "full_name": "", "email": "bukan-email"},    # nama kosong, email salah
        {"username": _name(), "full_name": "Grup", "group": "grup-tidak-ada"},
        {"username": _name(), "full_name": "Tanggal", "expires_at": "2020-01-01"},
    ]
    r = client.post("/api/v1/users/import", headers=auth(superadmin_token), json={"rows": rows, "dry_run": False})
    body = r.json()
    assert r.status_code == 200 and body["created"] == 0 and body["errors"] == 5
    assert _run(_sql("SELECT count(*) FROM users WHERE username = $1", good)) == 0   # tidak ada yang dibuat


def test_import_creates_accounts_with_generated_passwords(client, superadmin_token):
    gname = "tst-grup-" + uuid.uuid4().hex[:6]
    gid = client.post("/api/v1/groups", headers=auth(superadmin_token), json={"name": gname}).json()["id"]
    u1, u2 = _name(), _name()
    expiry = (datetime.now(timezone.utc) + timedelta(days=120)).strftime("%Y-%m-%d")
    rows = [{"username": u1, "full_name": "Siswa Satu", "group": gname, "expires_at": expiry},
            {"username": u2, "full_name": "Siswa Dua", "password": "PasswordSendiri1", "group": gname}]
    dry = client.post("/api/v1/users/import", headers=auth(superadmin_token), json={"rows": rows, "dry_run": True}).json()
    assert dry["errors"] == 0 and dry["created"] == 0

    r = client.post("/api/v1/users/import", headers=auth(superadmin_token), json={"rows": rows, "dry_run": False}).json()
    assert r["created"] == 2
    creds = {c["username"]: c["password"] for c in r["credentials"]}
    assert u1 in creds and u2 not in creds                                 # password sendiri tidak dikembalikan
    assert _login(client, u1, creds[u1]).status_code == 200
    assert _login(client, u2, "PasswordSendiri1").status_code == 200
    members = {m["username"] for m in client.get(f"/api/v1/groups/{gid}/members", headers=auth(superadmin_token)).json()}
    assert {u1, u2} <= members
    client.delete(f"/api/v1/groups/{gid}", headers=auth(superadmin_token))


# ── Masa sewa VM ─────────────────────────────────────────────────────────────

HOST = "labLease__pve"


async def _vm(vmid: str, lease_sql: str = "NULL"):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("DELETE FROM vms WHERE vm_id = $1 AND host_name = $2", vmid, HOST)
        await conn.execute(f"INSERT INTO vms (vm_id, host_name, vm_name, lease_until) VALUES ($1, $2, 'vm-sewa', {lease_sql})",
                           vmid, HOST)
    finally:
        await conn.close()


def test_lease_add_reduce_and_clear(client, sysadmin_token, student_token):
    _run(_vm("301"))
    url = f"/api/v1/proxmox/instances/labLease/nodes/pve/vms/301/lease"
    assert client.put(url, json={"add_days": 7}, headers=auth(student_token)).status_code == 403
    r = client.put(url, json={"add_days": 7}, headers=auth(sysadmin_token))
    assert r.status_code == 200, r.text
    first = datetime.fromisoformat(r.json()["lease_until"])
    assert timedelta(days=6, hours=23) < first - datetime.now(timezone.utc) <= timedelta(days=7)
    second = datetime.fromisoformat(client.put(url, json={"add_days": -2}, headers=auth(sysadmin_token)).json()["lease_until"])
    assert abs((first - second) - timedelta(days=2)) < timedelta(seconds=5)
    assert client.put(url, json={"clear": True}, headers=auth(sysadmin_token)).json()["lease_until"] is None
    missing = "/api/v1/proxmox/instances/labLease/nodes/pve/vms/999/lease"
    assert client.put(missing, json={"add_days": 1}, headers=auth(sysadmin_token)).status_code == 404


def test_student_cannot_start_vm_after_lease_expired(client, student_token):
    _run(_vm("302", "NOW() - INTERVAL '1 hour'"))
    uid = _run(_sql("SELECT id FROM users WHERE username = 'tst_student'"))
    _run(_sql("INSERT INTO vm_assignments (user_id, vm_id, host_name) VALUES ($1, '302', $2) RETURNING id", uid, HOST))
    r = client.post("/api/v1/proxmox/instances/labLease/nodes/pve/vms/302/action",
                    json={"action": "start"}, headers=auth(student_token))
    assert r.status_code == 403 and "Masa sewa" in r.json()["detail"]
    _run(_sql("DELETE FROM vm_assignments WHERE user_id = $1 AND vm_id = '302' RETURNING 1", uid))


def test_lease_extension_ticket_category(client, student_token):
    r = client.post("/api/tickets", headers=auth(student_token),
                    json={"title": "Perpanjang sewa", "category": "LEASE_EXTENSION"})
    assert r.status_code == 200
    t = client.get(f"/api/tickets/{r.json()['id']}", headers=auth(student_token)).json()["ticket"]
    assert t["category"] == "LEASE_EXTENSION"
