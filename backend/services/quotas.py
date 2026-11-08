"""
Kuota per mahasiswa: batas jumlah VM, total vCPU, total RAM, dan total disk dari VM yang di-assign langsung
kepadanya (vm_assignments). VM bersama lewat grup tidak dihitung. Hanya akun mahasiswa yang dibatasi.

Nilai per bidang diambil dari lapisan pertama yang terisi: kuota pengguna, kuota grup terbesar dari grup yang
ia ikuti, lalu bawaan sistem. 0 berarti tanpa batas, termasuk untuk menimpa batas dari lapisan di bawahnya.
Dicek saat VM di-assign (services/assignments.assign_vm), jadi berlaku juga untuk VM massal dan Infra Request.
"""
from fastapi import HTTPException

from database import get_pool
from i18n import tr
from services import app_settings

KEY = "quota_defaults"
FIELDS = ("max_vms", "max_vcpu", "max_ram_mb", "max_disk_gb")
DEFAULTS = {f: 0 for f in FIELDS}            # 0 = tanpa batas
MAX_VALUE = {"max_vms": 1000, "max_vcpu": 10000, "max_ram_mb": 100_000_000, "max_disk_gb": 1_000_000}


def clean(body: dict, allow_null: bool) -> dict:
    """Validasi masukan: bilangan bulat >= 0. allow_null: kosong berarti 'ikut lapisan berikutnya'."""
    out = {}
    for f in FIELDS:
        v = (body or {}).get(f)
        if v in (None, ""):
            if not allow_null:
                v = 0
            else:
                out[f] = None
                continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise HTTPException(400, tr(f"Nilai '{f}' harus bilangan bulat", f"'{f}' must be a whole number"))
        if n < 0 or n > MAX_VALUE[f]:
            raise HTTPException(400, tr(f"Nilai '{f}' di luar batas (0 sampai {MAX_VALUE[f]})", f"'{f}' is out of range (0 to {MAX_VALUE[f]})"))
        out[f] = n
    return out


async def get_defaults() -> dict:
    return await app_settings.get(KEY, DEFAULTS)


async def save_defaults(body: dict, by: str) -> dict:
    data = clean(body, allow_null=False)
    await app_settings.save(KEY, data, by)
    return data


async def layers(user_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        own = await conn.fetchrow("SELECT max_vms, max_vcpu, max_ram_mb, max_disk_gb FROM user_quotas WHERE user_id = $1", user_id)
        grp = await conn.fetchrow(
            """SELECT max(max_vms) AS max_vms, max(max_vcpu) AS max_vcpu, max(max_ram_mb) AS max_ram_mb, max(max_disk_gb) AS max_disk_gb
               FROM group_quotas q JOIN group_members m ON m.group_id = q.group_id WHERE m.user_id = $1""", user_id)
        # 0 (tanpa batas) harus menang atas angka dari grup lain: max() sudah benar kecuali 0 tersembunyi di balik NULL
        zero = await conn.fetchrow(
            """SELECT bool_or(max_vms = 0) AS max_vms, bool_or(max_vcpu = 0) AS max_vcpu,
                      bool_or(max_ram_mb = 0) AS max_ram_mb, bool_or(max_disk_gb = 0) AS max_disk_gb
               FROM group_quotas q JOIN group_members m ON m.group_id = q.group_id WHERE m.user_id = $1""", user_id)
    group = {f: (0 if zero[f] else grp[f]) for f in FIELDS}
    return {"user": dict(own) if own else {f: None for f in FIELDS}, "group": group, "defaults": await get_defaults()}


async def effective(user_id: int) -> dict:
    """Batas yang berlaku per bidang, dengan 'source' (user | group | default | none) dan 0 = tanpa batas."""
    ly = await layers(user_id)
    out, source = {}, {}
    for f in FIELDS:
        for name in ("user", "group", "defaults"):
            v = ly[name].get(f)
            if v is not None:
                out[f], source[f] = int(v), {"defaults": "default"}.get(name, name)
                break
        else:
            out[f], source[f] = 0, "none"
    return {"limits": out, "source": source}


async def usage(user_id: int, exclude: tuple[str, str] | None = None) -> dict:
    """Pemakaian dari VM yang di-assign langsung. exclude=(vm_id, host_name) melewatkan satu VM."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT count(*) AS vms, COALESCE(sum(v.cores), 0) AS vcpu, COALESCE(sum(v.memory_mb), 0) AS ram_mb,
                      COALESCE(sum(v.disk_gb), 0) AS disk_gb
               FROM vm_assignments a LEFT JOIN vms v ON v.vm_id = a.vm_id AND v.host_name = a.host_name
               WHERE a.user_id = $1 AND a.deleted_at IS NULL AND ($2::text IS NULL OR NOT (a.vm_id = $2 AND a.host_name = $3))""",
            user_id, exclude[0] if exclude else None, exclude[1] if exclude else None)
    return {"vms": int(row["vms"]), "vcpu": int(row["vcpu"]), "ram_mb": int(row["ram_mb"]), "disk_gb": int(row["disk_gb"])}


_LABEL = {"max_vms": (lambda: tr("jumlah VM", "number of VMs"), "vms", ""),
          "max_vcpu": (lambda: tr("total vCPU", "total vCPU"), "vcpu", ""),
          "max_ram_mb": (lambda: tr("total RAM", "total RAM"), "ram_mb", " MB"),
          "max_disk_gb": (lambda: tr("total disk", "total disk"), "disk_gb", " GB")}


async def check_assign(user_id: int, vm_id: str, host_name: str) -> None:
    """Tolak (409) kalau meng-assign VM ini membuat mahasiswa melewati kuotanya. Assignment yang sudah ada tidak dihitung dua kali."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        role = await conn.fetchval("SELECT role FROM users WHERE id = $1 AND deleted_at IS NULL", user_id)
        vm = await conn.fetchrow("SELECT cores, memory_mb, disk_gb FROM vms WHERE vm_id = $1 AND host_name = $2", vm_id, host_name)
    if role != "student":
        return
    limits = (await effective(user_id))["limits"]
    if not any(limits.values()):
        return
    now = await usage(user_id, exclude=(vm_id, host_name))
    add = {"vms": 1, "vcpu": (vm and vm["cores"]) or 0, "ram_mb": (vm and vm["memory_mb"]) or 0, "disk_gb": (vm and vm["disk_gb"]) or 0}
    for f in FIELDS:
        cap = limits[f]
        what_fn, key, unit = _LABEL[f]
        if cap and now[key] + add[key] > cap:
            what, total = what_fn(), now[key] + add[key]
            raise HTTPException(409, tr(
                f"Kuota {what} terlampaui: akan menjadi {total}{unit} dari batas {cap}{unit}. Naikkan kuota pengguna ini atau lepas assignment VM lain.",
                f"The {what} quota would be exceeded: {total}{unit} against a limit of {cap}{unit}. Raise this user's quota or unassign another VM."))


async def get_override(table: str, key_col: str, key: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT max_vms, max_vcpu, max_ram_mb, max_disk_gb FROM {table} WHERE {key_col} = $1", key)
    return dict(row) if row else {f: None for f in FIELDS}


async def set_override(table: str, key_col: str, key: int, body: dict, by: str) -> dict:
    data = clean(body, allow_null=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if all(v is None for v in data.values()):
            await conn.execute(f"DELETE FROM {table} WHERE {key_col} = $1", key)
        else:
            await conn.execute(
                f"""INSERT INTO {table} ({key_col}, max_vms, max_vcpu, max_ram_mb, max_disk_gb, updated_by, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    ON CONFLICT ({key_col}) DO UPDATE SET max_vms = EXCLUDED.max_vms, max_vcpu = EXCLUDED.max_vcpu,
                        max_ram_mb = EXCLUDED.max_ram_mb, max_disk_gb = EXCLUDED.max_disk_gb,
                        updated_by = EXCLUDED.updated_by, updated_at = NOW()""",
                key, data["max_vms"], data["max_vcpu"], data["max_ram_mb"], data["max_disk_gb"], by)
    return data
