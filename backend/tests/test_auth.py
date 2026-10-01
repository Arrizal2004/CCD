"""
Unit tests for auth.py — password hashing, JWT, and role hierarchy.
No database or Redis required.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timezone, timedelta
from jose import jwt
from fastapi import HTTPException

from auth import (
    hash_password,
    verify_password,
    create_token,
    decode_token,
    Role,
    SECRET_KEY,
    ALGORITHM,
)


# ── Password hashing ─────────────────────────────────────────────

def test_hash_verify_correct():
    h = hash_password("MyPassword123")
    assert verify_password("MyPassword123", h) is True


def test_verify_wrong_password():
    h = hash_password("correct_password")
    assert verify_password("wrong_password", h) is False


def test_hash_is_unique():
    h1 = hash_password("same_pass")
    h2 = hash_password("same_pass")
    assert h1 != h2  # bcrypt uses random salt


# ── JWT creation ─────────────────────────────────────────────────

def test_create_token_has_jti():
    token = create_token(1, "alice", "student")
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert "jti" in payload
    assert len(payload["jti"]) == 36  # UUID4 format


def test_create_token_has_required_fields():
    token = create_token(42, "bob", "sysadmin")
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "42"
    assert payload["username"] == "bob"
    assert payload["role"] == "sysadmin"
    assert "exp" in payload
    assert "iat" in payload


def test_decode_valid_token():
    token = create_token(99, "charlie", "superadmin")
    payload = decode_token(token)
    assert payload["username"] == "charlie"
    assert payload["role"] == "superadmin"


def test_decode_expired_token():
    payload = {
        "sub": "1",
        "username": "x",
        "role": "student",
        "jti": "test-jti",
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        "iat": datetime.now(timezone.utc) - timedelta(hours=9),
    }
    expired_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    with pytest.raises(HTTPException) as exc_info:
        decode_token(expired_token)
    assert exc_info.value.status_code == 401


def test_decode_tampered_token():
    token = create_token(1, "alice", "student")
    # Corrupt the signature segment
    parts = token.split(".")
    tampered = parts[0] + "." + parts[1] + "." + "invalidsignatureXXX"
    with pytest.raises(HTTPException) as exc_info:
        decode_token(tampered)
    assert exc_info.value.status_code == 401


def test_decode_wrong_secret():
    payload = {
        "sub": "1",
        "username": "x",
        "role": "student",
        "jti": "test-jti",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    bad_token = jwt.encode(payload, "wrong_secret_key", algorithm=ALGORITHM)
    with pytest.raises(HTTPException) as exc_info:
        decode_token(bad_token)
    assert exc_info.value.status_code == 401


# ── Role hierarchy ───────────────────────────────────────────────

def test_superadmin_has_all_permissions():
    assert Role.has_permission("superadmin", "superadmin") is True
    assert Role.has_permission("superadmin", "sysadmin") is True
    assert Role.has_permission("superadmin", "student") is True


def test_sysadmin_permissions():
    assert Role.has_permission("sysadmin", "sysadmin") is True
    assert Role.has_permission("sysadmin", "student") is True
    assert Role.has_permission("sysadmin", "superadmin") is False


def test_student_limited():
    assert Role.has_permission("student", "student") is True
    assert Role.has_permission("student", "sysadmin") is False
    assert Role.has_permission("student", "superadmin") is False


def test_invalid_role_returns_false():
    assert Role.has_permission("unknown_role", "student") is False
    assert Role.has_permission("superadmin", "unknown_role") is False
