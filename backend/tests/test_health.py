"""
Integration tests for /healthz and /health endpoints.
"""


def test_healthz_returns_200(client):
    r = client.get("/healthz")
    assert r.status_code == 200


def test_healthz_status_ok(client):
    data = client.get("/healthz").json()
    assert data["status"] == "ok"


def test_healthz_has_database_check(client):
    checks = client.get("/healthz").json()["checks"]
    assert "database" in checks
    assert checks["database"]["status"] == "ok"


def test_healthz_has_redis_check(client):
    checks = client.get("/healthz").json()["checks"]
    assert "redis" in checks
    assert checks["redis"]["status"] == "ok"


def test_healthz_has_latency_ms(client):
    checks = client.get("/healthz").json()["checks"]
    assert "latency_ms" in checks["database"]
    assert "latency_ms" in checks["redis"]
    assert checks["database"]["latency_ms"] >= 0
    assert checks["redis"]["latency_ms"] >= 0


def test_healthz_has_version(client):
    data = client.get("/healthz").json()
    assert "version" in data
    assert data["version"]  # not empty


def test_healthz_has_timestamp(client):
    data = client.get("/healthz").json()
    assert "timestamp" in data


def test_health_liveness_always_200(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
