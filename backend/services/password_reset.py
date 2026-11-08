"""
Reset password mandiri lewat email. Aktif kalau email (SMTP) aktif dan opsi 'self_reset' di Pengaturan → Email
dinyalakan. Tanpa itu, Lupa password tetap lewat admin (permintaan bantuan di halaman Users).

Alur: pengguna mengisi username atau email → kalau cocok dengan akun aktif yang punya email, tautan berisi
token dikirim ke email terdaftar → pengguna membuka tautan dan membuat password baru → semua sesinya diputus.

Keamanan:
  - jawaban permintaan selalu sama dan pekerjaannya dijalankan di latar belakang, jadi tidak bisa dipakai
    menebak akun atau email yang terdaftar (termasuk lewat lama respons);
  - token acak 256 bit, hanya hash SHA-256-nya yang disimpan, berlaku 30 menit, sekali pakai, dan tautan
    lama gugur saat tautan baru diminta;
  - dibatasi 3 permintaan per akun per jam dan 10 per alamat IP per jam;
  - 2FA tetap diminta saat login berikutnya.
"""
import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from database import get_pool
from i18n import tr
from services import mailer

log = logging.getLogger("password_reset")

TTL = timedelta(minutes=30)
MAX_PER_USER_HOUR = 3
MAX_PER_IP_HOUR = 10
MIN_LEN, MAX_LEN = 8, 128


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


async def available() -> bool:
    cfg = await mailer.get_config()
    return bool(cfg["smtp"]["enabled"] and cfg["notify"].get("self_reset") and mailer.dashboard_url(cfg))


async def _find_account(identifier: str):
    ident = (identifier or "").strip().lower()
    if not ident or len(ident) > 254:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM users
               WHERE deleted_at IS NULL AND is_active AND email IS NOT NULL AND email <> ''
                 AND (expires_at IS NULL OR expires_at > NOW())
                 AND (lower(username) = $1 OR lower(email) = $1)
               LIMIT 2""", ident)
    return rows[0] if len(rows) == 1 else None


async def request_reset(identifier: str, ip: str | None) -> bool:
    """Kirim tautan reset kalau akunnya memenuhi syarat. Return True kalau email diantrekan (untuk tes dan log;
    pemanggil tidak pernah memberitahukannya ke pengguna)."""
    if not await available():
        return False
    user = await _find_account(identifier)
    if not user:
        return False
    token = secrets.token_urlsafe(32)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Permintaan bersamaan untuk akun atau IP yang sama diproses berurutan, supaya batas per jam tidak
            # bisa dilewati dengan mengirim banyak permintaan sekaligus.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('ccd-pwreset-user'), $1)", user["id"])
            if ip:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext('ccd-pwreset-ip'), hashtext($1))", ip)
            per_user = await conn.fetchval(
                "SELECT count(*) FROM password_reset_tokens WHERE user_id = $1 AND created_at > NOW() - interval '1 hour'", user["id"])
            per_ip = await conn.fetchval(
                "SELECT count(*) FROM password_reset_tokens WHERE client_ip = $1 AND created_at > NOW() - interval '1 hour'", ip) if ip else 0
            if per_user >= MAX_PER_USER_HOUR or per_ip >= MAX_PER_IP_HOUR:
                log.warning("reset password untuk %s dibatasi (akun %s/jam, IP %s %s/jam)", user["username"], per_user, ip, per_ip)
                return False
            await conn.execute("UPDATE password_reset_tokens SET used_at = NOW() WHERE user_id = $1 AND used_at IS NULL", user["id"])
            await conn.execute(
                "INSERT INTO password_reset_tokens (user_id, token_hash, client_ip, expires_at) VALUES ($1, $2, $3, $4)",
                user["id"], _hash(token), ip, datetime.now(timezone.utc) + TTL)

    from services import system_settings as ss
    from services.audit import both, log_activity
    cfg = await mailer.get_config()
    brand = (await ss.get_settings()).get("name") or "CCD"
    link = f"{mailer.dashboard_url(cfg)}/?reset={token}"
    subject = tr("Reset password akun Anda", "Reset your account password")
    text, body_html = mailer.render(
        brand, tr("Reset password", "Reset your password"),
        [tr(f"Ada permintaan reset password untuk akun {user['username']}. Tautan di bawah berlaku 30 menit dan hanya bisa dipakai sekali.",
            f"Someone asked to reset the password of the account {user['username']}. The link below works for 30 minutes and only once."),
         tr("Setelah password diganti, semua sesi akun ini di perangkat lain akan diakhiri. VM dan akses Anda tidak berubah.",
            "After the password is changed, all sessions of this account on other devices are signed out. Your VMs and access stay the same."),
         tr("Kalau Anda tidak memintanya, abaikan email ini; password Anda tidak berubah.",
            "If you did not ask for this, ignore this email; your password stays the same.")],
        link, tr("Buat password baru", "Create a new password"))
    queued = await mailer.enqueue("password_reset", [user["email"]], subject, text, body_html, cfg=cfg)
    await log_activity({"sub": user["id"], "username": user["username"], "role": user["role"]},
                       "AUTH_PASSWORD_RESET_REQUEST", "INFO", None,
                       both(lambda: tr(f"Tautan reset password dikirim ke email akun '{user['username']}'",
                                       f"A password reset link was sent to the email of the account '{user['username']}'")), ip=ip)
    return queued > 0


async def account_for_token(token: str):
    """Akun pemilik token yang masih berlaku, atau None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT u.*, p.id AS reset_id FROM password_reset_tokens p JOIN users u ON u.id = p.user_id
               WHERE p.token_hash = $1 AND p.used_at IS NULL AND p.expires_at > NOW()
                 AND u.deleted_at IS NULL AND u.is_active AND (u.expires_at IS NULL OR u.expires_at > NOW())""", _hash(token))


def invalid_link() -> HTTPException:
    return HTTPException(400, tr("Tautan reset password tidak valid, sudah dipakai, atau kedaluwarsa. Minta tautan baru dari halaman login.",
                                 "This password reset link is invalid, already used, or expired. Request a new one from the sign-in page."))


async def confirm(token: str, new_password: str, ip: str | None) -> dict:
    """Ganti password lewat token. Return {username}."""
    from auth import forget_account, hash_password
    from services.audit import both, log_activity
    from services.guac_sync import sync_user, with_retry as guac_retry
    from services.login_rate_limit import clear as clear_login_lock

    if not await available():
        raise invalid_link()
    user = await account_for_token(token)
    if not user:
        raise invalid_link()
    if len(new_password) < MIN_LEN:
        raise HTTPException(400, tr(f"Password baru minimal {MIN_LEN} karakter", f"The new password must be at least {MIN_LEN} characters"))
    if len(new_password) > MAX_LEN:
        raise HTTPException(400, tr(f"Password baru maksimal {MAX_LEN} karakter", f"The new password may be at most {MAX_LEN} characters"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            used = await conn.fetchval(
                "UPDATE password_reset_tokens SET used_at = NOW() WHERE id = $1 AND used_at IS NULL RETURNING id", user["reset_id"])
            if not used:                                     # dipakai bersamaan di tab lain
                raise invalid_link()
            await conn.execute("UPDATE password_reset_tokens SET used_at = NOW() WHERE user_id = $1 AND used_at IS NULL", user["id"])
            await conn.execute(
                """UPDATE users SET password_hash = $2, password_version = password_version + 1,
                          must_change_password = FALSE, updated_at = NOW() WHERE id = $1""",
                user["id"], hash_password(new_password))
    forget_account(user["id"])
    await clear_login_lock(user["username"])
    try:
        from routers.guac import disconnect_user
        await disconnect_user(user["username"])
    except Exception:
        pass
    asyncio.create_task(guac_retry(sync_user, user["username"], new_password, user["full_name"] or "",
                                   op_name="self_reset:sync_user"))
    await log_activity({"sub": user["id"], "username": user["username"], "role": user["role"]},
                       "AUTH_PASSWORD_RESET_SELF", "WARNING", None,
                       both(lambda: tr(f"User '{user['username']}' mereset password lewat tautan email; semua sesinya diputus",
                                       f"User '{user['username']}' reset their password through an email link; all their sessions were ended")),
                       ip=ip)
    return {"username": user["username"]}
