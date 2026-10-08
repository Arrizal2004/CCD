"""
Memutus sesi SSH di bastion.

Backend tidak bisa menyentuh proses di container bastion, jadi perintahnya diantre di Redis.
Skrip ccd-kill di bastion (root) menunggu antrean ini lewat GET /api/v1/ssh-keys/kill-wait (long
poll), menghentikan proses sshd sesi itu, lalu melapor lewat POST /api/v1/ssh-keys/killed. Setelah
laporan itu sesi ditandai berakhir dengan alasan 'killed'.

Satu perintah = satu baris "<id-sesi> <pid> [<pid>]". PID diambil dari log sshd (proses monitor dan
anaknya); bastion memeriksa ulang bahwa PID itu memang proses sshd sebelum mengirim sinyal.
"""
import asyncio
import logging
import time

from database import get_pool

log = logging.getLogger("ssh_kill")

QUEUE = "ccd:ssh:kill"
ACK = "ccd:ssh:killed:{}"
MAX_BATCH = 50
WAIT_SECONDS = 8.0   # batas menunggu laporan bastion setelah admin menekan Putuskan

_redis = None


def set_redis(r) -> None:
    global _redis
    _redis = r


def _order(session) -> str:
    pids = [str(p) for p in (session["monitor_pid"], session["child_pid"]) if p]
    return " ".join([str(session["id"]), *pids])


async def mark_requested(session_ids: list[int], by: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE ssh_sessions SET killed_by = $2 WHERE id = ANY($1::int[]) AND ended_at IS NULL",
                           session_ids, by)


async def enqueue(sessions) -> list[str]:
    """Antrekan pemutusan tanpa menunggu (dipakai saat akun dinonaktifkan)."""
    orders = [_order(s) for s in sessions if s["monitor_pid"] or s["child_pid"]]
    if orders and _redis is not None:
        await _redis.rpush(QUEUE, *orders)
    return orders


async def kill_and_wait(session, by: str, timeout: float | None = None) -> bool:
    """Antrekan satu sesi lalu tunggu laporan bastion. False kalau bastion tidak menjawab; perintahnya
    ditarik lagi supaya tidak dijalankan belakangan tanpa sepengetahuan admin."""
    if _redis is None or not (session["monitor_pid"] or session["child_pid"]):
        return False
    await mark_requested([session["id"]], by)
    order = _order(session)
    key = ACK.format(session["id"])
    await _redis.delete(key)
    await _redis.rpush(QUEUE, order)
    deadline = time.monotonic() + (WAIT_SECONDS if timeout is None else timeout)
    while time.monotonic() < deadline:
        if await _redis.get(key):
            await _redis.delete(key)
            return True
        await asyncio.sleep(0.25)
    await _redis.lrem(QUEUE, 0, order)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE ssh_sessions SET killed_by = NULL WHERE id = $1 AND ended_at IS NULL", session["id"])
    return False


async def next_orders(wait: int) -> list[str]:
    """Dipanggil bastion: tunggu sampai `wait` detik untuk perintah pertama, lalu ambil sisanya."""
    if _redis is None:
        await asyncio.sleep(wait)
        return []
    first = await _redis.blpop(QUEUE, timeout=wait)
    if not first:
        return []
    orders = [first[1]]
    while len(orders) < MAX_BATCH:
        nxt = await _redis.lpop(QUEUE)
        if not nxt:
            break
        orders.append(nxt)
    return orders


async def acknowledge(session_ids: list[int]) -> int:
    """Laporan bastion: proses sesi sudah dihentikan (atau memang sudah tidak ada)."""
    if not session_ids:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """UPDATE ssh_sessions SET ended_at = COALESCE(ended_at, NOW()),
                      end_reason = CASE WHEN ended_at IS NULL THEN 'killed' ELSE end_reason END
               WHERE id = ANY($1::int[]) RETURNING id""", session_ids)
    if _redis is not None:
        for sid in session_ids:
            await _redis.set(ACK.format(sid), "1", ex=60)
    return len(rows)
