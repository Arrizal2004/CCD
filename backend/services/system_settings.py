"""
Pengaturan sistem yang bisa diubah superadmin dari dashboard: identitas (nama, logo, warna aksen),
bahasa bawaan, pengumuman, aturan pendaftaran mandiri, nilai bawaan (masa sewa VM, masa berlaku akun),
kategori tiket, pilihan OS di form Infra Request, dan alamat SSH (bastion) yang ditampilkan ke pengguna. Disimpan di tabel system_settings (satu baris), jadi setiap sekolah atau kampus
yang memasang CCD bisa menyesuaikannya tanpa mengubah kode. Nilai yang belum diatur memakai DEFAULTS,
sehingga perilaku bawaan sama dengan sebelum fitur ini ada.
"""
import ipaddress
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, available_timezones

from database import get_pool
from i18n import tr

DEFAULTS = {
    "name": "Campus Cloud Dashboard",
    "short_name": "CCD",
    "institution": "",
    "tagline": "Clientless Campus Cloud",
    "registration_open": True,
    "allowed_emails": [],      # "@domain" (termasuk subdomain) atau alamat lengkap; kosong = semua email
    "accent_color": "",        # "#rrggbb"; kosong = warna bawaan
    "default_language": "id",  # "id" atau "en"
    "default_theme": "dark",   # "dark", "light", atau "system" (ikuti perangkat); pengguna tetap bisa mengganti
    "announcement": {"text": "", "level": "info", "starts_at": None, "ends_at": None, "show_on_login": False},
    "default_vm_lease_days": None,   # diisi otomatis di form Create VM
    "default_account_days": None,    # masa berlaku akun hasil pendaftaran mandiri dan impor CSV
    # label kosong = label bawaan (diterjemahkan di frontend). LEASE_EXTENSION dan OTHERS selalu ada.
    "ticket_categories": [{"key": k, "label": ""} for k in
                          ("REMOTE_ISSUE", "PERFORMANCE", "RESOURCE_REQUEST", "LEASE_EXTENSION", "OTHERS")],
    "vps_os_options": ["Windows", "Ubuntu"],   # pilihan OS saat mahasiswa mengajukan VPS, urut sesuai tampilan
    # Alamat bastion SSH di perintah SSH pengguna. Kosong = BASTION_PUBLIC_HOST di .env, lalu alamat yang
    # dibuka pengguna di browser. Bisa diganti di sini saat domain berubah, tanpa menyunting .env.
    "ssh_public_host": "",
    # Berapa hari Activity Log dan riwayat sesi (Remote, Web, SSH) disimpan sebelum dihapus otomatis.
    # Kosong = AUDIT_RETENTION_DAYS di .env (bawaan 180).
    "audit_retention_days": None,
    # Zona waktu (IANA) untuk jam di header dan semua waktu yang tampil atau diekspor.
    "timezone": "Asia/Jakarta",
}
REQUIRED_CATEGORIES = ("LEASE_EXTENSION", "OTHERS")
MIN_RETENTION_DAYS = 7      # lebih pendek dari ini hampir pasti salah ketik dan menghapus jejak yang masih dibutuhkan
MAX_CATEGORIES = 20
MAX_OS_OPTIONS = 20
LEVELS = ("info", "warning", "critical")
LANGUAGES = ("id", "en")
THEMES = ("dark", "light", "system")
_HEX = re.compile(r"^#[0-9a-f]{6}$")
_LIMITS = {"name": 60, "short_name": 12, "institution": 100, "tagline": 100}
MAX_EMAIL_RULES = 200
_DOMAIN = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
_RULE_DOMAIN = re.compile(rf"^@{_DOMAIN}$")
_RULE_ADDRESS = re.compile(rf"^[a-z0-9._%+-]{{1,64}}@{_DOMAIN}$")
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")

OS_LOGO_MAX_BYTES = 256 * 1024

_cache: dict | None = None


async def get_settings() -> dict:
    global _cache
    if _cache is None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data, extract(epoch FROM logo_updated_at)::bigint AS logo_version FROM system_settings WHERE id = 1")
        raw = row["data"] if row else None
        stored = json.loads(raw) if isinstance(raw, str) else (raw or {})
        async with pool.acquire() as conn:
            logos = {r["key"]: r["version"] for r in await conn.fetch(
                "SELECT key, extract(epoch FROM updated_at)::bigint AS version FROM os_logos")}
        _cache = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS},
                  "logo_version": row["logo_version"] if row else None, "os_logos": logos}
    return dict(_cache)


def _luminance(hex_color: str) -> float:
    """Luminans relatif WCAG dari warna #rrggbb (0 = hitam, 1 = putih)."""
    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _days(val, name: str):
    if val in (None, "", 0, "0"):
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        raise ValueError(tr(f"{name} harus berupa angka hari", f"{name} must be a number of days"))
    if not 1 <= n <= 3650:
        raise ValueError(tr(f"{name} harus antara 1 dan 3650 hari",
                            f"{name} must be between 1 and 3650 days"))
    return n


def _retention(val):
    """Hari penyimpanan log audit; kosong = ikut .env."""
    if val in (None, "", 0, "0"):
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        raise ValueError(tr("Lama penyimpanan log harus berupa angka hari",
                            "The log retention must be a number of days"))
    if not MIN_RETENTION_DAYS <= n <= 3650:
        raise ValueError(tr(f"Lama penyimpanan log harus antara {MIN_RETENTION_DAYS} dan 3650 hari",
                            f"The log retention must be between {MIN_RETENTION_DAYS} and 3650 days"))
    return n


def _date(val, name: str):
    if not val:
        return None
    try:
        d = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(tr(f"Tanggal {name} tidak valid", f"The {name} date is invalid"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()


def _categories(items) -> list[dict]:
    out, keys = [], set()
    for item in items or []:
        label = " ".join(str((item or {}).get("label") or "").split())[:40]
        key = str((item or {}).get("key") or "").strip().upper()
        if not key:
            key = re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")[:30]
        if not re.fullmatch(r"[A-Z0-9_]{2,30}", key):
            raise ValueError(tr(f"Kategori '{label or key}' tidak valid; beri nama minimal 2 huruf",
                                f"Category '{label or key}' is invalid; give it a name of at least 2 letters"))
        if key in keys:
            continue
        keys.add(key)
        out.append({"key": key, "label": label})
    for key in REQUIRED_CATEGORIES:                     # dipakai alur "Minta perpanjangan" dan cadangan
        if key not in keys:
            out.append({"key": key, "label": ""})
    if len(out) > MAX_CATEGORIES:
        raise ValueError(tr(f"Maksimal {MAX_CATEGORIES} kategori tiket",
                            f"At most {MAX_CATEGORIES} ticket categories"))
    return out


def _os_entries(items) -> list[dict]:
    """Daftar pilihan OS. Setiap item berupa nama, atau {"name": ..., "from": nama lama} kalau OS ini
    hasil mengganti nama (logonya ikut pindah ke nama baru)."""
    out, seen = [], set()
    for item in items or []:
        raw, old = (item.get("name"), item.get("from")) if isinstance(item, dict) else (item, None)
        label = " ".join(str(raw or "").split())
        if not label:
            continue
        if len(label) > 40:
            raise ValueError(tr(f"Nama OS '{label[:20]}…' maksimal 40 karakter",
                                f"OS name '{label[:20]}…' may be at most 40 characters"))
        if label.lower() not in seen:
            seen.add(label.lower())
            out.append({"name": label, "from": " ".join(str(old or "").split()) or None})
    if not out:
        raise ValueError(tr("Isi minimal satu pilihan OS untuk Infra Request",
                            "Add at least one OS choice for infrastructure requests"))
    if len(out) > MAX_OS_OPTIONS:
        raise ValueError(tr(f"Maksimal {MAX_OS_OPTIONS} pilihan OS", f"At most {MAX_OS_OPTIONS} OS choices"))
    return out


def _os_options(items) -> list[str]:
    return [e["name"] for e in _os_entries(items)]


def _timezone(val) -> str:
    name = str(val or "").strip() or DEFAULTS["timezone"]
    if name not in available_timezones():
        raise ValueError(tr(f"Zona waktu '{name[:40]}' tidak dikenal; pakai nama IANA seperti Asia/Jakarta",
                            f"The time zone '{name[:40]}' is unknown; use an IANA name such as Asia/Jakarta"))
    return name


def tzinfo(s: dict | None = None) -> ZoneInfo:
    """Zona waktu yang berlaku (pengaturan Sistem). Cadangan: Asia/Jakarta."""
    try:
        return ZoneInfo((s or _cache or {}).get("timezone") or DEFAULTS["timezone"])
    except Exception:
        return ZoneInfo(DEFAULTS["timezone"])


def _ssh_host(val) -> str:
    """Nama host atau IPv4 untuk perintah SSH; kosong boleh."""
    host = str(val or "").strip().lower().rstrip(".")
    if not host:
        return ""
    if any(c in host for c in ":/@ "):
        raise ValueError(tr("Alamat SSH diisi nama host atau IP saja, tanpa http://, port, atau garis miring "
                            "(mis. ssh.kampus.ac.id)",
                            "The SSH address is a hostname or IP only, without http://, a port, or slashes "
                            "(e.g. ssh.campus.example)"))
    if all(part.isdigit() for part in host.split(".")):
        try:
            return str(ipaddress.IPv4Address(host))
        except ValueError:
            raise ValueError(tr(f"Alamat SSH '{host}' bukan alamat IPv4 yang valid",
                                f"The SSH address '{host}' is not a valid IPv4 address"))
    if not _HOSTNAME.match(host):
        raise ValueError(tr(f"Alamat SSH '{host}' bukan nama host yang valid",
                            f"The SSH address '{host}' is not a valid hostname"))
    return host


def normalize(body: dict) -> dict:
    """Validasi masukan superadmin. ValueError berisi pesan yang bisa ditampilkan ke pengguna."""
    out = {}
    for key, limit in _LIMITS.items():
        val = " ".join(str(body.get(key, DEFAULTS[key]) or "").split())
        if key in ("name", "short_name") and not val:
            raise ValueError(tr("Nama sistem dan nama singkat wajib diisi",
                                "The system name and short name are required"))
        if len(val) > limit:
            raise ValueError(tr(f"'{key}' maksimal {limit} karakter",
                                f"'{key}' may be at most {limit} characters"))
        out[key] = val
    out["registration_open"] = bool(body.get("registration_open", True))

    rules, bad = [], []
    for item in body.get("allowed_emails") or []:
        rule = str(item).strip().lower()
        if not rule:
            continue
        if not rule.startswith("@") and "@" not in rule:
            rule = "@" + rule                          # "kampus.ac.id" dianggap domain
        if _RULE_DOMAIN.match(rule) or _RULE_ADDRESS.match(rule):
            if rule not in rules:
                rules.append(rule)
        else:
            bad.append(rule)
    if bad:
        raise ValueError(tr("Format email/domain tidak valid: ",
                            "Invalid email/domain format: ") + ", ".join(bad[:5]))
    if len(rules) > MAX_EMAIL_RULES:
        raise ValueError(tr(f"Maksimal {MAX_EMAIL_RULES} email/domain",
                            f"At most {MAX_EMAIL_RULES} email addresses/domains"))
    out["allowed_emails"] = rules

    color = str(body.get("accent_color") or "").strip().lower()
    if color:
        if not _HEX.match(color):
            raise ValueError(tr("Warna aksen harus berformat #rrggbb",
                                "The accent colour must use the #rrggbb format"))
        if _luminance(color) < 0.3:
            raise ValueError(tr("Warna aksen terlalu gelap: teks hitam di tombol tidak akan terbaca. Pilih warna yang lebih terang",
                                "The accent colour is too dark: black text on buttons would be unreadable. Choose a lighter colour"))
    out["accent_color"] = color

    lang = str(body.get("default_language") or "id")
    if lang not in LANGUAGES:
        raise ValueError(tr("Bahasa harus 'id' atau 'en'", "The language must be 'id' or 'en'"))
    out["default_language"] = lang

    theme = str(body.get("default_theme") or "dark")
    if theme not in THEMES:
        raise ValueError(tr("Tema harus 'dark', 'light', atau 'system'",
                            "The theme must be 'dark', 'light' or 'system'"))
    out["default_theme"] = theme

    ann = body.get("announcement") or {}
    text = str(ann.get("text") or "").strip()
    if len(text) > 500:
        raise ValueError(tr("Pengumuman maksimal 500 karakter",
                            "The announcement may be at most 500 characters"))
    level = ann.get("level") or "info"
    if level not in LEVELS:
        raise ValueError(tr("Jenis pengumuman tidak dikenal", "Unknown announcement type"))
    starts, ends = _date(ann.get("starts_at"), tr("mulai",
                                                  "start")), _date(ann.get("ends_at"), tr("selesai",
                                                                                                   "end"))
    if starts and ends and ends <= starts:
        raise ValueError(tr("Tanggal selesai pengumuman harus setelah tanggal mulai",
                            "The announcement end date must be after its start date"))
    out["announcement"] = {"text": text, "level": level, "starts_at": starts, "ends_at": ends,
                           "show_on_login": bool(ann.get("show_on_login"))}

    out["default_vm_lease_days"] = _days(body.get("default_vm_lease_days"), tr("Masa sewa bawaan",
                                                                               "The default lease"))
    out["default_account_days"] = _days(body.get("default_account_days"), tr("Masa berlaku akun bawaan",
                                                                             "The default account validity"))
    # Kategori tiket dan pilihan OS diatur dari halaman Helpdesk dan Infra Requests (endpoint sendiri),
    # jadi tidak ikut disimpan atau diubah dari sini.
    out["timezone"] = _timezone(body.get("timezone"))
    out["ssh_public_host"] = _ssh_host(body.get("ssh_public_host"))
    out["audit_retention_days"] = _retention(body.get("audit_retention_days"))
    return out


async def audit_retention_days() -> int:
    """Hari penyimpanan log audit yang berlaku: pengaturan Sistem, kalau kosong AUDIT_RETENTION_DAYS."""
    from database import AUDIT_RETENTION_DAYS
    return (await get_settings()).get("audit_retention_days") or AUDIT_RETENTION_DAYS


def active_announcement(s: dict) -> dict | None:
    """Pengumuman yang sedang tayang (teks terisi dan sekarang di antara tanggal mulai dan selesai)."""
    ann = s.get("announcement") or {}
    if not ann.get("text"):
        return None
    now = datetime.now(timezone.utc)
    if ann.get("starts_at") and datetime.fromisoformat(ann["starts_at"]) > now:
        return None
    if ann.get("ends_at") and datetime.fromisoformat(ann["ends_at"]) <= now:
        return None
    return {"text": ann["text"], "level": ann["level"], "show_on_login": ann["show_on_login"],
            "id": f"{ann['text']}|{ann.get('starts_at')}"[:200]}


def category_keys(s: dict) -> set[str]:
    return {c["key"] for c in s["ticket_categories"]}


def match_os(s: dict, value: str) -> str | None:
    """Nama OS sesuai daftar (huruf besar/kecil diabaikan), atau None kalau tidak ada di daftar."""
    wanted = " ".join(str(value or "").split()).lower()
    return next((o for o in s["vps_os_options"] if o.lower() == wanted), None)


# ── Logo ──────────────────────────────────────────────────────────────────────
# SVG sengaja tidak diterima: berkas SVG bisa membawa script yang berjalan kalau dibuka langsung.
LOGO_MAX_BYTES = 512 * 1024
_LOGO_MAGIC = {"image/png": (b"\x89PNG\r\n\x1a\n",), "image/jpeg": (b"\xff\xd8\xff",)}


def logo_type(data: bytes) -> str | None:
    for mime, sigs in _LOGO_MAGIC.items():
        if any(data.startswith(sig) for sig in sigs):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def save_logo(data: bytes | None, mime: str | None) -> dict:
    global _cache
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO system_settings (id, logo, logo_type, logo_updated_at) VALUES (1, $1, $2, NOW())
               ON CONFLICT (id) DO UPDATE SET logo = EXCLUDED.logo, logo_type = EXCLUDED.logo_type,
                   logo_updated_at = CASE WHEN EXCLUDED.logo IS NULL THEN NULL ELSE NOW() END""",
            data, mime)
    _cache = None
    return await get_settings()


async def get_logo() -> tuple[bytes, str] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT logo, logo_type FROM system_settings WHERE id = 1 AND logo IS NOT NULL")
    return (bytes(row["logo"]), row["logo_type"]) if row else None


async def save_settings(data: dict, username: str) -> dict:
    """Simpan `data` di atas pengaturan yang sudah ada; kunci yang tidak ada di `data` dibiarkan
    (mis. kategori tiket dan pilihan OS, yang disimpan lewat save_ticket_categories dan save_os_options)."""
    global _cache
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            raw = await conn.fetchval("SELECT data FROM system_settings WHERE id = 1 FOR UPDATE")
            existing = json.loads(raw) if isinstance(raw, str) else (raw or {})
            await conn.execute(
                """INSERT INTO system_settings (id, data, updated_at, updated_by) VALUES (1, $1::jsonb, NOW(), $2)
                   ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW(), updated_by = EXCLUDED.updated_by""",
                json.dumps({**existing, **data}), username)
    _cache = None
    return await get_settings()


async def save_ticket_categories(items, username: str) -> dict:
    return await save_settings({"ticket_categories": _categories(items)}, username)


async def save_os_options(items, username: str) -> dict:
    """Simpan daftar OS. Logo ikut pindah ke nama baru untuk OS yang diganti namanya, dan logo OS yang
    sudah tidak ada di daftar dihapus."""
    global _cache
    entries = _os_entries(items)
    keep = {e["name"].lower() for e in entries}
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for e in entries:
                old = (e["from"] or "").lower()
                if old and old != e["name"].lower() and old not in keep:
                    await conn.execute(
                        "UPDATE os_logos SET key = $2, updated_at = NOW() WHERE key = $1 "
                        "AND NOT EXISTS (SELECT 1 FROM os_logos WHERE key = $2)", old, e["name"].lower())
            await conn.execute("DELETE FROM os_logos WHERE NOT (key = ANY($1::text[]))", sorted(keep))
    _cache = None
    return await save_settings({"vps_os_options": [e["name"] for e in entries]}, username)


async def save_os_logo(name: str, data: bytes, mime: str) -> None:
    global _cache
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO os_logos (key, content_type, data) VALUES ($1, $2, $3)
               ON CONFLICT (key) DO UPDATE SET content_type = EXCLUDED.content_type, data = EXCLUDED.data, updated_at = NOW()""",
            name.lower(), mime, data)
    _cache = None


async def delete_os_logo(name: str) -> bool:
    global _cache
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval("DELETE FROM os_logos WHERE key = $1 RETURNING key", name.lower())
    _cache = None
    return bool(deleted)


async def get_os_logo(name: str) -> tuple[bytes, str] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT data, content_type FROM os_logos WHERE key = $1", name.lower())
    return (bytes(row["data"]), row["content_type"]) if row else None


def email_allowed(email: str, rules: list[str]) -> bool:
    """Daftar kosong = semua email boleh. "@kampus.ac.id" juga menerima subdomain (@student.kampus.ac.id)."""
    if not rules:
        return True
    email = email.strip().lower()
    domain = email.rpartition("@")[2]
    for rule in rules:
        if rule.startswith("@"):
            d = rule[1:]
            if domain == d or domain.endswith("." + d):
                return True
        elif email == rule:
            return True
    return False


def public_view(s: dict) -> dict:
    """Untuk halaman login (tanpa login). Alamat email perorangan di daftar izin tidak ikut dikirim."""
    return {
        "name": s["name"], "short_name": s["short_name"], "institution": s["institution"],
        "tagline": s["tagline"], "registration_open": s["registration_open"],
        "email_required": bool(s["allowed_emails"]),
        "email_domains": [r for r in s["allowed_emails"] if r.startswith("@")],
        "accent_color": s["accent_color"], "default_language": s["default_language"], "default_theme": s["default_theme"],
        "logo_version": s.get("logo_version"),
        "announcement": (a if (a := active_announcement(s)) and a["show_on_login"] else None),
    }
