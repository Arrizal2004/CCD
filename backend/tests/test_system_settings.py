"""
Pengaturan Sistem: identitas yang bisa diubah superadmin dan aturan pendaftaran mandiri.
"""
import uuid

import pytest

from services.system_settings import DEFAULTS, email_allowed, normalize
from tests.conftest import auth


@pytest.fixture
def restore_settings(client, superadmin_token):
    """Kembalikan pengaturan bawaan setelah test, karena tabelnya dipakai bersama test lain."""
    yield
    client.put("/api/v1/system/settings", json=DEFAULTS, headers=auth(superadmin_token))
    client.delete("/api/v1/system/logo", headers=auth(superadmin_token))


def _register(client, email=None):
    name = "tst_reg_" + uuid.uuid4().hex[:8]
    return client.post("/api/v1/users/register",
                       json={"username": name, "password": "Rahasia123!", "full_name": "Uji Daftar", "email": email})


@pytest.mark.parametrize("email,rules,ok", [
    ("budi@kampus.ac.id", [], True),
    ("budi@kampus.ac.id", ["@kampus.ac.id"], True),
    ("budi@student.kampus.ac.id", ["@kampus.ac.id"], True),        # subdomain ikut
    ("budi@kampus.ac.id.evil.com", ["@kampus.ac.id"], False),
    ("budi@notkampus.ac.id", ["@kampus.ac.id"], False),
    ("Tamu@Gmail.com", ["@kampus.ac.id", "tamu@gmail.com"], True),
    ("lain@gmail.com", ["@kampus.ac.id", "tamu@gmail.com"], False),
])
def test_email_allowed(email, rules, ok):
    assert email_allowed(email, rules) is ok


def test_normalize_rules_and_rejects_invalid():
    out = normalize({**DEFAULTS, "allowed_emails": [" @Kampus.AC.id ", "kampus.ac.id", "Tamu@Gmail.com", ""]})
    assert out["allowed_emails"] == ["@kampus.ac.id", "tamu@gmail.com"]
    with pytest.raises(ValueError):
        normalize({**DEFAULTS, "allowed_emails": ["@bukan domain"]})
    with pytest.raises(ValueError):
        normalize({**DEFAULTS, "name": "  "})


def test_branding_is_public_and_hides_personal_addresses(client, superadmin_token, restore_settings):
    r = client.put("/api/v1/system/settings", headers=auth(superadmin_token), json={
        **DEFAULTS, "name": "Lab Cloud SMK Uji", "short_name": "LCU", "institution": "SMK Uji",
        "allowed_emails": ["@smkuji.sch.id", "tamu@gmail.com"]})
    assert r.status_code == 200, r.text
    b = client.get("/api/v1/system/branding").json()        # tanpa token
    assert b["name"] == "Lab Cloud SMK Uji" and b["short_name"] == "LCU" and b["institution"] == "SMK Uji"
    assert b["email_required"] is True and b["email_domains"] == ["@smkuji.sch.id"]
    assert "tamu@gmail.com" not in str(b)


def test_settings_superadmin_only(client, sysadmin_token, student_token):
    for token in (sysadmin_token, student_token):
        assert client.get("/api/v1/system/settings", headers=auth(token)).status_code == 403
        assert client.put("/api/v1/system/settings", json=DEFAULTS, headers=auth(token)).status_code == 403


def test_invalid_settings_rejected(client, superadmin_token):
    r = client.put("/api/v1/system/settings", headers=auth(superadmin_token),
                   json={**DEFAULTS, "allowed_emails": ["bukan email"]})
    assert r.status_code == 400 and "tidak valid" in r.json()["detail"]


def test_registration_closed(client, superadmin_token, restore_settings):
    client.put("/api/v1/system/settings", json={**DEFAULTS, "registration_open": False}, headers=auth(superadmin_token))
    assert client.get("/api/v1/system/branding").json()["registration_open"] is False
    assert _register(client, "a@b.id").status_code == 403


def test_registration_email_rules(client, superadmin_token, restore_settings):
    client.put("/api/v1/system/settings", headers=auth(superadmin_token),
               json={**DEFAULTS, "allowed_emails": ["@smkuji.sch.id"]})
    assert _register(client).status_code == 400                                  # email wajib
    assert _register(client, "orang@gmail.com").status_code == 400                # domain lain
    ok = "siswa." + uuid.uuid4().hex[:6] + "@smkuji.sch.id"
    assert _register(client, ok).status_code == 200, "email domain yang diizinkan harus diterima"
    assert _register(client, ok.upper()).status_code == 409                       # satu email satu akun


def test_default_registration_unchanged(client):
    """Tanpa pengaturan: pendaftaran terbuka dan email tetap opsional, seperti sebelumnya."""
    assert _register(client).status_code == 200


# ── Warna, bahasa, pengumuman, nilai bawaan, kategori, logo ──────────────────

def _put(client, token, **changes):
    return client.put("/api/v1/system/settings", headers=auth(token), json={**DEFAULTS, **changes})


def test_accent_color_must_be_bright_enough(client, superadmin_token, restore_settings):
    assert _put(client, superadmin_token, accent_color="#0b2a5b").status_code == 400      # biru tua: teks hitam tak terbaca
    assert _put(client, superadmin_token, accent_color="merah").status_code == 400
    assert _put(client, superadmin_token, accent_color="#F59E0B", default_language="en").status_code == 200
    b = client.get("/api/v1/system/branding").json()
    assert b["accent_color"] == "#f59e0b" and b["default_language"] == "en"


def test_announcement_window_and_login_visibility(client, superadmin_token, student_token, restore_settings):
    _put(client, superadmin_token, announcement={"text": "Maintenance Sabtu", "level": "warning", "show_on_login": False})
    assert client.get("/api/v1/system/branding").json()["announcement"] is None          # tidak tampil di login
    a = client.get("/api/v1/system/config", headers=auth(student_token)).json()["announcement"]
    assert a["text"] == "Maintenance Sabtu" and a["level"] == "warning"

    _put(client, superadmin_token, announcement={"text": "Libur", "level": "info", "show_on_login": True})
    assert client.get("/api/v1/system/branding").json()["announcement"]["text"] == "Libur"

    _put(client, superadmin_token, announcement={"text": "Nanti", "starts_at": "2099-01-01T00:00:00Z"})
    assert client.get("/api/v1/system/config", headers=auth(student_token)).json()["announcement"] is None
    r = _put(client, superadmin_token, announcement={"text": "x", "starts_at": "2030-01-02", "ends_at": "2030-01-01"})
    assert r.status_code == 400
    assert client.get("/api/v1/system/config").status_code == 401                         # perlu login


def test_default_account_days_on_registration(client, superadmin_token, restore_settings):
    _put(client, superadmin_token, default_account_days=30)
    r = _register(client)
    assert r.status_code == 200
    users = client.get("/api/v1/users", headers=auth(superadmin_token)).json()
    newest = max((u for u in users if u["username"].startswith("tst_reg_")), key=lambda u: u["id"])
    assert newest["expires_at"]


def test_custom_ticket_categories(client, superadmin_token, student_token, restore_settings):
    r = _put(client, superadmin_token, ticket_categories=[{"label": "Praktikum Jaringan"}, {"key": "REMOTE_ISSUE", "label": "Tidak bisa Connect"}])
    assert r.status_code == 200
    cats = r.json()["ticket_categories"]
    keys = [c["key"] for c in cats]
    assert keys[:2] == ["PRAKTIKUM_JARINGAN", "REMOTE_ISSUE"] and {"LEASE_EXTENSION", "OTHERS"} <= set(keys)
    assert client.get("/api/v1/system/config", headers=auth(student_token)).json()["ticket_categories"] == cats

    def created_category(cat):
        t = client.post("/api/tickets", headers=auth(student_token), json={"title": "uji kategori", "category": cat}).json()
        return client.get(f"/api/tickets/{t['id']}", headers=auth(student_token)).json()["ticket"]["category"]
    assert created_category("PRAKTIKUM_JARINGAN") == "PRAKTIKUM_JARINGAN"
    assert created_category("PERFORMANCE") == "OTHERS"                    # sudah dihapus dari daftar


PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360000002000100e5274a2f0000000049454e44ae426082")


def test_logo_upload_serve_and_reject(client, superadmin_token, sysadmin_token, restore_settings):
    files = {"file": ("logo.png", PNG_1PX, "image/png")}
    assert client.post("/api/v1/system/logo", files=files, headers=auth(sysadmin_token)).status_code == 403
    r = client.post("/api/v1/system/logo", files=files, headers=auth(superadmin_token))
    assert r.status_code == 200 and r.json()["logo_version"]
    assert client.get("/api/v1/system/branding").json()["logo_version"] == r.json()["logo_version"]
    img = client.get("/api/v1/system/logo")
    assert img.status_code == 200 and img.content == PNG_1PX and img.headers["content-type"] == "image/png"
    assert img.headers["x-content-type-options"] == "nosniff"

    svg = {"file": ("logo.svg", b"<svg onload='alert(1)'/>", "image/svg+xml")}
    assert client.post("/api/v1/system/logo", files=svg, headers=auth(superadmin_token)).status_code == 400
    big = {"file": ("big.png", PNG_1PX + b"0" * (600 * 1024), "image/png")}
    assert client.post("/api/v1/system/logo", files=big, headers=auth(superadmin_token)).status_code == 400

    assert client.delete("/api/v1/system/logo", headers=auth(superadmin_token)).status_code == 200
    assert client.get("/api/v1/system/logo").status_code == 404
    assert client.get("/api/v1/system/branding").json()["logo_version"] is None


def test_default_theme(client, superadmin_token, restore_settings):
    assert client.get("/api/v1/system/branding").json()["default_theme"] == "dark"         # bawaan tidak berubah
    assert _put(client, superadmin_token, default_theme="light").status_code == 200
    assert client.get("/api/v1/system/branding").json()["default_theme"] == "light"
    assert _put(client, superadmin_token, default_theme="system").status_code == 200
    assert _put(client, superadmin_token, default_theme="pink").status_code == 400


def test_vps_os_options(client, superadmin_token, student_token, restore_settings):
    config = lambda: client.get("/api/v1/system/config", headers=auth(student_token)).json()["vps_os_options"]
    assert config() == ["Windows", "Ubuntu"]                                               # bawaan tidak berubah
    r = _put(client, superadmin_token, vps_os_options=[" Rocky  Linux 9 ", "openSUSE Leap 16", "rocky linux 9", "", "Windows Server 2022"])
    assert r.status_code == 200, r.text
    assert r.json()["vps_os_options"] == ["Rocky Linux 9", "openSUSE Leap 16", "Windows Server 2022"]
    assert config()[0] == "Rocky Linux 9"
    assert _put(client, superadmin_token, vps_os_options=[]).status_code == 400
    assert _put(client, superadmin_token, vps_os_options=["x" * 41]).status_code == 400

    def request(os_name):
        return client.post("/api/v1/infra-requests", headers=auth(student_token), json={
            "request_type": "VPS", "notes": "uji OS", "specs": {"cpu": 2, "ram_gb": 4, "storage_gb": 40, "os": os_name}})
    r = request("OPENSUSE  leap 16")
    assert r.status_code == 200 and r.json()["specs"]["os"] == "openSUSE Leap 16"            # ditulis sesuai daftar
    r = request("Ubuntu")                                                                   # sudah dihapus dari daftar
    assert r.status_code == 400 and "tidak tersedia" in r.json()["detail"]


# ── Alamat SSH (bastion) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("host,expected", [
    ("", ""),
    ("ssh.kampus.ac.id", "ssh.kampus.ac.id"),
    (" SSH.Kampus.AC.ID. ", "ssh.kampus.ac.id"),
    ("203.0.113.10", "203.0.113.10"),
    ("localhost", "localhost"),
    ("https://ssh.kampus.ac.id", None),        # bukan URL
    ("ssh.kampus.ac.id:2222", None),           # port diatur terpisah
    ("tunnel@ssh.kampus.ac.id", None),
    ("999.1.1.1", None),
    ("ssh_kampus.id", None),
    ("-ssh.kampus.id", None),
])
def test_ssh_public_host_validation(host, expected):
    if expected is None:
        with pytest.raises(ValueError):
            normalize({**DEFAULTS, "ssh_public_host": host})
    else:
        assert normalize({**DEFAULTS, "ssh_public_host": host})["ssh_public_host"] == expected


def test_ssh_public_host_overrides_env(client, superadmin_token, student_token, restore_settings, monkeypatch):
    """Urutan: Pengaturan Sistem, lalu BASTION_PUBLIC_HOST di .env, lalu alamat yang dibuka pengguna."""
    host = lambda: client.get("/api/v1/ssh-keys/config", headers=auth(student_token)).json()["host"]
    monkeypatch.delenv("BASTION_PUBLIC_HOST", raising=False)
    assert host() == "testserver"
    monkeypatch.setenv("BASTION_PUBLIC_HOST", "ssh.lama.example")
    assert host() == "ssh.lama.example"
    s = client.get("/api/v1/system/settings", headers=auth(superadmin_token)).json()
    assert s["ssh_public_host"] == "" and s["ssh_env"]["env_host"] == "ssh.lama.example"

    r = _put(client, superadmin_token, ssh_public_host="SSH.Baru.Example", ssh_env={"diabaikan": True})
    assert r.status_code == 200, r.text
    assert r.json()["ssh_public_host"] == "ssh.baru.example" and r.json()["ssh_env"]["env_host"] == "ssh.lama.example"
    assert host() == "ssh.baru.example"
    assert _put(client, superadmin_token, ssh_public_host="https://ssh.baru.example").status_code == 400
    assert _put(client, superadmin_token, ssh_public_host="").status_code == 200
    assert host() == "ssh.lama.example"
