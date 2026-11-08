"""
Proxmox tiruan di memori untuk tes: VM dengan status, config, dan snapshot. Mencatat aksi di .calls.
Dipasang lewat fixture `pve` (lihat conftest) yang mengganti proxmox_instances.get_client.
"""
from services.proxmox_client import ProxmoxError


class FakePve:
    def __init__(self):
        self.vms: dict[int, dict] = {}
        self.calls: list[tuple] = []
        self.fail_on: set[str] = set()
        self._next = 9000

    # ── data ──
    def add(self, vmid: int, name: str = "vm", status: str = "stopped", **config):
        self.vms[vmid] = {"name": name, "status": status, "config": {"name": name, **config}, "snaps": []}
        return self.vms[vmid]

    def _vm(self, vmid: int) -> dict:
        if vmid not in self.vms:
            raise ProxmoxError(500, f"Configuration file 'nodes/pve/qemu-server/{vmid}.conf' does not exist")
        return self.vms[vmid]

    def _call(self, op: str, *args):
        self.calls.append((op, *args))
        if op in self.fail_on:
            raise ProxmoxError(500, f"{op} gagal (tiruan)")

    # ── baca ──
    async def list_nodes(self):
        return [{"node": "pve", "status": "online"}]

    async def list_vms(self, node):
        return [{"vmid": v, "name": d["name"], "status": d["status"], "template": 0, "maxmem": 2 << 30, "maxcpu": 2}
                for v, d in self.vms.items()]

    async def get_vm_status(self, node, vmid):
        return {"status": self._vm(vmid)["status"]}

    async def get_vm_config(self, node, vmid):
        return dict(self._vm(vmid)["config"])

    async def get_guest_ip(self, node, vmid):
        return None

    # ── ubah ──
    async def update_vm_config(self, node, vmid, config):
        self._call("config", vmid, dict(config))
        self._vm(vmid)["config"].update({k: str(v) for k, v in config.items()})

    async def vm_action(self, node, vmid, action):
        self._call("action", vmid, action)
        vm = self._vm(vmid)
        vm["status"] = {"start": "running", "resume": "running", "stop": "stopped", "shutdown": "stopped"}.get(action, vm["status"])
        return f"UPID:{vmid}:{action}"

    async def shutdown_vm(self, node, vmid, timeout=120):
        self._call("shutdown", vmid)
        self._vm(vmid)["status"] = "stopped"
        return f"UPID:{vmid}:shutdown"

    async def create_snapshot(self, node, vmid, snapname, description=""):
        self._call("snapshot", vmid, snapname)
        self._vm(vmid)["snaps"].append(snapname)
        return f"UPID:{vmid}:snap"

    async def list_snapshots(self, node, vmid):
        return [{"name": n} for n in self._vm(vmid)["snaps"]] + [{"name": "current"}]

    async def delete_snapshot(self, node, vmid, snapname):
        self._call("delsnap", vmid, snapname)
        self._vm(vmid)["snaps"].remove(snapname)
        return f"UPID:{vmid}:delsnap"

    async def rollback_snapshot(self, node, vmid, snapname):
        self._call("rollback", vmid, snapname)
        vm = self._vm(vmid)
        if snapname not in vm["snaps"]:
            raise ProxmoxError(500, f"snapshot '{snapname}' does not exist")
        vm["status"] = "stopped"
        return f"UPID:{vmid}:rollback"

    async def wait_task(self, node, upid, timeout=600):
        return None


class FakeImagePve(FakePve):
    """FakePve + permukaan yang dipakai pembuatan template dari cloud image."""

    def __init__(self):
        super().__init__()
        self.version_str = "9.1.4"
        self.import_files: set[str] = set()
        self.downloads: list[dict] = []
        self.task_fail: dict[str, str] = {}       # nama operasi -> exitstatus gagal
        self.privs: dict[str, dict] = {}          # path -> {hak: 1}; kosong = semua hak ada
        self.tasks: dict[str, str] = {}

    async def version(self):
        return {"version": self.version_str}

    async def list_storages(self, node, content=None, writable=True):
        return [{"storage": "local", "type": "dir", "avail": 80 << 30}, {"storage": "local-lvm", "type": "lvmthin", "avail": 200 << 30}] \
            if content in ("import", "images") else []

    async def list_pools(self):
        return [{"poolid": "lab"}]

    async def node_networks(self, node):
        return [{"iface": "vmbr0", "type": "bridge"}, {"iface": "eno1", "type": "eth"}]

    async def permissions(self, path):
        if path in self.privs:
            return self.privs[path]
        return {p: 1 for p in ("Sys.AccessNetwork", "Datastore.AllocateTemplate", "Datastore.AllocateSpace", "VM.Allocate",
                               "VM.Config.Disk", "VM.Config.Cloudinit", "VM.Clone", "Sys.Audit", "Sys.Modify")}

    async def storage_files(self, node, storage, content):
        return [{"volid": v} for v in self.import_files]

    async def delete_volume(self, node, storage, volume):
        self.import_files.discard(volume)

    async def download_url(self, node, storage, url, filename, content="import", checksum=None, checksum_algorithm=None):
        self._call("download", url, checksum)
        self.downloads.append({"url": url, "checksum": checksum, "algo": checksum_algorithm, "filename": filename})
        upid = f"UPID:dl:{len(self.downloads)}"
        self.tasks[upid] = "download"
        if "download" not in self.task_fail:
            self.import_files.add(f"{storage}:import/{filename}")
        return upid

    async def next_vmid(self):
        self._next += 1
        return self._next

    async def create_vm(self, node, config):
        self._call("create_vm", config["vmid"])
        self.add(config["vmid"], config["name"], "stopped", **{k: str(v) for k, v in config.items() if k not in ("vmid", "name")})
        self.vms[config["vmid"]]["config"]["scsi0"] = f"{config['scsi0'].split(':')[0]}:vm-{config['vmid']}-disk-0,size=3584M"
        upid = f"UPID:create:{config['vmid']}"
        self.tasks[upid] = "import"
        return upid

    async def resize_disk(self, node, vmid, disk, size):
        self._call("resize", vmid, size)
        self.vms[vmid]["config"][disk] = f"local-lvm:vm-{vmid}-disk-0,size={size}"

    async def convert_to_template(self, node, vmid):
        self._call("template", vmid)
        self.vms[vmid]["config"]["template"] = "1"
        return None

    async def destroy_vm(self, node, vmid):
        self._call("destroy", vmid)
        self.vms.pop(vmid, None)
        return f"UPID:destroy:{vmid}"

    async def get_task_status(self, node, upid):
        op = self.tasks.get(upid, "other")
        return {"status": "stopped", "exitstatus": self.task_fail.get(op, "OK")}

    async def task_log(self, node, upid, limit=50):
        return ["log baris terakhir"]
