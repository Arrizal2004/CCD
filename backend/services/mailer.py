"""
Email keluar lewat SMTP. Pengaturan disimpan di app_settings kunci 'email':
  smtp:   host, port, security (starttls | ssl | none), username, password (terenkripsi), from_address,
          from_name, reply_to, dashboard_url, enabled
  notify: jenis email yang aktif (lease, self_reset) dan lease_days_before

Email tidak dikirim langsung dari permintaan pengguna: enqueue() menaruhnya di tabel email_outbox, lalu
worker (run_worker, dari main.py) mengirimnya dengan percobaan ulang. Server SMTP yang lambat atau mati
tidak pernah memperlambat atau menggagalkan aksi di dashboard.
"""
import asyncio
import html
import json
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from fastapi import HTTPException

from database import get_pool
from i18n import tr
from services import app_settings
from services.ssh_client import decrypt_secret, encrypt_secret

log = logging.getLogger("mailer")

KEY = "email"
EVENTS = ("lease",)
SMTP_DEFAULTS = {"enabled": False, "host": "", "port": 587, "security": "starttls", "username": "", "password_enc": "",
                 "from_address": "", "from_name": "", "reply_to": "", "dashboard_url": ""}
# self_reset: tautan reset password mandiri lewat email (bawaan mati, karena mengubah cara akun dipulihkan).
NOTIFY_DEFAULTS = {**{e: True for e in EVENTS}, "lease_days_before": 3, "self_reset": False}
SECURITY = ("starttls", "ssl", "none")
ALWAYS = ("test", "password_reset")     # tidak punya sakelar sendiri; reset password diatur lewat notify.self_reset
MAX_ATTEMPTS = 8
TIMEOUT = 20
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")

_cache: dict | None = None


# ── pengaturan ────────────────────────────────────────────────────────────────

async def get_config() -> dict:
    global _cache
    if _cache is None:
        stored = await app_settings.get(KEY, {"smtp": {}, "notify": {}})
        smtp = {**SMTP_DEFAULTS, **{k: v for k, v in (stored.get("smtp") or {}).items() if k in SMTP_DEFAULTS}}
        notify = {**NOTIFY_DEFAULTS, **{k: v for k, v in (stored.get("notify") or {}).items() if k in NOTIFY_DEFAULTS}}
        _cache = {"smtp": smtp, "notify": notify}
    return json.loads(json.dumps(_cache))


def forget() -> None:
    global _cache
    _cache = None
    app_settings.forget()


def public_view(cfg: dict) -> dict:
    smtp = {k: v for k, v in cfg["smtp"].items() if k != "password_enc"}
    smtp["has_password"] = bool(cfg["smtp"]["password_enc"])
    return {"smtp": smtp, "notify": cfg["notify"], "events": list(EVENTS)}


def _bad(message: str) -> HTTPException:
    return HTTPException(400, message)


def normalize(body: dict, current: dict) -> dict:
    smtp_in, notify_in = body.get("smtp") or {}, body.get("notify") or {}
    smtp = dict(current["smtp"])
    for k in ("host", "username", "from_address", "from_name", "reply_to", "dashboard_url"):
        if k in smtp_in:
            smtp[k] = str(smtp_in[k] or "").strip()
    if "enabled" in smtp_in:
        smtp["enabled"] = bool(smtp_in["enabled"])
    if "security" in smtp_in:
        if smtp_in["security"] not in SECURITY:
            raise _bad(tr("Keamanan koneksi harus starttls, ssl, atau none", "Connection security must be starttls, ssl, or none"))
        smtp["security"] = smtp_in["security"]
    if "port" in smtp_in:
        try:
            smtp["port"] = int(smtp_in["port"])
        except (TypeError, ValueError):
            raise _bad(tr("Port SMTP harus angka", "The SMTP port must be a number"))
        if not 1 <= smtp["port"] <= 65535:
            raise _bad(tr("Port SMTP tidak valid", "Invalid SMTP port"))
    if "password" in smtp_in and smtp_in["password"] is not None:     # None = tidak diubah, "" = dihapus
        smtp["password_enc"] = encrypt_secret(smtp_in["password"]) if smtp_in["password"] else ""
    if smtp["dashboard_url"] and not re.match(r"^https?://[^\s/]+", smtp["dashboard_url"]):
        raise _bad(tr("Alamat dashboard harus diawali http:// atau https://", "The dashboard address must start with http:// or https://"))
    smtp["dashboard_url"] = smtp["dashboard_url"].rstrip("/")
    for k in ("from_address", "reply_to"):
        if smtp[k] and not _EMAIL.match(smtp[k]):
            raise _bad(tr(f"Alamat email tidak valid: {smtp[k]}", f"Invalid email address: {smtp[k]}"))
    if smtp["enabled"] and (not smtp["host"] or not smtp["from_address"]):
        raise _bad(tr("Isi server SMTP dan alamat pengirim sebelum mengaktifkan email",
                   "Fill in the SMTP server and sender address before turning email on"))
    if any(c in smtp["from_name"] for c in "\r\n<>\""):
        raise _bad(tr("Nama pengirim tidak boleh memuat tanda kutip, < >, atau baris baru",
                   "The sender name cannot contain quotes, < >, or line breaks"))
    notify = dict(current["notify"])
    for e in EVENTS:
        if e in notify_in:
            notify[e] = bool(notify_in[e])
    if "self_reset" in notify_in:
        notify["self_reset"] = bool(notify_in["self_reset"])
    if "lease_days_before" in notify_in:
        try:
            days = int(notify_in["lease_days_before"])
        except (TypeError, ValueError):
            raise _bad(tr("Jumlah hari harus angka", "The number of days must be a number"))
        if not 1 <= days <= 30:
            raise _bad(tr("Peringatan masa sewa 1 sampai 30 hari sebelumnya", "Lease warnings must be 1 to 30 days ahead"))
        notify["lease_days_before"] = days
    return {"smtp": smtp, "notify": notify}


async def save(body: dict, by: str) -> dict:
    cfg = normalize(body, await get_config())
    await app_settings.save(KEY, cfg, by)
    forget()
    return cfg


def dashboard_url(cfg: dict) -> str:
    """Alamat dashboard untuk tautan di email: dari pengaturan, lalu origin pertama di ALLOWED_ORIGINS."""
    if cfg["smtp"]["dashboard_url"]:
        return cfg["smtp"]["dashboard_url"]
    first = (os.getenv("ALLOWED_ORIGINS") or "").split(",")[0].strip().rstrip("/")
    return first if first.startswith("http") else ""


# ── pengiriman ────────────────────────────────────────────────────────────────

def _send_sync(smtp: dict, to: str, subject: str, text: str, body_html: str | None) -> None:
    msg = EmailMessage()
    msg["From"] = formataddr((smtp["from_name"] or smtp["from_address"], smtp["from_address"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=smtp["from_address"].rsplit("@", 1)[-1])
    if smtp["reply_to"]:
        msg["Reply-To"] = smtp["reply_to"]
    msg.set_content(text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    context = ssl.create_default_context()
    if smtp["security"] == "ssl":
        server = smtplib.SMTP_SSL(smtp["host"], smtp["port"], timeout=TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(smtp["host"], smtp["port"], timeout=TIMEOUT)
    try:
        server.ehlo()
        if smtp["security"] == "starttls":
            server.starttls(context=context)
            server.ehlo()
        if smtp["username"]:
            server.login(smtp["username"], decrypt_secret(smtp["password_enc"]) if smtp["password_enc"] else "")
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


async def send_now(cfg: dict, to: str, subject: str, text: str, body_html: str | None = None) -> None:
    await asyncio.to_thread(_send_sync, cfg["smtp"], to, subject, text, body_html)


def valid_address(addr) -> bool:
    return bool(addr) and bool(_EMAIL.match(str(addr)))


async def enqueue(kind: str, recipients, subject: str, text: str, body_html: str | None = None,
                  cfg: dict | None = None) -> int:
    """Masukkan email ke antrean, satu baris per penerima. Tidak melakukan apa pun kalau email nonaktif atau
    jenis email-nya dimatikan (kecuali kind di ALWAYS). Return jumlah email yang diantrekan."""
    cfg = cfg or await get_config()
    if not cfg["smtp"]["enabled"] or (kind not in ALWAYS and not cfg["notify"].get(kind, False)):
        return 0
    to = sorted({str(r).strip().lower() for r in recipients or [] if valid_address(r)})
    if not to:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO email_outbox (kind, to_address, subject, body_text, body_html) VALUES ($1, $2, $3, $4, $5)",
            [(kind, addr, subject[:300], text, body_html) for addr in to])
    return len(to)


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 30 * 2 ** max(attempts - 1, 0)))


async def process_due(limit: int = 20) -> int:
    """Kirim email yang sudah waktunya. Return jumlah yang terkirim."""
    cfg = await get_config()
    if not cfg["smtp"]["enabled"]:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT * FROM email_outbox WHERE status = 'pending' AND next_attempt_at <= NOW()
                   ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED""", limit)
            # Tandai sedang diproses supaya worker lain (atau putaran berikutnya) tidak mengambilnya lagi.
            if rows:
                await conn.execute("UPDATE email_outbox SET next_attempt_at = NOW() + interval '10 minutes' WHERE id = ANY($1)",
                                   [r["id"] for r in rows])
    sent = 0
    for r in rows:
        try:
            await send_now(cfg, r["to_address"], r["subject"], r["body_text"], r["body_html"])
        except Exception as e:
            attempts = r["attempts"] + 1
            failed = attempts >= MAX_ATTEMPTS
            async with pool.acquire() as conn:
                await conn.execute(
                    """UPDATE email_outbox SET attempts = $2, last_error = $3, status = $4,
                              next_attempt_at = NOW() + $5::interval WHERE id = $1""",
                    r["id"], attempts, f"{type(e).__name__}: {e}"[:500], "failed" if failed else "pending",
                    _backoff(attempts))
            log.warning("email #%s ke %s gagal (percobaan %s): %s", r["id"], r["to_address"], attempts, e)
            continue
        async with pool.acquire() as conn:
            await conn.execute("UPDATE email_outbox SET status = 'sent', sent_at = NOW(), attempts = attempts + 1, last_error = NULL WHERE id = $1",
                               r["id"])
        sent += 1
    return sent


async def purge_old(days: int = 30) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM email_outbox WHERE status <> 'pending' AND created_at < NOW() - make_interval(days => $1)",
                           days)


async def run_worker(interval: float = 5.0) -> None:
    log.info("Email worker started (tiap %ss)", interval)
    last_purge = datetime.now(timezone.utc)
    while True:
        await asyncio.sleep(interval)
        try:
            await process_due()
            if datetime.now(timezone.utc) - last_purge > timedelta(hours=6):
                await purge_old()
                last_purge = datetime.now(timezone.utc)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("email worker gagal")


async def recent(limit: int = 30) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, kind, to_address, subject, status, attempts, last_error, created_at, sent_at
               FROM email_outbox ORDER BY id DESC LIMIT $1""", limit)
        counts = await conn.fetch(
            "SELECT status, count(*) AS n FROM email_outbox WHERE created_at > NOW() - interval '7 days' GROUP BY status")
    return {"items": [dict(r) for r in rows], "last_7_days": {r["status"]: r["n"] for r in counts}}


# ── isi email ─────────────────────────────────────────────────────────────────

def render(brand: str, heading: str, lines: list[str], link: str | None = None, link_label: str | None = None,
           footer: str = "") -> tuple[str, str]:
    """Isi email teks dan HTML sederhana. lines: paragraf teks polos (di-escape untuk HTML)."""
    text_parts = [heading, "", *lines]
    if link:
        text_parts += ["", f"{link_label or link}: {link}"]
    if footer:
        text_parts += ["", "--", footer]
    text = "\n".join(text_parts) + "\n"
    paras = "".join(f'<p style="margin:0 0 12px;line-height:1.55">{html.escape(p)}</p>' for p in lines)
    button = (f'<p style="margin:20px 0"><a href="{html.escape(link)}" style="background:#1d5bd6;color:#ffffff;'
              f'padding:9px 16px;border-radius:6px;text-decoration:none;font-weight:600">{html.escape(link_label or link)}</a></p>'
              if link else "")
    body_html = (
        '<!doctype html><html><body style="margin:0;background:#f3f4f6;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#111827">'
        '<div style="max-width:560px;margin:24px auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;padding:24px">'
        f'<div style="font-size:13px;color:#6b7280;margin-bottom:16px">{html.escape(brand)}</div>'
        f'<h1 style="font-size:18px;margin:0 0 16px">{html.escape(heading)}</h1>{paras}{button}'
        + (f'<p style="margin:24px 0 0;font-size:12px;color:#6b7280">{html.escape(footer)}</p>' if footer else "")
        + "</div></body></html>")
    return text, body_html
