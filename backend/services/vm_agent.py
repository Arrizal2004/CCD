"""
VM Agent management — instalasi & pembacaan metrik berbasis exporter di dalam Guest OS.

Arsitektur:
  - Linux  : prometheus-node-exporter (port 9100), metrik counter
             node_network_receive_bytes_total / node_network_transmit_bytes_total
  - Windows: windows_exporter (port 9100), metrik counter
             windows_net_bytes_received_total / windows_net_bytes_sent_total
             (juga mendukung Telegraf: win_net_bytes_received_per_sec / win_net_bytes_sent_per_sec)

Alur:
  Poller (services/vm_agent_poller.py) HTTP GET ringan ke :9100/metrics tiap interval,
  hitung delta counter → throughput murni (bytes/sec), simpan ke Redis.
"""
import os
import re
import io
import json
import logging
import asyncio
import traceback

import httpx

from database import get_pool

log = logging.getLogger("vm_agent")

AGENT_PORT     = 9100   # node-exporter (Linux)
WIN_AGENT_PORT = 9182   # windows_exporter (Windows, default)
REDIS_URL  = os.getenv("REDIS_URL", "redis://localhost:6379")


def port_for_os(os_type: str) -> int:
    return WIN_AGENT_PORT if os_type == "windows" else AGENT_PORT


# ── Parser metrik Prometheus ──────────────────────────────────────────────────────

_VIRTUAL_IFACE = ("lo", "loopback", "isatap", "teredo")

def _is_physical_dev(dev: str) -> bool:
    d = (dev or "").lower()
    if not d:
        return False
    if d in _VIRTUAL_IFACE:
        return False
    if d.startswith(("docker", "br-", "veth", "virbr", "tap", "tun")):
        return False
    if "isatap" in d or "loopback" in d:
        return False
    return True


def sum_metric(text: str, metric_name: str, label: str = "device") -> float | None:
    """
    Jumlahkan nilai metrik Prometheus untuk semua interface fisik.
    Return None jika metrik tidak ditemukan sama sekali.
    """
    total = 0.0
    found = False
    prefix = metric_name + "{"
    for line in text.splitlines():
        if not (line.startswith(prefix) or line.startswith(metric_name + " ")):
            continue
        # pastikan nama metrik persis (bukan prefix metrik lain)
        name = line[:line.index("{")] if "{" in line else line.split(" ", 1)[0]
        if name != metric_name:
            continue
        m = re.search(label + r'="([^"]*)"', line)
        dev = m.group(1) if m else ""
        if dev and not _is_physical_dev(dev):
            continue
        try:
            val = float(line.rsplit(" ", 1)[1])
        except (ValueError, IndexError):
            continue
        total += val
        found = True
    return total if found else None


def sum_all_series(text: str, metric_name: str) -> float | None:
    """Jumlahkan SEMUA series metrik (lintas label) — untuk CPU total semua mode/core."""
    total = 0.0
    found = False
    for line in text.splitlines():
        if "{" in line:
            name = line[:line.index("{")]
        else:
            name = line.split(" ", 1)[0] if " " in line else ""
        if name != metric_name:
            continue
        try:
            total += float(line.rsplit(" ", 1)[1])
            found = True
        except (ValueError, IndexError):
            continue
    return total if found else None


def sum_series_where(text: str, metric_name: str, label: str, value: str) -> float | None:
    """Jumlahkan series metrik yang label-nya bernilai tertentu (mis. mode=idle)."""
    total = 0.0
    found = False
    needle = f'{label}="{value}"'
    for line in text.splitlines():
        name = line[:line.index("{")] if "{" in line else (line.split(" ", 1)[0] if " " in line else "")
        if name != metric_name or needle not in line:
            continue
        try:
            total += float(line.rsplit(" ", 1)[1])
            found = True
        except (ValueError, IndexError):
            continue
    return total if found else None


# ── Disk IOPS (whole-disk saja, abaikan partisi & device virtual) ──────────────
_PARTITION_RE   = re.compile(r'(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z]+)\d+$|nvme\d+n\d+p\d+$|mmcblk\d+p\d+$')
_VIRT_DISK_RE   = re.compile(r'^(loop|ram|sr|fd|dm-|zram|md|dm_)')

def _is_whole_disk(dev: str) -> bool:
    d = (dev or "").lower()
    if not d:
        return False
    if _VIRT_DISK_RE.match(d):      # loop/ram/dm-/zram dst.
        return False
    if _PARTITION_RE.search(d):     # sda1, nvme0n1p1, mmcblk0p1 → partisi
        return False
    return True


def sum_disk_metric(text: str, metric_name: str) -> float | None:
    """
    Jumlahkan counter disk untuk semua whole-disk (sda, vda, nvme0n1, ...).
    Default mencakup 'sda'. Partisi & device virtual diabaikan agar tidak dobel hitung.
    """
    total = 0.0
    found = False
    for line in text.splitlines():
        name = line[:line.index("{")] if "{" in line else (line.split(" ", 1)[0] if " " in line else "")
        if name != metric_name:
            continue
        m = re.search(r'device="([^"]*)"', line)
        dev = m.group(1) if m else ""
        if dev and not _is_whole_disk(dev):
            continue
        try:
            total += float(line.rsplit(" ", 1)[1])
            found = True
        except (ValueError, IndexError):
            continue
    return total if found else None


_WIN_DRIVE_RE = re.compile(r'^[A-Za-z]:?$')  # C, C:, D: — drive huruf saja

def sum_win_disk_metric(text: str, metric_name: str) -> float | None:
    """
    Jumlahkan counter disk Windows untuk semua drive huruf nyata (C:, D:, E:).
    Abaikan volume sistem/internal: '_Total', 'HarddiskVolume*'.
    """
    total = 0.0
    found = False
    for line in text.splitlines():
        name = line[:line.index("{")] if "{" in line else (line.split(" ", 1)[0] if " " in line else "")
        if name != metric_name:
            continue
        m = re.search(r'volume="([^"]*)"', line)
        vol = (m.group(1) if m else "").strip()
        if vol in ("_Total", "") or vol.lower().startswith("harddiskvolume"):
            continue
        if not _WIN_DRIVE_RE.match(vol):   # hanya drive huruf (C:, D:)
            continue
        try:
            total += float(line.rsplit(" ", 1)[1])
            found = True
        except (ValueError, IndexError):
            continue
    return total if found else None


def single_value(text: str, metric_name: str) -> float | None:
    """Ambil nilai metrik gauge tunggal (mem total/avail)."""
    for line in text.splitlines():
        name = line[:line.index("{")] if "{" in line else (line.split(" ", 1)[0] if " " in line else "")
        if name != metric_name:
            continue
        try:
            return float(line.rsplit(" ", 1)[1])
        except (ValueError, IndexError):
            continue
    return None


async def fetch_agent_sample(ip: str, port: int = AGENT_PORT, os_type: str = "linux") -> dict:
    """
    HTTP GET ringan ke exporter, kembalikan counter/gauge mentah.
      network counter (butuh delta): rx_total, tx_total   |  rate instan (Telegraf): rx_rate, tx_rate
      cpu counter (butuh delta):     cpu_idle, cpu_total
      memory gauge (instan):         mem_total, mem_avail  (bytes)
    """
    url = f"http://{ip}:{port}/metrics"
    try:
        import time as _time
        _t0 = _time.perf_counter()
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url)
        scrape_ms = round((_time.perf_counter() - _t0) * 1000, 1)
        if resp.status_code != 200:
            return {"ok": False, "reason": f"HTTP {resp.status_code}"}
        text = resp.text
    except Exception as e:
        return {"ok": False, "reason": str(e)}

    sample = {
        "ok": True, "scrape_ms": scrape_ms,
        "rx_total": None, "tx_total": None, "rx_rate": None, "tx_rate": None,
        "cpu_idle": None, "cpu_total": None, "mem_total": None, "mem_avail": None,
        "disk_reads": None, "disk_writes": None,
    }

    if os_type == "linux":
        sample["rx_total"]    = sum_metric(text, "node_network_receive_bytes_total")
        sample["tx_total"]    = sum_metric(text, "node_network_transmit_bytes_total")
        sample["cpu_idle"]    = sum_series_where(text, "node_cpu_seconds_total", "mode", "idle")
        sample["cpu_total"]   = sum_all_series(text, "node_cpu_seconds_total")
        sample["mem_total"]   = single_value(text, "node_memory_MemTotal_bytes")
        sample["mem_avail"]   = single_value(text, "node_memory_MemAvailable_bytes")
        sample["disk_reads"]  = sum_disk_metric(text, "node_disk_reads_completed_total")
        sample["disk_writes"] = sum_disk_metric(text, "node_disk_writes_completed_total")
    else:
        # windows_exporter (counter) — label NIC 'nic'
        sample["rx_total"]  = sum_metric(text, "windows_net_bytes_received_total", label="nic")
        sample["tx_total"]  = sum_metric(text, "windows_net_bytes_sent_total", label="nic")
        sample["cpu_idle"]  = sum_series_where(text, "windows_cpu_time_total", "mode", "idle")
        sample["cpu_total"] = sum_all_series(text, "windows_cpu_time_total")
        sample["mem_total"] = single_value(text, "windows_cs_physical_memory_bytes")
        # RAM free: windows_os_physical_memory_free_bytes (fallback windows_memory_available_bytes)
        sample["mem_avail"] = single_value(text, "windows_os_physical_memory_free_bytes")
        if sample["mem_avail"] is None:
            sample["mem_avail"] = single_value(text, "windows_memory_available_bytes")
        # IOPS: jumlah drive huruf nyata (C:, D:), abaikan _Total/HarddiskVolume*
        sample["disk_reads"]  = sum_win_disk_metric(text, "windows_logical_disk_reads_total")
        sample["disk_writes"] = sum_win_disk_metric(text, "windows_logical_disk_writes_total")
        # Telegraf (rate instan) — label 'interface'
        if sample["rx_total"] is None:
            sample["rx_rate"] = sum_metric(text, "win_net_bytes_received_per_sec", label="interface")
            sample["tx_rate"] = sum_metric(text, "win_net_bytes_sent_per_sec", label="interface")

    meaningful = ("rx_total", "tx_total", "rx_rate", "tx_rate", "cpu_total", "mem_total", "disk_reads")
    if all(sample[k] is None for k in meaningful):
        return {"ok": False, "reason": "Metrik tidak ditemukan di payload exporter"}
    return sample
