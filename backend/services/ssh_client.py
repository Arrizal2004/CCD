"""
SSH client berbasis Paramiko — digunakan untuk:
1. Fallback verifikasi state saat Redis timeout
2. Terminal Linux VM via PTY
3. Otomasi Linux VM (IP config, disk expansion)
"""
import asyncio
import base64
import hashlib
import io
import logging
import os
import socket
import time
import paramiko
from cryptography.fernet import Fernet
from i18n import tr

log = logging.getLogger("ssh_client")

# Banner timeout dinaikkan ke 30s agar tahan first-boot / VM sibuk (default paramiko 15s).
BANNER_TIMEOUT = float(os.getenv("SSH_BANNER_TIMEOUT", "30"))
# Berapa lama koneksi pool boleh idle sebelum ditutup (detik).
POOL_IDLE_TTL = float(os.getenv("SSH_POOL_IDLE_TTL", "60"))

# Connection pool: key (host,port,user,cred_fp) → entry {client, last, lock}.
# Mencegah handshake SSH baru tiap panggilan; koneksi yang masih hidup dipakai ulang.
_pool: dict = {}
_pool_guard = asyncio.Lock()

# Derive 32-byte Fernet key dari JWT_SECRET
_raw = (os.getenv("JWT_SECRET") or "").encode()
_key = base64.urlsafe_b64encode(hashlib.sha256(_raw).digest())
_fernet = Fernet(_key)


def _safe_close(client) -> None:
    """Tutup paramiko client tanpa pernah raise (anti zombie connection)."""
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass


async def close_all_pooled() -> None:
    """Tutup semua koneksi pool — dipanggil saat shutdown aplikasi."""
    async with _pool_guard:
        for entry in _pool.values():
            await asyncio.to_thread(_safe_close, entry.get("client"))
        _pool.clear()


def encrypt_secret(text: str) -> str:
    return _fernet.encrypt(text.encode()).decode()


def decrypt_secret(enc: str) -> str:
    return _fernet.decrypt(enc.encode()).decode()


class SshClient:
    """
    Thin async wrapper di atas paramiko.SSHClient.
    Semua I/O blocking dijalankan via asyncio.to_thread.
    """

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        password: str | None = None,
        pkey_str: str | None = None,
        timeout: float = 15.0,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.pkey_str = pkey_str
        self.timeout = timeout

    def _cred_fingerprint(self) -> str:
        secret = self.pkey_str or self.password or ""
        return hashlib.sha256(secret.encode()).hexdigest()[:12]

    def _pool_key(self) -> tuple:
        return (self.host, self.port, self.username, self._cred_fingerprint())

    def _make_client(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": self.timeout,            # TCP connect timeout
            "banner_timeout": BANNER_TIMEOUT,   # 30s — tahan first-boot lambat
            "auth_timeout": max(self.timeout, BANNER_TIMEOUT),
        }
        if self.pkey_str:
            pkey = paramiko.RSAKey.from_private_key(io.StringIO(self.pkey_str))
            kwargs["pkey"] = pkey
        elif self.password:
            kwargs["password"] = self.password
        else:
            raise ValueError(tr("Harus menyediakan password atau private key",
                                "A password or private key is required"))
        try:
            client.connect(**kwargs)
        except Exception:
            # PENTING: connect yang gagal (mis. banner timeout) tetap menyisakan
            # socket/transport → tutup eksplisit agar tidak jadi zombie connection.
            _safe_close(client)
            raise
        # Set ulang banner_timeout pada transport secara eksplisit (kebutuhan eksplisit).
        transport = client.get_transport()
        if transport is not None:
            transport.banner_timeout = BANNER_TIMEOUT
        return client

    async def test_connection(self) -> tuple[bool, str]:
        """Return (ok, error_message). Selalu menutup koneksi (try/finally)."""
        def _test():
            c = None
            try:
                c = self._make_client()
                return True, ""
            except Exception as e:
                return False, str(e)
            finally:
                _safe_close(c)
        return await asyncio.to_thread(_test)

    # ── Connection pool ────────────────────────────────────────────────────────
    async def _acquire_entry(self) -> dict:
        """Ambil/inisialisasi entry pool untuk host ini. Evict yang sudah idle (lazy)."""
        key = self._pool_key()
        now = time.monotonic()
        async with _pool_guard:
            entry = _pool.get(key)
            if entry is not None and (now - entry["last"]) > POOL_IDLE_TTL:
                await asyncio.to_thread(_safe_close, entry.get("client"))
                _pool.pop(key, None)
                entry = None
            if entry is None:
                entry = {"client": None, "last": now, "lock": asyncio.Lock()}
                _pool[key] = entry
            entry["last"] = now
            return entry

    def _ensure_live(self, entry: dict) -> paramiko.SSHClient:
        """Kembalikan client pool yang hidup; reconnect bila mati. Jalan di thread, di bawah entry['lock']."""
        client = entry.get("client")
        if client is not None:
            tr = client.get_transport()
            if tr is not None and tr.is_active():
                return client
            _safe_close(client)          # transport mati → bersihkan
            entry["client"] = None
        client = self._make_client()
        entry["client"] = client
        return client

    async def exec(self, command: str, timeout: float = 60.0, pooled: bool = True) -> tuple[str, str, int]:
        """
        Jalankan satu perintah. Return (stdout, stderr, exit_code).

        pooled=True (default): pakai ulang koneksi SSH dari pool (hindari handshake
        berulang). Operasi per-host diserialisasi via entry['lock'] sehingga aman.
        pooled=False: koneksi sekali pakai, dijamin ditutup via try/finally.
        """
        if not pooled:
            def _oneshot():
                client = self._make_client()
                try:
                    _, stdout, stderr = client.exec_command(command, timeout=timeout)
                    out = stdout.read().decode(errors="replace")
                    err = stderr.read().decode(errors="replace")
                    code = stdout.channel.recv_exit_status()
                    return out, err, code
                finally:
                    _safe_close(client)
            return await asyncio.to_thread(_oneshot)

        entry = await self._acquire_entry()
        async with entry["lock"]:
            def _run():
                client = self._ensure_live(entry)
                try:
                    _, stdout, stderr = client.exec_command(command, timeout=timeout)
                    out = stdout.read().decode(errors="replace")
                    err = stderr.read().decode(errors="replace")
                    code = stdout.channel.recv_exit_status()
                    entry["last"] = time.monotonic()
                    return out, err, code
                except Exception:
                    # Channel/transport bermasalah → buang koneksi pool agar tidak dipakai ulang.
                    _safe_close(entry.get("client"))
                    entry["client"] = None
                    raise
            return await asyncio.to_thread(_run)

    async def exec_sudo(
        self, command: str, sudo_password: str | None = None, timeout: float = 60.0,
        pooled: bool = True,
    ) -> tuple[str, str, int]:
        """
        Jalankan perintah dengan sudo non-interaktif.
        -S  : baca password dari stdin
        -p '': suppress prompt "Password:" agar tidak masuk ke stdout/stderr
        bash -c: jalankan seluruh compound command (&&, ;, pipe) sebagai root

        pooled=False → koneksi sekali pakai (fresh socket, dijamin ditutup). Wajib
        untuk alur yang sensitif lifecycle seperti instalasi agent saat first-boot.
        """
        if sudo_password is None:
            sudo_password = self.password
        if sudo_password:
            safe_pass = sudo_password.replace("'", "'\\''")
            cmd_esc   = command.replace("'", "'\\''")
            wrapped   = "echo '%s' | sudo -S -p '' bash -c '%s'" % (safe_pass, cmd_esc)
        else:
            wrapped = f"sudo -n bash -c {_shell_quote(command)}"
        return await self.exec(wrapped, timeout=timeout, pooled=pooled)

    async def sftp_write(self, remote_path: str, content: str) -> None:
        """
        Tulis file ke VM via SFTP — tanpa shell quoting sama sekali.
        Lebih reliable dari 'echo ... | tee' untuk konten yang mengandung karakter khusus.
        Raise Exception jika gagal.
        """
        def _write():
            client = self._make_client()
            try:
                sftp = client.open_sftp()
                try:
                    with sftp.file(remote_path, "w") as f:
                        f.write(content)
                finally:
                    sftp.close()
            finally:
                client.close()
        await asyncio.to_thread(_write)

    async def sftp_put_tree(self, files: list[tuple[str, bytes]], remote_base: str):
        """
        Kirim sekumpulan file ke remote dalam SATU sesi SFTP (hemat handshake).
        Direktori induk dibuat dinamis (semantik mkdir -p), mendukung path Windows
        absolut seperti 'C:/HyperView-Monitoring/src'.

        files       : list (relative_path, content_bytes); pemisah '/' atau '\\'.
        remote_base : direktori tujuan akar.

        Async generator: yield nama relatif tiap file setelah berhasil ditulis,
        agar caller bisa men-stream progres ke UI.
        """
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _put():
            try:
                client = self._make_client()
                try:
                    sftp = client.open_sftp()
                    try:
                        made: set = set()
                        base = remote_base.replace("\\", "/").rstrip("/")
                        _sftp_makedirs(sftp, base, made)
                        for rel, content in files:
                            relx = rel.replace("\\", "/").lstrip("/")
                            full = f"{base}/{relx}"
                            parent = full.rsplit("/", 1)[0]
                            _sftp_makedirs(sftp, parent, made)
                            with sftp.file(full, "wb") as f:
                                f.write(content)
                            loop.call_soon_threadsafe(queue.put_nowait, ("file", relx))
                        loop.call_soon_threadsafe(queue.put_nowait, ("done", base))
                    finally:
                        sftp.close()
                finally:
                    client.close()
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))

        asyncio.ensure_future(asyncio.to_thread(_put))
        while True:
            kind, val = await asyncio.wait_for(queue.get(), timeout=120.0)
            yield kind, val
            if kind in ("done", "error"):
                break

    async def open_pty(self, rows: int = 24, cols: int = 80) -> tuple[paramiko.Channel, paramiko.SSHClient]:
        """
        Buka interactive PTY shell. Return (channel, client).
        Caller bertanggung jawab menutup keduanya.
        """
        def _open():
            client = self._make_client()
            channel = client.invoke_shell(term="xterm-256color", width=cols, height=rows)
            channel.settimeout(0.05)
            return channel, client
        return await asyncio.to_thread(_open)

    async def stream_exec(self, command: str, timeout: float = 300.0):
        """
        Async generator yang yield baris output secara real-time.
        Yield tuple (stream, line) di mana stream adalah 'stdout' atau 'stderr'.
        """
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _run():
            try:
                client = self._make_client()
                try:
                    transport = client.get_transport()
                    channel = transport.open_session()
                    channel.set_combine_stderr(False)
                    channel.exec_command(command)

                    buf_out = b""
                    buf_err = b""
                    while True:
                        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                            break
                        if channel.recv_ready():
                            data = channel.recv(4096)
                            buf_out += data
                            while b"\n" in buf_out:
                                line, buf_out = buf_out.split(b"\n", 1)
                                loop.call_soon_threadsafe(
                                    queue.put_nowait, ("stdout", line.decode(errors="replace"))
                                )
                        if channel.recv_stderr_ready():
                            data = channel.recv_stderr(4096)
                            buf_err += data
                            while b"\n" in buf_err:
                                line, buf_err = buf_err.split(b"\n", 1)
                                loop.call_soon_threadsafe(
                                    queue.put_nowait, ("stderr", line.decode(errors="replace"))
                                )
                    # flush sisa buffer
                    if buf_out:
                        loop.call_soon_threadsafe(queue.put_nowait, ("stdout", buf_out.decode(errors="replace")))
                    if buf_err:
                        loop.call_soon_threadsafe(queue.put_nowait, ("stderr", buf_err.decode(errors="replace")))
                    exit_code = channel.recv_exit_status()
                    loop.call_soon_threadsafe(queue.put_nowait, ("exit", str(exit_code)))
                finally:
                    client.close()
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))

        thread = asyncio.to_thread(_run)
        asyncio.ensure_future(thread)

        while True:
            stream, line = await asyncio.wait_for(queue.get(), timeout=timeout)
            yield stream, line
            if stream in ("exit", "error"):
                break


def _shell_quote(s: str) -> str:
    """Single-quote string untuk shell safety."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _sftp_makedirs(sftp, path: str, made: set) -> None:
    """
    mkdir -p melalui SFTP. `path` memakai pemisah '/'. Komponen drive Windows
    (mis. 'C:') dilewati pembuatannya. `made` adalah cache path yang sudah dibuat.
    """
    parts = [p for p in path.split("/") if p != ""]
    cur = ""
    for i, p in enumerate(parts):
        cur = p if cur == "" else f"{cur}/{p}"
        # Lewati pembuatan untuk komponen drive seperti 'C:'.
        if i == 0 and p.endswith(":"):
            continue
        if cur in made:
            continue
        try:
            sftp.stat(cur)            # sudah ada → tidak perlu mkdir
        except IOError:
            try:
                sftp.mkdir(cur)
            except IOError:
                pass                  # race / sudah dibuat proses lain
        made.add(cur)
