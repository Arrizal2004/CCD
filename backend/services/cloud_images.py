"""
Template VM dari cloud image resmi. Katalog sengaja kecil dan tetap: Ubuntu Server 24.04 LTS, openSUSE Leap 16.0, dan openSUSE Leap 16.1. Job mengunduh image ke Proxmox (dengan checksum), membuat VM dari disk itu, menambah CloudInit drive,
menjadikannya template, dan hasilnya langsung muncul di form Create VM dan VM massal, tanpa langkah manual di Proxmox.

Job disimpan di image_jobs supaya progresnya tetap terlihat setelah halaman ditutup. Backend yang berhenti di tengah jalan
menandai job 'interrupted'; mengulanginya memakai ulang berkas yang sudah diunduh dan VM yang sudah dibuat.

Catatan guest agent: image Ubuntu tidak membawa qemu-guest-agent, jadi IP DHCP tidak terdeteksi dari template itu; pakai IP
statis (cloud-init) atau switch CCD. Edisi openSUSE Minimal-VM Cloud membawanya. Tidak ada langkah "bake" atau snippets.

Batasan Proxmox: tipe konten `import` dan `import-from` baru ada di Proxmox VE 8.2.
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx

from database import get_pool
from i18n import tr
from services import proxmox_instances
from services import proxmox_provision as provision
from services.proxmox_client import ProxmoxError

log = logging.getLogger("cloud_images")

CATALOG = [
    {"key": "ubuntu-2404", "name": "Ubuntu Server 24.04 LTS", "family": "debian", "version": "24.04",
     "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
     "checksum_url": "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS",
     "checksum_name": "noble-server-cloudimg-amd64.img", "checksum_algo": "sha256",
     "default_user": "ubuntu", "min_disk_gb": 10, "agent_included": False},
    {"key": "opensuse-leap-160", "name": "openSUSE Leap 16.0", "family": "suse", "version": "16.0",
     "url": "https://download.opensuse.org/distribution/leap/16.0/appliances/Leap-16.0-Minimal-VM.x86_64-Cloud.qcow2",
     "checksum_url": "https://download.opensuse.org/distribution/leap/16.0/appliances/Leap-16.0-Minimal-VM.x86_64-Cloud.qcow2.sha256",
     "checksum_name": "Leap-16.0-Minimal-VM.x86_64-Cloud.qcow2", "checksum_algo": "sha256",
     "default_user": "", "min_disk_gb": 10, "agent_included": True},
    {"key": "opensuse-leap-161", "name": "openSUSE Leap 16.1", "family": "suse", "version": "16.1",
     "url": "https://download.opensuse.org/distribution/leap/16.1/appliances/Leap-16.1-Minimal-VM.x86_64-Cloud.qcow2",
     "checksum_url": "https://download.opensuse.org/distribution/leap/16.1/appliances/Leap-16.1-Minimal-VM.x86_64-Cloud.qcow2.sha256",
     "checksum_name": "Leap-16.1-Minimal-VM.x86_64-Cloud.qcow2", "checksum_algo": "sha256",
     "default_user": "", "min_disk_gb": 10, "agent_included": True},
]

NAME_RE = provision.NAME_RE
BRIDGE_RE = provision.BRIDGE_RE
STORAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
POOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
ALGO_LEN = {"sha256": 64, "sha512": 128}
MIN_PVE = (8, 2)
TAG_IMAGE = "ccd-image"
TERMINAL = ("done", "failed", "interrupted")
MAX_CHECKSUM_BYTES = 2 * 1024 * 1024
POLL_S = 3.0                   # jeda cek task Proxmox; tes memperkecilnya
_JOB_COLS = ("id, instance, node, image_key, image_name, options, status, step, upid, volid, vmid, result, error, "
             "created_by_id, created_by, created_at, updated_at, finished_at")

_locks: dict[tuple, asyncio.Lock] = {}
_tasks: set = set()


class ImageError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def _j(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def get_image(key: str) -> dict:
    for img in CATALOG:
        if img["key"] == key:
            return img
    raise ImageError(tr("Image tidak ada di katalog", "The image is not in the catalog"), 404)


def catalog() -> list[dict]:
    return [{k: v for k, v in img.items() if k not in ("checksum_url", "checksum_name")} for img in CATALOG]


# ── checksum ─────────────────────────────────────────────────────────────────

def parse_checksums(text: str, name: str, algo: str) -> str | None:
    """Ambil hash `name` dari isi berkas checksum. Mengenali format GNU ('<hash> *<nama>') dan BSD
    ('SHA256 (<nama>) = <hash>'); jalur di depan nama diabaikan."""
    n = ALGO_LEN[algo]
    base = name.rsplit("/", 1)[-1]
    gnu = re.compile(rf"^([0-9a-fA-F]{{{n}}})\s+\*?(.+?)\s*$")
    bsd = re.compile(rf"^SHA{algo[3:]}\s*\((.+)\)\s*=\s*([0-9a-fA-F]{{{n}}})\s*$", re.IGNORECASE)
    for line in text.splitlines():
        m = gnu.match(line)
        if m and m.group(2).rsplit("/", 1)[-1] == base:
            return m.group(1).lower()
        m = bsd.match(line)
        if m and m.group(1).rsplit("/", 1)[-1] == base:
            return m.group(2).lower()
    return None


def _https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ImageError(tr("URL image harus memakai https:// tanpa nama pengguna", "The image URL must use https:// without a username"))


async def _fetch_text(url: str) -> str:
    """Unduh berkas teks kecil. Setiap pengalihan harus tetap https."""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as http:
        for _ in range(4):
            _https(url)
            resp = await http.get(url)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                continue
            if resp.status_code != 200:
                raise ImageError(tr(f"Berkas checksum tidak bisa diambil (HTTP {resp.status_code})",
                                    f"The checksum file could not be fetched (HTTP {resp.status_code})"), 502)
            if len(resp.content) > MAX_CHECKSUM_BYTES:
                raise ImageError(tr("Berkas checksum terlalu besar", "The checksum file is too large"), 502)
            return resp.text
    raise ImageError(tr("Terlalu banyak pengalihan saat mengambil checksum", "Too many redirects fetching the checksum"), 502)


async def resolve_checksum(image: dict) -> tuple[str, str]:
    algo = image["checksum_algo"]
    name = image.get("checksum_name") or urlparse(image["url"]).path.rsplit("/", 1)[-1]
    found = parse_checksums(await _fetch_text(image["checksum_url"]), name, algo)
    if not found:
        raise ImageError(tr(f"'{name}' tidak ditemukan di berkas checksum", f"'{name}' was not found in the checksum file"), 502)
    return found, algo


def volume_filename(image: dict) -> str:
    ext = ".raw" if image["url"].lower().endswith(".raw") else ".qcow2"      # .img (Ubuntu) dan .qcow2 sama-sama qcow2
    return f"ccd-{image['key']}{ext}"


# ── prasyarat node ───────────────────────────────────────────────────────────

def _pve_version(raw: dict) -> tuple[int, int]:
    m = re.match(r"(\d+)\.(\d+)", str((raw or {}).get("version", "")))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


async def can_access_network(client, node: str) -> bool:
    """download-url meminta Sys.AccessNetwork di node, atau (cara lama) Sys.Audit dan Sys.Modify di '/'."""
    try:
        if "Sys.AccessNetwork" in await client.permissions(f"/nodes/{node}"):
            return True
        root = await client.permissions("/")
        return "Sys.Audit" in root and "Sys.Modify" in root
    except ProxmoxError:
        return False


async def node_options(client, node: str) -> dict:
    """Apa yang bisa dipakai di node ini: versi, storage untuk unduhan dan disk, pool, bridge."""
    version = _pve_version(await client.version())
    imports = await client.list_storages(node, "import")
    disks = await client.list_storages(node, "images")
    try:
        pools = [p["poolid"] for p in await client.list_pools()]
    except ProxmoxError:
        pools = []
    try:
        bridges = [n["iface"] for n in await client.node_networks(node) if n.get("type") in ("bridge", "OVSBridge")]
    except ProxmoxError:
        bridges = []

    def row(s):
        return {"storage": s["storage"], "type": s.get("type"), "avail_gb": round(int(s.get("avail") or 0) / 1024 ** 3, 1)}
    return {"version_ok": version >= MIN_PVE, "version": f"{version[0]}.{version[1]}", "node_network": await can_access_network(client, node),
            "import_storages": [row(s) for s in imports], "disk_storages": [row(s) for s in disks],
            "pools": sorted(pools), "bridges": sorted(bridges)}


async def missing_privileges(client, node: str, opts: dict) -> list[str]:
    need = {
        f"/storage/{opts['storage']}": ["Datastore.AllocateTemplate", "Datastore.AllocateSpace"],
        f"/storage/{opts['disk_storage']}": ["Datastore.AllocateSpace"],
        f"/pool/{opts['pool']}": ["VM.Allocate", "VM.Config.Disk", "VM.Config.Cloudinit", "VM.Clone"],
    }
    missing = [] if await can_access_network(client, node) else [f"/nodes/{node}: Sys.AccessNetwork"]
    for path, privs in need.items():
        have = await client.permissions(path)
        missing += [f"{path}: {p}" for p in privs if p not in have]
    return missing


# ── job ──────────────────────────────────────────────────────────────────────

def _job_out(row) -> dict:
    out = dict(row)
    out["options"], out["result"] = _j(out["options"]), _j(out["result"])
    return out


async def get_job(job_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT {_JOB_COLS} FROM image_jobs WHERE id = $1", job_id)
    if not row:
        raise ImageError(tr("Proses tidak ditemukan", "Job not found"), 404)
    return _job_out(row)


async def list_jobs(labels: set[str] | None, limit: int = 30) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT {_JOB_COLS} FROM image_jobs ORDER BY id DESC LIMIT 200")
    return [_job_out(r) for r in rows if labels is None or r["instance"] in labels][:limit]


async def _update(job_id: int, **fields) -> None:
    sets, args = ["updated_at = NOW()"], [job_id]
    for k, v in fields.items():
        args.append(json.dumps(v) if k in ("result", "options") else v)
        sets.append(f"{k} = ${len(args)}" + ("::jsonb" if k in ("result", "options") else ""))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE image_jobs SET {', '.join(sets)} WHERE id = $1", *args)


def validate_options(image: dict, body: dict) -> dict:
    """Pilihan job yang sudah dibersihkan. Pesan galatnya langsung bisa ditampilkan."""
    def text(key, rx, label):
        val = str(body.get(key) or "").strip()
        if not rx.match(val):
            raise ImageError(tr(f"{label} tidak valid", f"{label} is not valid"))
        return val

    def number(key, lo, hi, label, default):
        raw = body.get(key)
        if raw in (None, ""):
            return default
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ImageError(tr(f"{label} harus angka", f"{label} must be a number"))
        if not lo <= n <= hi:
            raise ImageError(tr(f"{label} harus antara {lo} dan {hi}", f"{label} must be between {lo} and {hi}"))
        return n

    name = str(body.get("template_name") or f"tpl-{image['key']}").strip()
    if not NAME_RE.match(name):
        raise ImageError(tr("Nama template hanya huruf, angka, dan '-'", "The template name may only contain letters, digits and '-'"))
    return {
        "storage": text("storage", STORAGE_RE, tr("Storage unduhan", "Download storage")),
        "disk_storage": text("disk_storage", STORAGE_RE, tr("Storage disk", "Disk storage")),
        "pool": text("pool", POOL_RE, "Pool"),
        "bridge": text("bridge", BRIDGE_RE, "Bridge") if body.get("bridge") else "vmbr0",
        "template_name": name,
        "cores": number("cores", 1, 64, "CPU", 2),
        "memory_mb": number("memory_mb", 256, 262144, "RAM", 2048),
        "disk_gb": number("disk_gb", image["min_disk_gb"], 4096, tr("Disk", "Disk"), image["min_disk_gb"]),
        "force_download": bool(body.get("force_download")),
    }


async def start_job(user: dict, instance: str, node: str, image: dict, body: dict) -> int:
    opts = validate_options(image, body)
    pool = await get_pool()
    async with pool.acquire() as conn:
        busy = await conn.fetchval(
            "SELECT id FROM image_jobs WHERE instance = $1 AND node = $2 AND image_key = $3 AND status = ANY($4)",
            instance, node, image["key"], ["pending", "running"])
        if busy:
            raise ImageError(tr(f"Proses #{busy} sedang membuat template dari image ini di node yang sama",
                                f"Job #{busy} is already building a template from this image on the same node"), 409)
        job_id = await conn.fetchval(
            """INSERT INTO image_jobs (instance, node, image_key, image_name, options, created_by_id, created_by)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7) RETURNING id""",
            instance, node, image["key"], image["name"], json.dumps(opts), int(user["sub"]), user.get("username") or "")
    spawn(job_id)
    return job_id


def spawn(job_id: int) -> None:
    task = asyncio.create_task(run_job(job_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def recover_interrupted() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE image_jobs SET status = 'interrupted', updated_at = NOW(),
                   error = 'Backend berhenti saat proses ini berjalan. Ulangi untuk melanjutkan'
               WHERE status IN ('pending', 'running')""")
        await conn.execute("DELETE FROM image_jobs WHERE finished_at < NOW() - INTERVAL '30 days'")


async def retry_job(job_id: int) -> dict:
    job = await get_job(job_id)
    if job["status"] not in ("failed", "interrupted"):
        raise ImageError(tr("Hanya proses yang gagal atau terputus yang bisa diulang", "Only failed or interrupted jobs can be retried"), 409)
    await _update(job_id, status="pending", step="", error="", finished_at=None)
    spawn(job_id)
    return await get_job(job_id)


async def remove_job(job_id: int) -> dict:
    job = await get_job(job_id)
    if job["status"] not in TERMINAL:
        raise ImageError(tr("Proses yang masih berjalan tidak bisa dihapus dari riwayat", "A running job cannot be removed from the history"), 409)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM image_jobs WHERE id = $1", job_id)
    return job


async def _wait(client, job_id: int, node: str, upid: str, timeout: int, label: str) -> None:
    """Tunggu task Proxmox sambil menyimpan baris log terakhir sebagai progres."""
    deadline = time.monotonic() + timeout
    tick = 0
    while True:
        st = await client.get_task_status(node, upid)
        if st.get("status") == "stopped":
            status = st.get("exitstatus") or ""
            if status != "OK" and not status.startswith("WARNINGS"):
                tail = ""
                try:
                    tail = " | ".join((await client.task_log(node, upid, 200))[-3:])
                except Exception:
                    pass
                raise ProxmoxError(500, tr(f"{label} gagal: {status} {tail}", f"{label} failed: {status} {tail}"))
            return
        if time.monotonic() > deadline:
            raise ProxmoxError(504, tr(f"{label} belum selesai setelah {timeout} detik", f"{label} did not finish after {timeout} seconds"))
        tick += 1
        if tick % 4 == 0:
            try:
                tail = (await client.task_log(node, upid, 200))[-1:]
                if tail:
                    job = await get_job(job_id)
                    await _update(job_id, result={**job["result"], "progress": tail[0][:200]})
            except Exception:
                pass
        await asyncio.sleep(POLL_S)


async def _head_size(url: str) -> int | None:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as http:
            for _ in range(4):
                _https(url)
                resp = await http.head(url)
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                return int(resp.headers["content-length"]) if resp.headers.get("content-length") else None
    except Exception:
        pass
    return None


async def _fail(job_id: int, detail: str) -> None:
    await _update(job_id, status="failed", error=detail[:500], finished_at=datetime.now().astimezone())


async def run_job(job_id: int) -> None:
    job = await get_job(job_id)
    lock = _locks.setdefault((job["instance"], job["node"]), asyncio.Lock())        # satu job per node: unduhan besar tidak bertumpuk
    async with lock:
        pool = await get_pool()
        async with pool.acquire() as conn:
            claimed = await conn.fetchval("UPDATE image_jobs SET status = 'running', error = '', updated_at = NOW() "
                                          "WHERE id = $1 AND status = 'pending' RETURNING id", job_id)
        if not claimed:
            return
        try:
            await _execute(job_id)
        except ImageError as e:
            await _fail(job_id, e.detail)
        except ProxmoxError as e:
            await _fail(job_id, e.detail)
        except Exception as e:
            log.exception("Job image %s gagal", job_id)
            await _fail(job_id, f"{type(e).__name__}: {str(e)[:250]}")


async def _vm_exists(client, node: str, vmid: int | None) -> dict | None:
    if not vmid:
        return None
    try:
        return await client.get_vm_config(node, vmid)
    except ProxmoxError:
        return None


async def _discard_vm(client, node: str, vmid: int | None) -> None:
    """Hapus VM setengah jadi (bukan template) setelah job gagal, supaya percobaan berikutnya mulai bersih."""
    cfg = await _vm_exists(client, node, vmid)
    if cfg is None or str(cfg.get("template", "0")) == "1":
        return
    try:
        await client.wait_task(node, await client.destroy_vm(node, vmid), timeout=180)
    except Exception:
        log.warning("VM %s setengah jadi tidak bisa dihapus otomatis", vmid)


async def _finish(job_id: int, result: dict, warnings: list[str]) -> None:
    await _update(job_id, status="done", step="", upid="", result={**result, "warnings": warnings}, finished_at=datetime.now().astimezone())


async def _execute(job_id: int) -> None:
    job = await get_job(job_id)
    instance, node, opts = job["instance"], job["node"], job["options"]
    image = get_image(job["image_key"])
    client = await proxmox_instances.get_client(instance)
    result: dict = dict(job["result"])
    warnings: list[str] = []
    if image["agent_included"] is False:
        warnings.append(tr("Image ini tidak membawa guest agent: IP DHCP tidak terdeteksi. Pakai IP statis atau switch CCD saat membuat VM",
                           "This image has no guest agent: a DHCP IP will not be detected. Use a static IP or a CCD switch when creating VMs"))

    # Sudah jadi template (job diulang setelah selesai di Proxmox tetapi sebelum dicatat)?
    cfg = await _vm_exists(client, node, job["vmid"])
    if cfg and str(cfg.get("template", "0")) == "1":
        await _finish(job_id, result, warnings)
        return

    # 0. Prasyarat
    await _update(job_id, step="preflight")
    options = await node_options(client, node)
    if not options["version_ok"]:
        raise ImageError(tr(f"Proxmox VE {options['version']} terlalu lama; impor image butuh 8.2 atau lebih baru",
                            f"Proxmox VE {options['version']} is too old; image import needs 8.2 or newer"))
    names = {s["storage"]: s for s in options["import_storages"]}
    if opts["storage"] not in names:
        raise ImageError(tr(f"Storage '{opts['storage']}' tidak mengizinkan konten 'Import'. Aktifkan di Datacenter → Storage → Content, "
                            f"atau pilih: {', '.join(names) or '(tidak ada)'}",
                            f"Storage '{opts['storage']}' does not allow 'Import' content. Enable it in Datacenter → Storage → Content, "
                            f"or choose: {', '.join(names) or '(none)'}"))
    if opts["disk_storage"] not in {s["storage"] for s in options["disk_storages"]}:
        raise ImageError(tr(f"Storage disk '{opts['disk_storage']}' tidak mengizinkan konten 'Disk image'",
                            f"Disk storage '{opts['disk_storage']}' does not allow 'Disk image' content"))
    missing = await missing_privileges(client, node, opts)
    if missing:
        raise ImageError(tr(f"Token Proxmox kekurangan hak: {', '.join(missing)}. Lihat docs/CLOUD_IMAGES.md bagian Izin Proxmox",
                            f"The Proxmox token lacks privileges: {', '.join(missing)}. See docs/CLOUD_IMAGES.md, section Proxmox permissions"))

    # 1. Unduh
    volid = job["volid"] or f"{opts['storage']}:import/{volume_filename(image)}"
    if not cfg:
        have = {f.get("volid") for f in await client.storage_files(node, opts["storage"], "import")}
        if volid in have and not opts["force_download"]:
            warnings.append(tr("Berkas image yang sudah ada di storage dipakai ulang (checksum tidak diperiksa lagi)",
                               "The image file already in the storage was reused (checksum not re-checked)"))
        else:
            if volid in have:
                await client.delete_volume(node, opts["storage"], volid)
            checksum, algo = await resolve_checksum(image)
            size = await _head_size(image["url"])
            avail = names[opts["storage"]]["avail_gb"] * 1024 ** 3
            if size and avail and avail < size * 1.1:
                raise ImageError(tr(f"Ruang storage '{opts['storage']}' tidak cukup ({names[opts['storage']]['avail_gb']} GB kosong, image {round(size / 1024 ** 3, 1)} GB)",
                                    f"Not enough space on '{opts['storage']}' ({names[opts['storage']]['avail_gb']} GB free, image {round(size / 1024 ** 3, 1)} GB)"))
            await _update(job_id, step="download", volid=volid)
            upid = await client.download_url(node, opts["storage"], image["url"], volume_filename(image), "import", checksum, algo)
            await _update(job_id, upid=upid)
            await _wait(client, job_id, node, upid, 3600, tr("Unduhan image", "Image download"))
            result["checksum"] = f"{algo}:{checksum[:16]}…"
            if opts["force_download"]:
                await _update(job_id, options={**opts, "force_download": False})
        await _update(job_id, volid=volid, upid="")

    # 2. Buat VM dari disk itu
    vmid = job["vmid"] if cfg else None
    if not cfg:
        await _update(job_id, step="create")
        upid = None
        for attempt in range(3):
            vmid = await client.next_vmid()
            config = {
                "vmid": vmid, "name": opts["template_name"], "ostype": "l26", "memory": opts["memory_mb"], "cores": opts["cores"],
                "cpu": "x86-64-v2-AES", "scsihw": "virtio-scsi-single", "boot": "order=scsi0", "agent": "1",
                "scsi0": f"{opts['disk_storage']}:0,import-from={volid},discard=on", "ide2": f"{opts['disk_storage']}:cloudinit",
                "net0": f"virtio,bridge={opts['bridge']}", "serial0": "socket", "pool": opts["pool"],
                "tags": f"{TAG_IMAGE};ccd-{image['key']}",
                "description": f"CCD cloud image: {image['name']}. Dibuat dari {image['url']}",
            }
            try:
                upid = await client.create_vm(node, config)
                break
            except ProxmoxError as e:
                if "already exists" in e.detail and attempt < 2:
                    continue
                raise
        await _update(job_id, vmid=vmid, upid=upid)
        try:
            await _wait(client, job_id, node, upid, 1800, tr("Impor disk", "Disk import"))
            await _update(job_id, upid="")
            disk = provision._disk(await client.get_vm_config(node, vmid))
            if disk and disk[1] and opts["disk_gb"] > disk[1]:
                await client.resize_disk(node, vmid, disk[0], f"{opts['disk_gb']}G")
        except BaseException:
            await _discard_vm(client, node, vmid)
            raise

    # 3. Jadikan template
    await _update(job_id, step="template")
    try:
        upid = await client.convert_to_template(node, vmid)
        if isinstance(upid, str) and upid.startswith("UPID:"):
            await _wait(client, job_id, node, upid, 300, tr("Convert ke template", "Convert to template"))
    except BaseException:
        await _discard_vm(client, node, vmid)       # tidak menyentuh VM yang sudah menjadi template
        raise
    result["template_vmid"] = vmid
    result["dhcp_expected"] = bool(image["agent_included"])
    await _finish(job_id, result, warnings)
