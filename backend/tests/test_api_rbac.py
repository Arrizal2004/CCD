"""
Integration tests for role-based access control (RBAC).
Verifies that endpoints reject under-privileged callers.
"""


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── No / bad token ───────────────────────────────────────────────

def test_no_token_returns_401(client):
    r = client.get("/api/v1/users")
    assert r.status_code == 401


def test_invalid_token_returns_401(client):
    r = client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer invalid.token.here"},
    )
    assert r.status_code == 401


# ── User list endpoint (requires sysadmin+) ──────────────────────

def test_student_cannot_list_users(client, student_token):
    r = client.get("/api/v1/users", headers=_h(student_token))
    assert r.status_code == 403
    # Pesan singkat untuk pengguna, tanpa membeberkan nama role internal.
    assert r.json()["detail"] == "Forbidden: fitur ini khusus admin."


def test_sysadmin_forbidden_on_superadmin_feature(client, sysadmin_token):
    r = client.post("/api/v1/users/bulk", headers=_h(sysadmin_token), json={"user_ids": [1], "action": "activate"})
    assert r.status_code == 403
    assert r.json()["detail"] == "Forbidden: fitur ini khusus superadmin."


def test_sysadmin_can_list_users(client, sysadmin_token):
    r = client.get("/api/v1/users", headers=_h(sysadmin_token))
    assert r.status_code == 200


def test_superadmin_can_list_users(client, superadmin_token):
    r = client.get("/api/v1/users", headers=_h(superadmin_token))
    assert r.status_code == 200


# ── Admin panel (requires sysadmin+) ────────────────────────────

def test_student_cannot_access_admin_audit_logs(client, student_token):
    r = client.get("/api/admin/audit-logs", headers=_h(student_token))
    assert r.status_code == 403


def test_sysadmin_can_access_admin_audit_logs(client, sysadmin_token):
    r = client.get("/api/admin/audit-logs", headers=_h(sysadmin_token))
    assert r.status_code in (200, 404)  # exists but not forbidden


def test_superadmin_can_access_admin_audit_logs(client, superadmin_token):
    r = client.get("/api/admin/audit-logs", headers=_h(superadmin_token))
    assert r.status_code not in (401, 403)


# ── Superadmin-only endpoint ─────────────────────────────────────

def test_sysadmin_cannot_trigger_cleanup(client, sysadmin_token):
    """POST /api/v1/admin/cleanup requires superadmin."""
    r = client.post("/api/v1/admin/cleanup", headers=_h(sysadmin_token))
    assert r.status_code == 403


def test_student_cannot_trigger_cleanup(client, student_token):
    r = client.post("/api/v1/admin/cleanup", headers=_h(student_token))
    assert r.status_code == 403


def test_superadmin_can_trigger_cleanup(client, superadmin_token):
    r = client.post("/api/v1/admin/cleanup", headers=_h(superadmin_token))
    assert r.status_code == 200


# ── Create user (requires superadmin) ────────────────────────────

def test_student_cannot_create_user(client, student_token):
    r = client.post(
        "/api/v1/users",
        json={"username": "x", "password": "x", "role": "student", "full_name": "X"},
        headers=_h(student_token),
    )
    assert r.status_code == 403


def test_sysadmin_cannot_create_user(client, sysadmin_token):
    r = client.post(
        "/api/v1/users",
        json={"username": "x", "password": "x", "role": "student", "full_name": "X"},
        headers=_h(sysadmin_token),
    )
    assert r.status_code == 403
