"""
Penyimpanan pengaturan fitur bernilai JSON (tabel app_settings, satu baris per kunci): kebijakan MFA,
SMTP, kuota. Dipisah dari system_settings supaya form Pengaturan Sistem tidak ikut membesar.
Nilai yang belum disimpan memakai defaults dari pemanggil; hasil dibaca disimpan di memori sampai ada simpanan baru.
"""
import json

from database import get_pool

_cache: dict[str, dict] = {}


async def get(key: str, defaults: dict) -> dict:
    if key not in _cache:
        pool = await get_pool()
        async with pool.acquire() as conn:
            raw = await conn.fetchval("SELECT data FROM app_settings WHERE key = $1", key)
        stored = json.loads(raw) if isinstance(raw, str) else (raw or {})
        _cache[key] = {**defaults, **{k: v for k, v in stored.items() if k in defaults}}
    return dict(_cache[key])


async def save(key: str, data: dict, by: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO app_settings (key, data, updated_by, updated_at) VALUES ($1, $2::jsonb, $3, NOW())
               ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_by = EXCLUDED.updated_by, updated_at = NOW()""",
            key, json.dumps(data), by)
    _cache.pop(key, None)


def forget() -> None:
    _cache.clear()
