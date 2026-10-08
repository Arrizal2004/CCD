"""
Bahasa pesan untuk pengguna (galat, validasi, keterangan). Frontend mengirim bahasa pilihan pengguna lewat
header Accept-Language ("id" atau "en"); LanguageMiddleware menyimpannya untuk satu permintaan, lalu
tr("teks Indonesia", "English text") memilih versinya. Tugas latar yang dibuat dari permintaan itu ikut
mewarisi bahasanya. Tanpa header (skrip, bastion, test lama) pesan berbahasa Indonesia.
"""
from contextvars import ContextVar

LANGUAGES = ("id", "en")
_lang: ContextVar[str] = ContextVar("ccd_lang", default="id")


def parse_lang(header: str | None) -> str:
    """Bahasa pertama yang dikenal di Accept-Language, mis. 'en-US,en;q=0.9' -> 'en'."""
    for part in (header or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return "id"


def current() -> str:
    return _lang.get()


def tr(id_text: str, en_text: str) -> str:
    return en_text if _lang.get() == "en" else id_text


class LanguageMiddleware:
    """ASGI murni (bukan BaseHTTPMiddleware) supaya bahasanya pasti terlihat oleh endpoint dan dependensinya."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        # WebSocket dari browser membawa Accept-Language bawaan browser, bukan pilihan di dashboard, jadi
        # frontend menambahkan ?lang= ke URL-nya; kalau ada, itu yang dipakai.
        query = (scope.get("query_string") or b"").decode("latin-1")
        chosen = next((v for k, _, v in (p.partition("=") for p in query.split("&")) if k == "lang"), "")
        header = next((v for k, v in scope.get("headers") or [] if k == b"accept-language"), b"")
        token = _lang.set(parse_lang(chosen or header.decode("latin-1")))
        try:
            await self.app(scope, receive, send)
        finally:
            _lang.reset(token)
