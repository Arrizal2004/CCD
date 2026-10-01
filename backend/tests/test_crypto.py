"""
Tests for both encryption systems:
  1. Fernet  (services/ssh_client.py) — vm_password at rest
  2. AES-256-GCM (crypto.py)          — agent↔backend payload
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from cryptography.fernet import InvalidToken

from services.ssh_client import encrypt_secret, decrypt_secret
import crypto


# ── Fernet (vm_password) ─────────────────────────────────────────

def test_fernet_roundtrip():
    plaintext = "SuperSecret123!"
    assert decrypt_secret(encrypt_secret(plaintext)) == plaintext


def test_fernet_different_ciphertext():
    plaintext = "same_password"
    c1 = encrypt_secret(plaintext)
    c2 = encrypt_secret(plaintext)
    assert c1 != c2  # random nonce per encryption


def test_fernet_tamper_raises():
    enc = encrypt_secret("my_password")
    # Flip the last character to corrupt the MAC
    corrupted = enc[:-1] + ("A" if enc[-1] != "A" else "B")
    with pytest.raises(Exception):  # InvalidToken or similar
        decrypt_secret(corrupted)


def test_fernet_empty_string():
    assert decrypt_secret(encrypt_secret("")) == ""


def test_fernet_unicode():
    text = "Sandi_Saya_üñíçödé_🔑"
    assert decrypt_secret(encrypt_secret(text)) == text


# ── AES-256-GCM (agent payload) ──────────────────────────────────

@pytest.mark.skipif(not crypto.ENABLED, reason="AGENT_ENC_SECRET not set")
def test_aes_seal_unseal_roundtrip():
    plaintext = '{"vm_id": "abc", "host": "h1"}'
    assert crypto.unseal(crypto.seal(plaintext)) == plaintext


@pytest.mark.skipif(not crypto.ENABLED, reason="AGENT_ENC_SECRET not set")
def test_aes_different_nonce():
    plaintext = "identical_payload"
    assert crypto.seal(plaintext) != crypto.seal(plaintext)


@pytest.mark.skipif(not crypto.ENABLED, reason="AGENT_ENC_SECRET not set")
def test_aes_tamper_raises():
    blob = crypto.seal("some data")
    corrupted = blob[:-4] + "XXXX"
    with pytest.raises(ValueError):
        crypto.unseal(corrupted)


def test_aes_disabled_passthrough(monkeypatch):
    monkeypatch.setattr(crypto, "ENABLED", False)
    assert crypto.seal("hello") == "hello"
    assert crypto.unseal("hello") == "hello"


def test_aes_none_passthrough():
    # Both seal and unseal should treat None as no-op
    assert crypto.seal(None) is None
    assert crypto.unseal(None) is None
