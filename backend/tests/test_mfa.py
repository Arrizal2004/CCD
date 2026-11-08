"""
2FA TOTP: unit RFC 6238, pemasangan dari profil, login dua langkah, kode pemulihan, kebijakan wajib,
dan reset oleh admin.
"""
import time
import uuid

import pytest

from services import mfa
from tests.conftest import auth

U = "/api/v1/users"
PW = "Rahasia123!"


# ── unit TOTP ─────────────────────────────────────────────────────────────────

def test_rfc6238_vector():
    # RFC 6238 Appendix B (SHA1), rahasia ASCII "12345678901234567890", T=59 -> 94287082 (8 digit)
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert mfa.code_at(secret, 59 // 30, digits=8) == "94287082"
    assert mfa.code_at(secret, 1111111109 // 30, digits=8) == "07081804"


def test_match_step_window_and_replay():
    secret = mfa.new_secret()
    now = 1_700_000_000
    step = mfa.current_step(now)
    assert mfa.match_step(secret, mfa.code_at(secret, step), now=now) == step
    assert mfa.match_step(secret, mfa.code_at(secret, step - 1), now=now) == step - 1      # jam meleset satu langkah
    assert mfa.match_step(secret, mfa.code_at(secret, step - 3), now=now) is None
    assert mfa.match_step(secret, mfa.code_at(secret, step), last_step=step, now=now) is None   # tidak bisa diputar ulang
    assert mfa.match_step(secret, "12345", now=now) is None and mfa.match_step(secret, "", now=now) is None


def test_recovery_code_shape():
    code = mfa._new_code()
    assert mfa.looks_like_recovery(code) and not mfa.looks_like_recovery("123456")


def test_required_for_policy():
    assert mfa.required_for({"required": "all"}, "student")
    assert mfa.required_for({"required": "admins"}, "sysadmin") and not mfa.required_for({"required": "admins"}, "student")
    assert not mfa.required_for({"required": "off"}, "superadmin")


# ── API ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def make_user(client, superadmin_token):
    created = []

    def make(role="student"):
        name = f"tst_mfa_{role[:3]}_{uuid.uuid4().hex[:6]}"
        r = client.post(U, headers=auth(superadmin_token),
                        json={"username": name, "password": PW, "full_name": name.title(), "role": role})
        assert r.status_code == 200, r.text
        created.append(r.json()["id"])
        return r.json()["id"], name

    yield make
    for uid in created:
        client.delete(f"{U}/{uid}", headers=auth(superadmin_token))


@pytest.fixture(autouse=True)
def _policy_off(client, superadmin_token):
    yield
    client.put(f"{U}/mfa-policy", headers=auth(superadmin_token), json={"required": "off"})


def _login(client, name, password=PW):
    return client.post(f"{U}/login", json={"username": name, "password": password})


def _code(secret, offset):
    """Kode TOTP untuk langkah sekarang + offset (jendela penerimaan: -1..+1). Setiap pemakaian harus memakai
    langkah yang lebih baru dari sebelumnya, jadi tes berurutan memakai offset -1, 0, lalu +1."""
    step = mfa.current_step() + offset
    return mfa.code_at(secret, step), step


def _enroll(client, name):
    tok = _login(client, name).json()["access_token"]
    setup = client.post(f"{U}/me/mfa/setup", headers=auth(tok))
    assert setup.status_code == 200, setup.text
    sec = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/") and "<svg" in setup.json()["qr_svg"]
    code, step = _code(sec, -1)
    en = client.post(f"{U}/me/mfa/enable", headers=auth(tok), json={"code": code})
    assert en.status_code == 200, en.text
    assert len(en.json()["recovery_codes"]) == 10
    return tok, sec, step, en.json()["recovery_codes"]


def test_enable_then_two_step_login(client, make_user):
    _, name = make_user()
    tok, sec, step, codes = _enroll(client, name)
    assert client.get(f"{U}/me", headers=auth(tok)).json()["mfa_enabled"] is True

    r = _login(client, name)
    assert r.status_code == 200 and r.json()["mfa_required"] is True and "access_token" not in r.json()
    challenge = r.json()["mfa_token"]

    # kode salah ditolak, kode benar membuka sesi
    bad = client.post(f"{U}/login/mfa", json={"mfa_token": challenge, "code": "000000"})
    assert bad.status_code == 400
    code, step2 = _code(sec, 0)
    ok = client.post(f"{U}/login/mfa", json={"mfa_token": challenge, "code": code})
    assert ok.status_code == 200 and ok.json()["access_token"] and ok.json()["user"]["username"] == name

    # tantangan sekali pakai; kode yang sama tidak bisa dipakai lagi
    again = client.post(f"{U}/login/mfa", json={"mfa_token": challenge, "code": code})
    assert again.status_code == 401
    r2 = _login(client, name).json()
    replay = client.post(f"{U}/login/mfa", json={"mfa_token": r2["mfa_token"], "code": code})
    assert replay.status_code == 400


def test_recovery_code_logs_in_once(client, make_user):
    _, name = make_user()
    _, _, _, codes = _enroll(client, name)
    ch = _login(client, name).json()["mfa_token"]
    ok = client.post(f"{U}/login/mfa", json={"mfa_token": ch, "code": codes[0]})
    assert ok.status_code == 200 and ok.json()["recovery_codes_left"] == 9
    ch = _login(client, name).json()["mfa_token"]
    assert client.post(f"{U}/login/mfa", json={"mfa_token": ch, "code": codes[0]}).status_code == 400   # sudah dipakai


def test_too_many_wrong_codes_burn_the_challenge(client, make_user):
    _, name = make_user()
    _enroll(client, name)
    ch = _login(client, name).json()["mfa_token"]
    last = None
    for _ in range(mfa_tries()):
        last = client.post(f"{U}/login/mfa", json={"mfa_token": ch, "code": "111111"})
    assert last.status_code == 401
    assert client.post(f"{U}/login/mfa", json={"mfa_token": ch, "code": "111111"}).status_code == 401


def test_guessing_codes_across_new_logins_locks_the_account(client, make_user):
    """Password benar tidak boleh menghapus hitungan gagal, jadi menebak kode lewat login ulang tetap terkunci."""
    _, name = make_user()
    _enroll(client, name)
    from services.login_rate_limit import clear
    client.portal.call(clear, name)
    wrong = 0
    for _ in range(mfa_tries() + 3):
        r = _login(client, name)
        if r.status_code == 429:
            break
        ch = r.json()["mfa_token"]
        assert client.post(f"{U}/login/mfa", json={"mfa_token": ch, "code": "000000"}).status_code in (400, 401)
        wrong += 1
    assert r.status_code == 429 and wrong <= mfa_tries()
    assert "Terlalu banyak" in r.json()["detail"]
    client.portal.call(clear, name)


def mfa_tries():
    from routers.users import MFA_MAX_TRIES
    return MFA_MAX_TRIES


def test_disable_needs_password_and_code(client, make_user):
    _, name = make_user()
    tok, sec, step, _ = _enroll(client, name)
    code, _ = _code(sec, 0)
    assert client.post(f"{U}/me/mfa/disable", headers=auth(tok), json={"code": code, "password": "salah"}).status_code == 400
    assert client.post(f"{U}/me/mfa/disable", headers=auth(tok), json={"code": "000000", "password": PW}).status_code == 400
    assert client.post(f"{U}/me/mfa/disable", headers=auth(tok), json={"code": code, "password": PW}).status_code == 200
    assert "mfa_required" not in _login(client, name).json()


def test_policy_forces_enrollment_at_login(client, make_user, superadmin_token):
    _, name = make_user()
    assert client.put(f"{U}/mfa-policy", headers=auth(superadmin_token), json={"required": "all"}).status_code == 200
    r = _login(client, name).json()
    assert r["mfa_setup_required"] is True and r["mfa_required"] is False
    ch = r["mfa_token"]

    # tantangan pemasangan tidak bisa dipakai di langkah verifikasi biasa
    assert client.post(f"{U}/login/mfa", json={"mfa_token": ch, "code": "123456"}).status_code == 401
    setup = client.post(f"{U}/login/mfa/setup", json={"mfa_token": ch}).json()
    code, _ = _code(setup["secret"], -1)
    done = client.post(f"{U}/login/mfa/enable", json={"mfa_token": ch, "code": code})
    assert done.status_code == 200 and done.json()["access_token"] and len(done.json()["recovery_codes"]) == 10
    # akun yang wajib 2FA tidak boleh mematikannya
    code2, _ = _code(setup["secret"], 0)
    off = client.post(f"{U}/me/mfa/disable", headers=auth(done.json()["access_token"]),
                      json={"code": code2, "password": PW})
    assert off.status_code == 403


def test_policy_admins_only(client, make_user, superadmin_token):
    _, student = make_user("student")
    _, admin = make_user("sysadmin")
    client.put(f"{U}/mfa-policy", headers=auth(superadmin_token), json={"required": "admins"})
    assert "access_token" in _login(client, student).json()
    assert _login(client, admin).json()["mfa_setup_required"] is True


def test_policy_endpoints_are_superadmin_only(client, sysadmin_token, student_token, superadmin_token):
    assert client.get(f"{U}/mfa-policy", headers=auth(student_token)).status_code == 403
    assert client.put(f"{U}/mfa-policy", headers=auth(sysadmin_token), json={"required": "all"}).status_code == 403
    assert client.put(f"{U}/mfa-policy", headers=auth(superadmin_token), json={"required": "bogus"}).status_code == 400


def test_admin_reset_turns_mfa_off_and_ends_sessions(client, make_user, sysadmin_token, student_token):
    uid, name = make_user()
    tok, *_ = _enroll(client, name)
    assert client.post(f"{U}/{uid}/mfa/reset", headers=auth(student_token)).status_code == 403
    assert client.post(f"{U}/{uid}/mfa/reset", headers=auth(sysadmin_token)).status_code == 200
    assert client.get(f"{U}/me", headers=auth(tok)).status_code == 401
    assert "access_token" in _login(client, name).json()


def test_recovery_codes_regenerate(client, make_user):
    _, name = make_user()
    tok, sec, step, old = _enroll(client, name)
    code, _ = _code(sec, 0)
    r = client.post(f"{U}/me/mfa/recovery-codes", headers=auth(tok), json={"code": code})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    assert set(r.json()["recovery_codes"]).isdisjoint(old)
    assert client.get(f"{U}/me/mfa", headers=auth(tok)).json()["recovery_codes_left"] == 10
