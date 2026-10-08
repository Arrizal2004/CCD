"""
Akun OS di dalam VM lewat QEMU Guest Agent: reset password, buat dan hapus user, kredensial grup,
dan jalur cadangan SSH saat agent dibatasi SELinux. Proxmox dan SSH diganti tiruan yang mencatat
perintah, jadi test tidak menyentuh VM sungguhan.
"""
import re
import uuid

import asyncpg
import pytest

from services import guest_accounts, proxmox_instances
from services.proxmox_client import ProxmoxError
from services.ssh_client import decrypt_secret, encrypt_secret
from tests.conftest import DATABASE_URL, _run, auth

LABEL, HOST, VM = "labAkun", "labAkun__pve", "501"
BASE = f"/api/v1/ssh-creds"
PW_RE = re.compile(r"^[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}$")


class FakePve:
    """Tiruan satu VM Linux di Proxmox: daftar user, password, dan perintah yang dijalankan, lewat
    Guest Agent maupun SSH. selinux=True meniru openSUSE Leap 16: agent boleh getent, tapi useradd,
    userdel, dan ganti password ditolak."""

    def __init__(self):
        self.users = {"ujiadmin": 1001, "daemon": 1, "nobody": 65534}
        self.passwords = {}
        self.commands = []                  # lewat agent
        self.ssh_log = []                   # (argv, stdin) lewat SSH
        self.running, self.agent, self.ostype = True, True, "l26"
        self.selinux, self.nopasswd, self.sudo_password = False, True, "Password-Lama-1"
        self.logged_in = set()

    async def get_vm_config(self, node, vmid):
        return {"ostype": self.ostype, "agent": "1"}

    async def get_vm_status(self, node, vmid):
        return {"status": "running" if self.running else "stopped"}

    async def agent_ping(self, node, vmid):
        if not self.agent:
            raise ProxmoxError(500, "QEMU guest agent is not running")

    async def agent_exec(self, node, vmid, command, timeout=30):
        if self.selinux and command[0] != "getent":
            raise ProxmoxError(596, "")
        self.commands.append(command)
        return self._run(command)

    async def agent_set_user_password(self, node, vmid, username, password):
        if self.selinux:
            raise ProxmoxError(500, "chpasswd: PAM: Authentication failure")
        if username not in self.users:
            raise ProxmoxError(500, "user does not exist")
        self.passwords[username] = password

    def ssh_exec(self, argv, stdin):
        self.ssh_log.append((argv, stdin))
        if argv == ["sudo", "-n", "true"]:
            return self._result(0 if self.nopasswd else 1)
        if argv[:2] == ["sudo", "-n"]:
            argv = argv[2:]
        elif argv[:5] == ["sudo", "-k", "-S", "-p", ""]:
            given, _, stdin = stdin.partition("\n")
            if given != self.sudo_password:
                return self._result(1, "Sorry, try again.")
            argv = argv[5:]
        else:
            raise AssertionError(f"perintah SSH tanpa sudo: {argv}")
        if argv == ["chpasswd"]:
            user, _, password = stdin.rstrip("\n").partition(":")
            if user not in self.users:
                return self._result(1, f"chpasswd: user '{user}' does not exist")
            self.passwords[user] = password
            return self._result(0)
        return self._run(argv)

    @staticmethod
    def _result(code, err="", out=""):
        return {"exitcode": code, "out": out, "err": err}

    def _run(self, command):
        prog, name = command[0], command[-1]
        if prog == "getent":
            uid = self.users.get(name)
            return self._result(2) if uid is None else self._result(0, out=f"{name}:x:{uid}:{uid}::/home/{name}:/bin/bash\n")
        if prog == "useradd":
            if name in self.users:
                return self._result(9, f"useradd: user '{name}' already exists")
            self.users[name] = 1000 + len(self.users)
            return self._result(0)
        if prog == "userdel":
            if name in self.logged_in:
                return self._result(8, f"userdel: user {name} is currently used by process 42")
            self.users.pop(name, None)
            self.passwords.pop(name, None)
            return self._result(0)
        raise AssertionError(f"perintah tak terduga: {command}")


class FakeConn:
    closed = False

    def close(self):
        self.closed = True


async def _sql(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchval(query, *args)
    finally:
        await conn.close()


async def _reset_rows():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for table in ("vm_os_accounts", "group_vm_access", "vm_credentials", "vm_metadata"):
            await conn.execute(f"DELETE FROM {table} WHERE host_name = $1", HOST)
        await conn.execute(
            "INSERT INTO vm_credentials (vm_id, host_name, os_type, cred_type, guac_protocol, ssh_host, ssh_port, username, password_enc) "
            "VALUES ($1, $2, 'linux', 'ssh', 'ssh', '10.9.9.9', 22, 'ujiadmin', $3)", VM, HOST, encrypt_secret("Password-Lama-1"))
    finally:
        await conn.close()


@pytest.fixture
def pve(monkeypatch):
    fake = FakePve()

    async def get_client(label):
        if label != LABEL:
            raise ValueError(label)
        return fake

    monkeypatch.setattr(proxmox_instances, "get_client", get_client)
    conns = []
    monkeypatch.setattr(guest_accounts, "_ssh_connect", lambda ssh: conns.append(FakeConn()) or conns[-1])
    monkeypatch.setattr(guest_accounts, "_ssh_exec", lambda conn, argv, stdin: fake.ssh_exec(argv, stdin))
    fake.conns = conns
    _run(_reset_rows())
    yield fake
    _run(_reset_rows())


@pytest.fixture
def group(client, sysadmin_token):
    r = client.post("/api/v1/groups", headers=auth(sysadmin_token), json={"name": "tst-akun-" + uuid.uuid4().hex[:6]})
    gid = r.json()["id"]
    yield gid
    client.delete(f"/api/v1/groups/{gid}", headers=auth(sysadmin_token))


def _stored(table, column, where_user_col, username):
    enc = _run(_sql(f"SELECT {column} FROM {table} WHERE vm_id = $1 AND host_name = $2 AND {where_user_col} = $3",
                    VM, HOST, username))
    return decrypt_secret(enc) if enc else None


def _reset(client, token, **body):
    return client.post(f"{BASE}/vm/{HOST}/{VM}/reset-password", headers=auth(token), json=body)


def _add_account(client, token, **body):
    return client.post(f"{BASE}/vm-os-accounts/{HOST}/{VM}", headers=auth(token), json=body)


# ── Reset password ───────────────────────────────────────────────────────────

def test_reset_main_login_generates_password_and_updates_store(client, sysadmin_token, student_token, pve):
    assert _reset(client, student_token).status_code == 403
    r = _reset(client, sysadmin_token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "ujiadmin" and PW_RE.match(body["password"])
    assert pve.passwords["ujiadmin"] == body["password"]
    assert body["updated"] == ["Login Connect"]
    assert _stored("vm_credentials", "password_enc", "username", "ujiadmin") == body["password"]
    shown = client.get(f"{BASE}/vm/{HOST}/{VM}/password", headers=auth(sysadmin_token)).json()["password"]
    assert shown == body["password"]


def test_reset_with_chosen_password(client, sysadmin_token, pve):
    r = _reset(client, sysadmin_token, username="ujiadmin", password="Pilihan-Admin-9")
    assert r.status_code == 200 and pve.passwords["ujiadmin"] == "Pilihan-Admin-9"
    assert _reset(client, sysadmin_token, password="pendek").status_code == 400
    assert _reset(client, sysadmin_token, password="baris\nbaru-panjang").status_code == 400


@pytest.mark.parametrize("username,code", [
    ("root", 400), ("daemon", 400), ("nobody", 400),       # root dan akun sistem tidak boleh
    ("a;reboot", 400), ("Budi Santoso", 400),               # bukan username Linux yang valid
    ("tidakada", 404),
])
def test_reset_refuses_unsafe_or_missing_users(client, sysadmin_token, pve, username, code):
    assert _reset(client, sysadmin_token, username=username).status_code == code
    assert username not in pve.passwords
    assert all(";" not in arg for cmd in pve.commands for arg in cmd)


def test_reset_needs_running_linux_vm(client, sysadmin_token, pve):
    pve.running = False
    r = _reset(client, sysadmin_token)
    assert r.status_code == 409 and "menyala" in r.json()["detail"]
    pve.running, pve.ostype = True, "win11"
    assert _reset(client, sysadmin_token).status_code == 400
    assert client.post(f"{BASE}/vm/lain__pve/{VM}/reset-password", headers=auth(sysadmin_token),
                       json={"username": "ujiadmin"}).status_code == 404
    assert client.post(f"{BASE}/vm/HV01/{VM}/reset-password", headers=auth(sysadmin_token),
                       json={"username": "ujiadmin"}).status_code == 400
    assert pve.passwords == {} and pve.ssh_log == []


# ── Cadangan SSH (agent mati atau dibatasi SELinux) ─────────────────────────

def test_selinux_guest_falls_back_to_ssh_with_sudo(client, sysadmin_token, pve):
    pve.selinux = True
    r = _add_account(client, sysadmin_token, os_username="budi", create_in_vm=True)
    assert r.status_code == 200, r.text
    password = r.json()["password"]
    assert pve.passwords["budi"] == password
    ssh_cmds = [argv for argv, _ in pve.ssh_log]
    assert ["sudo", "-n", "useradd", "--create-home", "--shell", "/bin/bash", "budi"] in ssh_cmds
    # Password lewat stdin chpasswd, tidak pernah sebagai argumen perintah.
    assert (["sudo", "-n", "chpasswd"], f"budi:{password}\n") in pve.ssh_log
    assert all(password not in arg for argv in ssh_cmds for arg in argv)
    assert pve.conns and all(c.closed for c in pve.conns)

    r = _reset(client, sysadmin_token, username="budi")
    assert r.status_code == 200 and pve.passwords["budi"] == r.json()["password"]


def test_ssh_fallback_with_sudo_password(client, sysadmin_token, pve):
    pve.selinux, pve.nopasswd = True, False
    r = _reset(client, sysadmin_token, username="ujiadmin", password="Baru-Sekali-77")
    assert r.status_code == 200, r.text
    assert (["sudo", "-k", "-S", "-p", "", "chpasswd"], "Password-Lama-1\nujiadmin:Baru-Sekali-77\n") in pve.ssh_log
    assert pve.passwords["ujiadmin"] == "Baru-Sekali-77"
    # Login Connect ikut diperbarui, jadi cadangan SSH berikutnya memakai password baru.
    pve.sudo_password = "Baru-Sekali-77"
    assert _reset(client, sysadmin_token, username="ujiadmin").status_code == 200


def test_agent_down_uses_ssh_and_needs_login_connect(client, sysadmin_token, pve):
    pve.agent = False
    assert _reset(client, sysadmin_token, username="ujiadmin").status_code == 200
    assert pve.commands == [] and pve.ssh_log
    _run(_sql("DELETE FROM vm_credentials WHERE host_name = $1 RETURNING 1", HOST))
    r = _reset(client, sysadmin_token, username="ujiadmin", password="Apa-Saja-123")
    assert r.status_code == 409 and "Login Connect" in r.json()["detail"]


# ── Akun OS ──────────────────────────────────────────────────────────────────

def test_create_os_account_inside_vm(client, sysadmin_token, pve):
    r = _add_account(client, sysadmin_token, os_username="budi", create_in_vm=True)
    assert r.status_code == 200, r.text
    password = r.json()["password"]
    assert PW_RE.match(password) and r.json()["created_in_vm"] is True
    assert ["useradd", "--create-home", "--shell", "/bin/bash", "budi"] in pve.commands
    assert pve.passwords["budi"] == password
    assert _stored("vm_os_accounts", "password_enc", "os_username", "budi") == password

    useradds = sum(cmd[0] == "useradd" for cmd in pve.commands)
    assert _add_account(client, sysadmin_token, os_username="budi", create_in_vm=True).status_code == 409
    assert sum(cmd[0] == "useradd" for cmd in pve.commands) == useradds      # ditolak sebelum menyentuh VM


def test_create_refuses_existing_vm_user_and_bad_input(client, sysadmin_token, pve):
    r = _add_account(client, sysadmin_token, os_username="ujiadmin", create_in_vm=True)
    assert r.status_code == 409 and "sudah ada di dalam VM" in r.json()["detail"]
    assert _run(_sql("SELECT COUNT(*) FROM vm_os_accounts WHERE host_name = $1", HOST)) == 0
    assert _add_account(client, sysadmin_token, os_username="Budi Santoso", create_in_vm=True).status_code == 400
    assert _add_account(client, sysadmin_token, os_username="sinta", create_in_vm=True, password="pendek").status_code == 400
    # Tanpa create_in_vm: perilaku lama, password atau key tetap wajib.
    assert _add_account(client, sysadmin_token, os_username="sinta").status_code == 400
    assert _add_account(client, sysadmin_token, os_username="sinta", password="Sudah-Ada-123").status_code == 200
    assert "sinta" not in pve.users


def test_reset_updates_every_stored_copy(client, sysadmin_token, pve, group):
    _add_account(client, sysadmin_token, os_username="budi", create_in_vm=True)
    r = client.post(f"/api/v1/groups/{group}/vms", headers=auth(sysadmin_token), json={
        "vm_id": VM, "host_name": HOST, "auth_mode": "credentials", "os_username": "budi", "os_password": "Grup-Lama-123"})
    assert r.status_code == 201, r.text

    r = _reset(client, sysadmin_token, username="budi")
    assert r.status_code == 200
    new = r.json()["password"]
    assert r.json()["updated"] == ["Akun OS", "Kredensial grup (1)"]
    assert _stored("vm_os_accounts", "password_enc", "os_username", "budi") == new
    assert _stored("group_vm_access", "os_password_enc", "os_username", "budi") == new
    assert _stored("vm_credentials", "password_enc", "username", "ujiadmin") == "Password-Lama-1"   # user lain tidak berubah


def test_delete_os_account_inside_vm(client, sysadmin_token, pve):
    acc = _add_account(client, sysadmin_token, os_username="budi", create_in_vm=True).json()["id"]
    url = f"{BASE}/vm-os-accounts/{HOST}/{VM}/{acc}"

    pve.logged_in.add("budi")
    r = client.delete(url, params={"remove_in_vm": True}, headers=auth(sysadmin_token))
    assert r.status_code == 409 and "masih login" in r.json()["detail"]
    assert _run(_sql("SELECT COUNT(*) FROM vm_os_accounts WHERE id = $1", acc)) == 1     # akun dashboard tetap ada

    pve.logged_in.clear()
    r = client.delete(url, params={"remove_in_vm": True}, headers=auth(sysadmin_token))
    assert r.status_code == 200 and r.json()["removed_in_vm"] is True
    assert ["userdel", "--remove", "budi"] in pve.commands and "budi" not in pve.users


def test_delete_keeps_vm_user_still_in_use(client, sysadmin_token, pve):
    acc = _add_account(client, sysadmin_token, os_username="ujiadmin", password="Sama-Dengan-Login1").json()["id"]
    r = client.delete(f"{BASE}/vm-os-accounts/{HOST}/{VM}/{acc}", params={"remove_in_vm": True}, headers=auth(sysadmin_token))
    assert r.status_code == 400 and "Login Connect" in r.json()["detail"]
    assert "ujiadmin" in pve.users
    # Tanpa remove_in_vm hanya akun di dashboard yang dihapus.
    r = client.delete(f"{BASE}/vm-os-accounts/{HOST}/{VM}/{acc}", headers=auth(sysadmin_token))
    assert r.status_code == 200 and r.json()["removed_in_vm"] is False and "ujiadmin" in pve.users


# ── Kredensial grup ──────────────────────────────────────────────────────────

def test_group_credentials_applied_in_vm(client, sysadmin_token, pve, group):
    url = f"/api/v1/groups/{group}/vms"
    base = {"vm_id": VM, "host_name": HOST, "auth_mode": "credentials", "os_username": "kelas"}
    r = client.post(url, headers=auth(sysadmin_token), json={**base, "os_password": "Kelas-Pass-1", "apply_in_vm": True})
    assert r.status_code == 201, r.text
    assert pve.passwords["kelas"] == "Kelas-Pass-1"
    assert _stored("group_vm_access", "os_password_enc", "os_username", "kelas") == "Kelas-Pass-1"

    # Edit tanpa password: password tersimpan tidak berubah (dulu ditolak walau form bilang boleh kosong).
    r = client.put(url, headers=auth(sysadmin_token), json={**base, "guac_protocol": "ssh"})
    assert r.status_code == 200, r.text
    assert _stored("group_vm_access", "os_password_enc", "os_username", "kelas") == "Kelas-Pass-1"
    assert client.put(url, headers=auth(sysadmin_token), json={**base, "apply_in_vm": True}).status_code == 400
    assert client.put(url, headers=auth(sysadmin_token), json={**base, "os_username": "lain"}).status_code == 400

    r = client.put(url, headers=auth(sysadmin_token), json={**base, "os_password": "Kelas-Baru-22", "apply_in_vm": True})
    assert r.status_code == 200 and pve.passwords["kelas"] == "Kelas-Baru-22"


def test_group_without_apply_does_not_touch_vm(client, sysadmin_token, pve, group):
    r = client.post(f"/api/v1/groups/{group}/vms", headers=auth(sysadmin_token), json={
        "vm_id": VM, "host_name": HOST, "auth_mode": "credentials", "os_username": "kelas", "os_password": "Kelas-Pass-1"})
    assert r.status_code == 201 and pve.commands == []


def test_student_cannot_read_credentials_of_other_vm(client, student_token, pve):
    assert client.get(f"{BASE}/my-vm-cred/{HOST}/{VM}", headers=auth(student_token)).status_code == 403
