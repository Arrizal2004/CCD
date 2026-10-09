"""
Riwayat Open Web per pengguna: alamat, status, dan sampai kapan berlaku; sesi yang masih berlaku dibuka lagi
tanpa membuat sesi baru; waktu bisa ditambah 1 jam hanya saat sisa kurang dari 30 menit. Berlaku untuk semua
peran. Yang diuji termasuk bahwa path proxy yang sedang terbuka tetap sah setelah perpanjangan.
"""
import asyncpg
import pytest

from routers import openweb
from tests.conftest import DATABASE_URL, _run, auth

HOST, VMID, IP = "labOw__pve", "9601", "10.7.7.7"
URL = f"http://{IP}:8080/app?x=1"


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def _uid(name):
    return _run(_sql("SELECT id FROM users WHERE username = $1", name))[0]["id"]


def _clean():
    _run(_sql("DELETE FROM openweb_sessions WHERE target_ip = $1", IP))
    _run(_sql("DELETE FROM vm_assignments WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vm_credentials WHERE host_name = $1", HOST))
    _run(_sql("DELETE FROM vms WHERE host_name = $1", HOST))


@pytest.fixture
def env(student_token):
    _clean()
    _run(_sql("INSERT INTO vms (vm_id, host_name, vm_name) VALUES ($1, $2, 'vm-ow')", VMID, HOST))
    _run(_sql("INSERT INTO vm_credentials (vm_id, host_name, username, ssh_host) VALUES ($1, $2, 'mhs', $3)", VMID, HOST, IP))
    _run(_sql("INSERT INTO vm_assignments (user_id, vm_id, host_name, vm_name) VALUES ($1, $2, $3, 'vm-ow')",
              _uid("tst_student"), VMID, HOST))
    yield
    _clean()


def _open(client, token, url=URL):
    r = client.post("/api/v1/openweb/ticket", headers=auth(token), json={"url": url})
    assert r.status_code == 200, r.text
    return r.json()


def _history(client, token):
    return client.get("/api/v1/openweb/history", headers=auth(token)).json()["items"]


def _set_remaining(sid, seconds):
    _run(_sql("UPDATE openweb_sessions SET expires_at = NOW() + make_interval(secs => $2) WHERE id = $1", sid, seconds))
    openweb._cache.pop(sid, None)


def _auth_status(client, proxy_path):
    return client.get("/api/v1/openweb/auth", headers={"X-Original-URI": proxy_path, "X-Real-IP": "203.0.113.5"}).status_code


def test_history_shows_url_status_and_valid_until(client, student_token, env):
    r = _open(client, student_token)
    assert r["session_id"] and r["proxy_path"].startswith("/openweb/")
    item = _history(client, student_token)[0]
    assert item["url"] == URL and item["status"] == "active" and item["id"] == r["session_id"]
    assert 3500 < item["remaining"] <= 3600 and item["expires_at"]
    assert item["can_extend"] is False and item["extend_in"] > 0          # sisa 1 jam: belum boleh ditambah


def test_same_address_reuses_the_active_session(client, student_token, env):
    first, second = _open(client, student_token), _open(client, student_token)
    assert second["session_id"] == first["session_id"] and second["reused"] is True
    assert second["proxy_path"] == first["proxy_path"]
    assert len(_history(client, student_token)) == 1
    other = _open(client, student_token, f"http://{IP}:9090/")                 # alamat lain: sesi lain
    assert other["session_id"] != first["session_id"]


def test_extend_only_when_under_30_minutes(client, student_token, env):
    sid = _open(client, student_token)["session_id"]
    h = auth(student_token)
    early = client.post(f"/api/v1/openweb/sessions/{sid}/extend", headers=h)
    assert early.status_code == 409 and "30 menit" in early.json()["detail"]
    _set_remaining(sid, 29 * 60)
    item = _history(client, student_token)[0]
    assert item["can_extend"] is True and item["extend_in"] == 0
    ok = client.post(f"/api/v1/openweb/sessions/{sid}/extend", headers=h)
    assert ok.status_code == 200, ok.text
    assert 88 * 60 <= ok.json()["remaining"] <= 90 * 60                        # 29 menit + 1 jam
    assert _history(client, student_token)[0]["can_extend"] is False          # sisa > 30 menit lagi
    again = client.post(f"/api/v1/openweb/sessions/{sid}/extend", headers=h)
    assert again.status_code == 409


def test_proxy_path_stays_valid_after_extension(client, student_token, env):
    r = _open(client, student_token)
    sid = r["session_id"]
    assert _auth_status(client, r["proxy_path"]) == 204
    _set_remaining(sid, 5 * 60)
    assert client.post(f"/api/v1/openweb/sessions/{sid}/extend", headers=auth(student_token)).status_code == 200
    assert _auth_status(client, r["proxy_path"]) == 204                         # path lama tetap sah, iframe tidak perlu dimuat ulang
    ticket = r["proxy_path"].split("/")[2]
    assert openweb._verify(ticket)["e"] > _run(_sql("SELECT EXTRACT(EPOCH FROM expires_at) AS e FROM openweb_sessions WHERE id = $1", sid))[0]["e"]


def test_reopen_active_session_without_creating_a_new_one(client, student_token, env):
    r = _open(client, student_token)
    again = client.post(f"/api/v1/openweb/sessions/{r['session_id']}/open", headers=auth(student_token))
    assert again.status_code == 200 and again.json()["proxy_path"] == r["proxy_path"] and again.json()["url"] == URL
    assert len(_history(client, student_token)) == 1


def test_expired_and_revoked_sessions_cannot_be_reopened_or_extended(client, student_token, env):
    h = auth(student_token)
    sid = _open(client, student_token)["session_id"]
    _run(_sql("UPDATE openweb_sessions SET expires_at = NOW() - interval '1 minute' WHERE id = $1", sid))
    assert _history(client, student_token)[0]["status"] == "expired"
    for act in ("open", "extend"):
        assert client.post(f"/api/v1/openweb/sessions/{sid}/{act}", headers=h).status_code == 409
    # alamat yang sama sekarang membuat sesi baru
    new = _open(client, student_token)
    assert new["session_id"] != sid and "reused" not in new
    _run(_sql("UPDATE openweb_sessions SET revoked_at = NOW(), revoked_by = 'admin' WHERE id = $1", new["session_id"]))
    assert _history(client, student_token)[0]["status"] == "revoked"
    assert client.post(f"/api/v1/openweb/sessions/{new['session_id']}/extend", headers=h).status_code == 409


def test_session_age_limit_stops_extension(client, student_token, env):
    sid = _open(client, student_token)["session_id"]
    _run(_sql("UPDATE openweb_sessions SET created_at = NOW() - interval '24 hours 10 minutes', "
              "expires_at = NOW() + interval '10 minutes' WHERE id = $1", sid))
    assert _history(client, student_token)[0]["can_extend"] is False
    r = client.post(f"/api/v1/openweb/sessions/{sid}/extend", headers=auth(student_token))
    assert r.status_code == 409 and "24 jam" in r.json()["detail"]


def test_sessions_are_private_to_their_owner(client, student_token, superadmin_token, env):
    sid = _open(client, student_token)["session_id"]
    _set_remaining(sid, 60)
    for act in ("open", "extend"):
        assert client.post(f"/api/v1/openweb/sessions/{sid}/{act}", headers=auth(superadmin_token)).status_code == 404
    assert all(i["id"] != sid for i in _history(client, superadmin_token))


def test_lost_access_blocks_reopen_and_extend(client, student_token, env):
    sid = _open(client, student_token)["session_id"]
    _set_remaining(sid, 60)
    _run(_sql("DELETE FROM vm_assignments WHERE host_name = $1", HOST))
    for act in ("open", "extend"):
        assert client.post(f"/api/v1/openweb/sessions/{sid}/{act}", headers=auth(student_token)).status_code == 403


def test_admin_has_own_history_and_can_extend(client, superadmin_token, env):
    r = _open(client, superadmin_token, f"http://{IP}/")
    _set_remaining(r["session_id"], 10 * 60)
    ok = client.post(f"/api/v1/openweb/sessions/{r['session_id']}/extend", headers=auth(superadmin_token))
    assert ok.status_code == 200
    assert _history(client, superadmin_token)[0]["id"] == r["session_id"]
