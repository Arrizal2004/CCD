"""
Proxmox yang boleh dikelola setiap sysadmin (tabel user_instances).

Superadmin mengelola semua Proxmox. Sysadmin hanya Proxmox yang ditugaskan kepadanya, dan tanpa penugasan
ia tidak melihat apa pun. Mahasiswa tidak terpengaruh: aksesnya ditentukan oleh penugasan VM.
Satu Proxmox dikenali dari labelnya; host VM berbentuk '<label>__<node>'.
"""
from fastapi import Depends, HTTPException, Request

from auth import Role, get_current_user
from database import get_pool
from i18n import tr


def host_label(host_name: str) -> str:
    return (host_name or "").partition("__")[0]


def is_scoped(user: dict) -> bool:
    return user.get("role") == Role.SYSADMIN


def _forbidden() -> HTTPException:
    return HTTPException(403, tr("Anda tidak ditugaskan untuk mengelola Proxmox ini",
                                 "You are not assigned to manage this Proxmox"))


async def labels_of(user_id: int) -> set[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT instance FROM user_instances WHERE user_id = $1", user_id)
    return {r["instance"] for r in rows}


async def allowed_labels(user: dict) -> set[str] | None:
    """Label Proxmox yang boleh dipakai, atau None kalau tidak ada batasan."""
    return await labels_of(int(user["sub"])) if is_scoped(user) else None


async def instance_allowed(user: dict, label: str) -> bool:
    allowed = await allowed_labels(user)
    return allowed is None or label in allowed


async def require_instance(user: dict, label: str) -> None:
    if not await instance_allowed(user, label):
        raise _forbidden()


async def host_allowed(user: dict, host_name: str) -> bool:
    return await instance_allowed(user, host_label(host_name))


async def require_host(user: dict, host_name: str) -> None:
    if not await host_allowed(user, host_name):
        raise _forbidden()


async def hidden_labels(user: dict) -> list[str]:
    """Label Proxmox yang ada tetapi tidak boleh dilihat user ini ([] kalau tanpa batasan)."""
    allowed = await allowed_labels(user)
    if allowed is None:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT label FROM proxmox_instances")
    return sorted(r["label"] for r in rows if r["label"] not in allowed)


async def vm_ips(labels) -> list[str]:
    """IP semua VM di Proxmox-Proxmox ini (kredensial utama dan akun OS)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT ssh_host FROM vm_credentials WHERE ssh_host <> '' AND split_part(host_name, '__', 1) = ANY($1)
               UNION
               SELECT ssh_host FROM vm_os_accounts WHERE ssh_host <> '' AND split_part(host_name, '__', 1) = ANY($1)""",
            list(labels))
    return [r["ssh_host"] for r in rows]


async def scope_ips(user: dict) -> list[str] | None:
    """IP VM yang boleh dilihat user ini, atau None kalau tanpa batasan."""
    allowed = await allowed_labels(user)
    return None if allowed is None else await vm_ips(allowed)


async def set_instances(user_id: int, labels: list[str], by: str) -> tuple[list[str], list[str]]:
    """Ganti daftar Proxmox milik satu sysadmin. Return (ditambah, dicabut)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            known = {r["label"] for r in await conn.fetch("SELECT label FROM proxmox_instances")}
            wanted = {l for l in labels if l in known}
            have = {r["instance"] for r in await conn.fetch("SELECT instance FROM user_instances WHERE user_id = $1", user_id)}
            for l in sorted(wanted - have):
                await conn.execute("INSERT INTO user_instances (user_id, instance, assigned_by) VALUES ($1, $2, $3)",
                                   user_id, l, by)
            for l in sorted(have - wanted):
                await conn.execute("DELETE FROM user_instances WHERE user_id = $1 AND instance = $2", user_id, l)
    return sorted(wanted - have), sorted(have - wanted)


async def add_instance(user_id: int, label: str, by: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_instances (user_id, instance, assigned_by) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            user_id, label, by)


async def enforce_path(request: Request, user: dict = Depends(get_current_user)) -> None:
    """Dependency untuk router yang alamatnya memuat {label} atau {host_name}: sysadmin ditolak di Proxmox
    yang bukan miliknya. Peran lain tidak terpengaruh."""
    if not is_scoped(user):
        return
    params = request.path_params
    if "label" in params:
        await require_instance(user, params["label"])
    elif "host_name" in params:
        await require_host(user, params["host_name"])
