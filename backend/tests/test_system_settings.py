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
    h = auth(superadmin_token)
    client.put("/api/v1/system/settings", json=DEFAULTS, headers=h)
    client.put("/api/v1/system/ticket-categories", json={"categories": DEFAULTS["ticket_categories"]}, headers=h)
    client.put("/api/v1/system/os-options", json={"options": DEFAULTS["vps_os_options"]}, headers=h)
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


def test_custom_ticket_categories(client, superadmin_token, sysadmin_token, student_token, restore_settings):
    body = {"categories": [{"label": "Praktikum Jaringan"}, {"key": "REMOTE_ISSUE", "label": "Tidak bisa Connect"}]}
    url = "/api/v1/system/ticket-categories"
    assert client.put(url, json=body, headers=auth(sysadmin_token)).status_code == 403     # hanya superadmin
    r = client.put(url, json=body, headers=auth(superadmin_token))
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

    # Menyimpan pengaturan Sistem tidak menyentuh kategori (diatur dari Helpdesk).
    assert _put(client, superadmin_token, name="Nama Lain").status_code == 200
    assert client.get("/api/v1/system/config", headers=auth(student_token)).json()["ticket_categories"] == cats
    bad = client.put(url, json={"categories": [{"label": "x"}]}, headers=auth(superadmin_token))
    assert bad.status_code == 400


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
    put = lambda options: client.put("/api/v1/system/os-options", json={"options": options}, headers=auth(superadmin_token))
    assert client.put("/api/v1/system/os-options", json={"options": ["A"]}, headers=auth(student_token)).status_code == 403
    r = put([" Rocky  Linux 9 ", "openSUSE Leap 16", "rocky linux 9", "", "Windows Server 2022"])
    assert r.status_code == 200, r.text
    assert r.json()["vps_os_options"] == ["Rocky Linux 9", "openSUSE Leap 16", "Windows Server 2022"]
    assert config()[0] == "Rocky Linux 9"
    assert put([]).status_code == 400
    assert put(["x" * 41]).status_code == 400
    assert _put(client, superadmin_token, name="Nama Lain").status_code == 200
    assert config()[0] == "Rocky Linux 9"                                                  # pengaturan Sistem tidak menimpa

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


# ── Lama penyimpanan log audit ───────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), (0, None), ("0", None),
    (7, 7), ("90", 90), (3650, 3650),
])
def test_audit_retention_accepts(value, expected):
    assert normalize({**DEFAULTS, "audit_retention_days": value})["audit_retention_days"] == expected


@pytest.mark.parametrize("value", [1, 6, 3651, "abc", -5])
def test_audit_retention_rejects(value):
    with pytest.raises(ValueError):
        normalize({**DEFAULTS, "audit_retention_days": value})


def test_audit_stats_requires_superadmin(client, sysadmin_token, student_token, superadmin_token):
    assert client.get("/api/v1/system/audit-stats", headers=auth(student_token)).status_code == 403
    assert client.get("/api/v1/system/audit-stats", headers=auth(sysadmin_token)).status_code == 403
    r = client.get("/api/v1/system/audit-stats", headers=auth(superadmin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["rows"] >= 1 and data["bytes"] > 0 and data["oldest"]
    assert data["effective_days"] == data["env_default"]          # belum diatur: ikut .env


def test_purge_follows_the_setting(client, superadmin_token, restore_settings):
    import asyncpg
    from tests.conftest import DATABASE_URL, _run
    from database import purge_old_audit_logs

    async def seed():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute("DELETE FROM audit_logs WHERE username = 'tst_retention'")
            for days in (30, 2):
                await conn.execute(
                    "INSERT INTO audit_logs (username, action_type, severity_level, created_at) "
                    "VALUES ('tst_retention', 'TEST_RETENTION', 'INFO', NOW() - make_interval(days => $1))", days)
        finally:
            await conn.close()

    async def count():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            return await conn.fetchval("SELECT count(*) FROM audit_logs WHERE username = 'tst_retention'")
        finally:
            await conn.close()

    _run(seed())
    body = {**DEFAULTS, "audit_retention_days": 7}
    assert client.put("/api/v1/system/settings", json=body, headers=auth(superadmin_token)).status_code == 200
    assert client.get("/api/v1/system/audit-stats", headers=auth(superadmin_token)).json()["effective_days"] == 7
    client.portal.call(purge_old_audit_logs)
    assert _run(count()) == 1                                      # yang 30 hari hilang, yang 2 hari tetap

    # Dikosongkan: kembali ke AUDIT_RETENTION_DAYS (180), jadi yang tersisa tidak ikut terhapus.
    assert client.put("/api/v1/system/settings", json=DEFAULTS, headers=auth(superadmin_token)).status_code == 200
    client.portal.call(purge_old_audit_logs)
    assert _run(count()) == 1
    _run(seed())
    assert client.put("/api/v1/system/settings", json=DEFAULTS, headers=auth(superadmin_token)).status_code == 200
    client.portal.call(purge_old_audit_logs)
    assert _run(count()) == 2                                      # 30 hari < 180 hari: tetap ada


# ── Logo OS ──────────────────────────────────────────────────────────────────

def test_os_logo_lifecycle(client, superadmin_token, sysadmin_token, student_token, restore_settings):
    h = auth(superadmin_token)
    put = lambda options: client.put("/api/v1/system/os-options", json={"options": options}, headers=h)
    cfg = lambda: client.get("/api/v1/system/config", headers=auth(student_token)).json()
    assert put(["Ubuntu 24", "Rocky 9"]).status_code == 200
    png = {"file": ("u.png", PNG_1PX, "image/png")}

    assert client.post("/api/v1/system/os-logo?name=Ubuntu 24", files=png, headers=auth(sysadmin_token)).status_code == 403
    assert client.post("/api/v1/system/os-logo?name=Tidak Ada", files=png, headers=h).status_code == 404
    r = client.post("/api/v1/system/os-logo?name=ubuntu 24", files=png, headers=h)      # huruf kecil tetap cocok
    assert r.status_code == 200 and list(r.json()["os_logos"]) == ["Ubuntu 24"]
    assert list(cfg()["os_logos"]) == ["Ubuntu 24"]

    img = client.get("/api/v1/system/os-logo?name=Ubuntu 24")                         # tanpa login, seperti logo sistem
    assert img.status_code == 200 and img.content == PNG_1PX and img.headers["content-type"] == "image/png"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/v1/system/os-logo?name=Rocky 9").status_code == 404

    svg = {"file": ("u.svg", b"<svg onload='alert(1)'/>", "image/svg+xml")}
    assert client.post("/api/v1/system/os-logo?name=Rocky 9", files=svg, headers=h).status_code == 400
    big = {"file": ("b.png", PNG_1PX + b"0" * (300 * 1024), "image/png")}
    assert client.post("/api/v1/system/os-logo?name=Rocky 9", files=big, headers=h).status_code == 400

    # Ganti nama: logo ikut pindah. Hapus dari daftar: logonya ikut dihapus.
    assert put([{"name": "Ubuntu Server 24", "from": "Ubuntu 24"}, "Rocky 9"]).status_code == 200
    assert list(cfg()["os_logos"]) == ["Ubuntu Server 24"]
    assert client.get("/api/v1/system/os-logo?name=Ubuntu Server 24").content == PNG_1PX
    assert put(["Rocky 9"]).status_code == 200
    assert cfg()["os_logos"] == {} and client.get("/api/v1/system/os-logo?name=Ubuntu Server 24").status_code == 404

    client.post("/api/v1/system/os-logo?name=Rocky 9", files=png, headers=h)
    assert client.delete("/api/v1/system/os-logo?name=Rocky 9", headers=h).status_code == 200
    assert cfg()["os_logos"] == {}


# ── Zona waktu ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (None, "Asia/Jakarta"), ("", "Asia/Jakarta"), ("Asia/Makassar", "Asia/Makassar"), (" UTC ", "UTC"),
])
def test_timezone_accepts(value, expected):
    assert normalize({**DEFAULTS, "timezone": value})["timezone"] == expected


@pytest.mark.parametrize("value", ["WIB", "Asia/Jakartaa", "../etc/passwd", "x" * 100])
def test_timezone_rejects(value):
    with pytest.raises(ValueError):
        normalize({**DEFAULTS, "timezone": value})


def test_timezone_in_config_and_csv(client, superadmin_token, student_token, restore_settings):
    assert client.get("/api/v1/system/config", headers=auth(student_token)).json()["timezone"] == "Asia/Jakarta"
    assert _put(client, superadmin_token, timezone="Asia/Jayapura").status_code == 200
    assert client.get("/api/v1/system/config", headers=auth(student_token)).json()["timezone"] == "Asia/Jayapura"
    csv_head = client.get("/api/admin/audit-logs/export", headers={**auth(superadmin_token), "Accept-Language": "en"}).content
    assert csv_head.decode("utf-8-sig").splitlines()[0].startswith("Time (Asia/Jayapura),User")
    assert _put(client, superadmin_token, timezone="Mars/Olympus").status_code == 400
