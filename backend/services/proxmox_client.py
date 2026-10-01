"""
Async client for the Proxmox VE REST API (api2/json), authenticated via API token —
no session ticket/CSRF dance needed.

Replaces the Redis pub/sub command channel + C# agent used by the Hyper-V version:
Proxmox exposes VM control, config and metrics directly over HTTP, so routers can
call this client and await a normal HTTP response instead of publishing a command
and polling a Redis result key.

One `ProxmoxClient` instance = one Proxmox cluster/host (multi-Proxmox support — see
services/proxmox_instances.py, which builds these from the `proxmox_instances` DB table
rather than a single fixed PROXMOX_HOST/TOKEN_ID/TOKEN_SECRET env var).
"""
import asyncio

import httpx


def _first_ipv4(agent_result) -> str | None:
    for iface in (agent_result or {}).get("result", []):
        if iface.get("name") == "lo":
            continue
        for addr in iface.get("ip-addresses", []):
            if addr.get("ip-address-type") == "ipv4":
                return addr["ip-address"]
    return None


class ProxmoxError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Proxmox API error {status_code}: {detail}")


class ProxmoxClient:
    def __init__(self, host: str, token_id: str, token_secret: str, verify_ssl: bool = False):
        self.host = host
        self.token_id = token_id
        self.token_secret = token_secret
        self.verify_ssl = verify_ssl

    def _auth_header(self) -> dict:
        return {"Authorization": f"PVEAPIToken={self.token_id}={self.token_secret}"}

    async def _request(self, method: str, path: str, **kwargs):
        url = f"https://{self.host}/api2/json{path}"
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=15.0) as client:
            resp = await client.request(method, url, headers=self._auth_header(), **kwargs)
        if resp.status_code >= 400:
            raise ProxmoxError(resp.status_code, resp.text)
        body = resp.json()
        return body.get("data")

    async def list_nodes(self) -> list[dict]:
        return await self._request("GET", "/nodes")

    async def get_node_status(self, node: str) -> dict:
        """Snapshot live node: cpu (fraksi 0-1), memory {total,used,available,free},
        rootfs {total,used,free,avail}, swap {total,used,free}, uptime, loadavg, cpuinfo. Butuh
        Sys.Audit di /nodes/{node} — bukan default token yang cuma di-scope ke pool VM."""
        return await self._request("GET", f"/nodes/{node}/status")

    async def get_node_rrddata(self, node: str, timeframe: str = "hour") -> list[dict]:
        """Histori RRD level node (bukan per-VM) — dipakai untuk throughput network (netin/netout
        bytes/s), karena /status cuma snapshot sesaat tanpa rate."""
        return await self._request("GET", f"/nodes/{node}/rrddata", params={"timeframe": timeframe})

    async def list_vms(self, node: str) -> list[dict]:
        """Semua QEMU VM di satu node. Tiap item: {vmid, name, status, cpu, mem, maxmem, ...}."""
        return await self._request("GET", f"/nodes/{node}/qemu")

    async def get_vm_status(self, node: str, vmid: int) -> dict:
        return await self._request("GET", f"/nodes/{node}/qemu/{vmid}/status/current")

    async def get_vm_config(self, node: str, vmid: int) -> dict:
        return await self._request("GET", f"/nodes/{node}/qemu/{vmid}/config")

    async def vm_action(self, node: str, vmid: int, action: str) -> str:
        """action: start | stop | shutdown | reboot | suspend | resume. Returns Proxmox task UPID."""
        valid = {"start", "stop", "shutdown", "reboot", "suspend", "resume"}
        if action not in valid:
            raise ValueError(f"action harus salah satu dari {valid}, dapat: {action}")
        return await self._request("POST", f"/nodes/{node}/qemu/{vmid}/status/{action}")

    async def create_snapshot(self, node: str, vmid: int, snapname: str, description: str = "") -> str:
        return await self._request(
            "POST", f"/nodes/{node}/qemu/{vmid}/snapshot",
            data={"snapname": snapname, "description": description},
        )

    async def list_snapshots(self, node: str, vmid: int) -> list[dict]:
        return await self._request("GET", f"/nodes/{node}/qemu/{vmid}/snapshot")

    async def delete_snapshot(self, node: str, vmid: int, snapname: str) -> str:
        return await self._request("DELETE", f"/nodes/{node}/qemu/{vmid}/snapshot/{snapname}")

    async def rollback_snapshot(self, node: str, vmid: int, snapname: str) -> str:
        return await self._request("POST", f"/nodes/{node}/qemu/{vmid}/snapshot/{snapname}/rollback")

    async def clone_vm(self, node: str, vmid: int, newid: int, name: str, full: bool = True,
                       target_storage: str | None = None, pool: str | None = None) -> str:
        """Clone dari VM/template `vmid` menjadi VM baru `newid`. full=True → full clone (bukan linked)."""
        data = {"newid": newid, "name": name, "full": 1 if full else 0}
        if target_storage:
            data["storage"] = target_storage
        if pool:
            data["pool"] = pool
        return await self._request("POST", f"/nodes/{node}/qemu/{vmid}/clone", data=data)

    async def next_vmid(self) -> int:
        return int(await self._request("GET", "/cluster/nextid"))

    async def cluster_vm_resources(self) -> list[dict]:
        return await self._request("GET", "/cluster/resources", params={"type": "vm"})

    async def update_vm_config(self, node: str, vmid: int, config: dict) -> None:
        await self._request("PUT", f"/nodes/{node}/qemu/{vmid}/config", data=config)

    async def resize_disk(self, node: str, vmid: int, disk: str, size: str) -> None:
        result = await self._request("PUT", f"/nodes/{node}/qemu/{vmid}/resize", data={"disk": disk, "size": size})
        if isinstance(result, str) and result.startswith("UPID:"):
            await self.wait_task(node, result)

    async def destroy_vm(self, node: str, vmid: int) -> str:
        return await self._request("DELETE", f"/nodes/{node}/qemu/{vmid}",
                                   params={"purge": 1, "destroy-unreferenced-disks": 1})

    async def storage_content(self, node: str, storage: str, vmid: int) -> list[dict]:
        return await self._request("GET", f"/nodes/{node}/storage/{storage}/content", params={"vmid": vmid})

    async def wait_task(self, node: str, upid: str, timeout: int = 600) -> None:
        """Block until a Proxmox task finishes; ProxmoxError unless it ended OK (or with warnings)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            st = await self.get_task_status(node, upid)
            if st.get("status") == "stopped":
                exit_status = st.get("exitstatus") or ""
                if exit_status != "OK" and not exit_status.startswith("WARNINGS"):
                    raise ProxmoxError(500, f"Task {st.get('type')} gagal: {exit_status}")
                return
            if loop.time() > deadline:
                raise ProxmoxError(504, f"Task {st.get('type')} belum selesai setelah {timeout} detik")
            await asyncio.sleep(2)

    async def get_rrddata(self, node: str, vmid: int, timeframe: str = "hour") -> list[dict]:
        """Historical metrics langsung dari Proxmox RRD. timeframe: hour|day|week|month|year."""
        return await self._request("GET", f"/nodes/{node}/qemu/{vmid}/rrddata", params={"timeframe": timeframe})

    async def get_task_status(self, node: str, upid: str) -> dict:
        return await self._request("GET", f"/nodes/{node}/tasks/{upid}/status")

    async def get_guest_ip(self, node: str, vmid: int) -> str | None:
        """IP guest via QEMU Guest Agent. None jika agent belum terpasang/aktif atau tak ada IPv4 non-loopback."""
        try:
            result = await self._request("GET", f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
        except ProxmoxError:
            return None
        return _first_ipv4(result)

    async def guest_ip_status(self, node: str, vmid: int) -> dict:
        """{ip, agent_enabled, reason}: reason tells the UI what to fix when ip is None —
        agent_disabled (VM option off) | agent_not_running (not installed/started in the guest, or
        the option was just enabled and the VM hasn't been power-cycled) | no_ipv4."""
        config = await self.get_vm_config(node, vmid)
        enabled = any(p.strip() in ("1", "enabled=1") for p in str(config.get("agent", "0")).split(","))
        if not enabled:
            return {"ip": None, "agent_enabled": False, "reason": "agent_disabled"}
        try:
            result = await self._request("GET", f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
        except ProxmoxError:
            return {"ip": None, "agent_enabled": True, "reason": "agent_not_running"}
        ip = _first_ipv4(result)
        return {"ip": ip, "agent_enabled": True, "reason": None if ip else "no_ipv4"}

    async def enable_guest_agent(self, node: str, vmid: int) -> None:
        """Turns the VM's QEMU Guest Agent option on. Takes effect on the next full stop/start."""
        await self._request("PUT", f"/nodes/{node}/qemu/{vmid}/config", data={"agent": "1"})
