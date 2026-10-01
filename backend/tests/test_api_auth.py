"""
Integration tests for authentication endpoints:
  POST /api/v1/users/login
  POST /api/v1/users/logout
  GET  /api/v1/users/me
"""
from tests.conftest import _run, _create_user


# ── Login ────────────────────────────────────────────────────────

def test_login_success(client):
    _run(_create_user("tst_login_ok", "LoginOk@999"))
    r = client.post(
        "/api/v1/users/login",
        json={"username": "tst_login_ok", "password": "LoginOk@999"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["username"] == "tst_login_ok"


def test_login_wrong_password(client):
    _run(_create_user("tst_login_wp", "Correct@999"))
    r = client.post(
        "/api/v1/users/login",
        json={"username": "tst_login_wp", "password": "Wrong@999"},
    )
    assert r.status_code == 401


def test_login_nonexistent_user(client):
    r = client.post(
        "/api/v1/users/login",
        json={"username": "tst_no_such_user_xyz", "password": "anything"},
    )
    assert r.status_code == 401


def test_login_inactive_user(client):
    """is_active=False → 403 (admin must activate first)."""
    _run(_create_user("tst_login_inactive", "Inactive@999", is_active=False))
    r = client.post(
        "/api/v1/users/login",
        json={"username": "tst_login_inactive", "password": "Inactive@999"},
    )
    assert r.status_code == 403


def test_login_soft_deleted_user(client):
    """deleted_at IS NOT NULL → 401 (treated as non-existent)."""
    _run(_create_user("tst_login_deleted", "Deleted@999", deleted=True))
    r = client.post(
        "/api/v1/users/login",
        json={"username": "tst_login_deleted", "password": "Deleted@999"},
    )
    assert r.status_code == 401


def test_login_token_has_jti(client, superadmin_token):
    from auth import decode_token

    payload = decode_token(superadmin_token)
    assert "jti" in payload
    assert len(payload["jti"]) == 36  # UUID4


def test_login_token_has_correct_role(client, student_token):
    from auth import decode_token

    payload = decode_token(student_token)
    assert payload["role"] == "student"


def test_login_response_contains_user_info(client):
    _run(_create_user("tst_login_info", "Info@999", role="sysadmin"))
    r = client.post(
        "/api/v1/users/login",
        json={"username": "tst_login_info", "password": "Info@999"},
    )
    assert r.status_code == 200
    user = r.json()["user"]
    assert user["username"] == "tst_login_info"
    assert user["role"] == "sysadmin"


# ── Logout / token revocation ────────────────────────────────────

def test_logout_revokes_token(client):
    """Token must be rejected after logout."""
    _run(_create_user("tst_logout_user", "Logout@999"))
    login = client.post(
        "/api/v1/users/login",
        json={"username": "tst_logout_user", "password": "Logout@999"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Token valid before logout
    assert client.get("/api/v1/users/me", headers=headers).status_code == 200

    # Logout
    r_logout = client.post("/api/v1/users/logout", headers=headers)
    assert r_logout.status_code == 200

    # Token now rejected
    r_me = client.get("/api/v1/users/me", headers=headers)
    assert r_me.status_code == 401
    assert "direvoke" in r_me.json()["detail"].lower()


def test_logout_without_token_returns_401(client):
    r = client.post("/api/v1/users/logout")
    assert r.status_code == 401


# ── /me endpoint ─────────────────────────────────────────────────

def test_me_returns_current_user(client, superadmin_token):
    r = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["username"] == "tst_superadmin"


def test_me_requires_auth(client):
    r = client.get("/api/v1/users/me")
    assert r.status_code == 401


def test_me_rejects_invalid_token(client):
    r = client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer not.a.real.token"},
    )
    assert r.status_code == 401
