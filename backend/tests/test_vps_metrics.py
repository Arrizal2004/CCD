"""
Resource VPS dashboard: pembacaan /proc, penghitung jaringan, dan endpoint admin.
"""
import asyncpg

from services import vps_metrics
from tests.conftest import DATABASE_URL, _run, auth

NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  900000    100    0    0    0     0          0         0   900000    100    0    0    0     0       0          0
  eth0: 5000000   4000    0    0    0     0          0         0  2000000   3000    0    0    0     0       0          0
veth07fa8a1: 777777    10    0    0    0     0          0         0   888888     10    0    0    0     0       0          0
docker0: 111111    10    0    0    0     0          0         0   222222     10    0    0    0     0       0          0
tailscale0: 333333    10    0    0    0     0          0         0   444444     10    0    0    0     0       0          0
"""


def test_net_bytes_counts_only_physical_interfaces(tmp_path):
    f = tmp_path / "net_dev"
    f.write_text(NET_DEV)
    assert vps_metrics.net_bytes(str(f)) == (5000000, 2000000)
    assert vps_metrics.net_bytes(str(tmp_path / "tidak-ada")) is None


def test_read_sample_needs_two_readings():
    vps_metrics._prev.update(cpu=None, net=None, t=None)
    assert vps_metrics.read_sample() is None
    s = vps_metrics.read_sample()
    assert 0 <= s["cpu"] <= 100 and 0 <= s["iowait"] <= 100
    assert 0 < s["mem_used"] <= s["mem_total"]
    assert 0 < s["disk_used"] <= s["disk_total"]
    assert s["load1"] >= 0


async def _insert_rows():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for minutes, cpu in ((3, 20.0), (2, 40.0)):
            await conn.execute(
                """INSERT INTO vps_metrics (recorded_at, cpu_pct, cpu_max, iowait_pct, mem_used, mem_max, mem_total,
                       swap_used, load1, disk_used, disk_total, net_rx_bps, net_tx_bps)
                   VALUES (NOW() - make_interval(mins => $1), $2::real, $2::real + 30, 1, 1000, 1500, 2000, 0, 0.5, 10, 100, 300, 200)""",
                minutes, cpu)
    finally:
        await conn.close()


def test_history_and_live_endpoints(client, sysadmin_token):
    _run(_insert_rows())
    r = client.get("/api/admin/vps/history", params={"range": "1h"}, headers=auth(sysadmin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["since"] and len(body["points"]) >= 2
    assert {"t", "cpu", "cpu_max", "mem_used", "mem_total", "net_rx", "net_tx", "load1"} <= set(body["points"][0])

    live = client.get("/api/admin/vps/live", headers=auth(sysadmin_token)).json()
    assert live["cpus"] >= 1 and live["uptime"] > 0 and isinstance(live["recent"], list)


def test_vps_endpoints_admin_only_and_range_validated(client, student_token, sysadmin_token):
    assert client.get("/api/admin/vps/live", headers=auth(student_token)).status_code == 403
    assert client.get("/api/admin/vps/history", headers=auth(student_token)).status_code == 403
    assert client.get("/api/admin/vps/history", params={"range": "1y"}, headers=auth(sysadmin_token)).status_code == 422
