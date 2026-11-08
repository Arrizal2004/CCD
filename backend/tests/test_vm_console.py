"""
Konsol VM (noVNC): sesi sekali pakai, hanya admin dan hanya VM yang menyala, relay WebSocket ke Proxmox (tiruan).
"""
import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from services import proxmox_instances
from tests.conftest import auth

LABEL, NODE, VMID = "labCons", "pve", 7701
URL = f"/api/v1/proxmox/instances/{LABEL}/nodes/{NODE}/vms/{VMID}/console"


class FakeUpstream:
    """Pengganti koneksi websockets ke Proxmox: menjawab setiap kiriman dengan 'echo:<data>'."""
    def __init__(self):
        self.sent, self.closed, self._q = [], False, asyncio.Queue()

    async def send(self, data):
        self.sent.append(data)
        await self._q.put(b"echo:" + (data if isinstance(data, bytes) else data.encode()))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._q.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True
        await self._q.put(None)


@pytest.fixture
def vm(pve, monkeypatch):
    pve.add(VMID, "vm-konsol", "running")

    async def vnc_proxy(node, vmid):
        pve.calls.append(("vncproxy", vmid))
        return {"port": 5900, "ticket": "PVEVNC:abc/ticket", "user": "root@pam"}

    pve.vnc_proxy = vnc_proxy
    pve.verify_ssl = False
    pve.host = "pve.invalid:8006"
    pve._auth_header = lambda: {"Authorization": "PVEAPIToken=x"}
    pve.vnc_websocket_url = lambda node, vmid, port, ticket: f"wss://pve.invalid/{node}/{vmid}/{port}"
    return pve


def test_open_console_needs_admin_and_running_vm(client, vm, sysadmin_token, student_token):
    assert client.post(URL, headers=auth(student_token)).status_code == 403
    vm.vms[VMID]["status"] = "stopped"
    assert client.post(URL, headers=auth(sysadmin_token)).status_code == 409
    vm.vms[VMID]["status"] = "running"
    r = client.post(URL, headers=auth(sysadmin_token))
    assert r.status_code == 200, r.text
    assert r.json()["password"] == "PVEVNC:abc/ticket" and r.json()["vm_name"] == "vm-konsol" and len(r.json()["session"]) > 20
    assert ("vncproxy", VMID) in vm.calls


def test_websocket_relays_once_and_only_for_the_owner(client, vm, sysadmin_token, superadmin_token, monkeypatch):
    import websockets
    upstream = FakeUpstream()

    async def fake_connect(url, **kw):
        assert kw["subprotocols"] == ["binary"] and url.endswith("/5900")
        return upstream

    monkeypatch.setattr(websockets, "connect", fake_connect)
    sid = client.post(URL, headers=auth(sysadmin_token)).json()["session"]

    # akun lain tidak bisa memakai sesi ini (dan sesinya ikut hangus, sekali pakai)
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/ws/console/{sid}?token={superadmin_token}", subprotocols=["binary"]):
            pass
    assert e.value.code == 4403

    sid = client.post(URL, headers=auth(sysadmin_token)).json()["session"]
    with client.websocket_connect(f"/ws/console/{sid}?token={sysadmin_token}", subprotocols=["binary"]) as ws:
        ws.send_bytes(b"RFB 003.008\n")
        assert ws.receive_bytes() == b"echo:RFB 003.008\n"
    assert upstream.sent == [b"RFB 003.008\n"]

    with pytest.raises(WebSocketDisconnect) as e:                                 # sesi sudah terpakai
        with client.websocket_connect(f"/ws/console/{sid}?token={sysadmin_token}"):
            pass
    assert e.value.code == 4403


def test_websocket_rejects_bad_token_and_students(client, vm, student_token):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/console/whatever?token=bukan-token"):
            pass
    assert e.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/ws/console/tidak-ada?token={student_token}"):
            pass
    assert e.value.code == 4403
