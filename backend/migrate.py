import logging
"""
Versioned migration runner — lightweight alternative to Alembic for asyncpg.

Convention: backend/migrations/V{NNN}__description.sql
Applied versions are tracked in the schema_migrations table.
Migrations run inside a transaction; a failed migration rolls back cleanly.
"""
import re
from pathlib import Path

log = logging.getLogger("migrate")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}

    for f in sorted(MIGRATIONS_DIR.glob("V*.sql")):
        m = re.match(r"V(\d+)__", f.name)
        if not m:
            continue
        version = int(m.group(1))
        if version in applied:
            continue

        sql = f.read_text(encoding="utf-8")
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
                version, f.stem,
            )
        log.info("Applied migration: %s", f.stem)
