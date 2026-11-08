"""
Konsol VM (noVNC) lewat dashboard: browser ↔ WebSocket /ws/console/{sesi} ↔ backend ↔ vncwebsocket Proxmox.

Sesi disiapkan oleh POST .../vms/{vmid}/console (routers/vm_ops.py) dan hanya bisa dipakai sekali, oleh akun
yang sama, dalam 60 detik. Koneksi ke Proxmox memakai token API instance itu, jadi browser tidak butuh akses
ke port 8006. Khusus admin. Sesi paling lama 8 jam; pembukaan dan penutupan tercatat di audit log.
"""
import asyncio
import logging
import ssl
import time

from fastapi import APIRouter, Query, WebSocket

from auth import verify_token
from i18n import tr
from services import onetime, scope
from services import proxmox_instances as pve_instances
from services.audit import both, log_activity

log = logging.getLogger("console")
router = APIRouter()
MAX_SECONDS = 8 * 3600
ADMIN_ROLES = ("superadmin", "sysadmin")


@router.websocket("/{session}")
async def console_ws(websocket: WebSocket, session: str, token: str = Query(...)):
    try:
        user = await verify_token(token)
    except Exception:
        await websocket.close(code=4401)
        return
    data = await onetime.pop("console", session)
    if not data or data["uid"] != int(user["sub"]) or user.get("role") not in ADMIN_ROLES:
        await websocket.close(code=4403)
        return
    host_key = f"{data['label']}__{data['node']}"
    if not await scope.host_allowed(user, host_key):
        await websocket.close(code=4403)
        return

    import websockets
    try:
        client = await pve_instances.get_client(data["label"])
    except ValueError:
        await websocket.close(code=4404)
        return
    ctx = ssl.create_default_context()
    if not client.verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        upstream = await websockets.connect(
            client.vnc_websocket_url(data["node"], data["vmid"], data["port"], data["ticket"]), ssl=ctx,
            additional_headers=client._auth_header(), subprotocols=["binary"], open_timeout=10, max_size=None)
    except Exception as e:
        log.warning("konsol %s/%s gagal tersambung ke Proxmox: %s", host_key, data["vmid"], e)
        await websocket.close(code=4502)
        return

    requested = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol="binary" if "binary" in requested else None)
    started = time.monotonic()

    async def browser_to_pve():
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            payload = msg.get("bytes") if msg.get("bytes") is not None else (msg.get("text") or "").encode()
            await upstream.send(payload)

    async def pve_to_browser():
        async for chunk in upstream:
            if isinstance(chunk, str):
                await websocket.send_text(chunk)
            else:
                await websocket.send_bytes(chunk)

    tasks = [asyncio.create_task(browser_to_pve()), asyncio.create_task(pve_to_browser())]
    try:
        await asyncio.wait(tasks, timeout=MAX_SECONDS, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for closer in (upstream.close, websocket.close):
            try:
                await closer()
            except Exception:
                pass
        minutes = max(1, round((time.monotonic() - started) / 60))
        name = data["vm_name"]
        # shield: catatan penutupan tetap tersimpan walau tugas ini dibatalkan (klien putus, server berhenti)
        await asyncio.shield(log_activity(user, "VM_CONSOLE_CLOSE", "INFO", {"id": str(data["vmid"]), "name": f"{data['label']}/{data['node']}/{data['vmid']}"},
                             both(lambda: tr(f"{user.get('username')} menutup konsol VM {name} setelah sekitar {minutes} menit",
                                             f"{user.get('username')} closed the console of VM {name} after about {minutes} minutes"))))
