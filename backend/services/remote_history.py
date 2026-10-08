"""
Riwayat sesi Remote (Guacamole) dibaca langsung dari database Guacamole.

REST API Guacamole tidak punya offset dan membatasi jumlah baris, sehingga riwayat lewat API
berhenti di beberapa ratus sesi terakhir dan tidak bisa dicari. Database guacamoledb berada di
server PostgreSQL yang sama dengan ccddb (lihat initdb/01-guacamole-init.sh), jadi backend
membacanya dengan kredensialnya sendiri. Alamatnya bisa diatur lewat GUAC_DATABASE_URL; kalau
kosong, nama database di DATABASE_URL diganti guacamoledb. Bila database itu tidak bisa dibuka,
riwayat diambil lewat REST API seperti sebelumnya (tanpa pencarian di server).

Riwayat ikut dihapus setelah AUDIT_RETENTION_DAYS hari, sama dengan Activity Log.
"""
import logging
import os
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import asyncpg

log = logging.getLogger("remote_history")

_pool: asyncpg.Pool | None = None
_failed_at = 0.0
RETRY_AFTER = 60.0   # detik; jangan mencoba membuka koneksi yang gagal pada setiap request


def database_url() -> str:
    url = os.getenv("GUAC_DATABASE_URL", "")
    if url:
        return url
    base = os.getenv("DATABASE_URL", "")
    if not base:
        return ""
    parts = urlsplit(base)
    return urlunsplit(parts._replace(path="/guacamoledb"))


async def _get_pool() -> asyncpg.Pool | None:
    global _pool, _failed_at
    if _pool is not None:
        return _pool
    if time.monotonic() - _failed_at < RETRY_AFTER:
        return None
    url = database_url()
    if not url:
        return None
    try:
        _pool = await asyncpg.create_pool(url, min_size=0, max_size=3, command_timeout=15, timeout=5)
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1 FROM guacamole_connection_history LIMIT 1")
    except Exception as e:
        log.warning("database Guacamole tidak bisa dibaca, riwayat Remote lewat REST API: %s", e)
        if _pool is not None:
            await _pool.close()
        _pool = None
        _failed_at = time.monotonic()
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def reset() -> None:
    """Untuk test: lupakan pool dan kegagalan sebelumnya (mis. setelah GUAC_DATABASE_URL diubah)."""
    global _pool, _failed_at
    _pool = None
    _failed_at = 0.0


_own_ips_cache: set[str] | None = None


def _own_ips() -> set[str]:
    """IP backend sendiri. Sesi yang tokennya dibuat backend sebelum IP pengguna diteruskan ke
    Guacamole tercatat dengan IP ini, bukan IP pengguna; IP itu tidak ditampilkan."""
    global _own_ips_cache
    if _own_ips_cache is None:
        ips = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                ips.add(info[4][0])
        except OSError:
            pass
        _own_ips_cache = ips
    return _own_ips_cache


def split_name(name: str) -> tuple[str, str, str]:
    """'HV/{host}/{vm}' atau 'HV/{host}/{vm}@{akun-os}' -> (host, vm, akun-os)."""
    parts = (name or "").split("/", 2)
    if len(parts) < 3:
        return "", name or "", ""
    vm, _, os_user = parts[2].partition("@")
    return parts[1], vm, os_user


def _ms(dt) -> int | None:
    return int(dt.timestamp() * 1000) if dt else None


def _row(r) -> dict:
    host, vm, os_user = split_name(r["connection_name"])
    start, end = r["start_date"], r["end_date"]
    ip = r["remote_host"] or ""
    return {
        "id":          r["history_id"],
        "username":    r["username"],
        "connection":  r["connection_name"],
        "host":        host,
        "vm":          vm,
        "os_account":  os_user,
        "protocol":    (r["protocol"] or "").upper(),
        "remote_host": "" if ip in _own_ips() else ip,
        "start_date":  _ms(start),
        "end_date":    _ms(end),
        "active":      end is None,
        "duration_s":  round(((end or datetime.now(timezone.utc)) - start).total_seconds()) if start else None,
    }


async def history(limit: int = 50, offset: int = 0, search: str = "", username: str = "") -> dict:
    """Riwayat sesi Remote terbaru dulu. search: sebagian username atau nama VM; username: persis."""
    pool = await _get_pool()
    if pool is None:
        return await _history_rest(limit, offset, search, username)
    where, args = [], []
    if search:
        args.append(f"%{search}%")
        where.append(f"(h.username ILIKE ${len(args)} OR h.connection_name ILIKE ${len(args)})")
    if username:
        args.append(username)
        where.append(f"lower(h.username) = lower(${len(args)})")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM guacamole_connection_history h {clause}", *args)
        rows = await conn.fetch(
            # Koneksi VM dibuat ulang saat disinkronkan (id baru), jadi protokol dicari juga lewat namanya.
            f"""SELECT h.history_id, h.username, h.remote_host, h.connection_name, h.start_date, h.end_date,
                       COALESCE(c.protocol, (SELECT c2.protocol FROM guacamole_connection c2
                                             WHERE c2.connection_name = h.connection_name LIMIT 1)) AS protocol
                FROM guacamole_connection_history h
                LEFT JOIN guacamole_connection c ON c.connection_id = h.connection_id
                {clause}
                ORDER BY h.start_date DESC, h.history_id DESC
                LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}""", *args, limit, offset)
    return {"total": total or 0, "items": [_row(r) for r in rows]}


async def _history_rest(limit: int, offset: int, search: str, username: str) -> dict:
    from services.guac_sync import get_connection_history
    try:
        rows = await get_connection_history(limit=500)
    except Exception as e:
        log.warning("riwayat Remote lewat REST gagal: %s", e)
        rows = []
    s, u = search.lower(), username.lower()
    rows = [r for r in rows
            if (not s or s in (r["username"] or "").lower() or s in (r["connection"] or "").lower())
            and (not u or (r["username"] or "").lower() == u)]
    for r in rows:
        r.setdefault("remote_host", "")
        r.setdefault("os_account", split_name(r["connection"])[2])
        r["vm"] = split_name(r["connection"])[1] or r["vm"]
    return {"total": len(rows), "items": rows[offset:offset + limit]}


async def purge(cutoff: datetime) -> int:
    """Hapus riwayat sesi yang sudah selesai sebelum `cutoff`. Sesi yang masih berjalan tidak disentuh."""
    pool = await _get_pool()
    if pool is None:
        return 0
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "WITH d AS (DELETE FROM guacamole_connection_history WHERE start_date < $1 AND end_date IS NOT NULL "
            "RETURNING 1) SELECT count(*) FROM d", cutoff) or 0
