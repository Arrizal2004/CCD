"""
Email keluar (SMTP): pengaturan, antrean dengan percobaan ulang, reset password lewat tautan email,
dan peringatan masa sewa. Pengiriman SMTP diganti penampung di memori.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from services import mailer
from tests.conftest import DATABASE_URL, _run, auth

S = "/api/v1/system"
U = "/api/v1/users"
PW = "Rahasia123!"


async def _sql(sql, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


@pytest.fixture
def smtp_box(monkeypatch):
    """Mengganti pengiriman SMTP; kotak["fail"] = True membuat pengiriman gagal."""
    box = {"sent": [], "fail": False}

    async def fake_send(cfg, to, subject, text, body_html=None):
        if box["fail"]:
            raise ConnectionError("smtp mati")
        box["sent"].append({"to": to, "subject": subject, "text": text, "html": body_html})

    monkeypatch.setattr(mailer, "send_now", fake_send)
    return box


def _wipe(client, token):
    client.put(f"{S}/email", headers=auth(token), json={
        "smtp": {"enabled": False, "host": "", "from_address": "", "from_name": "", "username": "", "password": "",
                 "dashboard_url": ""},
        "notify": {"self_reset": False, "lease": True, "lease_days_before": 3}})


@pytest.fixture
def email_on(client, superadmin_token):
    def configure(**notify):
        r = client.put(f"{S}/email", headers=auth(superadmin_token), json={
            "smtp": {"enabled": True, "host": "smtp.example.com", "port": 587, "security": "starttls", "username": "u",
                     "password": "rahasia-smtp", "from_address": "no-reply@example.com", "from_name": "CCD",
                     "dashboard_url": "https://ccd.example.com/"},
            "notify": {"self_reset": True, **notify}})
        assert r.status_code == 200, r.text
        return r.json()

    yield configure
    _wipe(client, superadmin_token)
    _run(_sql("DELETE FROM email_outbox"))


# ── pengaturan ────────────────────────────────────────────────────────────────

def test_settings_never_return_the_smtp_password(client, superadmin_token, email_on):
    out = email_on()
    assert out["smtp"]["has_password"] is True and "password" not in out["smtp"] and "password_enc" not in out["smtp"]
    assert out["smtp"]["dashboard_url"] == "https://ccd.example.com"
    stored = _run(_sql("SELECT data FROM app_settings WHERE key = 'email'"))[0]["data"]
    assert "rahasia-smtp" not in str(stored)                                  # tersimpan terenkripsi
    # menyimpan ulang tanpa field password tidak menghapusnya
    r = client.put(f"{S}/email", headers=auth(superadmin_token), json={"smtp": {"from_name": "Lab"}})
    assert r.json()["smtp"]["has_password"] is True and r.json()["smtp"]["from_name"] == "Lab"


def test_settings_validation(client, superadmin_token):
    _wipe(client, superadmin_token)
    put = lambda body: client.put(f"{S}/email", headers=auth(superadmin_token), json=body)
    assert put({"smtp": {"enabled": True}}).status_code == 400                           # host dan pengirim kosong
    assert put({"smtp": {"port": 70000}}).status_code == 400
    assert put({"smtp": {"security": "plain"}}).status_code == 400
    assert put({"smtp": {"from_address": "bukan-email"}}).status_code == 400
    assert put({"smtp": {"dashboard_url": "ftp://x"}}).status_code == 400
    assert put({"smtp": {"from_name": 'A"B'}}).status_code == 400
    assert put({"notify": {"lease_days_before": 99}}).status_code == 400


def test_email_settings_are_superadmin_only(client, sysadmin_token, student_token):
    for tok in (sysadmin_token, student_token):
        assert client.get(f"{S}/email", headers=auth(tok)).status_code == 403
        assert client.put(f"{S}/email", headers=auth(tok), json={}).status_code == 403
        assert client.post(f"{S}/email/test", headers=auth(tok), json={"to": "a@b.id"}).status_code == 403
        assert client.get(f"{S}/email/outbox", headers=auth(tok)).status_code == 403


def test_test_email_reports_smtp_errors(client, superadmin_token, email_on, smtp_box):
    email_on()
    assert client.post(f"{S}/email/test", headers=auth(superadmin_token), json={"to": "x"}).status_code == 400
    ok = client.post(f"{S}/email/test", headers=auth(superadmin_token), json={"to": "guru@example.com"})
    assert ok.status_code == 200 and smtp_box["sent"][0]["to"] == "guru@example.com"
    smtp_box["fail"] = True
    bad = client.post(f"{S}/email/test", headers=auth(superadmin_token), json={"to": "guru@example.com"})
    assert bad.status_code == 502 and "smtp mati" in bad.json()["detail"]


# ── antrean ───────────────────────────────────────────────────────────────────

def test_queue_sends_retries_and_gives_up(client, email_on, smtp_box):
    email_on()
    assert client.portal.call(mailer.enqueue, "test", ["Budi@Example.com", "budi@example.com", "salah"], "Halo", "isi") == 1
    smtp_box["fail"] = True
    assert client.portal.call(mailer.process_due) == 0
    row = _run(_sql("SELECT status, attempts, last_error FROM email_outbox"))[0]
    assert row["status"] == "pending" and row["attempts"] == 1 and "smtp mati" in row["last_error"]
    _run(_sql("UPDATE email_outbox SET next_attempt_at = NOW()"))
    smtp_box["fail"] = False
    assert client.portal.call(mailer.process_due) == 1
    row = _run(_sql("SELECT status, attempts FROM email_outbox"))[0]
    assert row["status"] == "sent" and row["attempts"] == 2
    # menyerah setelah MAX_ATTEMPTS
    client.portal.call(mailer.enqueue, "test", ["c@example.com"], "Lagi", "isi")
    _run(_sql(f"UPDATE email_outbox SET attempts = {mailer.MAX_ATTEMPTS - 1}, next_attempt_at = NOW() WHERE status = 'pending'"))
    smtp_box["fail"] = True
    client.portal.call(mailer.process_due)
    assert _run(_sql("SELECT status FROM email_outbox WHERE to_address = 'c@example.com'"))[0]["status"] == "failed"


def test_disabled_email_queues_nothing(client, superadmin_token, smtp_box):
    client.put(f"{S}/email", headers=auth(superadmin_token), json={"smtp": {"enabled": False}})
    assert client.portal.call(mailer.enqueue, "test", ["a@example.com"], "x", "y") == 0


def test_render_escapes_html():
    text, html = mailer.render("CCD", "Judul", ["<script>x</script>"], "https://a.example/?x=1&y=2", "Buka")
    assert "<script>" not in html and "&lt;script&gt;" in html and "https://a.example/?x=1&amp;y=2" in html
    assert "Buka: https://a.example" in text


# ── reset password lewat email ────────────────────────────────────────────────

@pytest.fixture
def account(client, superadmin_token):
    created = []

    def make(email="auto"):
        name = f"tst_em_{uuid.uuid4().hex[:6]}"
        mail = f"{name}@example.com" if email == "auto" else email
        r = client.post(U, headers=auth(superadmin_token),
                        json={"username": name, "password": PW, "full_name": "Uji Email", "role": "student", "email": mail})
        assert r.status_code == 200, r.text
        created.append(r.json()["id"])
        return name, mail

    yield make
    for uid in created:
        client.delete(f"{U}/{uid}", headers=auth(superadmin_token))


def _token_from_outbox(to):
    rows = _run(_sql("SELECT body_text FROM email_outbox WHERE to_address = $1 AND kind = 'password_reset' ORDER BY id DESC", to))
    assert rows, "tidak ada email reset di antrean"
    m = re.search(r"/\?reset=([\w-]+)", rows[0]["body_text"])
    assert m
    return m.group(1)


def _ask(client, who):
    r = client.post(f"{U}/password-reset/request", json={"identifier": who},
                    headers={"X-Real-IP": f"198.51.100.{uuid.uuid4().int % 250 + 1}"})
    assert r.status_code == 200
    return r


def _wait_outbox(to, n=1):
    import time
    for _ in range(50):
        if len(_run(_sql("SELECT 1 FROM email_outbox WHERE to_address = $1 AND kind = 'password_reset'", to))) >= n:
            return
        time.sleep(0.1)


def test_reset_flow_end_to_end(client, email_on, account):
    email_on()
    name, mail = account()
    old = client.post(f"{U}/login", json={"username": name, "password": PW}).json()["access_token"]
    assert client.get(f"{U}/password-reset").json() == {"enabled": True}

    # dua jenis pengenal sama-sama diterima, jawabannya sama dengan akun yang tidak ada
    same = _ask(client, "tidak-ada-akun").json()
    assert _ask(client, name).json() == same
    _wait_outbox(mail)
    token = _token_from_outbox(mail)

    assert client.post(f"{U}/password-reset/verify", json={"token": "salah"}).status_code == 400
    assert client.post(f"{U}/password-reset/verify", json={"token": token}).json() == {"username": name}
    assert client.post(f"{U}/password-reset/confirm", json={"token": token, "password": "pendek"}).status_code == 400
    ok = client.post(f"{U}/password-reset/confirm", json={"token": token, "password": "PasswordBaru#9"})
    assert ok.status_code == 200, ok.text

    # sekali pakai, sesi lama berakhir, password lama tidak berlaku
    assert client.post(f"{U}/password-reset/confirm", json={"token": token, "password": "LainLagi#99"}).status_code == 400
    assert client.get(f"{U}/me", headers=auth(old)).status_code == 401
    assert client.post(f"{U}/login", json={"username": name, "password": PW}).status_code == 401
    assert client.post(f"{U}/login", json={"username": name, "password": "PasswordBaru#9"}).status_code == 200
    # hanya hash yang tersimpan
    assert token not in str(_run(_sql("SELECT token_hash FROM password_reset_tokens")))


def test_reset_by_email_address_and_new_link_replaces_old(client, email_on, account):
    email_on()
    name, mail = account()
    _ask(client, mail.upper())
    _wait_outbox(mail)
    first = _token_from_outbox(mail)
    _ask(client, name)
    _wait_outbox(mail, 2)
    second = _token_from_outbox(mail)
    assert first != second
    assert client.post(f"{U}/password-reset/verify", json={"token": first}).status_code == 400
    assert client.post(f"{U}/password-reset/verify", json={"token": second}).status_code == 200


def test_reset_is_limited_per_account(client, email_on, account):
    email_on()
    name, mail = account()
    for _ in range(mailer_limit() + 2):
        client.portal.call(_request, name)
    n = len(_run(_sql("SELECT 1 FROM password_reset_tokens p JOIN users u ON u.id = p.user_id WHERE u.username = $1", name)))
    assert n == mailer_limit()


def mailer_limit():
    from services import password_reset
    return password_reset.MAX_PER_USER_HOUR


async def _request(who):
    from services import password_reset
    return await password_reset.request_reset(who, "203.0.113.9")


def test_reset_needs_email_and_active_account(client, superadmin_token, email_on, account):
    email_on()
    name, _ = account(email="")
    assert client.portal.call(_request, name) is False                                       # tanpa email
    name2, mail2 = account()
    uid = _run(_sql("SELECT id FROM users WHERE username = $1", name2))[0]["id"]
    client.put(f"{U}/{uid}", headers=auth(superadmin_token), json={"is_active": False})
    assert client.portal.call(_request, name2) is False                                      # akun nonaktif
    assert _run(_sql("SELECT 1 FROM email_outbox WHERE to_address = $1", mail2)) == []


def test_reset_off_by_default(client, superadmin_token, account):
    _wipe(client, superadmin_token)
    name, mail = account()
    assert client.get(f"{U}/password-reset").json() == {"enabled": False}
    assert client.portal.call(_request, name) is False
    assert client.post(f"{U}/password-reset/confirm", json={"token": "x", "password": "PasswordBaru#9"}).status_code == 400


def test_reset_requires_dashboard_url(client, superadmin_token, monkeypatch):
    client.put(f"{S}/email", headers=auth(superadmin_token), json={
        "smtp": {"enabled": True, "host": "h", "from_address": "a@example.com", "dashboard_url": ""}, "notify": {"self_reset": True}})
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    assert client.get(f"{U}/password-reset").json() == {"enabled": False}      # tanpa alamat, tautan tidak bisa dibuat
    _wipe(client, superadmin_token)


# ── peringatan masa sewa ──────────────────────────────────────────────────────

def test_lease_warning_goes_to_owners_once(client, superadmin_token, email_on, account):
    from services import lifecycle
    email_on(lease=True, lease_days_before=3)
    name, mail = account()
    uid = _run(_sql("SELECT id FROM users WHERE username = $1", name))[0]["id"]
    vm = f"9{uuid.uuid4().int % 90000 + 10000}"
    host = "labMail__pve"
    _run(_sql("INSERT INTO vms (vm_id, host_name, vm_name, lease_until) VALUES ($1, $2, 'vm-mail', NOW() + interval '2 days')", vm, host))
    _run(_sql("INSERT INTO vm_assignments (user_id, vm_id, host_name) VALUES ($1, $2, $3)", uid, vm, host))
    try:
        assert client.portal.call(lifecycle.warn_leases) >= 1
        assert _run(_sql("SELECT 1 FROM email_outbox WHERE to_address = $1 AND kind = 'lease'", mail))
        before = len(_run(_sql("SELECT 1 FROM email_outbox WHERE kind = 'lease'")))
        client.portal.call(lifecycle.warn_leases)
        assert len(_run(_sql("SELECT 1 FROM email_outbox WHERE kind = 'lease'"))) == before     # sekali per batas sewa
        # lease diperpanjang -> peringatan berikutnya dikirim lagi
        _run(_sql("UPDATE vms SET lease_until = NOW() + interval '2 days 1 hour' WHERE vm_id = $1", vm))
        client.portal.call(lifecycle.warn_leases)
        assert len(_run(_sql("SELECT 1 FROM email_outbox WHERE kind = 'lease'"))) == before + 1
    finally:
        _run(_sql("DELETE FROM vm_assignments WHERE vm_id = $1", vm))
        _run(_sql("DELETE FROM vms WHERE vm_id = $1", vm))
