"""
Guacamole WebSocket tunnel.

Alur:
  Browser (guacamole-common-js)
    ↕ WebSocket /ws/guac/{host}/{vm}
  FastAPI (relay)
    ↕ TCP guacd:4822
  guacd
    ↕ SSH (Linux) / RDP (Windows)
  VM

Backend melakukan handshake dengan guacd menggunakan credentials dari DB,
meneruskan 'ready' ke browser, lalu relay bidireksional penuh.

Bug lama: _read_instr menggunakan reader.read(BUF) yang bisa over-read —
bytes awal display dari guacd ikut termakan dan hilang dari stream, sehingga
browser tidak pernah menerima sync instruction → stuck di WAITING selamanya.
Fix: gunakan reader.readexactly(n) yang membaca persis n byte tanpa over-read.
"""
import asyncio
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from auth import verify_token
from database import get_pool
from services.ssh_client import decrypt_secret
from i18n import tr

logger = logging.getLogger("guac")

router = APIRouter()

GUACD_HOST = os.getenv("GUACD_HOST", "guacd")
GUACD_PORT = int(os.getenv("GUACD_PORT", "4822"))
BUF        = 32768   # relay buffer — lebih besar = lebih efisien untuk burst display data

# Registry koneksi guacd aktif: username -> list of (websocket, writer)
_active_connections: dict[str, list[tuple]] = {}


async def disconnect_user(username: str) -> int:
    """Putuskan semua sesi guacd aktif untuk user. Return jumlah koneksi yang diputus."""
    entries = list(_active_connections.get(username, []))
    for ws, writer in entries:
        try:
            await ws.close(code=4401)
        except Exception:
            pass
        try:
            writer.close()
        except Exception:
            pass
    return len(entries)


# ── Guacamole protocol helpers ─────────────────────────────────────────────────

def _enc(*args) -> bytes:
    """Encode satu instruksi Guacamole: len.value[,...];"""
    parts = [f"{len(str(a))}.{a}" for a in args]
    return (",".join(parts) + ";").encode()


async def _read_element(reader: asyncio.StreamReader, timeout: float = 15.0) -> tuple[str, bool]:
    """
    Baca satu elemen length-prefixed dari stream guacd.
    Menggunakan readexactly() — tidak pernah over-read; sisa data tetap di buffer reader.
    Return (value, is_end_of_instruction) di mana is_end=True jika diikuti ';'.
    """
    # Baca digit panjang sampai '.'
    length_buf = b""
    while True:
        byte = await asyncio.wait_for(reader.readexactly(1), timeout=timeout)
        if byte == b".":
            break
        if byte.isdigit():
            length_buf += byte
        else:
            raise ValueError(f"Karakter tak terduga di length prefix: {byte!r}")

    length = int(length_buf)

    # Baca persis 'length' byte untuk value
    value_bytes = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)

    # Baca separator: ',' (lanjut) atau ';' (akhir instruksi)
    sep = await asyncio.wait_for(reader.readexactly(1), timeout=timeout)
    if sep == b";":
        return value_bytes.decode(errors="replace"), True
    elif sep == b",":
        return value_bytes.decode(errors="replace"), False
    else:
        raise ValueError(f"Separator tak terduga: {sep!r}")


async def _read_instr(reader: asyncio.StreamReader, timeout: float = 15.0) -> list[str]:
    """
    Baca tepat satu instruksi Guacamole dari reader.
    Tidak over-read: instruksi berikutnya tetap utuh di buffer reader.
    """
    elements = []
    while True:
        val, is_end = await _read_element(reader, timeout)
        elements.append(val)
        if is_end:
            break
    return elements


async def _guacd_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    protocol: str,
    params: dict,
) -> str:
    """
    Handshake penuh dengan guacd:
      1. select <protocol>
      2. baca args yang diminta
      3. kirim connect dengan nilai dari DB
      4. baca ready → return connection_id
    """
    logger.info("guacd handshake: select %s", protocol)
    writer.write(_enc("select", protocol))
    await writer.drain()

    # Baca daftar arg yang dibutuhkan guacd
    elements = await _read_instr(reader)
    logger.info("guacd args: %s", elements)
    if not elements or elements[0] != "args":
        raise RuntimeError(f"Expected 'args' dari guacd, dapat: {elements}")
    arg_names = elements[1:]

    # Kirim connect — isi nilai sesuai urutan arg yang diminta
    values = [params.get(name, "") for name in arg_names]
    logger.info("guacd connect dengan %d arg: %s", len(arg_names), arg_names)
    writer.write(_enc("connect", *values))
    await writer.drain()

    # Baca konfirmasi
    resp = await _read_instr(reader)
    logger.info("guacd response: %s", resp[:2] if resp else resp)
    if not resp:
        raise RuntimeError("Respons kosong dari guacd setelah connect")
    if resp[0] == "error":
        code = resp[2] if len(resp) > 2 else "?"
        raise RuntimeError(f"guacd error {code}: {resp[1] if len(resp) > 1 else 'unknown'}")
    if resp[0] != "ready":
        raise RuntimeError(f"Expected 'ready', dapat: {resp[0]}")

    conn_id = resp[1] if len(resp) > 1 else ""
    return conn_id


# ── Credential loader ──────────────────────────────────────────────────────────

async def _load_vm_creds(vm_id_or_name: str, host_name: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT os_type, cred_type, ssh_host, ssh_port, username,
                      password_enc, pkey_enc
               FROM vm_credentials
               WHERE vm_id = $1 AND host_name = $2
               LIMIT 1""",
            vm_id_or_name, host_name
        )
    if not row:
        raise HTTPException(
            status_code=404,
            detail=tr("Credentials VM belum dikonfigurasi. "
                      "Buka tab 'Koneksi' → pilih OS & isi credentials.",
                      "The VM credentials are not configured. "
                      "Set them in the VM details window.")
        )
    password = decrypt_secret(row["password_enc"]) if row["password_enc"] else ""
    pkey     = decrypt_secret(row["pkey_enc"])     if row["pkey_enc"]     else ""
    return {
        "os_type":   row["os_type"]   or "linux",
        "cred_type": row["cred_type"] or "ssh",
        "ssh_host":  row["ssh_host"]  or "",
        "ssh_port":  int(row["ssh_port"] or 22),
        "username":  row["username"]  or "",
        "password":  password,
        "pkey":      pkey,
    }


# ── Parameter builders ─────────────────────────────────────────────────────────

def _build_ssh_params(creds: dict, cols: int = 220, rows: int = 50) -> dict:
    return {
        "hostname":         creds["ssh_host"],
        "port":             str(creds["ssh_port"]),
        "username":         creds["username"],
        "password":         creds["password"],
        "private-key":      creds["pkey"],
        "passphrase":       "",
        "color-scheme":     "gray-black",
        "font-name":        "monospace",
        "font-size":        "14",
        "scrollback":       "5000",
        "terminal-type":    "xterm-256color",
        "width":            str(cols),
        "height":           str(rows),
        "enable-sftp":      "true",
        "sftp-hostname":    creds["ssh_host"],
        "sftp-port":        str(creds["ssh_port"]),
        "sftp-username":    creds["username"],
        "sftp-password":    creds["password"],
        "sftp-private-key": creds["pkey"],
    }


def _build_rdp_params(creds: dict, cols: int = 1280, rows: int = 800) -> dict:
    return {
        "hostname":      creds["ssh_host"],
        "port":          str(creds["ssh_port"] or 3389),
        "username":      creds["username"],
        "password":      creds["password"],
        "domain":        "",
        "security":      "any",
        "ignore-cert":   "true",
        "width":         str(cols),
        "height":        str(rows),
        "dpi":           "96",
        "color-depth":   "16",
        "enable-drive":  "false",
        "enable-audio":  "false",
        "resize-method": "display-update",
    }


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@router.websocket("/{host_name}/{vm_name}")
async def guac_tunnel(
    websocket: WebSocket,
    host_name: str,
    vm_name:   str,
    token:     str = Query(...),
    vm_id:     str = Query(default=""),
):
    """
    WebSocket relay antara browser (guacamole-common-js) dan guacd.
    Backend melakukan handshake dengan guacd menggunakan credentials dari DB,
    meneruskan 'ready' ke browser, lalu relay penuh.
    """
    # Auth
    try:
        user_info = await verify_token(token)
        username = user_info.get("username", "")
    except Exception:
        logger.warning("guac: token tidak valid dari %s", websocket.client)
        await websocket.close(code=4401)
        return

    # Cek blocklist — token yang sudah di-logout harus ditolak
    jti = user_info.get("jti")
    if jti:
        import token_blocklist
        if await token_blocklist.is_revoked(jti):
            logger.warning("guac: token direvoke untuk %s", username)
            await websocket.close(code=4401)
            return

    # VM access check untuk student
    if user_info.get("role") == "student":
        from database import get_student_vm_ids
        vm_id_clean = (vm_id or "").rstrip("?& ")
        if not vm_id_clean:
            logger.warning("guac: akses ditolak — student %s tanpa vm_id", username)
            await websocket.close(code=4403)
            return
        allowed = await get_student_vm_ids(int(user_info["sub"]), host_name, full_only=True)
        if (vm_id_clean, host_name) not in allowed:
            logger.warning("guac: akses ditolak — student %s tidak punya akses ke %s/%s",
                           username, host_name, vm_id_clean)
            await websocket.close(code=4403)
            return

    # guacamole-common-js meminta subprotocol "guacamole"
    await websocket.accept(subprotocol="guacamole")

    # Bersihkan trailing chars dari URL browser
    vm_id    = vm_id.rstrip("?& ")
    cred_key = vm_id if vm_id else vm_name

    logger.info("guac: WebSocket diterima — host=%s vm=%s cred_key=%s", host_name, vm_name, cred_key)

    # Load credentials dari DB
    try:
        creds = await _load_vm_creds(cred_key, host_name)
    except HTTPException as e:
        logger.warning("guac: credentials tidak ditemukan — %s", e.detail)
        await websocket.send_text(_enc("error", e.detail, "771").decode())
        await websocket.close()
        return
    except Exception as e:
        logger.error("guac: error load credentials — %s", e, exc_info=True)
        await websocket.send_text(_enc("error", str(e), "512").decode())
        await websocket.close()
        return

    os_type  = creds["os_type"]
    protocol = "ssh" if os_type == "linux" else "rdp"
    params   = _build_ssh_params(creds) if protocol == "ssh" else _build_rdp_params(creds)

    logger.info("guac: menghubungkan ke guacd %s:%s protokol=%s host_ssh=%s",
                GUACD_HOST, GUACD_PORT, protocol, creds["ssh_host"])

    # Koneksi TCP ke guacd
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(GUACD_HOST, GUACD_PORT),
            timeout=10.0
        )
    except asyncio.TimeoutError:
        logger.error("guac: timeout menghubungi guacd %s:%s", GUACD_HOST, GUACD_PORT)
        await websocket.send_text(_enc("error", f"Timeout koneksi ke guacd ({GUACD_HOST}:{GUACD_PORT})", "512").decode())
        await websocket.close()
        return
    except ConnectionRefusedError:
        logger.error("guac: guacd %s:%s menolak koneksi", GUACD_HOST, GUACD_PORT)
        await websocket.send_text(_enc("error", f"guacd tidak dapat dihubungi di {GUACD_HOST}:{GUACD_PORT}", "512").decode())
        await websocket.close()
        return
    except Exception as e:
        logger.error("guac: gagal terhubung ke guacd — %s", e, exc_info=True)
        await websocket.send_text(_enc("error", f"Koneksi guacd gagal: {e}", "512").decode())
        await websocket.close()
        return

    logger.info("guac: TCP ke guacd OK, mulai handshake")

    # Handshake Guacamole
    try:
        conn_id = await _guacd_handshake(reader, writer, protocol, params)
        logger.info("guac: handshake OK — conn_id=%s protocol=%s vm=%s", conn_id, protocol, vm_name)
    except Exception as e:
        logger.error("guac: handshake gagal — %s", e, exc_info=True)
        try:
            writer.close()
        except Exception:
            pass
        await websocket.send_text(_enc("error", f"Handshake guacd gagal: {e}", "512").decode())
        await websocket.close()
        return

    # Teruskan 'ready' ke browser agar guacamole-common-js mendapat connection UUID
    # dan tidak perlu menunggu instruksi pertama untuk mengetahui conn_id
    try:
        await websocket.send_text(_enc("ready", conn_id).decode())
        logger.info("guac: 'ready' diteruskan ke browser conn_id=%s", conn_id)
    except Exception as e:
        logger.warning("guac: gagal kirim 'ready' ke browser: %s", e)

    # Daftarkan ke registry agar bisa diputus paksa saat logout
    _conn_entry = (websocket, writer)
    _active_connections.setdefault(username, []).append(_conn_entry)

    # Relay bidireksional: guacd ↔ browser
    logger.info("guac: mulai relay bidireksional vm=%s user=%s", vm_name, username)
    try:
        ws_task = asyncio.create_task(_ws_to_guacd(websocket, writer),   name="ws→guacd")
        gd_task = asyncio.create_task(_guacd_to_ws(reader, websocket),   name="guacd→ws")

        done, pending = await asyncio.wait(
            [ws_task, gd_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        # Task lain dibatalkan saat salah satu selesai (koneksi terputus dari satu sisi)
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    except Exception as e:
        logger.debug("guac: relay berakhir — %s", e)
    finally:
        try:
            _active_connections.get(username, []).remove(_conn_entry)
        except ValueError:
            pass
        logger.info("guac: relay selesai, menutup koneksi vm=%s", vm_name)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


# ── Relay helpers ──────────────────────────────────────────────────────────────

async def _ws_to_guacd(ws: WebSocket, writer: asyncio.StreamWriter):
    """Forward keyboard/mouse input dari browser → guacd TCP."""
    try:
        while True:
            try:
                msg = await ws.receive()
            except (WebSocketDisconnect, Exception):
                break

            if msg["type"] != "websocket.receive":
                break

            # guacamole-common-js bisa kirim text atau bytes
            raw = msg.get("bytes") or (msg.get("text") or "").encode("utf-8")
            if not raw:
                continue

            try:
                writer.write(raw)
                await writer.drain()
            except Exception:
                break
    except Exception:
        pass


async def _guacd_to_ws(reader: asyncio.StreamReader, ws: WebSocket):
    """Forward display instructions dari guacd TCP → browser WebSocket."""
    try:
        while True:
            try:
                data = await asyncio.wait_for(reader.read(BUF), timeout=60.0)
            except asyncio.TimeoutError:
                # Tidak kirim apapun saat timeout — WS keepalive ditangani oleh layer TCP/WS
                continue
            except Exception:
                break

            if not data:
                break

            try:
                # Guacamole protocol adalah text (ASCII/UTF-8)
                await ws.send_text(data.decode("utf-8", errors="replace"))
            except Exception:
                break
    except Exception:
        pass
