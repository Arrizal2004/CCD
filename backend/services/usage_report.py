"""
Laporan pemakaian per kelas (grup) untuk satu rentang tanggal: berapa sesi dan menit tiap mahasiswa memakai VM
lewat Remote (Guacamole), Open Web, dan SSH bastion, berapa kali login, hari aktif, dan kapan terakhir aktif.
Dipakai dosen sebagai bukti kehadiran praktikum. Menit dihitung dari bagian sesi yang jatuh di dalam rentang;
sesi yang masih berjalan dihitung sampai sekarang (atau akhir rentang).

Sumber: guacamole_connection_history (Remote), openweb_sessions, ssh_sessions, dan audit_logs (AUTH_LOGIN).
Menit Open Web adalah perkiraan: dari pembukaan sesi sampai akses terakhir (last_seen), bukan masa berlaku tiketnya.
"""
from datetime import datetime, timedelta, timezone

from database import get_pool
from services import remote_history

MAX_DAYS = 366


def clip_minutes(start: datetime | None, end: datetime | None, lo: datetime, hi: datetime, now: datetime) -> float:
    """Menit sesi [start, end) yang jatuh di [lo, hi). end None = masih berjalan sampai `now`."""
    if start is None:
        return 0.0
    a, b = max(start, lo), min(end or now, hi)
    return max((b - a).total_seconds(), 0.0) / 60


async def class_report(group_id: int, start: datetime, end: datetime, tz, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        members = await conn.fetch(
            """SELECT u.id, u.username, u.full_name, u.last_login FROM group_members gm JOIN users u ON u.id = gm.user_id
               WHERE gm.group_id = $1 AND u.deleted_at IS NULL AND u.role = 'student' ORDER BY u.full_name, u.username""", group_id)
        ids = [m["id"] for m in members]
        logins = {r["user_id"]: r["n"] for r in await conn.fetch(
            """SELECT user_id, count(*) AS n FROM audit_logs WHERE action_type = 'AUTH_LOGIN' AND user_id = ANY($1)
               AND created_at >= $2 AND created_at < $3 GROUP BY user_id""", ids, start, end)}
        web = await conn.fetch(
            """SELECT user_id, created_at, COALESCE(last_seen, created_at) AS seen FROM openweb_sessions
               WHERE user_id = ANY($1) AND created_at < $3 AND COALESCE(last_seen, created_at) > $2""", ids, start, end)
        ssh = await conn.fetch(
            """SELECT user_id, started_at, ended_at FROM ssh_sessions
               WHERE user_id = ANY($1) AND started_at < $3 AND COALESCE(ended_at, NOW()) > $2""", ids, start, end)
    remote = await remote_history.sessions_between([m["username"] for m in members], start, end)

    rows = {m["id"]: {"user_id": m["id"], "username": m["username"], "full_name": m["full_name"] or "",
                      "logins": logins.get(m["id"], 0), "remote_sessions": 0, "remote_minutes": 0.0, "web_sessions": 0,
                      "web_minutes": 0.0, "ssh_sessions": 0, "ssh_minutes": 0.0, "vms": set(), "days": set(), "last_active": None}
            for m in members}
    by_name = {m["username"].lower(): m["id"] for m in members}

    def touch(r, moment):
        if moment is None:
            return
        r["days"].add(moment.astimezone(tz).date())
        if r["last_active"] is None or moment > r["last_active"]:
            r["last_active"] = moment

    for s in remote or []:
        r = rows.get(by_name.get(s["username"].lower()))
        if not r:
            continue
        r["remote_sessions"] += 1
        r["remote_minutes"] += clip_minutes(s["start"], s["end"], start, end, now)
        r["vms"].add(remote_history.split_name(s["connection"])[1])
        touch(r, s["start"])
        touch(r, s["end"])
    for s in web:
        r = rows.get(s["user_id"])
        r["web_sessions"] += 1
        r["web_minutes"] += clip_minutes(s["created_at"], s["seen"], start, end, now)
        touch(r, s["created_at"])
    for s in ssh:
        r = rows.get(s["user_id"])
        r["ssh_sessions"] += 1
        r["ssh_minutes"] += clip_minutes(s["started_at"], s["ended_at"], start, end, now)
        touch(r, s["started_at"])

    students = []
    for r in rows.values():
        total = r["remote_minutes"] + r["web_minutes"] + r["ssh_minutes"]
        sessions = r["remote_sessions"] + r["web_sessions"] + r["ssh_sessions"]
        students.append({**r, "vms": sorted(r["vms"]), "active_days": len(r["days"]), "total_minutes": round(total),
                         "total_sessions": sessions, "active": sessions > 0,
                         "remote_minutes": round(r["remote_minutes"]), "web_minutes": round(r["web_minutes"]),
                         "ssh_minutes": round(r["ssh_minutes"]),
                         "last_active": r["last_active"].isoformat() if r["last_active"] else None, "days": None})
    for s in students:
        s.pop("days")
    active = [s for s in students if s["active"]]
    return {
        "from": start.isoformat(), "to": end.isoformat(),
        "remote_available": remote is not None,
        "summary": {"students": len(students), "active_students": len(active),
                    "participation_pct": round(100 * len(active) / len(students)) if students else 0,
                    "total_minutes": sum(s["total_minutes"] for s in students),
                    "avg_minutes_active": round(sum(s["total_minutes"] for s in active) / len(active)) if active else 0,
                    "total_sessions": sum(s["total_sessions"] for s in students)},
        "students": students,
    }


def parse_range(date_from: str | None, date_to: str | None, tz, today=None) -> tuple[datetime, datetime]:
    """Rentang [dari 00:00, sampai 24:00) di zona waktu sistem. Bawaan: 30 hari terakhir. ValueError kalau tidak valid."""
    today = today or datetime.now(tz).date()
    try:
        d_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today
        d_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else d_to - timedelta(days=29)
    except ValueError:
        raise ValueError("format")
    if d_to < d_from:
        raise ValueError("order")
    if (d_to - d_from).days + 1 > MAX_DAYS:
        raise ValueError("span")
    lo = datetime.combine(d_from, datetime.min.time(), tzinfo=tz)
    hi = datetime.combine(d_to + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return lo.astimezone(timezone.utc), hi.astimezone(timezone.utc)
