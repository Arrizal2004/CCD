"""
Resource VPS tempat dashboard berjalan: live (sampel tiap 5 detik, disimpan di memori 15 menit) dan
riwayat (rata-rata dan puncak per menit di tabel vps_metrics, disimpan 30 hari).

CPU, RAM, swap, load, dan uptime dibaca dari /proc container backend. Docker tidak mengisolasi
berkas-berkas itu, jadi isinya angka seluruh VPS. Disk dibaca dari partisi tempat Docker menyimpan
container, biasanya partisi root VPS. Penghitung jaringan memang terisolasi per container, jadi
lalu lintas VPS dibaca dari /proc/1/net/dev host yang di-mount read-only ke /host/net_dev
(docker-compose.yml). Tanpa mount itu, angka jaringan dikosongkan.
"""
import asyncio
import logging
import os
import shutil
import time
from collections import deque

from database import get_pool

log = logging.getLogger("vps_metrics")

SAMPLE_SECONDS = 5
STORE_EVERY = 12            # 12 sampel x 5 detik = 1 baris per menit
LIVE_POINTS = 180           # 15 menit terakhir; retensi 30 hari diatur cleanup_old_metrics() di database.py
PROC = os.getenv("VPS_PROC", "/proc")
NET_DEV = os.getenv("VPS_NET_DEV", "/host/net_dev")
# Antarmuka virtual tidak dihitung: lalu lintasnya sudah terhitung di antarmuka fisik (eth0).
_VIRTUAL_IF = ("lo", "veth", "docker", "br-", "tailscale", "virbr", "wg")

# Rentang riwayat -> (panjang, ukuran ember) dalam detik.
RANGES = {"1h": (3600, 60), "24h": (86400, 300), "7d": (7 * 86400, 1800), "30d": (30 * 86400, 7200)}

_live: deque = deque(maxlen=LIVE_POINTS)
_prev: dict = {"cpu": None, "net": None, "t": None}


def _cpu_times() -> tuple[int, int, int]:
    with open(f"{PROC}/stat") as fh:
        v = [int(x) for x in fh.readline().split()[1:9]]   # user nice system idle iowait irq softirq steal
    return sum(v), v[3] + v[4], v[4]


def _meminfo() -> dict[str, int]:
    out = {}
    with open(f"{PROC}/meminfo") as fh:
        for line in fh:
            key, rest = line.split(":", 1)
            out[key] = int(rest.split()[0]) * 1024          # kB -> byte
    return out


def net_bytes(path: str = NET_DEV) -> tuple[int, int] | None:
    """Total byte diterima/dikirim antarmuka fisik, dari format /proc/net/dev."""
    if not os.path.exists(path):
        return None
    rx = tx = 0
    with open(path) as fh:
        for line in fh.readlines()[2:]:
            name, data = line.split(":", 1)
            if name.strip().startswith(_VIRTUAL_IF):
                continue
            f = data.split()
            rx, tx = rx + int(f[0]), tx + int(f[8])
    return rx, tx


def read_sample() -> dict | None:
    """Satu sampel. CPU dan jaringan dihitung dari selisih dengan sampel sebelumnya, jadi panggilan
    pertama hanya menyimpan titik awal dan mengembalikan None."""
    now = time.time()
    total, idle, iowait = _cpu_times()
    net = net_bytes()
    prev_cpu, prev_net, prev_t = _prev["cpu"], _prev["net"], _prev["t"]
    _prev.update(cpu=(total, idle, iowait), net=net, t=now)
    if prev_cpu is None:
        return None

    d_total, dt = total - prev_cpu[0], now - prev_t
    m = _meminfo()
    with open(f"{PROC}/loadavg") as fh:
        load = [float(x) for x in fh.read().split()[:3]]
    disk = shutil.disk_usage("/")
    sample = {
        "t": int(now * 1000),
        "cpu": round(100 * (1 - (idle - prev_cpu[1]) / d_total), 1) if d_total > 0 else 0.0,
        "iowait": round(100 * (iowait - prev_cpu[2]) / d_total, 1) if d_total > 0 else 0.0,
        "mem_used": m["MemTotal"] - m.get("MemAvailable", m.get("MemFree", 0)),
        "mem_total": m["MemTotal"],
        "swap_used": m.get("SwapTotal", 0) - m.get("SwapFree", 0),
        "swap_total": m.get("SwapTotal", 0),
        "load1": load[0], "load5": load[1], "load15": load[2],
        "disk_used": disk.used, "disk_total": disk.total,
        "net_rx": None, "net_tx": None,
    }
    if net and prev_net and dt > 0:
        # max(0, ...): penghitung bisa kembali ke nol saat antarmuka di-reset.
        sample["net_rx"] = round(max(0, net[0] - prev_net[0]) / dt)
        sample["net_tx"] = round(max(0, net[1] - prev_net[1]) / dt)
    return sample


def uptime_seconds() -> int:
    with open(f"{PROC}/uptime") as fh:
        return int(float(fh.read().split()[0]))


def live() -> dict:
    return {
        "current": _live[-1] if _live else None,
        "recent": list(_live),
        "cpus": os.cpu_count(),
        "uptime": uptime_seconds(),
        "net_available": os.path.exists(NET_DEV),
    }


def _avg(batch: list[dict], key: str):
    vals = [s[key] for s in batch if s[key] is not None]
    return sum(vals) / len(vals) if vals else None


async def _store(batch: list[dict]) -> None:
    last = batch[-1]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO vps_metrics (cpu_pct, cpu_max, iowait_pct, mem_used, mem_max, mem_total,
                   swap_used, load1, disk_used, disk_total, net_rx_bps, net_tx_bps)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
            _avg(batch, "cpu"), max(s["cpu"] for s in batch), _avg(batch, "iowait"),
            int(_avg(batch, "mem_used")), max(s["mem_used"] for s in batch), last["mem_total"],
            int(_avg(batch, "swap_used")), _avg(batch, "load1"), last["disk_used"], last["disk_total"],
            _avg(batch, "net_rx"), _avg(batch, "net_tx"))


async def history(rng: str) -> dict:
    span, bucket = RANGES[rng]
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT (floor(extract(epoch FROM recorded_at) / $2) * $2)::bigint * 1000 AS t,
                      avg(cpu_pct) AS cpu, max(cpu_max) AS cpu_max, avg(iowait_pct) AS iowait,
                      avg(mem_used)::bigint AS mem_used, max(mem_max) AS mem_max, max(mem_total) AS mem_total,
                      avg(swap_used)::bigint AS swap_used, avg(load1) AS load1,
                      max(disk_used) AS disk_used, max(disk_total) AS disk_total,
                      avg(net_rx_bps) AS net_rx, avg(net_tx_bps) AS net_tx
               FROM vps_metrics WHERE recorded_at > NOW() - make_interval(secs => $1)
               GROUP BY 1 ORDER BY 1""", float(span), bucket)
        since = await conn.fetchval("SELECT min(recorded_at) FROM vps_metrics")
    return {"since": since.isoformat() if since else None,
            "points": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in dict(r).items()} for r in rows]}


async def run_vps_sampler() -> None:
    log.info("VPS sampler started (tiap %ss, simpan tiap %s sampel)", SAMPLE_SECONDS, STORE_EVERY)
    batch: list[dict] = []
    while True:
        try:
            sample = read_sample()
            if sample:
                _live.append(sample)
                batch.append(sample)
                if len(batch) >= STORE_EVERY:
                    await _store(batch)
                    batch = []
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("sampel resource VPS gagal: %s", e)
            batch = []
        await asyncio.sleep(SAMPLE_SECONDS)
