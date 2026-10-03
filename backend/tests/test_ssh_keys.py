"""
Test SSH key untuk bastion: validasi key, opsi permitopen yang aman, dan endpoint.
"""
import base64
import hashlib
import struct

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from routers.ssh_keys import parse_public_key, _target
from tests.conftest import auth


def _ed25519_pub(comment="laptop") -> str:
    key = ed25519.Ed25519PrivateKey.generate().public_key()
    raw = key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    return f"{raw} {comment}"


def _rsa_pub(bits: int) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits).public_key()
    return key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()


def test_parse_ed25519_fingerprint_matches_openssh_format():
    pub = _ed25519_pub()
    ktype, normalized, fp = parse_public_key(pub)
    assert ktype == "ssh-ed25519"
    assert normalized == " ".join(pub.split()[:2])          # komentar dibuang
    blob = base64.b64decode(pub.split()[1])
    assert fp == "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def test_parse_rejects_weak_rsa_but_accepts_3072():
    with pytest.raises(ValueError, match="terlalu lemah"):
        parse_public_key(_rsa_pub(2048))
    assert parse_public_key(_rsa_pub(3072))[0] == "ssh-rsa"


@pytest.mark.parametrize("bad", [
    "",
    "bukan key",
    "ssh-dss AAAAB3NzaC1kc3MAAACBAP",
    "ssh-ed25519 !!!bukan-base64!!!",
    'command="/bin/sh" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA',  # opsi authorized_keys di depan
])
def test_parse_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_public_key(bad)


def test_parse_rejects_type_mismatch():
    blob = struct.pack(">I", 7) + b"ssh-rsa" + struct.pack(">I", 32) + b"\0" * 32
    with pytest.raises(ValueError, match="tidak cocok"):
        parse_public_key("ssh-ed25519 " + base64.b64encode(blob).decode())


@pytest.mark.parametrize("host,port,expected", [
    ("172.16.111.35", 22, "172.16.111.35:22"),
    ("172.16.111.35", None, "172.16.111.35:22"),
    ("10.0.0.5", 2200, "10.0.0.5:2200"),
    ('1.2.3.4",command="/bin/sh', 22, None),   # upaya injeksi opsi
    ("vm.example.com", 22, None),
    ("10.0.0.5", 70000, None),
    ("", 22, None),
])
def test_target_only_accepts_ipv4_and_valid_port(host, port, expected):
    assert _target(host, port) == expected


def test_authorized_requires_bastion_token(client):
    r = client.get("/api/v1/ssh-keys/authorized", params={"fingerprint": "SHA256:x"})
    assert r.status_code == 403
    r = client.get("/api/v1/ssh-keys/authorized", params={"fingerprint": "SHA256:x"},
                   headers={"X-Bastion-Token": "tebakan"})
    assert r.status_code == 403


def test_keys_require_login(client):
    assert client.get("/api/v1/ssh-keys").status_code == 401
    assert client.post("/api/v1/ssh-keys", json={"public_key": _ed25519_pub()}).status_code == 401


def test_add_list_delete_key(client, student_token):
    pub = _ed25519_pub()
    r = client.post("/api/v1/ssh-keys", json={"name": "laptop uji", "public_key": pub}, headers=auth(student_token))
    assert r.status_code == 200, r.text
    key = r.json()
    assert key["key_type"] == "ssh-ed25519" and key["fingerprint"].startswith("SHA256:")

    dup = client.post("/api/v1/ssh-keys", json={"public_key": pub}, headers=auth(student_token))
    assert dup.status_code == 409

    listed = client.get("/api/v1/ssh-keys", headers=auth(student_token)).json()
    assert any(k["id"] == key["id"] for k in listed)

    assert client.delete(f"/api/v1/ssh-keys/{key['id']}", headers=auth(student_token)).status_code == 200
    assert client.delete(f"/api/v1/ssh-keys/{key['id']}", headers=auth(student_token)).status_code == 404


def test_cannot_delete_other_users_key(client, student_token, sysadmin_token):
    r = client.post("/api/v1/ssh-keys", json={"public_key": _ed25519_pub()}, headers=auth(sysadmin_token))
    assert r.status_code == 200, r.text
    kid = r.json()["id"]
    assert client.delete(f"/api/v1/ssh-keys/{kid}", headers=auth(student_token)).status_code == 404
    assert client.delete(f"/api/v1/ssh-keys/{kid}", headers=auth(sysadmin_token)).status_code == 200
