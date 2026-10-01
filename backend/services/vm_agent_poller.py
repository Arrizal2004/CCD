"""
VM Agent Poller — loop background yang mengutamakan jalur exporter :9100.

Untuk tiap VM yang Running, punya IP, dan agent_installed=true:
  - HTTP GET ringan http://{ip}:9100/metrics  (Node Exporter / windows_exporter)
  - Hitung delta network (bytes/sec) & CPU% (idle vs total), memory% (instan)
  - Simpan ke Redis  agent:tput:{host}:{vm_id}  (TTL 30s, untuk fallback HTTP)
  - PUBLISH ke channel  metrics:agent:live      (untuk streaming WebSocket real-time)

State sample sebelumnya disimpan di memori (_prev) untuk kalkulasi delta.
Loop bersifat aditif & defensif: error per-VM tidak menghentikan loop.
"""
import os
import json
import time
import logging
import asyncio
from collections import deque
from datetime import datetime, timezone

import redis.asyncio as aioredis

from database import get_pool
from services.vm_agent import fetch_agent_sample, port_for_os

log = logging.getLogger("vm_agent_poller")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
# Interval scrape exporter — default 5 detik (Linux :9100 / Windows :9182).
POLL_INTERVAL = int(os.getenv("AGENT_POLL_INTERVAL", "5"))
MAX_CONCURRENCY = 8

# Channel Redis pub/sub untuk streaming metrik agent ke WebSocket frontend
LIVE_CHANNEL = "metrics:agent:live"

# Cache delta per VM: deque(maxlen=12) snapshot terakhir (cegah memory leak).
#   snapshot = {ts, rx_total, tx_total, cpu_idle, cpu_total, disk_reads, disk_writes}
_samples: dict[tuple[str, str], deque] = {}
SAMPLE_HISTORY = 12

# Jendela bergerak IOPS 5 detik terakhir per VM: {(host,vm_id): [(ts, iops_total), ...]}
_iops_window: dict[tuple[str, str], list] = {}
IOPS_WINDOW_SEC = 5.0

# Throttle penyimpanan IOPS ke DB (poller jalan ~2s, simpan tiap 15s agar storage terkendali)
_last_db_write: dict[tuple[str, str], float] = {}

# ── Exponential backoff per-VM saat scrape gagal ──────────────────────────────
# Mencegah flooding guest yang exporter-nya down/timeout: skip VM tsb untuk
# periode mendingin (5→10→20→30 detik, cap 30s), reset begitu berhasil.
_fail_streak: dict[tuple[str, str], int] = {}
_cooldown_until: dict[tuple[str, str], float] = {}
BACKOFF_BASE_SEC = 5.0
BACKOFF_MAX_SEC = 30.0
IOPS_DB_INTERVAL = 15.0


def compute_metrics(prev: dict | None, sample: dict, now: float, os_type: str) -> dict | None:
    """
    Kalkulasi metrik final dari raw sample exporter + sample sebelumnya.
    Pure function (tanpa I/O) agar mudah diuji.

    Network : (current_bytes - previous_bytes) / interval_detik  → bytes/sec
    CPU     : 100 * (1 - Δidle/Δtotal)
    Memory  : 100 * (1 - MemAvailable/MemTotal)  (instan, tanpa delta)

    Return payload dict, atau None jika belum ada metrik bermakna (mis. butuh 2 sample).
    """
    rx_t, tx_t = sample.get("rx_total"), sample.get("tx_total")
    cpu_idle, cpu_total = sample.get("cpu_idle"), sample.get("cpu_total")

    rx_bps = tx_bps = cpu_pct = None

    # ── Network throughput (delta speed) ──
    if sample.get("rx_rate") is not None or sample.get("tx_rate") is not None:
        # Telegraf: sudah rate instan
        rx_bps = max(0.0, sample.get("rx_rate") or 0.0)
        tx_bps = max(0.0, sample.get("tx_rate") or 0.0)
    elif prev and rx_t is not None and tx_t is not None \
            and prev.get("rx_total") is not None and prev.get("tx_total") is not None:
        dt = now - prev["ts"]
        if 0.5 <= dt <= 120 and rx_t >= prev["rx_total"] and tx_t >= prev["tx_total"]:
            rx_bps = (rx_t - prev["rx_total"]) / dt
            tx_bps = (tx_t - prev["tx_total"]) / dt

    # ── CPU usage% (delta idle vs total) ──
    if prev and cpu_idle is not None and cpu_total is not None \
            and prev.get("cpu_idle") is not None and prev.get("cpu_total") is not None:
        d_idle  = cpu_idle - prev["cpu_idle"]
        d_total = cpu_total - prev["cpu_total"]
        if d_total > 0 and d_idle >= 0:
            cpu_pct = round(max(0.0, min(100.0, 100.0 * (1.0 - d_idle / d_total))), 1)

    # ── Memory usage (instan) — konversi byte → GB biner (1024^3), bulat 2 desimal ──
    mem_used_mb = mem_total_mb = mem_pct = None
    mem_used_gb = mem_total_gb = None
    total_b = sample.get("mem_total")
    avail_b = sample.get("mem_avail")
    # Hanya hitung jika KEDUA nilai valid; cegah used=total saat avail gagal di-parse
    if total_b and avail_b is not None and 0.0 <= avail_b <= total_b:
        used_b = total_b - avail_b
        _GB = 1024 ** 3
        mem_total_mb = round(total_b / 1048576, 1)
        mem_used_mb  = round(used_b / 1048576, 1)
        mem_total_gb = round(total_b / _GB, 2)
        mem_used_gb  = round(used_b / _GB, 2)
        mem_pct      = round(used_b / total_b * 100.0, 1) if total_b > 0 else None

    # ── Disk IOPS (delta reads/writes completed per detik) ──
    #   iops_avg = ( (reads+writes)_akhir - (reads+writes)_awal ) / interval_detik
    read_iops = write_iops = iops_total = None
    d_reads, d_writes = sample.get("disk_reads"), sample.get("disk_writes")
    if prev and d_reads is not None and d_writes is not None \
            and prev.get("disk_reads") is not None and prev.get("disk_writes") is not None:
        dt = now - prev["ts"]
        if 0.5 <= dt <= 120 and d_reads >= prev["disk_reads"] and d_writes >= prev["disk_writes"]:
            read_iops  = round((d_reads  - prev["disk_reads"])  / dt, 1)
            write_iops = round((d_writes - prev["disk_writes"]) / dt, 1)
            iops_total = round(read_iops + write_iops, 1)

    # Butuh minimal satu metrik bermakna
    if rx_bps is None and cpu_pct is None and mem_used_mb is None and iops_total is None:
        return None

    return {
        "ok": True,
        "source": "agent-linux" if os_type == "linux" else "agent-windows",
        "rx_bps": round(rx_bps, 1) if rx_bps is not None else None,
        "tx_bps": round(tx_bps, 1) if tx_bps is not None else None,
        "total_bps": round((rx_bps or 0) + (tx_bps or 0), 1) if rx_bps is not None else None,
        "cpu_pct": cpu_pct,
        "mem_used_mb": mem_used_mb,
        "mem_total_mb": mem_total_mb,
        "mem_used_gb": mem_used_gb,
        "mem_total_gb": mem_total_gb,
        "mem_pct": mem_pct,
        "read_iops": read_iops,
        "write_iops": write_iops,
        "iops_total": iops_total,
        "ts": now,
    }


def _first_ipv4(vm: dict) -> str | None:
    for nic in vm.get("network_adapters", []) or []:
        ips = nic.get("ip_addresses") or []
        if isinstance(ips, str):
            ips = [x.strip() for x in ips.split(",") if x.strip()]
        for ip in ips:
            if ip and ":" not in ip and not ip.startswith("169.254"):
                return ip
    return None


async def _persist_iops(host_name: str, vm_id: str, read_iops, write_iops):
    """Simpan satu sample IOPS ke vm_iops_history (best-effort, tidak menggagalkan loop)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO vm_iops_history (vm_id, host_name, read_iops, write_iops) "
                "VALUES ($1, $2, $3, $4)",
                vm_id, host_name, read_iops or 0, write_iops or 0,
            )
    except Exception as e:
        log.debug("persist IOPS %s/%s gagal: %s", host_name, vm_id, e)


async def _update_mem_used(host_name: str, vm_id: str, mem_used_mb: float):
    """Update kolom mem_used_mb pada baris history terbaru (best-effort)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE vm_metrics_history SET mem_used_mb = $1
                WHERE id = (
                    SELECT id FROM vm_metrics_history
                    WHERE vm_id = $2 AND host_name = $3
                      AND recorded_at > NOW() - INTERVAL '30 seconds'
                    ORDER BY recorded_at DESC
                    LIMIT 1
                )
            """, mem_used_mb, vm_id, host_name)
    except Exception as e:
        log.debug("update mem_used %s/%s gagal: %s", host_name, vm_id, e)


async def _agent_vms() -> dict[tuple[str, str], str]:
    """Map (host_name, vm_id) → os_type untuk VM yang agent-nya sudah terpasang."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT vm_id, host_name, os_type FROM vm_credentials WHERE agent_installed = true"
        )
    return {(r["host_name"], r["vm_id"]): r["os_type"] for r in rows}


async def _poll_one(r, host_name: str, vm_id: str, os_type: str, ip: str):
    # Port dinamis per OS: Linux :9100, Windows :9182
    sample = await fetch_agent_sample(ip, port_for_os(os_type), os_type)
    now = time.time()
    if not sample.get("ok"):
        return False   # scrape gagal → picu backoff di _tick

    key = (host_name, vm_id)
    dq = _samples.setdefault(key, deque(maxlen=SAMPLE_HISTORY))
    prev = dq[-1] if dq else None
    # Simpan snapshot ke deque (maxlen=12 → bounded, cegah memory leak)
    dq.append({
        "ts": now,
        "rx_total": sample.get("rx_total"), "tx_total": sample.get("tx_total"),
        "cpu_idle": sample.get("cpu_idle"), "cpu_total": sample.get("cpu_total"),
        "disk_reads": sample.get("disk_reads"), "disk_writes": sample.get("disk_writes"),
    })

    payload = compute_metrics(prev, sample, now, os_type)
    if payload is None:
        return True  # scrape sukses, hanya butuh sample kedua untuk hitung delta

    # IOPS: nilai instan (current) + rata-rata bergerak 5 detik terakhir
    if payload.get("iops_total") is not None:
        win = _iops_window.setdefault(key, [])
        win.append((now, payload["iops_total"]))
        cutoff = now - IOPS_WINDOW_SEC
        while win and win[0][0] < cutoff:
            win.pop(0)
        vals = [v for _, v in win]
        payload["current_iops"]  = payload["iops_total"]
        payload["iops_avg_5s"]   = round(sum(vals) / len(vals), 1) if vals else None
    else:
        payload["current_iops"]  = None
        payload["iops_avg_5s"]   = None

    payload["ip"] = ip
    payload["scrape_ms"] = sample.get("scrape_ms")
    data = json.dumps(payload)

    # 1) Cache untuk fallback HTTP (endpoint agent-throughput / augment dashboard)
    await r.set(f"agent:tput:{host_name}:{vm_id}", data, ex=30)
    # 2) Stream real-time ke WebSocket frontend via pub/sub
    await r.publish(LIVE_CHANNEL, json.dumps({
        "host_name": host_name, "vm_id": vm_id, **payload,
    }))
    # 3) Simpan IOPS ke DB (throttle 15s) untuk history 1m–7d
    if payload.get("read_iops") is not None:
        last = _last_db_write.get(key, 0.0)
        if now - last >= IOPS_DB_INTERVAL:
            _last_db_write[key] = now
            await _persist_iops(host_name, vm_id, payload["read_iops"], payload["write_iops"])
    # 4) Tulis mem_used_mb ke baris history terbaru (agar grafik memory history terisi)
    mem_used_mb = payload.get("mem_used_mb")
    if mem_used_mb is not None:
        await _update_mem_used(host_name, vm_id, mem_used_mb)
    # 5) Rolling buffer exporter per-VM — 240 titik (~20 menit) — persists across modal close/open
    buf_key = f"exporter:buf:{host_name}:{vm_id}"
    buf_point = json.dumps({
        "t":           datetime.fromtimestamp(now, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        "cpu_pct":     payload.get("cpu_pct"),
        "mem_used_mb": mem_used_mb,
        "mem_total_mb":payload.get("mem_total_mb"),
        "net_tx_bps":  round(payload.get("tx_bps") or 0),
        "net_rx_bps":  round(payload.get("rx_bps") or 0),
        "read_iops":   payload.get("read_iops"),
        "write_iops":  payload.get("write_iops"),
        "iops_total":  payload.get("iops_total"),
    })
    await r.zadd(buf_key, {buf_point: now})
    await r.zremrangebyrank(buf_key, 0, -241)   # keep last 240 points (~20min at 5s)
    await r.expire(buf_key, 3600)

    return True


async def _tick(r):
    agents = await _agent_vms()
    if not agents:
        return

    # Kumpulkan target dari cache metrik (hanya VM Running + punya IP)
    targets = []
    hosts = {h for (h, _) in agents}
    for host_name in hosts:
        raw = await r.get(f"metrics:vms:{host_name}")
        if not raw:
            continue
        try:
            vms = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for vm in vms:
            vm_id = vm.get("vm_id")
            key = (host_name, vm_id)
            if key not in agents:
                continue
            if vm.get("state") != "Running":
                continue
            ip = _first_ipv4(vm)
            if not ip:
                continue
            # Skip VM yang sedang dalam masa cooldown (backoff) agar tidak flooding.
            if _cooldown_until.get(key, 0.0) > time.monotonic():
                continue
            targets.append((host_name, vm_id, agents[key], ip))

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    def _trip_backoff(key, reason):
        streak = _fail_streak.get(key, 0) + 1
        _fail_streak[key] = streak
        delay = min(BACKOFF_MAX_SEC, BACKOFF_BASE_SEC * (2 ** (streak - 1)))
        _cooldown_until[key] = time.monotonic() + delay
        log.debug("poll %s/%s gagal (%s) → cooldown %.0fs (streak %d)",
                  key[0], key[1], reason, delay, streak)

    async def _guarded(args):
        host_name, vm_id = args[0], args[1]
        key = (host_name, vm_id)
        async with sem:
            try:
                ok = await _poll_one(r, *args)
            except Exception as e:
                _trip_backoff(key, repr(e))
                return
            if ok:
                # Pulih → reset backoff.
                _fail_streak.pop(key, None)
                _cooldown_until.pop(key, None)
            else:
                _trip_backoff(key, "exporter unreachable")

    await asyncio.gather(*(_guarded(t) for t in targets))


async def run_agent_poller():
    """Loop utama — dijalankan sebagai background task di lifespan."""
    from crypto import secure_from_url
    r = secure_from_url(aioredis.from_url(REDIS_URL, decode_responses=True))
    log.info("VM agent poller started (interval=%ss, Linux :9100 / Windows :9182)", POLL_INTERVAL)
    try:
        while True:
            try:
                await _tick(r)
            except Exception as e:
                log.warning("agent poller tick error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await r.aclose()
