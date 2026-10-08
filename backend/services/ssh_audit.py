"""
Audit SSH lewat bastion.

Container bastion meneruskan baris log sshd ke POST /api/v1/ssh-keys/events. sshd menulis log
lewat syslog, jadi setiap baris membawa PID proses sesinya. Modul ini menyusun baris-baris itu
menjadi sesi di tabel ssh_sessions dan menulis Activity Log:

  SSH_LOGIN   key diterima bastion: siapa, key mana, dari IP mana
  SSH_DENIED  penerusan ke IP:port yang bukan haknya (key milik akun yang tidak berhak dicatat
              di routers/ssh_keys.py)
  SSH_LOGOUT  sesi selesai, dengan durasi dan VM tujuan

Isi sesi tidak terlihat oleh bastion (terenkripsi antara laptop dan VM), jadi tidak direkam.

Urutan baris untuk satu koneksi `ssh -J` (M = proses monitor, N = proses anak setelah login):
  sshd-session[M]: Accepted publickey for tunnel from <ip> port <p> ssh2: ED25519 SHA256:...
  sshd-session[M]: User child is on pid N
  sshd-session[N]: debug1: serverloop.c:server_request_direct_tcpip():417 (...): originator ..., target <ip-vm> port 22
  sshd-session[N]: Received request from <ip> port <p> to connect to host <h> port <n>, but the request was denied.
  sshd-session[N]: Transferred: sent <a>, received <b> bytes
  sshd-session[N]: Closing connection to <ip> port <p>

Semua pola dicocokkan dari awal pesan, sehingga teks kiriman klien (mis. nama user pada baris
"Invalid user ...") tidak bisa menyamar sebagai kejadian lain.
"""
import ipaddress
import logging
import re

from database import get_pool
from services.audit import log_activity

log = logging.getLogger("ssh_audit")

MAX_TARGETS = 20      # batas tujuan yang disimpan per sesi
MAX_LINES = 1000      # batas baris per kiriman

_LINE = re.compile(r"^\w{3} [ \d]\d \d\d:\d\d:\d\d \S+ \w+\.\w+ sshd(?:-session)?\[(\d+)\]: (.*)$")
_ACCEPTED = re.compile(r"^Accepted publickey for tunnel from (\S+) port (\d+) ssh2: \S+ (SHA256:[A-Za-z0-9+/]{43})$")
_CHILD = re.compile(r"^User child is on pid (\d+)$")
_TARGET_PREFIX = "debug1: serverloop.c:server_request_direct_tcpip():"
_TARGET = re.compile(r", target (\S+) port (\d+)$")
_DENIED = re.compile(r"^Received request from (\S+) port (\d+) to connect to host (.{1,100}) port (\d+), "
                     r"but the request was denied\.$")
_TRANSFERRED = re.compile(r"^Transferred: sent (\d+), received (\d+) bytes$")
_ENDS = (
    (re.compile(r"^Closing connection to (\S+) port (\d+)$"), "closed"),
    (re.compile(r"^(?:Connection closed by|Disconnected from) user tunnel (\S+) port (\d+)$"), "closed"),
    (re.compile(r"^Timeout, client not responding from user tunnel (\S+) port (\d+)$"), "timeout"),
    (re.compile(r"^Connection from user tunnel (\S+) port (\d+) timed out$"), "timeout"),
    (re.compile(r"^Connection reset by user tunnel (\S+) port (\d+)$"), "error"),
    (re.compile(r"^[\w.]+: Connection from user tunnel (\S+) port (\d+): "), "error"),
)
_END_TEXT = {"timeout": "klien tidak merespons", "error": "koneksi terputus"}


def parse_line(line: str) -> tuple[int, str] | None:
    """Baris syslog busybox -> (pid, pesan), atau None kalau bukan dari sshd."""
    m = _LINE.match(line.rstrip("\r\n"))
    return (int(m[1]), m[2]) if m else None


def _ip(text: str) -> str | None:
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def _fmt_target(host: str, port: str) -> str:
    """IP:port untuk ditampilkan. Host yang bukan IP (ketikan klien) dibersihkan dan dipotong."""
    ip = _ip(host)
    return f"{ip or re.sub(r'[^A-Za-z0-9.:_-]', '?', host)[:64]}:{int(port)}"


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    h, m = divmod(s // 60, 60)
    if h:
        return f"{h} jam {m} menit"
    return f"{m} menit {s % 60} detik" if m else f"{s} detik"


def _user(row) -> dict | None:
    if row["user_id"] is None:
        return None
    return {"sub": row["user_id"], "username": row["username"], "role": row["role"]}


async def _session_by(conn, where: str, *args):
    return await conn.fetchrow(
        f"SELECT * FROM ssh_sessions WHERE ended_at IS NULL AND {where} ORDER BY id DESC LIMIT 1", *args)


async def _close_stale(conn, pid: int) -> None:
    # PID yang sedang hidup tidak mungkin dipakai dua proses. Sesi terbuka yang masih memegang PID
    # ini berarti sudah selesai tanpa baris penutup yang sampai ke backend.
    await conn.execute(
        """UPDATE ssh_sessions SET ended_at = NOW(), end_reason = 'lost'
           WHERE ended_at IS NULL AND (monitor_pid = $1 OR child_pid = $1)""", pid)


async def _handle(conn, pid: int, msg: str, audits: list) -> bool:
    if msg.startswith("Server listening on "):
        # sshd baru (container dibuat ulang): sesi lama pasti sudah putus.
        await conn.execute("UPDATE ssh_sessions SET ended_at = NOW(), end_reason = 'restart' WHERE ended_at IS NULL")
        return True

    if m := _ACCEPTED.match(msg):
        ip = _ip(m[1])
        if not ip:
            return False
        await _close_stale(conn, pid)
        key = await conn.fetchrow(
            """SELECT k.name, u.id, u.username, u.role FROM user_ssh_keys k
               JOIN users u ON u.id = k.user_id WHERE k.fingerprint = $1""", m[3])
        row = await conn.fetchrow(
            """INSERT INTO ssh_sessions (user_id, username, role, key_name, fingerprint, client_ip, client_port, monitor_pid)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *""",
            key["id"] if key else None, key["username"] if key else "", key["role"] if key else "",
            key["name"] if key else "", m[3], ip, int(m[2]), pid)
        who = row["username"] or "(key tidak terdaftar)"
        audits.append((_user(row), "SSH_LOGIN", "INFO", {"id": row["fingerprint"], "name": row["key_name"]},
                       f"{who} login ke bastion SSH dengan key {row['key_name'] or row['fingerprint']}", ip))
        return True

    if m := _CHILD.match(msg):
        child = int(m[1])
        await _close_stale(conn, child)
        sess = await _session_by(conn, "monitor_pid = $1", pid)
        if sess:
            await conn.execute("UPDATE ssh_sessions SET child_pid = $2 WHERE id = $1", sess["id"], child)
        return bool(sess)

    if msg.startswith(_TARGET_PREFIX) and (m := _TARGET.search(msg)):
        sess = await _session_by(conn, "child_pid = $1", pid)
        target = _fmt_target(m[1], m[2])
        if sess and target not in sess["targets"] and len(sess["targets"]) < MAX_TARGETS:
            await conn.execute("UPDATE ssh_sessions SET targets = array_append(targets, $2) WHERE id = $1",
                               sess["id"], target)
        return bool(sess)

    if m := _DENIED.match(msg):
        sess = await _session_by(conn, "client_ip = $1 AND client_port = $2", _ip(m[1]) or "", int(m[2]))
        if not sess:
            return False
        target = _fmt_target(m[3], m[4])
        await conn.execute("UPDATE ssh_sessions SET targets = array_remove(targets, $2) WHERE id = $1",
                           sess["id"], target)
        if target not in sess["denied_targets"] and len(sess["denied_targets"]) < MAX_TARGETS:
            await conn.execute("UPDATE ssh_sessions SET denied_targets = array_append(denied_targets, $2) WHERE id = $1",
                               sess["id"], target)
            audits.append((_user(sess), "SSH_DENIED", "WARNING", {"id": target, "name": target},
                           f"{sess['username'] or sess['fingerprint']} mencoba membuka {target} lewat bastion SSH, "
                           f"ditolak karena bukan VM yang boleh diaksesnya", sess["client_ip"]))
        return True

    if m := _TRANSFERRED.match(msg):
        sess = await _session_by(conn, "child_pid = $1", pid)
        if sess:
            await conn.execute("UPDATE ssh_sessions SET bytes_sent = $2, bytes_received = $3 WHERE id = $1",
                               sess["id"], int(m[1]), int(m[2]))
        return bool(sess)

    for rx, reason in _ENDS:
        if m := rx.match(msg):
            sess = await conn.fetchrow(
                """UPDATE ssh_sessions SET ended_at = NOW(), end_reason = $3
                   WHERE id = (SELECT id FROM ssh_sessions WHERE ended_at IS NULL AND client_ip = $1
                               AND client_port = $2 ORDER BY id DESC LIMIT 1)
                   RETURNING *, EXTRACT(EPOCH FROM ended_at - started_at) AS duration""",
                _ip(m[1]) or "", int(m[2]), reason)
            if not sess:
                return False
            labels = await vm_labels(conn, sess["targets"])
            dest = ", ".join(f"{t} ({labels[t]})" if t in labels else t for t in sess["targets"])
            why = f", {_END_TEXT[reason]}" if reason in _END_TEXT else ""
            audits.append((_user(sess), "SSH_LOGOUT", "INFO", {"id": sess["fingerprint"], "name": sess["key_name"]},
                           f"{sess['username'] or sess['fingerprint']} keluar dari bastion SSH setelah "
                           f"{_fmt_duration(sess['duration'])}{why}, {f'tujuan {dest}' if dest else 'tanpa membuka VM'}",
                           sess["client_ip"]))
            return True
    return False


async def handle_lines(text: str) -> int:
    """Proses baris log dari bastion secara berurutan. Return jumlah baris yang dipakai."""
    handled = 0
    audits: list = []
    pool = await get_pool()
    async with pool.acquire() as conn:
        for line in text.splitlines()[:MAX_LINES]:
            parsed = parse_line(line)
            if not parsed:
                continue
            try:
                handled += await _handle(conn, parsed[0], parsed[1], audits)
            except Exception as e:
                log.warning("baris log bastion gagal diproses: %s", e)
    for user, action, severity, target, detail, ip in audits:
        await log_activity(user, action, severity, target, detail, ip=ip)
    return handled


async def vm_labels(conn, targets: list[str]) -> dict[str, str]:
    """'ip:port' -> nama VM (atau 'VM <id>') dari kredensial yang tersimpan."""
    ips = sorted({t.rsplit(":", 1)[0] for t in targets})
    if not ips:
        return {}
    rows = await conn.fetch(
        """SELECT DISTINCT ON (c.ssh_host) c.ssh_host, c.ssh_port, c.vm_id,
                  COALESCE((SELECT va.vm_name FROM vm_assignments va
                            WHERE va.vm_id = c.vm_id AND va.host_name = c.host_name AND va.vm_name IS NOT NULL
                            ORDER BY va.id DESC LIMIT 1),
                           (SELECT h.vm_name FROM vm_metrics_history h
                            WHERE h.host_name = c.host_name AND h.vm_id = c.vm_id
                            ORDER BY h.recorded_at DESC LIMIT 1)) AS vm_name
           FROM (SELECT vm_id, host_name, ssh_host, ssh_port FROM vm_credentials
                 UNION ALL SELECT vm_id, host_name, ssh_host, ssh_port FROM vm_os_accounts) c
           WHERE c.ssh_host = ANY($1::text[])""", ips)
    by_ip = {r["ssh_host"]: r["vm_name"] or f"VM {r['vm_id']}" for r in rows}
    return {t: by_ip[ip] for t in targets if (ip := t.rsplit(":", 1)[0]) in by_ip}


async def list_sessions(active_only: bool, limit: int = 50, offset: int = 0, username: str = "") -> dict:
    where = ["ended_at IS NULL"] if active_only else []
    args: list = []
    if username:
        args.append(username)
        where.append(f"lower(username) = lower(${len(args)})")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM ssh_sessions {clause}", *args)
        rows = await conn.fetch(
            f"""SELECT id, username, role, key_name, fingerprint, client_ip, targets, denied_targets,
                       bytes_sent, bytes_received, started_at, ended_at, end_reason, killed_by,
                       EXTRACT(EPOCH FROM COALESCE(ended_at, NOW()) - started_at)::int AS duration,
                       CASE WHEN ended_at IS NULL THEN 'active'
                            WHEN killed_by IS NOT NULL THEN 'killed'
                            ELSE COALESCE(end_reason, 'closed') END AS status
                FROM ssh_sessions {clause} ORDER BY started_at DESC
                LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}""", *args, limit, offset)
        labels = await vm_labels(conn, [t for r in rows for t in r["targets"]])
    items = []
    for r in rows:
        d = dict(r)
        d["targets"] = [{"target": t, "vm": labels.get(t)} for t in r["targets"]]
        items.append(d)
    return {"total": total, "items": items}
