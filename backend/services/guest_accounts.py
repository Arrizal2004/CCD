"""
Akun OS di dalam VM Proxmox lewat QEMU Guest Agent: membuat user, mengganti password, dan menghapus
user tanpa SSH, tanpa password lama, dan tanpa jaringan di dalam VM. Kalau agent tidak ada atau
dibatasi SELinux, perintah yang sama dijalankan lewat SSH dengan Login Connect VM itu (sudo).

Perintah dikirim ke agent sebagai daftar argumen (bukan teks shell) dan username divalidasi dulu,
jadi isi form tidak bisa menyisipkan perintah lain. Hanya akun pengguna biasa (UID 1000 ke atas)
yang bisa direset atau dihapus; akun sistem dan root tidak.

Setelah password berubah di dalam VM, semua salinan yang disimpan dashboard untuk user itu
(Login Connect, akun OS, kredensial grup, data VM) ikut diperbarui supaya Connect tetap jalan.
"""
import asyncio
import contextlib
import json
import logging
import re
import secrets
import shlex

from fastapi import HTTPException

from database import get_pool
from services import proxmox_instances
from services.proxmox_client import ProxmoxError
from services.ssh_client import decrypt_secret, encrypt_secret
from i18n import tr

log = logging.getLogger("guest_accounts")

USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
UID_MIN, UID_MAX = 1000, 60000          # rentang akun pengguna biasa (login.defs bawaan)
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # tanpa 0/O, 1/l/I


def generate_password() -> str:
    """12 karakter acak, dikelompokkan empat-empat supaya mudah dibacakan dan diketik."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def check_username(username: str | None) -> str:
    name = (username or "").strip()
    if not USER_RE.match(name) or name == "root":
        raise HTTPException(400, tr("Username tidak valid: huruf kecil, angka, _ atau -, diawali huruf, dan bukan root",
                                    "Invalid username: lowercase letters, digits, _ or -, starting with a letter, and not root"))
    return name


def check_password(password: str | None) -> str:
    password = password or ""
    if not 8 <= len(password) <= 128:
        raise HTTPException(400, tr("Password minimal 8 dan maksimal 128 karakter",
                                    "The password must be 8 to 128 characters"))
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
        raise HTTPException(400, tr("Password tidak boleh berisi baris baru atau karakter kontrol",
                                    "The password may not contain line breaks or control characters"))
    return password


def _reason(e: ProxmoxError) -> str:
    text = (e.detail or "").strip()
    if text.startswith("{"):
        try:
            body = json.loads(text)
            text = body.get("message") or str(body.get("errors") or text)
        except ValueError:
            pass
    return text[:200] or f"HTTP {e.status_code}"


async def _target(host_name: str, vm_id: str):
    """(client, node, vmid). Host VM Proxmox di dashboard berbentuk '<label>__<node>'."""
    label, sep, node = host_name.partition("__")
    if not sep or not node or not str(vm_id).isdigit():
        raise HTTPException(400, tr("Fitur ini hanya untuk VM Proxmox",
                                    "This feature is only for Proxmox VMs"))
    try:
        client = await proxmox_instances.get_client(label)
    except ValueError:
        raise HTTPException(404, tr(f"Proxmox '{label}' tidak terdaftar di dashboard",
                                    f"Proxmox '{label}' is not registered in the dashboard"))
    return client, node, int(vm_id)


class _Guest:
    """Menjalankan perintah root di dalam satu VM. Jalur utama QEMU Guest Agent. Kalau agent tidak
    merespons atau menolak (SELinux di openSUSE Leap 16, Fedora, atau RHEL mengurung qemu-ga sehingga
    useradd dan chpasswd gagal), pindah ke SSH dengan Login Connect VM itu lewat sudo, dan tetap di
    SSH untuk sisa aksi. Dipakai lewat `async with _guest(...)` supaya koneksi SSH-nya ditutup."""

    def __init__(self, host_name: str, vm_id: str, client, node: str, vmid: int, agent_ok: bool):
        self.host_name, self.vm_id = host_name, vm_id
        self.client, self.node, self.vmid = client, node, vmid
        self.agent_ok = agent_ok
        self.why = "" if agent_ok else tr("QEMU Guest Agent tidak merespons",
                                          "The QEMU Guest Agent is not responding")
        self._conn = None
        self._sudo: tuple[list[str], str] = ([], "")

    def _refused(self, e: ProxmoxError) -> None:
        self.agent_ok = False
        self.why = tr(f"Guest Agent menolak perintah ini (biasanya karena SELinux): {_reason(e)}",
                      f"The Guest Agent refused this command (usually because of SELinux): {_reason(e)}")
        log.info("VM %s/%s: %s, pindah ke SSH", self.host_name, self.vm_id, self.why)

    async def run(self, argv: list[str]) -> dict:
        if self.agent_ok:
            try:
                return await self.client.agent_exec(self.node, self.vmid, argv)
            except ProxmoxError as e:
                self._refused(e)
        return await self._ssh(argv)

    async def set_password(self, username: str, password: str) -> None:
        if self.agent_ok:
            try:
                return await self.client.agent_set_user_password(self.node, self.vmid, username, password)
            except ProxmoxError as e:
                self._refused(e)
        # Password lewat stdin chpasswd, bukan argumen, supaya tidak terlihat di daftar proses VM.
        r = await self._ssh(["chpasswd"], f"{username}:{password}\n")
        if r["exitcode"] != 0:
            raise HTTPException(502, tr(f"Gagal mengganti password di dalam VM: {_output(r)}",
                                        f"Could not change the password inside the VM: {_output(r)}"))

    async def _ssh(self, argv: list[str], stdin: str = "") -> dict:
        if self._conn is None:
            await self._connect()
        prefix, sudo_input = self._sudo
        try:
            return await asyncio.to_thread(_ssh_exec, self._conn, prefix + argv, sudo_input + stdin)
        except Exception as e:
            raise HTTPException(502, tr(f"SSH ke VM terputus saat menjalankan {argv[0]}: {e}",
                                        f"The SSH connection to the VM dropped while running {argv[0]}: {e}"))

    async def _connect(self) -> None:
        from routers.ssh_creds import get_vm_ssh_client          # di sini supaya tidak saling impor
        try:
            ssh = await get_vm_ssh_client(self.vm_id, self.host_name)
        except HTTPException:
            raise HTTPException(409, tr(f"{self.why}. Jalur cadangan lewat SSH butuh Login Connect VM ini, "
                                        "tapi belum diatur",
                                        f"{self.why}. The SSH fallback needs this VM's Connect login, "
                                        "but it is not set"))
        target = f"{ssh.username}@{ssh.host}"
        try:
            self._conn = await asyncio.to_thread(_ssh_connect, ssh)
        except Exception as e:
            raise HTTPException(502, tr(f"{self.why}. Cadangan lewat SSH ({target}) juga gagal: {str(e)[:150]}",
                                        f"{self.why}. The SSH fallback ({target}) failed too: {str(e)[:150]}"))
        if ssh.username == "root":
            return
        if (await asyncio.to_thread(_ssh_exec, self._conn, ["sudo", "-n", "true"], ""))["exitcode"] == 0:
            self._sudo = (["sudo", "-n"], "")
        elif ssh.password:
            # -k: selalu minta password, jadi baris pertama stdin pasti dibaca sudo, bukan perintahnya.
            self._sudo = (["sudo", "-k", "-S", "-p", ""], ssh.password + "\n")
        else:
            raise HTTPException(502, tr(f"{self.why}. Cadangan lewat SSH ({target}) butuh sudo tanpa password "
                                        "karena Login Connect memakai private key",
                                        f"{self.why}. The SSH fallback ({target}) needs passwordless sudo "
                                        "because the Connect login uses a private key"))

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()


def _ssh_connect(ssh):
    return ssh._make_client()


def _ssh_exec(conn, argv: list[str], stdin: str) -> dict:
    stdin_f, out, err = conn.exec_command(" ".join(shlex.quote(a) for a in argv), timeout=60)
    if stdin:
        stdin_f.write(stdin)
        stdin_f.flush()
    stdin_f.channel.shutdown_write()
    text, error = out.read().decode(errors="replace"), err.read().decode(errors="replace")
    return {"exitcode": out.channel.recv_exit_status(), "out": text, "err": error}


@contextlib.asynccontextmanager
async def _guest(host_name: str, vm_id: str):
    """VM harus Linux dan menyala. Agent yang tidak merespons tidak langsung ditolak: SSH dicoba."""
    client, node, vmid = await _target(host_name, vm_id)
    try:
        config = await client.get_vm_config(node, vmid)
        status = await client.get_vm_status(node, vmid)
    except ProxmoxError as e:
        if e.status_code in (403, 404) or "does not exist" in (e.detail or ""):
            raise HTTPException(404, tr(f"VM {vmid} tidak ditemukan di {node}",
                                        f"VM {vmid} not found on {node}"))
        raise HTTPException(502, f"Proxmox: {_reason(e)}")
    if (config.get("ostype") or "").startswith("w"):
        raise HTTPException(400, tr("VM Windows belum didukung. Ganti password lewat RDP atau console Proxmox",
                                    "Windows VMs are not supported yet. Change the password through RDP or the Proxmox console"))
    if status.get("status") != "running":
        raise HTTPException(409, tr("VM harus menyala dulu", "The VM must be running first"))
    try:
        await client.agent_ping(node, vmid)
        agent_ok = True
    except ProxmoxError:
        agent_ok = False
    guest = _Guest(host_name, vm_id, client, node, vmid, agent_ok)
    try:
        yield guest
    finally:
        guest.close()


def _output(result: dict) -> str:
    return ((result.get("err") or result.get("out") or "").strip() or f"exit code {result.get('exitcode')}")[:200]


async def _uid(guest: _Guest, username: str) -> int | None:
    """UID user di dalam VM, atau None kalau user belum ada."""
    r = await guest.run(["getent", "passwd", username])
    if r["exitcode"] == 2:
        return None
    if r["exitcode"] != 0:
        raise HTTPException(502, tr(f"Gagal membaca daftar user di dalam VM: {_output(r)}",
                                    f"Could not read the user list inside the VM: {_output(r)}"))
    try:
        return int(r["out"].split(":")[2])
    except (IndexError, ValueError):
        raise HTTPException(502, tr("Jawaban getent dari dalam VM tidak dikenali",
                                    "Unrecognised getent output from inside the VM"))


def _regular(username: str, uid: int) -> None:
    if not UID_MIN <= uid < UID_MAX:
        raise HTTPException(400, tr(f"'{username}' adalah akun sistem (UID {uid}). Dari dashboard hanya akun "
                                    f"pengguna biasa (UID {UID_MIN} ke atas) yang bisa diubah",
                                    f"'{username}' is a system account (UID {uid}). The dashboard can only change "
                                    f"regular user accounts (UID {UID_MIN} and above)"))


async def _create(guest: _Guest, username: str, password: str) -> None:
    r = await guest.run(["useradd", "--create-home", "--shell", "/bin/bash", username])
    if r["exitcode"] != 0:
        raise HTTPException(502, tr(f"useradd gagal: {_output(r)}", f"useradd failed: {_output(r)}"))
    try:
        await guest.set_password(username, password)
    except HTTPException:
        # Jangan tinggalkan user tanpa password yang diketahui siapa pun.
        await guest.run(["userdel", "--remove", username])
        raise


async def create_user(host_name: str, vm_id: str, username: str, password: str) -> None:
    async with _guest(host_name, vm_id) as guest:
        if await _uid(guest, username) is not None:
            raise HTTPException(409, tr(f"User '{username}' sudah ada di dalam VM. Hapus centang \"buat di dalam VM\" "
                                        "untuk mendaftarkan user itu, atau pakai Reset password",
                                        f"User '{username}' already exists inside the VM. Untick \"create inside the VM\" "
                                        "to register that user, or use Reset password"))
        await _create(guest, username, password)


async def reset_password(host_name: str, vm_id: str, username: str, password: str) -> None:
    async with _guest(host_name, vm_id) as guest:
        uid = await _uid(guest, username)
        if uid is None:
            raise HTTPException(404, tr(f"User '{username}' tidak ada di dalam VM",
                                        f"User '{username}' does not exist inside the VM"))
        _regular(username, uid)
        await guest.set_password(username, password)


async def ensure_user(host_name: str, vm_id: str, username: str, password: str) -> str:
    """Buat user kalau belum ada, kalau sudah ada ganti password-nya. Return 'created' | 'updated'."""
    async with _guest(host_name, vm_id) as guest:
        uid = await _uid(guest, username)
        if uid is None:
            await _create(guest, username, password)
            return "created"
        _regular(username, uid)
        await guest.set_password(username, password)
        return "updated"


async def delete_user(host_name: str, vm_id: str, username: str) -> bool:
    """Hapus user beserta folder home-nya. False kalau user memang sudah tidak ada."""
    async with _guest(host_name, vm_id) as guest:
        uid = await _uid(guest, username)
        if uid is None:
            return False
        _regular(username, uid)
        r = await guest.run(["userdel", "--remove", username])
    if r["exitcode"] == 8:
        raise HTTPException(409, tr(f"User '{username}' masih login di dalam VM. Minta keluar dulu, lalu coba lagi",
                                    f"User '{username}' is still signed in inside the VM. Ask them to sign out, then try again"))
    if r["exitcode"] not in (0, 12):           # 12: user terhapus, tapi folder home gagal dihapus
        raise HTTPException(502, tr(f"userdel gagal: {_output(r)}", f"userdel failed: {_output(r)}"))
    return True


async def users_in_use(host_name: str, vm_id: str) -> dict[str, str]:
    """{username: dipakai untuk apa} untuk user yang tidak boleh dihapus dari dalam VM."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        main = await conn.fetchval(
            "SELECT username FROM vm_credentials WHERE vm_id = $1 AND host_name = $2", vm_id, host_name)
        groups = await conn.fetch(
            "SELECT DISTINCT os_username FROM group_vm_access WHERE vm_id = $1 AND host_name = $2 "
            "AND auth_mode = 'credentials' AND os_username IS NOT NULL", vm_id, host_name)
    used = {r["os_username"]: "kredensial grup" for r in groups}
    if main:
        used[main] = "Login Connect VM ini"
    return used


async def update_stored_password(host_name: str, vm_id: str, username: str, password: str) -> list[str]:
    """Samakan semua password tersimpan untuk user ini di VM ini, lalu perbarui koneksi Guacamole-nya.
    Return daftar tempat yang ikut diperbarui (untuk ditampilkan ke admin)."""
    from services.guac_sync import sync_os_account_connection, sync_vm_connection

    enc = encrypt_secret(password)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            vm_name = await conn.fetchval("SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2", vm_id, host_name)
            main = await conn.fetchrow("""
                UPDATE vm_credentials SET password_enc = $3, updated_at = NOW()
                WHERE vm_id = $1 AND host_name = $2 AND username = $4
                RETURNING os_type, guac_protocol, ssh_host, ssh_port, username, pkey_enc""",
                vm_id, host_name, enc, username)
            accounts = await conn.fetch("""
                UPDATE vm_os_accounts SET password_enc = $3, updated_at = NOW()
                WHERE vm_id = $1 AND host_name = $2 AND os_username = $4
                RETURNING os_type, guac_protocol, ssh_host, ssh_port, os_username, pkey_enc""",
                vm_id, host_name, enc, username)
            groups = await conn.fetch("""
                UPDATE group_vm_access SET os_password_enc = $3
                WHERE vm_id = $1 AND host_name = $2 AND auth_mode = 'credentials' AND os_username = $4
                RETURNING group_id""", vm_id, host_name, enc, username)
            meta = await conn.fetchval("""
                UPDATE vm_metadata SET vm_password_enc = $3, updated_at = NOW()
                WHERE vm_id = $1 AND host_name = $2 AND vm_username = $4 RETURNING 1""",
                vm_id, host_name, enc, username)

    def creds(row, user_field):
        return {"os_type": row["os_type"], "guac_protocol": row["guac_protocol"], "ssh_host": row["ssh_host"],
                "ssh_port": row["ssh_port"], "username": row[user_field], "password": password,
                "pkey": decrypt_secret(row["pkey_enc"]) if row["pkey_enc"] else ""}

    # Koneksi grup dibangun ulang setiap kali Connect, jadi cukup koneksi utama dan akun OS yang disinkronkan.
    if vm_name and main and main["ssh_host"]:
        asyncio.create_task(sync_vm_connection(host_name, vm_name, vm_id, creds(main, "username")))
    for acc in accounts if vm_name else []:
        asyncio.create_task(sync_os_account_connection(host_name, vm_name, username, creds(acc, "os_username")))

    places = []
    if main:
        places.append(tr("Login Connect", "Connect login"))
    if accounts:
        places.append(tr("Akun OS", "OS account"))
    if groups:
        places.append(tr(f"Kredensial grup ({len(groups)})", f"Group credentials ({len(groups)})"))
    if meta:
        places.append(tr("Data VM", "VM data"))
    return places
