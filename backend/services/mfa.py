"""
2FA dengan TOTP (RFC 6238: 6 digit, 30 detik, HMAC-SHA1), cocok dengan Google Authenticator, Microsoft
Authenticator, Authy, 1Password, dan sejenisnya. Ditambah 10 kode pemulihan sekali pakai.

Kebijakan (app_settings 'mfa_policy'):
  required: "off" | "admins" | "all"   siapa yang wajib memakai 2FA saat login
  "admins" = superadmin dan sysadmin. Akun yang wajib tetapi belum memasangnya diminta memasangnya saat login.
"""
import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

from fastapi import HTTPException

from i18n import tr
from services import app_settings
from services.ssh_client import decrypt_secret, encrypt_secret

KEY = "mfa_policy"
DEFAULTS = {"required": "off"}
REQUIRED_VALUES = ("off", "admins", "all")
STEP = 30
DIGITS = 6
WINDOW = 1                 # terima kode satu langkah sebelum/sesudah (jam ponsel meleset ±30 detik)
RECOVERY_COUNT = 10


# ── kebijakan ─────────────────────────────────────────────────────────────────

async def get_policy() -> dict:
    return await app_settings.get(KEY, DEFAULTS)


async def save_policy(data: dict, by: str) -> dict:
    required = (data or {}).get("required", "off")
    if required not in REQUIRED_VALUES:
        raise HTTPException(400, tr("Nilai 'required' harus off, admins, atau all", "'required' must be off, admins, or all"))
    policy = {"required": required}
    await app_settings.save(KEY, policy, by)
    return policy


def required_for(policy: dict, role: str) -> bool:
    if policy["required"] == "all":
        return True
    return policy["required"] == "admins" and role in ("superadmin", "sysadmin")


# ── TOTP ──────────────────────────────────────────────────────────────────────

def new_secret() -> str:
    """Rahasia 160 bit dalam base32 tanpa padding, format yang dipakai aplikasi authenticator."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, step: int, digits: int = DIGITS) -> str:
    mac = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP)


def match_step(secret: str, code: str, last_step: int | None = None, now: float | None = None) -> int | None:
    """Langkah waktu yang cocok dengan `code`, atau None. Langkah yang sudah pernah dipakai (<= last_step)
    ditolak supaya kode yang tersadap tidak bisa diputar ulang."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    base = current_step(now)
    for step in range(base - WINDOW, base + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_svg(uri: str) -> str:
    import segno
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, dark="#111111", light="#ffffff", xmldecl=False)
    return buf.getvalue().decode()


def enrollment(secret: str, account: str, issuer: str) -> dict:
    uri = otpauth_uri(secret, account, issuer)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": qr_svg(uri)}


# ── kode pemulihan ────────────────────────────────────────────────────────────

_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"     # tanpa i, l, o, 0, 1 yang mudah tertukar


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


def _new_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


async def replace_recovery_codes(conn, user_id: int) -> list[str]:
    codes = [_new_code() for _ in range(RECOVERY_COUNT)]
    await conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id = $1", user_id)
    await conn.executemany("INSERT INTO mfa_recovery_codes (user_id, code_hash) VALUES ($1, $2)",
                           [(user_id, _hash_code(c)) for c in codes])
    return codes


def looks_like_recovery(code: str) -> bool:
    c = str(code or "").replace("-", "").strip().lower()
    return len(c) == 10 and all(ch in _RECOVERY_ALPHABET for ch in c)


# ── status pengguna ───────────────────────────────────────────────────────────

async def enable(conn, user_id: int, secret: str, step: int) -> list[str]:
    await conn.execute(
        "UPDATE users SET mfa_secret_enc = $2, mfa_enabled_at = NOW(), mfa_last_step = $3 WHERE id = $1",
        user_id, encrypt_secret(secret), step)
    return await replace_recovery_codes(conn, user_id)


async def disable(conn, user_id: int) -> None:
    await conn.execute("UPDATE users SET mfa_secret_enc = NULL, mfa_enabled_at = NULL, mfa_last_step = NULL WHERE id = $1",
                       user_id)
    await conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id = $1", user_id)


async def verify_user_code(conn, user_id: int, code: str) -> str | None:
    """Periksa kode TOTP atau kode pemulihan milik pengguna. Return 'totp', 'recovery', atau None.
    Dijalankan dengan kunci baris supaya dua permintaan bersamaan tidak sama-sama lolos dengan kode yang sama."""
    async with conn.transaction():
        row = await conn.fetchrow("SELECT mfa_secret_enc, mfa_last_step FROM users WHERE id = $1 FOR UPDATE", user_id)
        if not row or not row["mfa_secret_enc"]:
            return None
        step = match_step(decrypt_secret(row["mfa_secret_enc"]), code, row["mfa_last_step"])
        if step is not None:
            await conn.execute("UPDATE users SET mfa_last_step = $2 WHERE id = $1", user_id, step)
            return "totp"
        if looks_like_recovery(code):
            used = await conn.fetchval(
                """UPDATE mfa_recovery_codes SET used_at = NOW()
                   WHERE id = (SELECT id FROM mfa_recovery_codes WHERE user_id = $1 AND code_hash = $2 AND used_at IS NULL LIMIT 1)
                   RETURNING id""", user_id, _hash_code(code))
            if used:
                return "recovery"
    return None


async def status(conn, user_id: int) -> dict:
    row = await conn.fetchrow("SELECT mfa_secret_enc IS NOT NULL AS enabled, mfa_enabled_at FROM users WHERE id = $1", user_id)
    left = await conn.fetchval("SELECT count(*) FROM mfa_recovery_codes WHERE user_id = $1 AND used_at IS NULL", user_id)
    return {"enabled": bool(row and row["enabled"]), "enabled_at": row["mfa_enabled_at"] if row else None,
            "recovery_codes_left": int(left or 0)}


def bad_code() -> HTTPException:
    return HTTPException(400, tr("Kode verifikasi salah atau sudah dipakai", "The verification code is wrong or was already used"))
