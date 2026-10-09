import asyncio
import datetime
import os
import sys

import asyncpg
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://hyperv:hyperv123@postgres:5432/hypervdb"
)


def _run(coro):
    """Run an async coroutine synchronously (safe outside any running loop)."""
    return asyncio.run(coro)


async def _create_user(
    username: str,
    password: str,
    role: str = "student",
    is_active: bool = True,
    deleted: bool = False,
) -> None:
    from auth import hash_password

    deleted_at = (
        datetime.datetime.now(datetime.timezone.utc) if deleted else None
    )
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            """
            INSERT INTO users
                (username, full_name, password_hash, role, is_active, is_verified, deleted_at)
            VALUES ($1, $2, $3, $4, $5, true, $6)
            ON CONFLICT (username) DO UPDATE SET
                password_hash = EXCLUDED.password_hash,
                role          = EXCLUDED.role,
                is_active     = EXCLUDED.is_active,
                deleted_at    = EXCLUDED.deleted_at
            """,
            username,
            f"Test {username}",
            hash_password(password),
            role,
            is_active,
            deleted_at,
        )
    finally:
        await conn.close()


async def _delete_test_users() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        names = [r["username"] for r in await conn.fetch(
            "SELECT username FROM users WHERE username LIKE 'tst\\_%%' ESCAPE '\\'")]
        await conn.execute(
            "DELETE FROM users WHERE username LIKE 'tst\\_%%' ESCAPE '\\'"
        )
    finally:
        await conn.close()
    # Logins (re)create Guacamole accounts; never leave test accounts with known passwords behind.
    from services.guac_sync import delete_user
    for name in names:
        try:
            await delete_user(name)
        except Exception:
            pass


# ── Session-scoped fixtures ──────────────────────────────────────

@pytest.fixture(scope="session")
def client():
    from main import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    _run(_delete_test_users())


@pytest.fixture(scope="session")
def superadmin_token(client):
    _run(_create_user("tst_superadmin", "Tst@Admin999", role="superadmin"))
    r = client.post(
        "/api/v1/users/login",
        json={"username": "tst_superadmin", "password": "Tst@Admin999"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def sysadmin_token(client):
    _run(_create_user("tst_sysadmin", "Tst@Sysadmin999", role="sysadmin"))
    r = client.post(
        "/api/v1/users/login",
        json={"username": "tst_sysadmin", "password": "Tst@Sysadmin999"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def student_token(client):
    _run(_create_user("tst_student", "Tst@Student999", role="student"))
    r = client.post(
        "/api/v1/users/login",
        json={"username": "tst_student", "password": "Tst@Student999"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _sysadmin_not_scoped(request, monkeypatch):
    """Sysadmin dibatasi per Proxmox (services/scope.py). Tes lama memakai satu akun sysadmin untuk label
    Proxmox apa saja dan tidak menguji pembatasan, jadi di sana sysadmin diperlakukan tanpa batas.
    Pembatasannya sendiri diuji di tests/test_instance_scope.py."""
    if request.module.__name__.endswith("test_instance_scope"):
        return
    from services import scope
    monkeypatch.setattr(scope, "is_scoped", lambda user: False)
