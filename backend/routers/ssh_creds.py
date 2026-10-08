"""
CRUD SSH/PS credentials untuk host (fallback) dan VM (terminal + otomasi).
Semua password/key disimpan terenkripsi di PostgreSQL.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

import asyncio
from auth import get_current_user, require_sysadmin, Role
from database import get_pool
from services.ssh_client import encrypt_secret, decrypt_secret, SshClient
from services.vm_credentials import save_vm_credentials
from services import guest_accounts
from services.guac_sync import (
    sync_vm_connection, delete_vm_connection, grant_vm_to_all_admins,
    sync_os_account_connection, delete_os_account_connection,
    get_connection_url_for_os_account, grant_connection, _find_connection_id,
    sync_mandiri_connection, sync_group_connection,
    get_connection_url_by_name, _conn_name_mandiri, _conn_name_group,
)
from i18n import tr

router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────────────────

class HostSshCredBody(BaseModel):
    ssh_host:  str
    ssh_port:  int = 22
    ssh_user:  str = "Administrator"
    password:  Optional[str] = None
    pkey:      Optional[str] = None  # private key PEM string


class VmCredBody(BaseModel):
    os_type:       str = "linux"         # "linux" | "windows"
    cred_type:     str = "ssh"           # "ssh" | "ps_direct"
    guac_protocol: Optional[str] = None  # "ssh" | "rdp" — protokol Quick-Connect Guacamole
    ssh_host:      str                   # IP/hostname VM
    ssh_port:      int = 22
    username:      str = "root"
    password:      Optional[str] = None
    pkey:          Optional[str] = None


class VmOsAccountBody(BaseModel):
    os_username:  str
    password:     Optional[str] = None
    pkey:         Optional[str] = None
    create_in_vm: bool = False           # buat juga user-nya di dalam VM lewat QEMU Guest Agent


class ResetPasswordBody(BaseModel):
    username: Optional[str] = None       # kosong = user Login Connect VM ini
    password: Optional[str] = None       # kosong = dibuatkan acak


class TestResult(BaseModel):
    ok:    bool
    error: str = ""


# ── Helper ─────────────────────────────────────────────────────────────────────

def _require_admin(user: dict):
    if user["role"] not in (Role.SUPERADMIN, Role.SYSADMIN):
        raise HTTPException(status_code=403, detail=tr("Aksi ini hanya untuk admin/sysadmin",
                                                       "Only admins/sysadmins can do this"))


# ── Host SSH Credentials ───────────────────────────────────────────────────────

@router.get("/host/{host_name}")
async def get_host_ssh(host_name: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT ssh_host, ssh_port, ssh_user, "
            "  (password_enc IS NOT NULL) AS has_password, "
            "  (pkey_enc IS NOT NULL) AS has_pkey, "
            "  created_at, updated_at "
            "FROM host_ssh_credentials WHERE host_name = $1",
            host_name
        )
    if not row:
        raise HTTPException(status_code=404, detail=tr("SSH credentials tidak ditemukan",
                                                       "SSH credentials not found"))
    return dict(row)


@router.put("/host/{host_name}")
async def upsert_host_ssh(
    host_name: str, body: HostSshCredBody,
    user: dict = Depends(get_current_user)
):
    _require_admin(user)
    if not body.password and not body.pkey:
        raise HTTPException(status_code=400, detail=tr("Harus mengisi password atau private key",
                                                       "Enter a password or a private key"))

    password_enc = encrypt_secret(body.password) if body.password else None
    pkey_enc     = encrypt_secret(body.pkey)     if body.pkey     else None

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO host_ssh_credentials
                (host_name, ssh_host, ssh_port, ssh_user, password_enc, pkey_enc, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (host_name) DO UPDATE SET
                ssh_host     = EXCLUDED.ssh_host,
                ssh_port     = EXCLUDED.ssh_port,
                ssh_user     = EXCLUDED.ssh_user,
                password_enc = EXCLUDED.password_enc,
                pkey_enc     = EXCLUDED.pkey_enc,
                updated_at   = NOW()
        """, host_name, body.ssh_host, body.ssh_port, body.ssh_user, password_enc, pkey_enc)

    return {"status": "saved", "host_name": host_name}


@router.delete("/host/{host_name}")
async def delete_host_ssh(host_name: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM host_ssh_credentials WHERE host_name = $1", host_name)
    return {"status": "deleted"}


@router.post("/host/{host_name}/test")
async def test_host_ssh(host_name: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT ssh_host, ssh_port, ssh_user, password_enc, pkey_enc "
            "FROM host_ssh_credentials WHERE host_name = $1",
            host_name
        )
    if not row:
        raise HTTPException(status_code=404, detail=tr("SSH credentials tidak ditemukan",
                                                       "SSH credentials not found"))

    client = _build_client(row)
    ok, err = await client.test_connection()
    return TestResult(ok=ok, error=err)


# ── VM Credentials ─────────────────────────────────────────────────────────────

@router.get("/vm-os/{host_name}")
async def list_vm_os_types(host_name: str, user: dict = Depends(get_current_user)):
    """Return {vm_id: os_type} map untuk semua VM yang sudah dikonfigurasi di host ini."""
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT vm_id, os_type FROM vm_credentials WHERE host_name = $1",
            host_name
        )
    return {r["vm_id"]: r["os_type"] for r in rows}


@router.get("/vm/{host_name}/{vm_id}")
async def get_vm_cred(host_name: str, vm_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT os_type, cred_type, guac_protocol, ssh_host, ssh_port, username, "
            "  (password_enc IS NOT NULL) AS has_password, "
            "  (pkey_enc IS NOT NULL) AS has_pkey, "
            "  created_at, updated_at "
            "FROM vm_credentials WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
    if not row:
        raise HTTPException(status_code=404, detail=tr("VM credentials tidak ditemukan",
                                                       "VM credentials not found"))
    return dict(row)


@router.get("/vm/{host_name}/{vm_id}/password")
async def reveal_vm_password(host_name: str, vm_id: str, request: Request, user: dict = Depends(get_current_user)):
    """Password login VM yang tersimpan (admin). Setiap pembukaan dicatat di audit log."""
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_enc FROM vm_credentials WHERE vm_id = $1 AND host_name = $2", vm_id, host_name)
    if not row or not row["password_enc"]:
        raise HTTPException(status_code=404, detail=tr("Password VM belum tersimpan",
                                                       "No password is stored for this VM"))
    from services.audit import both, log_activity
    await log_activity(user, "CRED_VIEW", "WARNING", {"id": vm_id, "name": host_name},
                       both(lambda: tr(f"{user.get('username')} menampilkan password VM {vm_id} ({host_name})",
                                    f"{user.get('username')} viewed the password of VM {vm_id} ({host_name})")), request)
    return {"password": decrypt_secret(row["password_enc"])}


@router.post("/vm/{host_name}/{vm_id}/reset-password")
async def reset_vm_password(host_name: str, vm_id: str, body: ResetPasswordBody, request: Request,
                            user: dict = Depends(get_current_user)):
    """Ganti password user di dalam VM lewat QEMU Guest Agent (tanpa password lama), lalu samakan
    semua password tersimpan untuk user itu. Password baru dikembalikan sekali untuk diberikan ke pemakai."""
    _require_admin(user)
    username = body.username
    if not username:
        pool = await get_pool()
        async with pool.acquire() as conn:
            username = await conn.fetchval(
                "SELECT username FROM vm_credentials WHERE vm_id = $1 AND host_name = $2", vm_id, host_name)
        if not username:
            raise HTTPException(404, tr("Login Connect VM ini belum diatur. Isi username yang mau direset",
                                        "This VM's Connect login is not set. Enter the username to reset"))
    username = guest_accounts.check_username(username)
    password = guest_accounts.check_password(body.password) if body.password else guest_accounts.generate_password()

    await guest_accounts.reset_password(host_name, vm_id, username, password)
    places = await guest_accounts.update_stored_password(host_name, vm_id, username, password)
    from services.audit import both, log_activity
    await log_activity(user, "VM_PASSWORD_RESET", "WARNING", {"id": vm_id, "name": host_name},
                       both(lambda: tr(f"{user.get('username')} mereset password user '{username}' di dalam VM {vm_id} ({host_name})",
                                    f"{user.get('username')} reset the password of user '{username}' inside VM {vm_id} ({host_name})")), request)
    return {"username": username, "password": password, "updated": places}


@router.put("/vm/{host_name}/{vm_id}")
async def upsert_vm_cred(
    host_name: str, vm_id: str, body: VmCredBody,
    request: Request,
    user: dict = Depends(get_current_user)
):
    _require_admin(user)
    from services.audit import both, log_activity
    await log_activity(user, "CRED_UPDATE", "WARNING",
                       {"id": vm_id, "name": host_name},
                       both(lambda: tr(f"Update credentials VM {vm_id} ({body.os_type}/{body.cred_type})",
                                    f"Updated the credentials of VM {vm_id} ({body.os_type}/{body.cred_type})")), request)
    if body.os_type not in ("linux", "windows"):
        raise HTTPException(status_code=400, detail=tr("os_type harus 'linux' atau 'windows'",
                                                       "os_type must be 'linux' or 'windows'"))
    if body.cred_type not in ("ssh", "ps_direct"):
        raise HTTPException(status_code=400, detail=tr("cred_type harus 'ssh' atau 'ps_direct'",
                                                       "cred_type must be 'ssh' or 'ps_direct'"))
    guac_protocol = (body.guac_protocol or "").lower()
    if guac_protocol not in ("", "ssh", "rdp"):
        raise HTTPException(status_code=400, detail=tr("guac_protocol harus 'ssh' atau 'rdp'",
                                                       "guac_protocol must be 'ssh' or 'rdp'"))
    # Default protokol Guacamole bila user tidak memilih: linux→ssh, windows→rdp
    if not guac_protocol:
        guac_protocol = "ssh" if body.os_type == "linux" else "rdp"
    if not body.password and not body.pkey:
        raise HTTPException(status_code=400, detail=tr("Harus mengisi password atau private key",
                                                       "Enter a password or a private key"))

    await save_vm_credentials(
        host_name, vm_id, os_type=body.os_type, cred_type=body.cred_type, guac_protocol=guac_protocol,
        ssh_host=body.ssh_host, ssh_port=body.ssh_port, username=body.username,
        password=body.password or "", pkey=body.pkey or "")
    return {"status": "saved", "vm_id": vm_id}


@router.delete("/vm/{host_name}/{vm_id}")
async def delete_vm_cred(host_name: str, vm_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        vm_name_row = await conn.fetchrow(
            "SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
        await conn.execute(
            "DELETE FROM vm_credentials WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
    if vm_name_row:
        asyncio.create_task(delete_vm_connection(host_name, vm_name_row["vm_name"]))
    return {"status": "deleted"}


@router.post("/vm/{host_name}/{vm_id}/test")
async def test_vm_cred(host_name: str, vm_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT cred_type, ssh_host, ssh_port, username, password_enc, pkey_enc "
            "FROM vm_credentials WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
    if not row:
        raise HTTPException(status_code=404, detail=tr("VM credentials tidak ditemukan",
                                                       "VM credentials not found"))
    if row["cred_type"] != "ssh":
        raise HTTPException(status_code=400, detail=tr("Test hanya untuk tipe 'ssh'",
                                                       "The test only works for type 'ssh'"))

    client = _build_client(row, host_field="ssh_host", user_field="username")
    ok, err = await client.test_connection()
    return TestResult(ok=ok, error=err)


# ── Internal helper ────────────────────────────────────────────────────────────

def _build_client(
    row, host_field: str = "ssh_host", user_field: str = "ssh_user"
) -> SshClient:
    try:
        password = decrypt_secret(row["password_enc"]) if row.get("password_enc") else None
        pkey     = decrypt_secret(row["pkey_enc"])     if row.get("pkey_enc")     else None
    except Exception:
        raise HTTPException(
            status_code=422,
            detail=tr("Credentials SSH tidak dapat didekripsi — kemungkinan JWT_SECRET berubah sejak "
                      "credentials disimpan. Silakan konfigurasi ulang credentials SSH VM ini via tab Koneksi.",
                      "The SSH credentials cannot be decrypted — JWT_SECRET has probably changed since "
                      "they were saved. Please set this VM's SSH credentials again.")
        )
    return SshClient(
        host=row[host_field],
        port=row["ssh_port"],
        username=row[user_field],
        password=password,
        pkey_str=pkey,
    )


async def get_vm_ssh_client(vm_id: str, host_name: str) -> SshClient:
    """Ambil VM credentials dari DB dan buat SshClient. Raise 404 jika tidak ada."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT cred_type, ssh_host, ssh_port, username, password_enc, pkey_enc "
            "FROM vm_credentials WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
    if not row:
        raise HTTPException(
            status_code=404,
            detail=tr("SSH credentials VM belum dikonfigurasi. "
                      "Konfigurasi via tab 'Koneksi' pada VM detail modal.",
                      "SSH credentials for this VM are not configured. "
                      "Set them in the VM details window.")
        )
    return _build_client(row, host_field="ssh_host", user_field="username")


async def get_host_ssh_client(host_name: str) -> SshClient | None:
    """Ambil host SSH credentials dari DB. Return None jika tidak ada."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT ssh_host, ssh_port, ssh_user, password_enc, pkey_enc "
            "FROM host_ssh_credentials WHERE host_name = $1",
            host_name
        )
    if not row:
        return None
    return _build_client(row, host_field="ssh_host", user_field="ssh_user")


# ── VM OS Accounts (multi-user per VM) ───────────────────────────────────────

@router.get("/vm-os-accounts/{host_name}/{vm_id}")
async def list_vm_os_accounts(host_name: str, vm_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, label, os_type, guac_protocol, ssh_host, ssh_port, os_username,
                      (password_enc IS NOT NULL) AS has_password,
                      (pkey_enc IS NOT NULL) AS has_pkey,
                      created_at, updated_at
               FROM vm_os_accounts WHERE vm_id = $1 AND host_name = $2
               ORDER BY os_username""",
            vm_id, host_name
        )
    return [dict(r) for r in rows]


@router.post("/vm-os-accounts/{host_name}/{vm_id}")
async def create_vm_os_account(
    host_name: str, vm_id: str, body: VmOsAccountBody,
    request: Request, user: dict = Depends(get_current_user)
):
    """Daftarkan akun OS untuk VM ini. Dengan create_in_vm, user-nya sekaligus dibuat di dalam VM lewat
    QEMU Guest Agent; password kosong berarti dibuatkan acak dan dikembalikan sekali di respons."""
    _require_admin(user)
    from services.audit import both, log_activity
    username = body.os_username.strip()
    password = body.password
    generated = False
    if body.create_in_vm:
        username = guest_accounts.check_username(username)
        if body.pkey:
            raise HTTPException(400, tr("User yang dibuat di dalam VM memakai password, bukan private key",
                                        "Users created inside the VM use a password, not a private key"))
        if password:
            guest_accounts.check_password(password)
        else:
            password, generated = guest_accounts.generate_password(), True
    else:
        if not username:
            raise HTTPException(400, tr("os_username wajib diisi", "os_username is required"))
        if not body.password and not body.pkey:
            raise HTTPException(400, tr("Harus mengisi password atau private key",
                                        "Enter a password or a private key"))

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Ambil ssh_host, ssh_port, os_type dari vm_credentials utama
        main_cred = await conn.fetchrow(
            "SELECT ssh_host, ssh_port, os_type FROM vm_credentials WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
        taken = await conn.fetchval(
            "SELECT 1 FROM vm_os_accounts WHERE vm_id = $1 AND host_name = $2 AND os_username = $3",
            vm_id, host_name, username)
    if not main_cred:
        raise HTTPException(404, tr("Credentials utama VM belum dikonfigurasi. "
                                    "Simpan dulu di tab 'Koneksi' sebelum menambah OS account.",
                                    "The VM's main credentials are not configured. "
                                    "Save them first before adding an OS account."))
    if taken:
        raise HTTPException(409, tr(f"OS username '{username}' sudah ada untuk VM ini",
                                    f"OS username '{username}' already exists for this VM"))

    # User dibuat di VM dulu: kalau gagal, tidak ada akun di dashboard yang password-nya tidak berlaku.
    if body.create_in_vm:
        await guest_accounts.create_user(host_name, vm_id, username, password)

    os_type       = main_cred["os_type"] or "linux"
    ssh_host      = main_cred["ssh_host"]
    ssh_port      = main_cred["ssh_port"]
    guac_protocol = "ssh" if os_type == "linux" else "rdp"
    password_enc  = encrypt_secret(password) if password else None
    pkey_enc      = encrypt_secret(body.pkey) if body.pkey else None

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """INSERT INTO vm_os_accounts
                       (vm_id, host_name, label, os_type, guac_protocol,
                        ssh_host, ssh_port, os_username, password_enc, pkey_enc)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   RETURNING id, os_username, label""",
                vm_id, host_name, "", os_type, guac_protocol,
                ssh_host, ssh_port, username,
                password_enc, pkey_enc
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, tr(f"OS username '{username}' sudah ada untuk VM ini",
                                            f"OS username '{username}' already exists for this VM"))
            raise

        vm_row = await conn.fetchrow(
            "SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )

    def _detail():
        where = tr(" (user dibuat di dalam VM)", " (user created inside the VM)") if body.create_in_vm else ""
        return tr(f"Tambah OS account '{username}' untuk VM {vm_id}{where}", f"Added the OS account '{username}' for VM {vm_id}{where}")
    await log_activity(user, "OS_ACCOUNT_CREATE", "WARNING",
                       {"id": vm_id, "name": host_name}, both(_detail), request)

    if vm_row:
        creds = {
            "os_type": os_type, "guac_protocol": guac_protocol,
            "ssh_host": ssh_host, "ssh_port": ssh_port,
            "username": username,
            "password": password or "", "pkey": body.pkey or "",
        }
        async def _setup_os_account(h, vn, uname, c):
            conn_id = await sync_os_account_connection(h, vn, uname, c)
            if conn_id:
                await grant_vm_to_all_admins(conn_id)
        asyncio.create_task(
            _setup_os_account(host_name, vm_row["vm_name"], username, creds)
        )

    return {"id": row["id"], "os_username": row["os_username"], "label": row["label"],
            "ssh_host": ssh_host, "ssh_port": ssh_port, "os_type": os_type,
            "created_in_vm": body.create_in_vm, "password": password if generated else None}


@router.put("/vm-os-accounts/{host_name}/{vm_id}/{account_id}")
async def update_vm_os_account(
    host_name: str, vm_id: str, account_id: int, body: VmOsAccountBody,
    request: Request, user: dict = Depends(get_current_user)
):
    _require_admin(user)
    from services.audit import both, log_activity
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT os_username FROM vm_os_accounts WHERE id = $1 AND vm_id = $2 AND host_name = $3",
            account_id, vm_id, host_name
        )
        if not existing:
            raise HTTPException(404, tr("OS account tidak ditemukan", "OS account not found"))

        main_cred = await conn.fetchrow(
            "SELECT ssh_host, ssh_port, os_type FROM vm_credentials WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )

        updates: list = ["updated_at=NOW()"]
        values = []

        if body.password:
            updates.append(f"password_enc=${len(values)+1}")
            values.append(encrypt_secret(body.password))
        if body.pkey:
            updates.append(f"pkey_enc=${len(values)+1}")
            values.append(encrypt_secret(body.pkey))

        values.append(account_id)
        await conn.execute(
            f"UPDATE vm_os_accounts SET {', '.join(updates)} WHERE id = ${len(values)}",
            *values
        )
        vm_row = await conn.fetchrow(
            "SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )

    await log_activity(user, "OS_ACCOUNT_UPDATE", "WARNING",
                       {"id": vm_id, "name": host_name},
                       both(lambda: tr(f"Update OS account '{existing['os_username']}' VM {vm_id}",
                                    f"Updated the OS account '{existing['os_username']}' of VM {vm_id}")), request)

    if vm_row and (body.password or body.pkey) and main_cred:
        _os_type = main_cred["os_type"] or "linux"
        _guac_protocol = "ssh" if _os_type == "linux" else "rdp"
        creds = {
            "os_type": _os_type, "guac_protocol": _guac_protocol,
            "ssh_host": main_cred["ssh_host"], "ssh_port": main_cred["ssh_port"],
            "username": existing["os_username"],
            "password": body.password or "", "pkey": body.pkey or "",
        }
        asyncio.create_task(sync_os_account_connection(host_name, vm_row["vm_name"], existing["os_username"], creds))

    return {"status": "updated", "account_id": account_id}


@router.delete("/vm-os-accounts/{host_name}/{vm_id}/{account_id}")
async def delete_vm_os_account(
    host_name: str, vm_id: str, account_id: int,
    request: Request, remove_in_vm: bool = False, user: dict = Depends(get_current_user)
):
    """Hapus akun OS dari dashboard. Dengan remove_in_vm, user-nya (beserta folder home) ikut dihapus
    dari dalam VM, kecuali user itu masih dipakai untuk Login Connect atau kredensial grup."""
    _require_admin(user)
    from services.audit import both, log_activity
    pool = await get_pool()
    async with pool.acquire() as conn:
        acc = await conn.fetchrow(
            "SELECT os_username FROM vm_os_accounts WHERE id = $1 AND vm_id = $2 AND host_name = $3",
            account_id, vm_id, host_name
        )
    if not acc:
        raise HTTPException(404, tr("OS account tidak ditemukan", "OS account not found"))

    removed = False
    if remove_in_vm:
        in_use = (await guest_accounts.users_in_use(host_name, vm_id)).get(acc["os_username"])
        if in_use:
            raise HTTPException(400, tr(f"User '{acc['os_username']}' masih dipakai untuk {in_use}, "
                                        "jadi tidak dihapus dari dalam VM",
                                        f"User '{acc['os_username']}' is still used for {in_use}, "
                                        "so it was not removed from inside the VM"))
        removed = await guest_accounts.delete_user(host_name, vm_id, guest_accounts.check_username(acc["os_username"]))

    async with pool.acquire() as conn:
        vm_row = await conn.fetchrow(
            "SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
        await conn.execute("DELETE FROM vm_os_accounts WHERE id = $1", account_id)

    def _detail():
        where = (tr(" (user dan folder home-nya dihapus dari dalam VM)", " (the user and its home folder were deleted inside the VM)")
                 if removed else "")
        return tr(f"Hapus OS account '{acc['os_username']}' VM {vm_id}{where}",
                  f"Deleted the OS account '{acc['os_username']}' of VM {vm_id}{where}")
    await log_activity(user, "OS_ACCOUNT_DELETE", "WARNING",
                       {"id": vm_id, "name": host_name}, both(_detail), request)

    if vm_row:
        asyncio.create_task(delete_os_account_connection(host_name, vm_row["vm_name"], acc["os_username"]))

    return {"status": "deleted", "removed_in_vm": removed}


# ── Guacamole Quick-Connect URL ───────────────────────────────────────────────

def _vm_creds(row) -> dict:
    return {
        "os_type":       row["os_type"],
        "guac_protocol": row["guac_protocol"],
        "ssh_host":      row["ssh_host"],
        "ssh_port":      row["ssh_port"],
        "username":      row["username"],
        "password":      decrypt_secret(row["password_enc"]) if row["password_enc"] else "",
        "pkey":          decrypt_secret(row["pkey_enc"])     if row["pkey_enc"]     else "",
    }


async def _main_connection(host_name: str, vm_id: str, vm_name: str, guac_public: str) -> tuple:
    """(url, connection_id) of the VM's main connection, recreating it from vm_credentials when it's
    missing (e.g. credentials saved while Guacamole was refusing writes). (None, None) = no credentials."""
    from services.guac_sync import (
        _conn_name, _find_connection_id, get_connection_url, sync_vm_connection, grant_vm_to_all_admins,
    )
    cid = await _find_connection_id(_conn_name(host_name, vm_name))
    if not cid:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT os_type, cred_type, guac_protocol, ssh_host, ssh_port, username, password_enc, pkey_enc "
                "FROM vm_credentials WHERE vm_id = $1 AND host_name = $2", vm_id, host_name)
        if not row:
            return None, None
        if not row["ssh_host"]:
            raise HTTPException(409, tr("IP VM belum diisi — isi lewat 'Atur kredensial & IP' di detail VM",
                                        "The VM IP is not set — set it with 'Set credentials & IP' in the VM details"))
        cid = await sync_vm_connection(host_name, vm_name, vm_id, _vm_creds(row))
        if not cid:
            raise HTTPException(502, tr("Guacamole menolak membuat koneksi untuk VM ini — cek log backend",
                                        "Guacamole refused to create a connection for this VM — check the backend log"))
        await grant_vm_to_all_admins(cid)
    return await get_connection_url(host_name, vm_name, guac_public), cid


@router.get("/guac-url/{host_name}/{vm_id}")
async def get_guac_url(host_name: str, vm_id: str, user: dict = Depends(get_current_user)):
    """Return URL Guacamole untuk membuka koneksi VM langsung di browser tab baru."""
    import os as _os
    from services.guac_sync import get_connection_url
    pool = await get_pool()
    guac_public = _os.getenv("GUAC_PUBLIC_URL", "").rstrip("/")

    # RBAC: student hanya boleh connect VM yang bisa diakses (direct assignment ATAU via group)
    if user["role"] == Role.STUDENT:
        from database import get_student_vm_ids
        allowed = await get_student_vm_ids(int(user["sub"]), host_name)
        if (vm_id, host_name) not in allowed:
            raise HTTPException(403, tr("Anda tidak punya akses ke VM ini",
                                        "You do not have access to this VM"))

        # Coba ambil OS account dari direct assignment (opsional — bisa None untuk group access)
        async with pool.acquire() as conn:
            assignment = await conn.fetchrow(
                """SELECT va.os_account_id, voa.os_username, voa.ssh_host, voa.ssh_port,
                          voa.os_type, voa.guac_protocol, voa.password_enc, voa.pkey_enc
                   FROM vm_assignments va
                   LEFT JOIN vm_os_accounts voa ON voa.id = va.os_account_id
                   WHERE va.user_id = $1 AND va.vm_id = $2 AND va.host_name = $3
                     AND va.deleted_at IS NULL""",
                int(user["sub"]), vm_id, host_name
            )

        async with pool.acquire() as conn:
            vm_row = await conn.fetchrow(
                "SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2",
                vm_id, host_name
            )
        if not vm_row:
            raise HTTPException(404, tr("VM tidak ditemukan", "VM not found"))

        if assignment and assignment["os_account_id"] and assignment["os_username"]:
            # Per-student OS account (direct assignment)
            os_uname = assignment["os_username"]
            from services.guac_sync import _conn_name_os
            conn_name = _conn_name_os(host_name, vm_row["vm_name"], os_uname)
            cid = await _find_connection_id(conn_name)
            if not cid:
                creds = {
                    "os_type":       assignment["os_type"] or "linux",
                    "guac_protocol": assignment["guac_protocol"] or "",
                    "ssh_host":      assignment["ssh_host"] or "",
                    "ssh_port":      assignment["ssh_port"] or 22,
                    "username":      os_uname,
                    "password":      decrypt_secret(assignment["password_enc"]) if assignment["password_enc"] else "",
                    "pkey":          decrypt_secret(assignment["pkey_enc"])     if assignment["pkey_enc"]     else "",
                }
                cid = await sync_os_account_connection(host_name, vm_row["vm_name"], os_uname, creds)
            if cid:
                await grant_connection(user["username"], cid)
            url = await get_connection_url_for_os_account(host_name, vm_row["vm_name"], os_uname, guac_public)
        else:
            # Tidak ada OS account personal — cek auth_mode dari group_vm_access
            async with pool.acquire() as conn:
                group_cred = await conn.fetchrow("""
                    SELECT gva.auth_mode, gva.os_username, gva.os_password_enc,
                           gva.os_type, gva.guac_protocol, gva.group_id,
                           vc.ssh_host, vc.ssh_port
                    FROM group_members gm
                    JOIN group_vm_access gva ON gva.group_id = gm.group_id
                    LEFT JOIN vm_credentials vc
                           ON vc.vm_id = gva.vm_id AND vc.host_name = gva.host_name
                    WHERE gm.user_id = $1 AND gva.vm_id = $2 AND gva.host_name = $3
                    ORDER BY CASE WHEN gva.auth_mode = 'credentials' THEN 0 ELSE 1 END
                    LIMIT 1
                """, int(user["sub"]), vm_id, host_name)

            if group_cred and group_cred["auth_mode"] == "credentials" and group_cred["os_username"]:
                creds = {
                    "os_type":       group_cred["os_type"] or "linux",
                    "guac_protocol": group_cred["guac_protocol"] or "",
                    "ssh_host":      group_cred["ssh_host"] or "",
                    "ssh_port":      group_cred["ssh_port"] or 22,
                    "username":      group_cred["os_username"],
                    "password":      decrypt_secret(group_cred["os_password_enc"]) if group_cred["os_password_enc"] else "",
                }
                cid = await sync_group_connection(host_name, vm_row["vm_name"], group_cred["group_id"], creds)
                if cid:
                    await grant_connection(user["username"], cid)
                url = await get_connection_url_by_name(
                    _conn_name_group(host_name, vm_row["vm_name"], group_cred["group_id"]), guac_public
                )
            elif group_cred and group_cred["auth_mode"] == "mandiri" and group_cred["ssh_host"]:
                # Mandiri: koneksi dengan SSH host dari vm_credentials tapi tanpa username/password
                vm_creds = {
                    "os_type":       group_cred["os_type"] or "linux",
                    "guac_protocol": group_cred["guac_protocol"] or "",
                    "ssh_host":      group_cred["ssh_host"],
                    "ssh_port":      group_cred["ssh_port"] or 22,
                }
                cid = await sync_mandiri_connection(host_name, vm_row["vm_name"], vm_creds)
                if cid:
                    await grant_connection(user["username"], cid)
                url = await get_connection_url_by_name(
                    _conn_name_mandiri(host_name, vm_row["vm_name"]), guac_public
                )
            else:
                # Fallback: koneksi utama VM (default credentials)
                url, cid = await _main_connection(host_name, vm_id, vm_row["vm_name"], guac_public)
                if cid:
                    await grant_connection(user["username"], cid)

        if not url:
            raise HTTPException(404, tr("Koneksi Guacamole belum tersedia. Hubungi admin.",
                                        "The Guacamole connection is not available yet. Contact an administrator."))
        return {"url": url, "vm_name": vm_row["vm_name"]}

    # Admin / sysadmin: pakai koneksi utama VM
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
    if not row:
        raise HTTPException(404, tr("VM tidak ditemukan di history metrics",
                                    "VM not found in the metrics history"))

    url, _ = await _main_connection(host_name, vm_id, row["vm_name"], guac_public)
    if not url:
        raise HTTPException(404, tr("Koneksi Guacamole belum dibuat. Simpan credentials VM terlebih dahulu.",
                                    "The Guacamole connection does not exist yet. Save the VM credentials first."))
    return {"url": url, "vm_name": row["vm_name"]}


@router.get("/my-vm-cred/{host_name}/{vm_id}")
async def get_my_vm_cred(host_name: str, vm_id: str, user: dict = Depends(get_current_user)):
    """
    Kredensial login VM untuk student yang sedang login.
    - Jika assignment pakai OS account → return os_username + password dari vm_os_accounts
    - Jika assignment default cred / bukan student → return vm_username + vm_password dari vm_metadata
    """
    if user["role"] == Role.STUDENT:
        from database import get_student_vm_ids
        if (vm_id, host_name) not in await get_student_vm_ids(int(user["sub"]), host_name):
            raise HTTPException(403, tr("Anda tidak punya akses ke VM ini",
                                        "You do not have access to this VM"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Cek direct assignment untuk ambil OS account (opsional — group access tidak punya OS account)
        assignment = await conn.fetchrow(
            """SELECT va.os_account_id, voa.os_username, voa.password_enc
               FROM vm_assignments va
               LEFT JOIN vm_os_accounts voa ON voa.id = va.os_account_id
               WHERE va.user_id = $1 AND va.vm_id = $2 AND va.host_name = $3
                 AND va.deleted_at IS NULL""",
            int(user["sub"]), vm_id, host_name
        ) if user["role"] == Role.STUDENT else None

        if assignment and assignment["os_account_id"]:
            password = decrypt_secret(assignment["password_enc"]) if assignment["password_enc"] else None
            return {"username": assignment["os_username"], "password": password, "source": "os_account"}

        # Fallback: vm_metadata
        meta = await conn.fetchrow(
            "SELECT vm_username, vm_password_enc FROM vm_metadata WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
    password = decrypt_secret(meta["vm_password_enc"]) if (meta and meta["vm_password_enc"]) else None
    return {
        "username": meta["vm_username"] if meta else None,
        "password": password,
        "source": "metadata",
    }


@router.post("/guac-sync/{host_name}/{vm_id}")
async def force_guac_sync(host_name: str, vm_id: str, user: dict = Depends(get_current_user)):
    """Paksa re-sync koneksi Guacamole untuk satu VM."""
    _require_admin(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        cred_row = await conn.fetchrow(
            "SELECT os_type, cred_type, guac_protocol, ssh_host, ssh_port, username, password_enc, pkey_enc "
            "FROM vm_credentials WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
        vm_row = await conn.fetchrow(
            "SELECT vm_name FROM vms WHERE vm_id = $1 AND host_name = $2",
            vm_id, host_name
        )
    if not cred_row:
        raise HTTPException(404, tr("Credentials VM belum dikonfigurasi",
                                    "The VM credentials are not configured"))
    if not vm_row:
        raise HTTPException(404, tr("VM tidak ditemukan", "VM not found"))

    conn_id = await sync_vm_connection(host_name, vm_row["vm_name"], vm_id, _vm_creds(cred_row))
    if not conn_id:
        raise HTTPException(500, tr("Gagal sync ke Guacamole", "Could not sync to Guacamole"))
    await grant_vm_to_all_admins(conn_id)
    return {"status": "synced", "connection_id": conn_id, "vm_name": vm_row["vm_name"]}
