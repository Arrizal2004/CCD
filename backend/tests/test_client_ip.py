"""
IP klien di audit log harus berasal dari X-Real-IP (diisi nginx), bukan X-Forwarded-For yang bisa
diisi sendiri oleh klien.
"""
from types import SimpleNamespace

from services.audit import _client_ip


def _req(headers: dict, host: str = "172.18.0.6"):
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()}, client=SimpleNamespace(host=host))


def test_uses_x_real_ip_from_nginx():
    assert _client_ip(_req({"X-Real-IP": "203.0.113.7"})) == "203.0.113.7"


def test_ignores_spoofed_x_forwarded_for():
    req = _req({"X-Forwarded-For": "1.2.3.4, 203.0.113.7", "X-Real-IP": "203.0.113.7"})
    assert _client_ip(req) == "203.0.113.7"


def test_falls_back_to_socket_address():
    assert _client_ip(_req({})) == "172.18.0.6"
    assert _client_ip(None) == ""
