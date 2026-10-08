"""
Otomasi Linux VM via SSH dengan streaming progress real-time (SSE).

Endpoints:
  POST /api/v1/linux-vm/{host}/{vm}/configure-network  — ubah/tambah IP, gateway, subnet
  POST /api/v1/linux-vm/{host}/{vm}/expand-disk        — GPT fix → growpart → pvresize → lvextend → resize2fs
  GET  /api/v1/linux-vm/{host}/{vm}/disk-info          — info partisi/LVM untuk panduan expand

Semua progress dikirim via Server-Sent Events (text/event-stream).
Setiap event adalah JSON: { "step": N, "total": N, "status": "ok"|"err"|"info", "msg": "..." }
"""
import asyncio
import json
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from auth import get_current_user, Role
from routers.ssh_creds import get_vm_ssh_client
from i18n import tr

router = APIRouter()


def _require_admin(user: dict):
    if user["role"] not in (Role.SUPERADMIN, Role.SYSADMIN):
        raise HTTPException(403, tr("Aksi ini hanya untuk admin/sysadmin",
                                    "Only admins/sysadmins can do this"))


# ── Pydantic models ────────────────────────────────────────────────────────────

class NetworkConfig(BaseModel):
    interface:   str            # mis. "eth0" atau "ens3"
    ip_address:  str            # mis. "192.168.1.10"
    prefix_len:  int = 24       # mis. 24 → /24
    gateway:     Optional[str] = None
    dns:         list[str] = ["8.8.8.8", "1.1.1.1"]
    dhcp:        bool = False   # jika True, abaikan ip_address/gateway




# ── SSE helper ─────────────────────────────────────────────────────────────────

def _evt(step: int, total: int, status: str, msg: str) -> str:
    """Format satu SSE event."""
    payload = json.dumps({"step": step, "total": total, "status": status, "msg": msg})
    return f"data: {payload}\n\n"


def _done(success: bool, msg: str) -> str:
    payload = json.dumps({"step": -1, "total": -1, "status": "done" if success else "failed", "msg": msg})
    return f"data: {payload}\n\n"


# ── Network Configuration ──────────────────────────────────────────────────────

@router.post("/{host_name}/{vm_id}/configure-network")
async def configure_network(
    host_name: str, vm_id: str,
    body: NetworkConfig,
    user: dict = Depends(get_current_user)
):
    _require_admin(user)
    if not body.dhcp:
        _validate_ip(body.ip_address)
        if body.gateway:
            _validate_ip(body.gateway)

    async def generate():
        total = 5
        client = None
        try:
            yield _evt(0, total, "info", f"Menghubungkan ke VM {vm_id}...")
            client = await get_vm_ssh_client(vm_id, host_name)
            ok, err = await client.test_connection()
            if not ok:
                yield _done(False, f"Koneksi SSH gagal: {err}")
                return
            yield _evt(1, total, "ok", "SSH terhubung")

            # Langkah 1: deteksi OS & network manager
            yield _evt(2, total, "info", "Mendeteksi network manager...")
            out, _, _ = await client.exec("command -v netplan 2>/dev/null; cat /etc/os-release | grep ^ID=")
            has_netplan = "netplan" in out.lower()
            yield _evt(2, total, "ok", f"Network manager: {'netplan' if has_netplan else 'NetworkManager/ifupdown'}")

            # Langkah 2: backup konfigurasi lama
            yield _evt(3, total, "info", "Membuat backup konfigurasi lama...")
            ts_out, _, _ = await client.exec("date +%Y%m%d%H%M%S")
            ts = ts_out.strip()
            if has_netplan:
                await client.exec_sudo(f"cp /etc/netplan/00-installer-config.yaml /etc/netplan/00-installer-config.yaml.bak.{ts} 2>/dev/null || true")
            else:
                await client.exec_sudo(f"cp /etc/network/interfaces /etc/network/interfaces.bak.{ts} 2>/dev/null || true")
            yield _evt(3, total, "ok", "Backup selesai")

            # Langkah 3: tulis konfigurasi baru
            yield _evt(4, total, "info", "Menulis konfigurasi jaringan baru...")
            # Slug aman untuk nama file (strip karakter non-alfanumerik)
            iface_slug = re.sub(r'[^a-zA-Z0-9_-]', '', body.interface)
            if has_netplan:
                config_content = _build_netplan(body)
                # Satu file per interface agar mengonfigurasi eth1 tidak menghapus eth0.
                # 99-hyperpanel-{iface}.yaml — prioritas tinggi, hanya berisi satu interface.
                tmp_file   = f"/tmp/99-hyperpanel-{iface_slug}.yaml"
                dest_file  = f"/etc/netplan/99-hyperpanel-{iface_slug}.yaml"
                await client.sftp_write(tmp_file, config_content)
                setup_cmd = (
                    "mkdir -p /etc/cloud/cloud.cfg.d && "
                    "echo 'network: {config: disabled}' > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg && "
                    # Hapus format lama tunggal (50-). Format 99-hyperpanel.yaml (lama, berisi semua
                    # interface) JANGAN dihapus langsung — rename ke prioritas 50- agar interface lain
                    # yang belum dikonfigurasi ulang tetap punya config. File 99-hyperpanel-{iface}.yaml
                    # baru (prioritas 99-) akan override untuk interface yang dikonfigurasi.
                    "rm -f /etc/netplan/50-hyperpanel.yaml && "
                    "{ [ -f /etc/netplan/99-hyperpanel.yaml ] && "
                    "mv /etc/netplan/99-hyperpanel.yaml /etc/netplan/50-hyperpanel-legacy.yaml; } || true && "
                    f"mv {tmp_file} {dest_file} && "
                    f"chmod 600 {dest_file}"
                )
                _, err, code = await client.exec_sudo(setup_cmd)
                yield _evt(4, total, "ok" if code == 0 else "err",
                           f"File konfigurasi {'ditulis (cloud-init dinonaktifkan)' if code == 0 else 'gagal ditulis'}")
                if code != 0:
                    yield _done(False, f"Gagal menulis konfigurasi: {err}")
                    return

                # Langkah 4: generate + apply. 'netplan generate' memvalidasi YAML dulu,
                # 'netplan apply' menerapkan tanpa memutus worker SSH bila config valid.
                yield _evt(5, total, "info", "Menerapkan konfigurasi (netplan generate && netplan apply)...")
                out, err, code = await client.exec_sudo(
                    "netplan generate && netplan apply 2>&1", timeout=30.0
                )
                if code == 0:
                    yield _evt(5, total, "ok", "netplan generate & apply berhasil")
                    yield _done(True, f"Konfigurasi jaringan {body.interface} berhasil diterapkan")
                else:
                    yield _evt(5, total, "err", f"netplan apply error: {err or out}")
                    yield _done(False, "Konfigurasi diterapkan sebagian, cek log VM")

            else:
                # ifupdown — satu file per interface di /etc/network/interfaces.d/
                # agar mengonfigurasi satu interface tidak menimpa interface lain.
                ifaces_content = _build_interfaces(body)
                tmp_file  = f"/tmp/hv-iface-{iface_slug}"
                dest_file = f"/etc/network/interfaces.d/{iface_slug}"
                await client.sftp_write(tmp_file, ifaces_content)
                _, err, code = await client.exec_sudo(
                    f"mkdir -p /etc/network/interfaces.d && "
                    f"mv {tmp_file} {dest_file} && "
                    # Pastikan /etc/network/interfaces menyertakan interfaces.d
                    f"grep -q 'source /etc/network/interfaces.d' /etc/network/interfaces 2>/dev/null || "
                    f"echo 'source /etc/network/interfaces.d/*' >> /etc/network/interfaces"
                )
                yield _evt(4, total, "ok" if code == 0 else "err",
                           f"File /etc/network/interfaces.d/{iface_slug} {'ditulis' if code == 0 else f'gagal: {err}'}")
                if code != 0:
                    yield _done(False, "Gagal menulis konfigurasi")
                    return

                yield _evt(5, total, "info", "Memuat ulang interface...")
                out, err, code = await client.exec_sudo(
                    f"ifdown {iface_slug} 2>/dev/null; ifup {iface_slug} 2>&1",
                    timeout=30.0
                )
                if code == 0:
                    yield _done(True, f"Interface {iface_slug} berhasil dikonfigurasi")
                else:
                    yield _done(False, f"Error saat reload interface: {err or out}")

        except Exception as e:
            yield _done(False, f"Error: {str(e)}")

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Disk Expansion ─────────────────────────────────────────────────────────────

def _parse_part_device(dev: str):
    """Return (disk, part_num) from /dev/sda3, /dev/nvme0n1p3, etc."""
    m = re.match(r'^(/dev/nvme\d+n\d+)p(\d+)$', dev)
    if not m:
        m = re.match(r'^(/dev/[a-z]+)(\d+)$', dev)
    return (m.group(1), m.group(2)) if m else (None, None)


@router.post("/{host_name}/{vm_id}/expand-disk")
async def expand_disk(
    host_name: str, vm_id: str,
    user: dict = Depends(get_current_user)
):
    _require_admin(user)
    total_steps = 9

    async def generate():
        try:
            yield _evt(0, total_steps, "info", f"Menghubungkan ke VM {vm_id}...")
            client = await get_vm_ssh_client(vm_id, host_name)
            ok, err = await client.test_connection()
            if not ok:
                yield _done(False, f"Koneksi SSH gagal: {err}")
                return
            yield _evt(1, total_steps, "ok", "SSH terhubung")

            # Step 2: Install tools
            yield _evt(2, total_steps, "info", "Memeriksa tools (growpart, sgdisk)...")
            out, _, code = await client.exec_sudo(
                "command -v growpart > /dev/null && command -v sgdisk > /dev/null || "
                "(apt-get update -qq && apt-get install -y -qq cloud-guest-utils gdisk parted 2>&1 | tail -5)",
                timeout=120.0
            )
            yield _evt(2, total_steps, "ok" if code == 0 else "info",
                       "Tools tersedia" if code == 0 else f"Tools diinstall: {out.strip()[-100:]}")

            # Step 3: Deteksi PV LVM aktif
            yield _evt(3, total_steps, "info", "Mendeteksi Physical Volume LVM...")
            pv_out, _, pv_code = await client.exec_sudo(
                "pvs --noheadings -o pv_name 2>/dev/null | tr -d ' ' | head -1"
            )
            pv_name = pv_out.strip()
            use_lvm = bool(pv_name and pv_code == 0 and pv_name.startswith("/dev/"))

            if use_lvm:
                disk, part_num = _parse_part_device(pv_name)
                if not disk:
                    yield _done(False, f"Tidak bisa parse PV: {pv_name}")
                    return
                yield _evt(3, total_steps, "ok",
                           f"PV LVM ditemukan: {pv_name} → disk={disk}, partisi={part_num}")
            else:
                # Tidak ada LVM — deteksi partisi root langsung
                root_out, _, _ = await client.exec(
                    "findmnt -n -o SOURCE / 2>/dev/null || df / 2>/dev/null | awk 'NR==2{print $1}'"
                )
                pv_name = root_out.strip()
                disk, part_num = _parse_part_device(pv_name)
                if not disk:
                    yield _done(False, f"Tidak bisa mendeteksi partisi root: {pv_name}")
                    return
                yield _evt(3, total_steps, "ok",
                           f"No LVM, partisi root: {pv_name} → disk={disk}, partisi={part_num}")

            # Step 4: Deteksi LV path & filesystem type
            yield _evt(4, total_steps, "info", "Mendeteksi filesystem...")
            fs_out, _, _ = await client.exec(
                "findmnt -no FSTYPE / 2>/dev/null || df -T / 2>/dev/null | awk 'NR==2{print $2}'"
            )
            fs_type = fs_out.strip() or "ext4"
            lv_path = None
            if use_lvm:
                root_src, _, _ = await client.exec(
                    "findmnt -n -o SOURCE / 2>/dev/null || df / 2>/dev/null | awk 'NR==2{print $1}'"
                )
                lv_path = root_src.strip()
                yield _evt(4, total_steps, "ok", f"LV: {lv_path}, filesystem: {fs_type}")
            else:
                yield _evt(4, total_steps, "ok", f"Filesystem: {fs_type} pada {pv_name}")

            # Step 5: Fix GPT partition table + partprobe
            yield _evt(5, total_steps, "info", f"Memperbaiki GPT partition table pada {disk}...")
            await client.exec_sudo(
                f"sgdisk -e {disk} 2>&1 || parted -s {disk} print 2>&1",
                timeout=30.0
            )
            await client.exec_sudo(f"partprobe {disk} 2>/dev/null; sleep 1", timeout=15.0)
            yield _evt(5, total_steps, "ok", "GPT partition table diperbaiki, kernel diperbarui")

            # Step 6: growpart
            yield _evt(6, total_steps, "info", f"Memperluas partisi {part_num} pada {disk}...")
            out, err, code = await client.exec_sudo(
                f"growpart {disk} {part_num} 2>&1", timeout=60.0
            )
            if code != 0 and "NOCHANGE" in (out + err):
                yield _evt(6, total_steps, "info", "Partisi sudah memenuhi disk (NOCHANGE)")
            elif code != 0:
                yield _done(False, f"growpart gagal: {err or out}")
                return
            else:
                yield _evt(6, total_steps, "ok", out.strip()[:150] or "Partisi diperluas")

            if use_lvm:
                # Step 7: pvresize
                yield _evt(7, total_steps, "info", f"pvresize {pv_name}...")
                out, err, code = await client.exec_sudo(f"pvresize {pv_name} 2>&1", timeout=30.0)
                yield _evt(7, total_steps, "ok" if code == 0 else "err",
                           out.strip() or err.strip())
                if code != 0:
                    yield _done(False, f"pvresize gagal: {err}")
                    return

                # Step 8: lvextend
                yield _evt(8, total_steps, "info", f"lvextend -l +100%FREE {lv_path}...")
                out, err, code = await client.exec_sudo(
                    f"lvextend -l +100%FREE {lv_path} 2>&1", timeout=60.0
                )
                if code != 0 and "already" in (out + err).lower():
                    yield _evt(8, total_steps, "info", "LV sudah penuh (no free space to extend)")
                elif code != 0:
                    yield _done(False, f"lvextend gagal: {err or out}")
                    return
                else:
                    yield _evt(8, total_steps, "ok", out.strip() or "LV diperluas")

            # Step 9: resize filesystem
            resize_target = lv_path if use_lvm else pv_name
            yield _evt(9, total_steps, "info", f"Resize filesystem ({fs_type}) pada {resize_target}...")
            if fs_type == "xfs":
                out, err, code = await client.exec_sudo("xfs_growfs / 2>&1", timeout=60.0)
            elif fs_type == "btrfs":
                out, err, code = await client.exec_sudo("btrfs filesystem resize max / 2>&1", timeout=60.0)
            else:
                out, err, code = await client.exec_sudo(
                    f"resize2fs {resize_target} 2>&1", timeout=120.0
                )

            if code == 0:
                yield _evt(9, total_steps, "ok", out.strip()[:200] or "Resize selesai")
                df_out, _, _ = await client.exec("df -h 2>/dev/null | head -10")
                yield _evt(9, total_steps, "info", f"Disk usage sekarang:\n{df_out.strip()}")
                yield _done(True, "Disk expansion selesai!")
            else:
                yield _done(False, f"Resize filesystem gagal: {err or out}")

        except Exception as e:
            yield _done(False, f"Error: {str(e)}")

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Disk Info (helper untuk frontend) ─────────────────────────────────────────

@router.get("/{host_name}/{vm_id}/disk-info")
async def get_disk_info(
    host_name: str, vm_id: str,
    user: dict = Depends(get_current_user)
):
    _require_admin(user)
    client = await get_vm_ssh_client(vm_id, host_name)
    ok, err = await client.test_connection()
    if not ok:
        raise HTTPException(503, tr(f"SSH gagal: {err}", f"SSH failed: {err}"))

    lsblk_out, _, _ = await client.exec("lsblk -J -o NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE 2>/dev/null")
    df_out, _, _    = await client.exec("df -h 2>/dev/null")
    pvs_out, _, _   = await client.exec_sudo("pvs 2>/dev/null || echo 'no-lvm'")
    vgs_out, _, _   = await client.exec_sudo("vgs 2>/dev/null || echo 'no-lvm'")
    lvs_out, _, _   = await client.exec_sudo("lvs 2>/dev/null || echo 'no-lvm'")

    lsblk = {}
    try:
        lsblk = json.loads(lsblk_out)
    except Exception:
        pass

    return {
        "lsblk": lsblk,
        "df":    df_out.strip(),
        "pvs":   pvs_out.strip(),
        "vgs":   vgs_out.strip(),
        "lvs":   lvs_out.strip(),
    }


# ── Network Info ───────────────────────────────────────────────────────────────

@router.get("/{host_name}/{vm_id}/network-info")
async def get_network_info(
    host_name: str, vm_id: str,
    user: dict = Depends(get_current_user)
):
    _require_admin(user)
    client = await get_vm_ssh_client(vm_id, host_name)
    ok, err = await client.test_connection()
    if not ok:
        raise HTTPException(503, tr(f"SSH gagal: {err}", f"SSH failed: {err}"))

    ip_out,   _, _ = await client.exec("ip -j addr show 2>/dev/null || ip addr show")
    route_out, _, _ = await client.exec("ip -j route show 2>/dev/null || ip route show")
    dns_out,   _, _ = await client.exec("cat /etc/resolv.conf 2>/dev/null")

    interfaces = []
    try:
        interfaces = json.loads(ip_out)
    except Exception:
        pass

    return {
        "interfaces": interfaces,
        "ip_raw":  ip_out.strip(),
        "routes":  route_out.strip(),
        "dns":     dns_out.strip(),
    }


# ── Config builders ────────────────────────────────────────────────────────────

def _build_netplan(cfg: NetworkConfig) -> str:
    if cfg.dhcp:
        return f"""network:
  version: 2
  ethernets:
    {cfg.interface}:
      dhcp4: true
"""
    gw_line = f"      gateway4: {cfg.gateway}\n" if cfg.gateway else ""
    dns_list = ", ".join(f'"{d}"' for d in cfg.dns)
    return f"""network:
  version: 2
  ethernets:
    {cfg.interface}:
      dhcp4: false
      addresses:
        - {cfg.ip_address}/{cfg.prefix_len}
{gw_line}      nameservers:
        addresses: [{dns_list}]
"""


def _build_interfaces(cfg: NetworkConfig) -> str:
    if cfg.dhcp:
        return f"auto {cfg.interface}\niface {cfg.interface} inet dhcp\n"
    gw_line = f"  gateway {cfg.gateway}\n" if cfg.gateway else ""
    import ipaddress
    try:
        net = ipaddress.ip_network(f"0.0.0.0/{cfg.prefix_len}", strict=False)
        netmask = str(net.netmask)
    except Exception:
        netmask = "255.255.255.0"
    dns_str = " ".join(cfg.dns)
    return (
        f"auto {cfg.interface}\n"
        f"iface {cfg.interface} inet static\n"
        f"  address {cfg.ip_address}\n"
        f"  netmask {netmask}\n"
        f"{gw_line}"
        f"  dns-nameservers {dns_str}\n"
    )


def _validate_ip(ip: str):
    import ipaddress
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(400, tr(f"IP address tidak valid: {ip}", f"Invalid IP address: {ip}"))
