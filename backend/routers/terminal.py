"""
WebSocket terminal endpoint.

Dua mode:
  /ws/terminal/linux/{host}/{vm}   — SSH PTY ke Linux VM via Paramiko
  /ws/terminal/windows/{host}/{vm} — PS Direct relay via agent (Redis pub/sub)

Auth: token dikirim sebagai query param karena browser tidak bisa set header pada WebSocket.

Protokol pesan (JSON):
  Client → Server: { "type": "input"|"resize"|"close", "data": "...", "rows": 24, "cols": 80 }
  Server → Client: { "type": "output"|"error"|"closed",  "data": "...", "message": "..." }
"""
import asyncio
import json
import secrets
import select as _select

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from auth import verify_token
from routers.ssh_creds import get_vm_ssh_client
from i18n import tr

router = APIRouter()

PING_INTERVAL = 20  # detik


# ── Linux SSH Terminal ─────────────────────────────────────────────────────────

@router.websocket("/linux/{host_name}/{vm_name}")
async def terminal_linux(
    websocket: WebSocket,
    host_name: str,
    vm_name: str,
    token: str = Query(...),
    vm_id: str = Query(default=""),
):
    # Autentikasi dari query param
    try:
        user = await verify_token(token)
    except Exception:
        await websocket.close(code=4401)
        return

    # Cek blocklist
    jti = user.get("jti")
    if jti:
        import token_blocklist
        if await token_blocklist.is_revoked(jti):
            await websocket.close(code=4401)
            return

    # VM access check untuk student
    if user.get("role") == "student":
        from database import get_student_vm_ids
        vm_id_clean = (vm_id or "").rstrip("?& ")
        if not vm_id_clean:
            await websocket.close(code=4403)
            return
        allowed = await get_student_vm_ids(int(user["sub"]), host_name, full_only=True)
        if (vm_id_clean, host_name) not in allowed:
            await websocket.close(code=4403)
            return
    elif user.get("role") == "sysadmin":
        from services import scope
        if not await scope.host_allowed(user, host_name):
            await websocket.close(code=4403)
            return

    await websocket.accept()

    # Gunakan vm_id untuk lookup credentials (lebih akurat dari vm_name)
    cred_key = vm_id if vm_id else vm_name
    try:
        client = await get_vm_ssh_client(cred_key, host_name)
    except Exception as e:
        await _send(websocket, {"type": "error", "message": str(e)})
        await websocket.close()
        return

    # Buka PTY
    try:
        channel, ssh_client = await client.open_pty(rows=24, cols=80)
    except Exception as e:
        await _send(websocket, {"type": "error", "message": tr(f"SSH gagal: {e}", f"SSH failed: {e}")})
        await websocket.close()
        return

    await _send(websocket, {"type": "output", "data": f"\r\n\x1b[32mTerhubung ke {vm_name}\x1b[0m\r\n"})

    try:
        # Dua goroutine paralel: baca dari SSH → WS, baca dari WS → SSH
        ssh_to_ws = asyncio.create_task(_read_ssh_to_ws(channel, websocket))
        ws_to_ssh = asyncio.create_task(_read_ws_to_ssh(websocket, channel))

        done, pending = await asyncio.wait(
            [ssh_to_ws, ws_to_ssh],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            channel.close()
            ssh_client.close()
        except Exception:
            pass
        try:
            await _send(websocket, {"type": "closed", "message": tr("Sesi SSH ditutup",
                                                                    "SSH session closed")})
            await websocket.close()
        except Exception:
            pass


async def _read_ssh_to_ws(channel, ws: WebSocket):
    """Baca output SSH channel dan kirim ke WebSocket."""
    loop = asyncio.get_event_loop()
    while True:
        # Non-blocking read via to_thread dengan timeout
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(_read_channel, channel),
                timeout=PING_INTERVAL + 5,
            )
        except asyncio.TimeoutError:
            # Kirim keepalive kosong
            await _send(ws, {"type": "output", "data": ""})
            continue

        if data is None:
            break
        if data:
            await _send(ws, {"type": "output", "data": data.decode(errors="replace")})


def _read_channel(channel) -> bytes | None:
    """Blocking read dari paramiko channel. Return None jika channel tertutup."""
    while True:
        if channel.closed or channel.exit_status_ready():
            remaining = b""
            while channel.recv_ready():
                remaining += channel.recv(4096)
            return remaining or None
        if channel.recv_ready():
            return channel.recv(4096)
        # poll setiap 50ms
        import time
        time.sleep(0.05)


async def _read_ws_to_ssh(ws: WebSocket, channel):
    """Terima input dari WebSocket dan tulis ke SSH channel."""
    while True:
        try:
            raw = await ws.receive_text()
        except WebSocketDisconnect:
            break
        except Exception:
            break

        try:
            msg = json.loads(raw)
        except Exception:
            continue

        if msg.get("type") == "input":
            data = msg.get("data", "")
            if data:
                await asyncio.to_thread(channel.send, data.encode())

        elif msg.get("type") == "resize":
            rows = int(msg.get("rows", 24))
            cols = int(msg.get("cols", 80))
            await asyncio.to_thread(channel.resize_pty, width=cols, height=rows)

        elif msg.get("type") == "close":
            break


# ── Windows PS Direct Terminal ─────────────────────────────────────────────────

@router.websocket("/windows/{host_name}/{vm_name}")
async def terminal_windows(
    websocket: WebSocket,
    host_name: str,
    vm_name: str,
    token: str = Query(...),
    vm_id: str = Query(default=""),
):
    """
    Terminal Windows via PS Direct relay melalui agent.
    Mode: command-execution (bukan PTY sejati).
    User mengetik perintah PS, tekan Enter → agent eksekusi → output kembali.
    """
    try:
        user = await verify_token(token)
    except Exception:
        await websocket.close(code=4401)
        return

    # Cek blocklist
    jti = user.get("jti")
    if jti:
        import token_blocklist
        if await token_blocklist.is_revoked(jti):
            await websocket.close(code=4401)
            return

    # VM access check untuk student
    if user.get("role") == "student":
        from database import get_student_vm_ids
        vm_id_clean = (vm_id or "").rstrip("?& ")
        if not vm_id_clean:
            await websocket.close(code=4403)
            return
        allowed = await get_student_vm_ids(int(user["sub"]), host_name, full_only=True)
        if (vm_id_clean, host_name) not in allowed:
            await websocket.close(code=4403)
            return
    elif user.get("role") == "sysadmin":
        from services import scope
        if not await scope.host_allowed(user, host_name):
            await websocket.close(code=4403)
            return

    await websocket.accept()

    # Ambil VM credentials untuk PS Direct
    cred_key = vm_id if vm_id else vm_name
    try:
        pool_row = await _get_vm_ps_creds(cred_key, host_name)
    except Exception as e:
        await _send(websocket, {"type": "error", "message": str(e)})
        await websocket.close()
        return

    r = websocket.app.state.redis

    banner = (
        f"\r\n\x1b[36m╔══════════════════════════════════════╗\r\n"
        f"║  HyperPanel PS Console — {vm_name[:18]:<18} ║\r\n"
        f"╚══════════════════════════════════════╝\x1b[0m\r\n"
        f"\x1b[33mMode: PowerShell Direct (non-interactive)\x1b[0m\r\n"
        f"Ketik perintah PowerShell dan tekan Enter.\r\n\r\n"
        f"\x1b[32mPS [{vm_name}]>\x1b[0m "
    )
    await _send(websocket, {"type": "output", "data": banner})

    input_buf = ""

    while True:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
        except asyncio.TimeoutError:
            continue
        except WebSocketDisconnect:
            break
        except Exception:
            break

        try:
            msg = json.loads(raw)
        except Exception:
            continue

        if msg.get("type") == "close":
            break

        if msg.get("type") == "input":
            char = msg.get("data", "")
            # Echo karakter ke terminal
            await _send(websocket, {"type": "output", "data": char})

            if char in ("\r", "\n"):
                command = input_buf.strip()
                input_buf = ""
                if not command:
                    await _send(websocket, {"type": "output", "data": f"\r\n\x1b[32mPS [{vm_name}]>\x1b[0m "})
                    continue

                await _send(websocket, {"type": "output", "data": "\r\n"})

                # Kirim ke agent via Redis dan tunggu hasil
                req_id = secrets.token_hex(8)
                cmd_payload = json.dumps({
                    "vm_name":    vm_name,
                    "action":     "ps-exec",
                    "command":    command,
                    "request_id": req_id,
                    "vm_user":    pool_row.get("username", ""),
                    "vm_pass":    pool_row.get("password_plain", ""),
                })
                await r.publish(f"commands:{host_name}", cmd_payload)

                # Poll hasil
                result_key = f"ps-exec:{host_name}:{vm_name}:{req_id}"
                output = None
                for _ in range(60):  # 30s timeout
                    await asyncio.sleep(0.5)
                    raw_result = await r.get(result_key)
                    if raw_result:
                        await r.delete(result_key)
                        result = json.loads(raw_result)
                        output = result.get("output", "")
                        err = result.get("error", "")
                        if err:
                            output = f"\x1b[31m{err}\x1b[0m"
                        break

                if output is None:
                    output = "\x1b[33mTimeout: agent tidak merespons dalam 30 detik\x1b[0m"

                # Tampilkan output
                lines = output.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                for line in lines:
                    await _send(websocket, {"type": "output", "data": line + "\r\n"})

                await _send(websocket, {"type": "output", "data": f"\x1b[32mPS [{vm_name}]>\x1b[0m "})

            elif char in ("\x7f", "\x08"):  # backspace
                if input_buf:
                    input_buf = input_buf[:-1]
                    await _send(websocket, {"type": "output", "data": "\x1b[D \x1b[D"})
            elif char == "\x03":  # Ctrl+C
                input_buf = ""
                await _send(websocket, {"type": "output", "data": f"^C\r\n\x1b[32mPS [{vm_name}]>\x1b[0m "})
            else:
                input_buf += char

    await _send(websocket, {"type": "closed", "message": tr("Sesi terminal ditutup",
                                                            "Terminal session closed")})
    try:
        await websocket.close()
    except Exception:
        pass


async def _get_vm_ps_creds(vm_name: str, host_name: str) -> dict:
    """Ambil PS Direct credentials dari DB. Raise jika tidak ada."""
    from database import get_pool
    from services.ssh_client import decrypt_secret

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT cred_type, username, password_enc "
            "FROM vm_credentials WHERE vm_id = $1 AND host_name = $2",
            vm_name, host_name
        )
    if not row:
        raise Exception(
            "PS Direct credentials belum dikonfigurasi. "
            "Set via tab 'Koneksi' pada VM detail modal."
        )
    password_plain = decrypt_secret(row["password_enc"]) if row.get("password_enc") else ""
    return {"username": row["username"], "password_plain": password_plain}


async def _send(ws: WebSocket, data: dict):
    try:
        await ws.send_text(json.dumps(data))
    except Exception:
        pass
