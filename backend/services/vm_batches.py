"""
Buat VM massal untuk satu kelas (grup): satu VM per anggota dari template yang sama, dengan spek, switch,
dan masa sewa yang sama. VM dibuat satu per satu di latar belakang lewat jalur yang sama dengan form
Create VM (routers.proxmox.create_vm_core), lalu langsung di-assign ke mahasiswanya. Progres disimpan di
vm_batches/vm_batch_items supaya bisa dipantau walau halaman ditutup dan dilanjutkan setelah gagal.
"""
import asyncio
import csv
import io
import ipaddress
import json
import logging
import re

from fastapi import HTTPException

from database import get_pool
from services import networks, proxmox_instances
from services import proxmox_provision as provision
from services.guest_accounts import generate_password
from services.proxmox_client import ProxmoxError
from services.ssh_client import decrypt_secret, encrypt_secret
from i18n import tr

log = logging.getLogger("vm_batches")

USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
MAX_ITEMS = 200
_locks: dict[str, asyncio.Lock] = {}
_tasks: set = set()


def slug(text: str) -> str:
    """Bagian nama VM/hostname: huruf kecil, angka, dan tanda hubung."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(text or "").lower())).strip("-")


def vm_name_for(prefix: str, username: str) -> str:
    return f"{prefix}-{slug(username) or 'vm'}"[:63].rstrip("-")


def os_username_for(username: str) -> str:
    """Username OS dari username dashboard; yang tidak memenuhi aturan Linux jatuh ke 'siswa'."""
    name = re.sub(r"[^a-z0-9_-]+", "_", str(username or "").lower()).strip("_-")[:32]
    if name and name[0].isdigit():
        name = ("u" + name)[:32]
    return name if USER_RE.match(name or "") and name != "root" else "siswa"


async def _members(group_id: int) -> tuple[dict, list[dict]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        group = await conn.fetchrow("SELECT id, name FROM groups WHERE id = $1", group_id)
        if not group:
            raise HTTPException(404, tr("Grup tidak ditemukan", "Group not found"))
        rows = await conn.fetch(
            """SELECT u.id, u.username, u.full_name, u.is_active FROM group_members gm
               JOIN users u ON u.id = gm.user_id
               WHERE gm.group_id = $1 AND u.deleted_at IS NULL
               ORDER BY u.username""", group_id)
    return dict(group), [dict(r) for r in rows]


async def plan(instance: str, node: str, group_id: int, template_vmid: int, prefix: str | None,
               network_id: int | None, os_username: str | None, memory_mb: int | None, start: bool,
               user_ids: list[int] | None = None) -> dict:
    """Rencana tanpa membuat apa pun: nama VM dan username OS per mahasiswa, bentrok nama, dan kapasitas."""
    group, members = await _members(group_id)
    if user_ids is not None:
        wanted = set(user_ids)
        members = [m for m in members if m["id"] in wanted]
    if not members:
        raise HTTPException(400, tr("Grup ini belum punya anggota",
                                    "This group has no members yet") if user_ids is None else tr("Tidak ada anggota grup yang dipilih",
                                                                                                                                 "No group members selected"))
    if len(members) > MAX_ITEMS:
        raise HTTPException(400, tr(f"Maksimal {MAX_ITEMS} VM per batch",
                                    f"At most {MAX_ITEMS} VMs per batch"))
    prefix = slug(prefix if (prefix or "").strip() else group["name"]) or "vm"
    if len(prefix) > 40:
        raise HTTPException(400, tr("Awalan nama VM maksimal 40 karakter",
                                    "The VM name prefix may be at most 40 characters"))
    fixed_user = (os_username or "").strip()
    if fixed_user and (not USER_RE.match(fixed_user) or fixed_user == "root"):
        raise HTTPException(400, tr("Username OS tidak valid: huruf kecil, angka, _ atau -, diawali huruf, bukan root",
                                    "Invalid OS username: lowercase letters, digits, _ or -, starting with a letter, not root"))

    try:
        client = await proxmox_instances.get_client(instance)
    except ValueError as e:
        raise HTTPException(404, str(e))
    try:
        tpl = next((t for t in await provision.list_templates(client, node) if t["vmid"] == template_vmid), None)
        existing = {(vm.get("name") or "").lower() for vm in await client.list_vms(node)}
    except ProxmoxError as e:
        raise HTTPException(502, f"Proxmox: {(e.detail or '')[:150]}")
    if not tpl:
        raise HTTPException(404, tr("Template tidak ditemukan di node ini",
                                    "Template not found on this node"))
    if not tpl["cloudinit"]:
        raise HTTPException(400, tr("Template belum punya CloudInit drive, jadi akun dan IP VM tidak bisa diatur otomatis",
                                    "The template has no CloudInit drive, so the VM account and IP cannot be set automatically"))

    items, seen = [], set()
    for m in members:
        name = vm_name_for(prefix, m["username"])
        conflict = None
        if name.lower() in existing:
            conflict = tr("Nama VM sudah dipakai VM lain di node ini",
                          "The VM name is already used by another VM on this node")
        elif name.lower() in seen:
            conflict = tr("Nama VM kembar di batch ini", "Duplicate VM name in this batch")
        seen.add(name.lower())
        items.append({"user_id": m["id"], "username": m["username"], "full_name": m["full_name"], "vm_name": name,
                      "os_username": fixed_user or os_username_for(m["username"]), "conflict": conflict,
                      "inactive": not m["is_active"]})

    warnings = []
    free_ips = None
    if network_id is not None:
        net = await networks.get_network(network_id)
        if net["instance"] != instance:
            raise HTTPException(400, tr("Switch itu milik Proxmox lain",
                                        "That switch belongs to another Proxmox"))
        subnet = ipaddress.ip_network(net["cidr"])
        used = await networks.used_ips(client, instance, subnet)
        free_ips = sum(1 for ip in subnet.hosts() if str(ip) != net["gateway"] and ip not in used)
        if free_ips < len(items):
            warnings.append(tr(f"Switch {net['name']} hanya punya {free_ips} IP kosong untuk {len(items)} VM",
                               f"Switch {net['name']} has only {free_ips} free IPs for {len(items)} VMs"))
    memory = memory_mb or tpl["memory_mb"]
    node_free_mb = None
    try:
        mem = (await client.get_node_status(node)).get("memory") or {}
        node_free_mb = int((mem.get("available") or mem.get("free") or 0) / 1048576) or None
    except ProxmoxError:
        pass
    needed = memory * len(items)
    if start and node_free_mb is not None and needed > node_free_mb:
        warnings.append(tr(f"Butuh sekitar {needed} MB RAM untuk menyalakan semua VM, sedangkan RAM kosong node "
                           f"sekitar {node_free_mb} MB. Matikan opsi 'Nyalakan' atau kurangi RAM per VM",
                           f"Starting every VM needs about {needed} MB of RAM, but the node has about "
                           f"{node_free_mb} MB free. Turn off 'Start' or lower the RAM per VM"))
    inactive = sum(1 for i in items if i["inactive"])
    if inactive and user_ids is None:
        warnings.append(tr(f"{inactive} anggota berstatus nonaktif, jadi tidak dicentang. Centang kalau VM-nya tetap ingin disiapkan",
                           f"{inactive} members are inactive, so they are not ticked. Tick them if their VMs should still be prepared"))
    conflicts = sum(1 for i in items if i["conflict"])
    if conflicts:
        warnings.append(tr(f"{conflicts} nama VM bentrok; ganti awalan nama atau hapus centang mahasiswanya",
                           f"{conflicts} VM name conflicts; change the name prefix or untick those students"))
    return {"group": group, "prefix": prefix, "items": items, "count": len(items),
            "template": {k: tpl[k] for k in ("vmid", "name", "cores", "memory_mb", "disk_gb")},
            "memory_needed_mb": needed, "node_free_mb": node_free_mb, "free_ips": free_ips, "warnings": warnings}


async def start(instance: str, node: str, body: dict, user: dict) -> int:
    """Simpan batch beserta item-itemnya lalu jalankan pembuatannya di latar belakang."""
    p = await plan(instance, node, body["group_id"], body["template_vmid"], body.get("prefix"), body.get("network_id"),
                   body.get("os_username"), body.get("memory_mb"), body.get("start", False), body.get("user_ids"))
    if any(i["conflict"] for i in p["items"]):
        raise HTTPException(409, tr("Ada nama VM yang bentrok. Ganti awalan nama atau hapus centang mahasiswanya",
                                    "Some VM names conflict. Change the name prefix or untick those students"))
    if p["free_ips"] is not None and p["free_ips"] < p["count"]:
        raise HTTPException(409, p["warnings"][0])
    settings = {k: body.get(k) for k in ("cores", "memory_mb", "disk_gb", "network_id", "lease_days", "start")}
    settings["created_by_role"] = user.get("role")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            batch_id = await conn.fetchval(
                """INSERT INTO vm_batches (instance, node, group_id, group_name, template_vmid, settings, created_by_id, created_by)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8) RETURNING id""",
                instance, node, p["group"]["id"], p["group"]["name"], body["template_vmid"], json.dumps(settings),
                int(user["sub"]), user.get("username"))
            for i in p["items"]:
                await conn.execute(
                    """INSERT INTO vm_batch_items (batch_id, user_id, username, full_name, vm_name, os_username, password_enc)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                    batch_id, i["user_id"], i["username"], i["full_name"], i["vm_name"], i["os_username"],
                    encrypt_secret(generate_password()))
    _spawn(batch_id)
    return batch_id


def _spawn(batch_id: int) -> None:
    task = asyncio.create_task(run(batch_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def run(batch_id: int) -> None:
    """Kerjakan item yang masih pending satu per satu. Satu batch per Proxmox berjalan bergantian."""
    from routers.proxmox import CreateVmRequest, create_vm_core      # di sini supaya tidak saling impor
    from services.assignments import assign_vm

    pool = await get_pool()
    async with pool.acquire() as conn:
        batch = await conn.fetchrow("SELECT * FROM vm_batches WHERE id = $1", batch_id)
    if not batch:
        return
    s = json.loads(batch["settings"]) if isinstance(batch["settings"], str) else batch["settings"]
    actor = {"sub": str(batch["created_by_id"] or 0), "username": batch["created_by"], "role": s.get("created_by_role")}
    host_key = f"{batch['instance']}__{batch['node']}"
    async with _locks.setdefault(batch["instance"], asyncio.Lock()):
        while True:
            async with pool.acquire() as conn:
                item = await conn.fetchrow(
                    """UPDATE vm_batch_items SET status = 'creating', updated_at = NOW()
                       WHERE id = (SELECT id FROM vm_batch_items WHERE batch_id = $1 AND status = 'pending' ORDER BY id LIMIT 1)
                       RETURNING *""", batch_id)
            if not item:
                break
            result, error = None, None
            try:
                req = CreateVmRequest(
                    template_vmid=batch["template_vmid"], name=item["vm_name"], username=item["os_username"],
                    password=decrypt_secret(item["password_enc"]), ip_mode="static" if s.get("network_id") else "dhcp",
                    network_id=s.get("network_id"), cores=s.get("cores"), memory_mb=s.get("memory_mb"),
                    disk_gb=s.get("disk_gb"), start=bool(s.get("start")), lease_days=s.get("lease_days"))
                result = await create_vm_core(batch["instance"], batch["node"], req, actor, None, wait_for_agent=False)
                if item["user_id"]:
                    await assign_vm(item["user_id"], str(result["vmid"]), host_key, result["name"])
            except HTTPException as e:
                error = str(e.detail)[:300]
            except Exception as e:                     # satu VM gagal tidak menghentikan VM lainnya
                log.exception("Batch %s: VM %s gagal dibuat", batch_id, item["vm_name"])
                error = f"{type(e).__name__}: {str(e)[:250]}"
            async with pool.acquire() as conn:
                if error:
                    await conn.execute("UPDATE vm_batch_items SET status = 'failed', error = $2, updated_at = NOW() WHERE id = $1",
                                       item["id"], error)
                else:
                    await conn.execute(
                        "UPDATE vm_batch_items SET status = 'done', vmid = $2, ip = $3, error = NULL, updated_at = NOW() WHERE id = $1",
                        item["id"], result["vmid"], result.get("static_ip") or result.get("agent_ip"))
        async with pool.acquire() as conn:
            await conn.execute("UPDATE vm_batches SET status = 'done', finished_at = NOW() WHERE id = $1", batch_id)


async def retry(batch_id: int) -> None:
    """Ulangi item yang gagal (dan lanjutkan yang belum sempat dibuat)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        batch = await conn.fetchrow("SELECT status FROM vm_batches WHERE id = $1", batch_id)
        if not batch:
            raise HTTPException(404, tr("Batch tidak ditemukan", "Batch not found"))
        if batch["status"] == "running":
            raise HTTPException(409, tr("Batch ini masih berjalan", "This batch is still running"))
        n = await conn.fetchval(
            """WITH u AS (UPDATE vm_batch_items SET status = 'pending', error = NULL, updated_at = NOW()
                          WHERE batch_id = $1 AND status IN ('failed', 'pending') RETURNING 1)
               SELECT COUNT(*) FROM u""", batch_id)
        if not n:
            raise HTTPException(400, tr("Tidak ada VM yang perlu diulang", "No VMs need to be retried"))
        await conn.execute("UPDATE vm_batches SET status = 'running', finished_at = NULL WHERE id = $1", batch_id)
    _spawn(batch_id)


async def recover_interrupted() -> None:
    """Saat backend mulai: batch yang tadinya berjalan ditandai terputus supaya bisa dilanjutkan dari halaman."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE vm_batch_items SET status = 'failed', updated_at = NOW(),
                   error = 'Backend berhenti saat VM ini dibuat. Cek di Proxmox, hapus kalau setengah jadi, lalu ulangi'
               WHERE status = 'creating'""")
        await conn.execute("UPDATE vm_batches SET status = 'interrupted' WHERE status = 'running'")


async def get(batch_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        batch = await conn.fetchrow("SELECT * FROM vm_batches WHERE id = $1", batch_id)
        if not batch:
            raise HTTPException(404, tr("Batch tidak ditemukan", "Batch not found"))
        items = await conn.fetch(
            """SELECT id, user_id, username, full_name, vm_name, os_username, status, vmid, ip, error, updated_at
               FROM vm_batch_items WHERE batch_id = $1 ORDER BY id""", batch_id)
    out = dict(batch)
    out["settings"] = json.loads(out["settings"]) if isinstance(out["settings"], str) else out["settings"]
    out["items"] = [dict(i) for i in items]
    out["counts"] = {s: sum(1 for i in items if i["status"] == s) for s in ("pending", "creating", "done", "failed")}
    return out


async def recent(limit: int = 20) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT b.id, b.instance, b.node, b.group_name, b.status, b.created_by, b.created_at, b.finished_at,
                      COUNT(i.*) AS total,
                      COUNT(i.*) FILTER (WHERE i.status = 'done')   AS done,
                      COUNT(i.*) FILTER (WHERE i.status = 'failed') AS failed
               FROM vm_batches b LEFT JOIN vm_batch_items i ON i.batch_id = b.id
               GROUP BY b.id ORDER BY b.id DESC LIMIT $1""", limit)
    return [dict(r) for r in rows]


async def credentials_csv(batch_id: int) -> str:
    """CSV kredensial (dengan BOM supaya terbaca benar di Excel)."""
    batch = await get(batch_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        pw = {r["id"]: decrypt_secret(r["password_enc"])
              for r in await conn.fetch("SELECT id, password_enc FROM vm_batch_items WHERE batch_id = $1", batch_id)}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["vm_name", "username", "full_name", "ip", "os_username", "os_password", "vmid", "status", "error"])
    for i in batch["items"]:
        w.writerow([i["vm_name"], i["username"], i["full_name"], i["ip"] or "", i["os_username"], pw[i["id"]],
                    i["vmid"] or "", i["status"], i["error"] or ""])
    return "﻿" + buf.getvalue()
