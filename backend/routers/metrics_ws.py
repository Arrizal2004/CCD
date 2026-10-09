"""
WebSocket streaming metrik agent real-time.

  /ws/metrics/live?token=...&host=...&vm_id=...

Auth: token via query param (browser tidak bisa set header pada WebSocket).
Server men-subscribe channel Redis 'metrics:agent:live' (di-publish oleh poller
exporter :9100) dan meneruskan tiap update ke client.

Pesan Server → Client (JSON):
  { "type": "metric", "host_name": "...", "vm_id": "...", "cpu_pct": .., "mem_pct": ..,
    "rx_bps": .., "tx_bps": .., "total_bps": .., "source": "agent-linux", "ts": .. }
  { "type": "ping" }   # keepalive
"""
import json
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from auth import verify_token, Role
from database import get_pool
from services.vm_agent_poller import LIVE_CHANNEL

router = APIRouter()
log = logging.getLogger("metrics_ws")


async def _student_assigned_vms(user_id: int) -> set:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT vm_id FROM vm_assignments WHERE user_id = $1 AND deleted_at IS NULL", user_id)
    return {r["vm_id"] for r in rows}


@router.websocket("/live")
async def metrics_live(
    websocket: WebSocket,
    token: str = Query(...),
    host: str = Query(default=""),
    vm_id: str = Query(default=""),
):
    # Auth: validasi JWT
    try:
        user = await verify_token(token)
    except Exception:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    # RBAC: student hanya boleh stream VM yang di-assign — langgar → drop 1008
    allowed_vms = None
    if user.get("role") == Role.STUDENT:
        try:
            allowed_vms = await _student_assigned_vms(int(user["sub"]))
        except Exception:
            allowed_vms = set()
        if vm_id and vm_id not in allowed_vms:
            await websocket.close(code=1008)  # policy violation
            return
    scoped = None                         # sysadmin: hanya metrik VM di Proxmox yang ditugaskan kepadanya
    if user.get("role") == Role.SYSADMIN:
        from services import scope
        scoped = await scope.allowed_labels(user)
    r = websocket.app.state.redis
    pubsub = r.pubsub()
    await pubsub.subscribe(LIVE_CHANNEL)

    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=20.0)
            if msg is None:
                # Keepalive — sekaligus deteksi koneksi mati
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue
            try:
                obj = json.loads(msg["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            # RBAC: student tidak boleh menerima metrik VM yang bukan miliknya
            if allowed_vms is not None and obj.get("vm_id") not in allowed_vms:
                continue
            if scoped is not None and (obj.get("host_name") or "").partition("__")[0] not in scoped:
                continue
            # Filter opsional per host / per VM
            if host and obj.get("host_name") != host:
                continue
            if vm_id and obj.get("vm_id") != vm_id:
                continue
            await websocket.send_text(json.dumps({"type": "metric", **obj}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.debug("metrics_ws closed: %s", e)
    finally:
        try:
            await pubsub.unsubscribe(LIVE_CHANNEL)
            await pubsub.aclose()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
