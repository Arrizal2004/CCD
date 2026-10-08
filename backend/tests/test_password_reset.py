"""
Lupa password akun CCD: reset oleh admin (password sementara, wajib ganti, sesi lama berakhir) dan
permintaan "Lupa password?" dari halaman login (tanpa membocorkan username mana yang terdaftar).
"""
import re
import time
import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from auth import verify_token
from tests.conftest import DATABASE_URL, _run, auth

U = "/api/v1/users"
PW = "Rahasia123!"


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def _login(client, username, password):
    return client.post(f"{U}/login", json={"username": username, "password": password})


def _ip():
    return {"X-Real-IP": f"198.51.100.{uuid.uuid4().int % 250 + 1}-{uuid.uuid4().hex[:6]}"}


@pytest.fixture
def make_user(client, superadmin_token):
    created = []

    def make(role="student"):
        name = f"tst_pw_{role[:3]}_{uuid.uuid4().hex[:6]}"
        r = client.post(U, headers=auth(superadmin_token),
                        json={"username": name, "password": PW, "full_name": name.title(), "role": role})
        assert r.status_code == 200, r.text
        created.append(r.json()["id"])
        return r.json()["id"], name

    yield make
    for uid in created:
        client.delete(f"{U}/{uid}", headers=auth(superadmin_token))


def test_reset_forces_change_and_ends_old_sessions(client, sysadmin_token, make_user):
    uid, name = make_user()
    old = _login(client, name, PW).json()["access_token"]

    r = client.post(f"{U}/{uid}/reset-password", headers=auth(sysadmin_token))
    assert r.status_code == 200, r.text
    temp = r.json()["password"]
    assert re.fullmatch(r"[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}", temp) and r.json()["must_change_password"]

    assert client.get(f"{U}/me", headers=auth(old)).status_code == 401          # sesi lama berakhir
    assert _login(client, name, PW).status_code == 401                          # password lama tidak berlaku
    r = _login(client, name, temp)
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
    assert r.json()["guac_auth"] == {}                                          # belum boleh Connect
    tok = r.json()["access_token"]

    # Selama wajib ganti password, hanya profil/ganti password/logout yang boleh dipakai.
    assert client.get(f"{U}/me", headers=auth(tok)).json()["must_change_password"] is True
    blocked = client.get("/api/tickets", headers=auth(tok))
    assert blocked.status_code == 403 and blocked.headers.get("x-password-change-required") == "1"
    with pytest.raises(HTTPException) as e:                                     # WebSocket/unduhan juga
        client.portal.call(verify_token, tok)
    assert e.value.status_code == 403

    same = client.post(f"{U}/me/change-password", headers=auth(tok),
                       json={"old_password": temp, "new_password": temp})
    assert same.status_code == 400
    r = client.post(f"{U}/me/change-password", headers=auth(tok),
                    json={"old_password": temp, "new_password": "BaruSekali456!"})
    assert r.status_code == 200, r.text
    new = r.json()["access_token"]
    assert client.get(f"{U}/me", headers=auth(tok)).status_code == 401          # token sementara ikut berakhir
    assert client.get("/api/tickets", headers=auth(new)).status_code == 200
    assert client.get(f"{U}/me", headers=auth(new)).json()["must_change_password"] is False

    rows = _run(_sql("SELECT action_type FROM audit_logs WHERE detail_message LIKE $1", f"%'{name}'%"))
    assert "USER_PASSWORD_RESET" in {r["action_type"] for r in rows}


def test_reset_permissions(client, sysadmin_token, superadmin_token, student_token, make_user):
    sid, _ = make_user("sysadmin")
    uid, _ = make_user()
    assert client.post(f"{U}/{uid}/reset-password", headers=auth(student_token)).status_code == 403
    assert client.post(f"{U}/{sid}/reset-password", headers=auth(sysadmin_token)).status_code == 403
    me = client.get(f"{U}/me", headers=auth(sysadmin_token)).json()["id"]
    assert client.post(f"{U}/{me}/reset-password", headers=auth(sysadmin_token)).status_code == 400
    assert client.post(f"{U}/999999/reset-password", headers=auth(sysadmin_token)).status_code == 404
    assert client.post(f"{U}/{sid}/reset-password", headers=auth(superadmin_token)).status_code == 200


def test_change_password_ends_other_sessions(client, make_user):
    _, name = make_user()
    a = _login(client, name, PW).json()["access_token"]
    b = _login(client, name, PW).json()["access_token"]
    assert client.post(f"{U}/me/change-password", headers=auth(a),
                       json={"old_password": "salah-salah", "new_password": "BaruSekali456!"}).status_code == 400
    r = client.post(f"{U}/me/change-password", headers=auth(a),
                    json={"old_password": PW, "new_password": "BaruSekali456!"})
    assert r.status_code == 200
    assert client.get(f"{U}/me", headers=auth(b)).status_code == 401
    assert client.get(f"{U}/me", headers=auth(r.json()["access_token"])).status_code == 200


def test_admin_edit_password_or_role_ends_sessions(client, superadmin_token, make_user):
    uid, name = make_user()
    tok = _login(client, name, PW).json()["access_token"]
    client.put(f"{U}/{uid}", headers=auth(superadmin_token), json={"full_name": "Nama Baru"})
    assert client.get(f"{U}/me", headers=auth(tok)).status_code == 200          # ubah nama: sesi tetap
    client.put(f"{U}/{uid}", headers=auth(superadmin_token), json={"role": "sysadmin"})
    assert client.get(f"{U}/me", headers=auth(tok)).status_code == 401          # peran berubah: login ulang
    tok = _login(client, name, PW).json()["access_token"]
    client.put(f"{U}/{uid}", headers=auth(superadmin_token), json={"password": "DariAdmin789!"})
    assert client.get(f"{U}/me", headers=auth(tok)).status_code == 401
    r = _login(client, name, "DariAdmin789!")
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is False


def _open_requests(client, token):
    return client.get(f"{U}/password-help", headers=auth(token)).json()


def _wait_request(client, token, name, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        hit = [r for r in _open_requests(client, token) if r["username"] == name]
        if hit:
            return hit[0]
        time.sleep(0.1)
    return None


def test_password_help_request_flow(client, sysadmin_token, superadmin_token, student_token, make_user):
    uid, name = make_user()
    sid, sname = make_user("sysadmin")
    ip = _ip()
    known = client.post(f"{U}/password-help", headers=ip, json={"username": name.upper(), "message": "Kelas TKJ, HP 08xx"})
    unknown = client.post(f"{U}/password-help", headers=ip, json={"username": f"tidak_ada_{uuid.uuid4().hex[:6]}"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()                                       # tidak membocorkan username
    assert client.post(f"{U}/password-help", headers=ip, json={"username": "  "}).status_code == 400
    client.post(f"{U}/password-help", headers=ip, json={"username": sname})

    req = _wait_request(client, sysadmin_token, name)
    assert req and req["message"] == "Kelas TKJ, HP 08xx" and req["user_id"] == uid
    assert _wait_request(client, superadmin_token, sname)
    assert not [r for r in _open_requests(client, sysadmin_token) if r["username"] == sname]   # bukan mahasiswa
    assert client.get(f"{U}/password-help", headers=auth(student_token)).status_code == 403

    # Permintaan berulang tidak menumpuk: tetap satu baris terbuka per akun.
    client.post(f"{U}/password-help", headers=ip, json={"username": name, "message": "lagi"})
    time.sleep(0.3)
    assert len([r for r in _open_requests(client, sysadmin_token) if r["username"] == name]) == 1

    # Reset password menandai permintaannya selesai.
    assert client.post(f"{U}/{uid}/reset-password", headers=auth(sysadmin_token)).status_code == 200
    assert not [r for r in _open_requests(client, sysadmin_token) if r["username"] == name]

    # Abaikan: sysadmin tidak boleh menyentuh permintaan akun sysadmin, superadmin boleh.
    sreq = _wait_request(client, superadmin_token, sname)
    assert client.post(f"{U}/password-help/{sreq['id']}/dismiss", headers=auth(sysadmin_token)).status_code == 403
    assert client.post(f"{U}/password-help/{sreq['id']}/dismiss", headers=auth(superadmin_token)).status_code == 200
    assert client.post(f"{U}/password-help/{sreq['id']}/dismiss", headers=auth(superadmin_token)).status_code == 404


def test_password_help_rate_limited_per_ip(client):
    ip = _ip()
    codes = [client.post(f"{U}/password-help", headers=ip, json={"username": f"x{i}"}).status_code for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
