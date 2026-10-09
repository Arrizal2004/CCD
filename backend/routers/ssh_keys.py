"""
SSH key pengguna untuk bastion SSH (opt-in, container `bastion` di docker-compose).

Mahasiswa mendaftarkan public key di Profil, lalu SSH ke VM miliknya lewat bastion:
    ssh -J tunnel@<host>:2222 <akun-os>@<ip-vm>

Bastion (sshd) tidak menyimpan key sendiri. Setiap login, AuthorizedKeysCommand bertanya ke
GET /authorized dengan fingerprint key, dan backend menjawab dengan satu baris authorized_keys
yang hanya mengizinkan penerusan ke IP:port VM yang boleh diakses pemilik key saat itu. Karena
dihitung ulang setiap login, menonaktifkan akun atau mencabut assignment langsung berlaku.
"""
import asyncio, base64, hashlib, hmac, ipaddress, os, re, socket, struct, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from auth import get_current_user, Role
from database import get_pool, get_student_vm_ids
from services.audit import both, log_activity
from services.ssh_audit import handle_lines
from i18n import tr

router = APIRouter()

MAX_KEYS_PER_USER = 5
MIN_RSA_BITS = 3072
_ALLOWED_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa")
_ADMIN_ROLES = (Role.SUPERADMIN, Role.SYSADMIN)


def bastion_enabled() -> bool:
    profiles = [p.strip() for p in os.getenv("COMPOSE_PROFILES", "").split(",")]
    return "ssh" in profiles and bool(os.getenv("BASTION_TOKEN"))


def public_port() -> int:
    """Port bastion di VPS; mengikuti pemetaan port di docker-compose, jadi tetap diatur di .env."""
    return int(os.getenv("BASTION_PUBLIC_PORT") or 2222)


async def public_host(request: Request) -> str:
    """Alamat bastion untuk pengguna: Pengaturan Sistem, lalu BASTION_PUBLIC_HOST di .env, lalu alamat
    yang sedang dibuka pengguna. Domain lewat proxy Cloudflare tidak meneruskan SSH, jadi dalam kasus itu
    alamatnya perlu diisi dengan nama yang langsung ke IP VPS."""
    from services.system_settings import get_settings
    return (await get_settings())["ssh_public_host"] or os.getenv("BASTION_PUBLIC_HOST") or (request.url.hostname or "")


# ── Parsing public key ─────────────────────────────────────────────────────────

def _read_string(blob: bytes, off: int) -> tuple[bytes, int]:
    if off + 4 > len(blob):
        raise ValueError(tr("key terpotong", "truncated key"))
    (n,) = struct.unpack(">I", blob[off:off + 4])
    if off + 4 + n > len(blob):
        raise ValueError(tr("key terpotong", "truncated key"))
    return blob[off + 4:off + 4 + n], off + 4 + n


def parse_public_key(text: str) -> tuple[str, str, str]:
    """Validasi satu baris public key OpenSSH. Return (type, "type base64", fingerprint SHA256:...).
    Komentar di akhir baris dibuang; opsi authorized_keys di depan baris ditolak."""
    parts = text.strip().split()
    if len(parts) < 2:
        raise ValueError(tr("Format tidak dikenali. Tempel isi file .pub, mis. 'ssh-ed25519 AAAA... nama'",
                            "Unrecognised format. Paste the contents of the .pub file, e.g. 'ssh-ed25519 AAAA... name'"))
    ktype, b64 = parts[0], parts[1]
    if ktype not in _ALLOWED_TYPES:
        raise ValueError(tr(f"Jenis key '{ktype[:30]}' tidak didukung. Pakai ssh-ed25519 (disarankan), ecdsa, atau rsa minimal {MIN_RSA_BITS} bit",
                            f"Key type '{ktype[:30]}' is not supported. Use ssh-ed25519 (recommended), ecdsa, or rsa with at least {MIN_RSA_BITS} bits"))
    try:
        blob = base64.b64decode(b64, validate=True)
    except Exception:
        raise ValueError(tr("Isi key bukan base64 yang valid", "The key contents are not valid base64"))
    inner_type, off = _read_string(blob, 0)
    if inner_type.decode(errors="replace") != ktype:
        raise ValueError(tr("Jenis key tidak cocok dengan isinya",
                            "The key type does not match its contents"))
    if ktype == "ssh-rsa":
        e, off = _read_string(blob, off)
        n, off = _read_string(blob, off)
        bits = int.from_bytes(n, "big").bit_length()
        if bits < MIN_RSA_BITS:
            raise ValueError(tr(f"Key RSA {bits} bit terlalu lemah, minimal {MIN_RSA_BITS} bit. Lebih baik pakai ssh-ed25519",
                                f"A {bits}-bit RSA key is too weak; at least {MIN_RSA_BITS} bits are required. ssh-ed25519 is better"))
    elif ktype == "ssh-ed25519":
        pk, off = _read_string(blob, off)
        if len(pk) != 32:
            raise ValueError(tr("Key ed25519 tidak valid", "Invalid ed25519 key"))
    if len(blob) > 4096:
        raise ValueError(tr("Key terlalu panjang", "The key is too long"))
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return ktype, f"{ktype} {base64.b64encode(blob).decode()}", fp


# ── Target yang boleh diakses ──────────────────────────────────────────────────

def _target(host: str, port) -> str | None:
    """Hanya IPv4 dan port valid yang boleh masuk ke opsi permitopen (mencegah injeksi opsi)."""
    try:
        ip = ipaddress.IPv4Address((host or "").strip())
        p = int(port or 22)
    except (ValueError, TypeError):
        return None
    return f"{ip}:{p}" if 1 <= p <= 65535 else None


async def allowed_targets(user_id: int, role: str) -> list[str]:
    """IP:port SSH VM yang boleh dituju lewat bastion. Admin: semua VM Linux/SSH terdaftar.
    Mahasiswa: hanya VM yang di-assign langsung atau lewat group."""
    def ssh_vm(a: str) -> str:
        # VM yang aksesnya lewat SSH: protokol dipilih 'ssh', atau otomatis dan OS-nya Linux.
        return (f"{a}ssh_host <> '' AND ({a}guac_protocol = 'ssh' "
                f"OR ({a}guac_protocol = '' AND {a}os_type = 'linux'))")

    pool = await get_pool()
    async with pool.acquire() as conn:
        creds = await conn.fetch(f"SELECT vm_id, host_name, ssh_host, ssh_port FROM vm_credentials WHERE {ssh_vm('')}")
        if role in _ADMIN_ROLES:
            accts = await conn.fetch(f"SELECT ssh_host, ssh_port FROM vm_os_accounts WHERE {ssh_vm('')}")
            rows = list(creds) + list(accts)
        else:
            allowed = await get_student_vm_ids(user_id, full_only=True)
            accts = await conn.fetch(
                f"""SELECT voa.ssh_host, voa.ssh_port FROM vm_assignments va
                    JOIN vm_os_accounts voa ON voa.id = va.os_account_id
                    WHERE va.user_id = $1 AND va.deleted_at IS NULL AND va.access = 'full' AND {ssh_vm('voa.')}""",
                user_id)
            rows = [r for r in creds if (r["vm_id"], r["host_name"]) in allowed] + list(accts)
    return sorted({t for r in rows if (t := _target(r["ssh_host"], r["ssh_port"]))})


# ── Fingerprint host key bastion ───────────────────────────────────────────────

_fp_cache = {"at": 0.0, "fp": None}


def _fetch_bastion_fingerprint() -> str | None:
    """Ambil host key ed25519 bastion lewat jaringan Docker (tanpa login), format SHA256 seperti
    yang ditampilkan ssh saat pertama kali terhubung."""
    import paramiko
    sock = socket.create_connection(("bastion", 2222), timeout=3)
    transport = paramiko.Transport(sock)
    try:
        transport.get_security_options().key_types = ("ssh-ed25519",)
        transport.start_client(timeout=5)
        blob = transport.get_remote_server_key().asbytes()
        return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    finally:
        transport.close()


async def bastion_fingerprint() -> str | None:
    # Disimpan 1 jam kalau berhasil, dicoba lagi setelah 1 menit kalau gagal.
    ttl = 3600 if _fp_cache["fp"] else 60
    if time.monotonic() - _fp_cache["at"] > ttl:
        try:
            _fp_cache["fp"] = await asyncio.to_thread(_fetch_bastion_fingerprint)
        except Exception:
            _fp_cache["fp"] = None
        _fp_cache["at"] = time.monotonic()
    return _fp_cache["fp"]


# ── Endpoint pengguna ──────────────────────────────────────────────────────────

class KeyBody(BaseModel):
    name: str = ""
    public_key: str


@router.get("/config")
async def ssh_config(request: Request, user: dict = Depends(get_current_user)):
    """Apakah bastion aktif, dan alamat yang ditampilkan di perintah SSH."""
    enabled = bastion_enabled()
    return {
        "enabled": enabled,
        "host_fingerprint": await bastion_fingerprint() if enabled else None,
        "host": await public_host(request),
        "port": public_port(),
        "user": "tunnel",
    }


@router.get("")
async def list_keys(user: dict = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, key_type, fingerprint, created_at, last_used_at
               FROM user_ssh_keys WHERE user_id = $1 ORDER BY created_at""", int(user["sub"]))
    return [dict(r) for r in rows]


@router.post("")
async def add_key(body: KeyBody, request: Request, user: dict = Depends(get_current_user)):
    try:
        ktype, normalized, fp = parse_public_key(body.public_key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    name = re.sub(r"[^\w .@-]", "", body.name or "").strip()[:60] or ktype
    uid = int(user["sub"])
    pool = await get_pool()
    async with pool.acquire() as conn:
        if await conn.fetchval("SELECT count(*) FROM user_ssh_keys WHERE user_id = $1", uid) >= MAX_KEYS_PER_USER:
            raise HTTPException(400, tr(f"Maksimal {MAX_KEYS_PER_USER} SSH key per akun. Hapus key lama dulu",
                                        f"At most {MAX_KEYS_PER_USER} SSH keys per account. Delete an old key first"))
        if await conn.fetchval("SELECT 1 FROM user_ssh_keys WHERE fingerprint = $1", fp):
            raise HTTPException(409, tr("Key ini sudah terdaftar", "This key is already registered"))
        row = await conn.fetchrow(
            """INSERT INTO user_ssh_keys (user_id, name, key_type, public_key, fingerprint)
               VALUES ($1, $2, $3, $4, $5) RETURNING id, name, key_type, fingerprint, created_at, last_used_at""",
            uid, name, ktype, normalized, fp)
    await log_activity(user, "SSH_KEY_ADD", "INFO", {"id": fp, "name": name},
                       both(lambda: tr(f"{user.get('username')} menambahkan SSH key {name} ({fp})", f"{user.get('username')} added the SSH key {name} ({fp})")), request)
    return dict(row)


@router.delete("/{key_id}")
async def delete_key(key_id: int, request: Request, user: dict = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM user_ssh_keys WHERE id = $1 AND user_id = $2 RETURNING name, fingerprint",
            key_id, int(user["sub"]))
    if not row:
        raise HTTPException(404, tr("SSH key tidak ditemukan", "SSH key not found"))
    await log_activity(user, "SSH_KEY_DELETE", "WARNING", {"id": row["fingerprint"], "name": row["name"]},
                       both(lambda: tr(f"{user.get('username')} menghapus SSH key {row['name']} ({row['fingerprint']})",
                                    f"{user.get('username')} deleted the SSH key {row['name']} ({row['fingerprint']})")), request)
    return {"status": "deleted"}


# ── Endpoint internal untuk bastion ────────────────────────────────────────────

def _require_bastion(request: Request) -> None:
    expected = os.getenv("BASTION_TOKEN", "")
    given = request.headers.get("X-Bastion-Token", "")
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(403, "Forbidden")


_denied_logged: dict[str, float] = {}


async def _log_key_denied(row, reason: str, client: str) -> None:
    """Key terdaftar tetapi akunnya sedang tidak berhak. Dicatat paling sering sekali per menit per
    key, karena public key bisa diketahui orang lain dan dipakai untuk mencoba berulang kali."""
    now = time.monotonic()
    if now - _denied_logged.get(row["fingerprint"], -60.0) < 60:
        return
    if len(_denied_logged) > 1000:
        _denied_logged.clear()
    _denied_logged[row["fingerprint"]] = now
    try:
        ip = str(ipaddress.ip_address(client))
    except ValueError:
        ip = ""
    await log_activity({"sub": row["uid"], "username": row["username"], "role": row["role"]},
                       "SSH_DENIED", "WARNING", {"id": row["fingerprint"], "name": row["name"]},
                       both(lambda: tr(f"Key SSH {row['name']} milik {row['username']} ditolak bastion: {reason.t()}",
                                       f"SSH key {row['name']} of {row['username']} was rejected by the bastion: {reason.t()}")), ip=ip)


@router.get("/authorized", response_class=PlainTextResponse)
async def authorized_keys(request: Request, fingerprint: str = Query(..., max_length=80),
                          client: str = Query("", max_length=64)):
    """Dipanggil AuthorizedKeysCommand bastion lewat jaringan Docker (nginx memblokir path ini dari
    luar). Body kosong = key ditolak. Dilindungi token bersama BASTION_TOKEN."""
    _require_bastion(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT k.id, k.name, k.fingerprint, k.public_key, u.id AS uid, u.username, u.role,
                      u.is_active, u.deleted_at, u.is_verified,
                      (u.expires_at IS NOT NULL AND u.expires_at <= NOW()) AS expired
               FROM user_ssh_keys k JOIN users u ON u.id = k.user_id WHERE k.fingerprint = $1""", fingerprint)
        if not row:
            return ""
        targets = []
        if not row["is_active"] or row["deleted_at"] is not None:
            reason = both(lambda: tr("akun nonaktif", "account is inactive"))
        elif row["expired"]:
            reason = both(lambda: tr("masa berlaku akun habis", "the account has expired"))
        elif row["role"] == Role.STUDENT and not row["is_verified"]:
            reason = both(lambda: tr("akun belum diverifikasi", "the account is not verified"))
        else:
            targets = await allowed_targets(row["uid"], row["role"])
            # Tanpa permitopen, port-forwarding akan terbuka ke mana saja: tolak.
            reason = "" if targets else both(lambda: tr("tidak ada VM yang bisa diakses lewat SSH", "no VM can be reached through SSH"))
        if reason:
            await _log_key_denied(row, reason, client)
            return ""
        await conn.execute("UPDATE user_ssh_keys SET last_used_at = NOW() WHERE id = $1", row["id"])
    opts = ",".join(["restrict", "port-forwarding", *[f'permitopen="{t}"' for t in targets], 'command="/bin/false"'])
    owner = re.sub(r"[^A-Za-z0-9._-]", "", row["username"])[:64]
    return f"{opts} {row['public_key']} ccd:{owner}\n"


@router.post("/events")
async def bastion_events(request: Request):
    """Baris log sshd dari bastion untuk audit SSH (services/ssh_audit.py). Internal seperti
    /authorized: diblokir nginx dari luar dan dilindungi BASTION_TOKEN."""
    _require_bastion(request)
    body = await request.body()
    if len(body) > 512 * 1024:
        raise HTTPException(413, tr("Terlalu besar", "Too large"))
    return {"handled": await handle_lines(body.decode("utf-8", "replace"))}


@router.get("/kill-wait", response_class=PlainTextResponse)
async def bastion_kill_wait(request: Request, wait: int = Query(25, ge=1, le=30)):
    """Long poll dari skrip ccd-kill di bastion: perintah pemutusan sesi, satu per baris
    "<id-sesi> <pid> [<pid>]" (services/ssh_kill.py). Kosong = tidak ada perintah selama `wait` detik."""
    _require_bastion(request)
    from services import ssh_kill
    orders = await ssh_kill.next_orders(wait)
    return "".join(f"{o}\n" for o in orders)


@router.post("/killed")
async def bastion_killed(request: Request):
    """Laporan ccd-kill: id sesi yang prosesnya sudah dihentikan, satu per baris."""
    _require_bastion(request)
    body = (await request.body())[:64 * 1024].decode("ascii", "replace")
    ids = sorted({int(x) for x in body.split() if x.isdigit() and len(x) < 10})
    from services import ssh_kill
    return {"ended": await ssh_kill.acknowledge(ids)}
