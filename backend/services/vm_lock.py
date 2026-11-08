"""
Kunci VM = flag Protection di config Proxmox: Proxmox sendiri menolak menghapus VM (dan disknya) selama aktif,
juga dari antarmuka Proxmox. Daftar VM Proxmox tidak memuat flag ini, jadi config dibaca per VM dan disimpan
sementara 60 detik. (label, node, vmid) → (waktu_baca, terkunci atau None kalau tidak terbaca).
"""
import asyncio
import time

TTL = 60
_cache: dict[tuple[str, str, int], tuple[float, bool | None]] = {}


def is_protected(cfg: dict) -> bool:
    return str(cfg.get("protection", "0")) == "1"


def remember(label: str, node: str, vmid: int, locked: bool) -> None:
    _cache[(label, node, vmid)] = (time.monotonic(), locked)


async def flags(client, label: str, node: str, vmids: list[int]) -> dict[str, bool | None]:
    sem = asyncio.Semaphore(8)

    async def one(vmid: int):
        hit = _cache.get((label, node, vmid))
        if hit and time.monotonic() - hit[0] < TTL:
            return vmid, hit[1]
        async with sem:
            try:
                locked = is_protected(await asyncio.wait_for(client.get_vm_config(node, vmid), timeout=5))
            except Exception:
                locked = None
        _cache[(label, node, vmid)] = (time.monotonic(), locked)
        return vmid, locked

    return {str(v): locked for v, locked in await asyncio.gather(*(one(v) for v in vmids))}
