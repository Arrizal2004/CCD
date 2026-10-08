"""
Unit tests for token_blocklist.py using mocked Redis.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock
import token_blocklist


@pytest.fixture(autouse=True)
def reset_redis():
    """Kembalikan klien Redis aplikasi setelah tiap test. Kalau dibiarkan None, test berikutnya yang
    memanggil API dengan token gagal karena blocklist menganggap Redis mati."""
    original = token_blocklist._redis
    yield
    token_blocklist.set_redis(original)


@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.setex = AsyncMock(return_value=True)
    r.exists = AsyncMock(return_value=0)
    return r


async def test_revoke_sets_correct_key(mock_redis):
    token_blocklist.set_redis(mock_redis)
    await token_blocklist.revoke("abc-jti-123", 3600)
    mock_redis.setex.assert_called_once_with("jwt:revoked:abc-jti-123", 3600, "1")


async def test_is_revoked_true_when_key_exists(mock_redis):
    mock_redis.exists = AsyncMock(return_value=1)
    token_blocklist.set_redis(mock_redis)
    assert await token_blocklist.is_revoked("abc-jti-456") is True


async def test_is_revoked_false_when_key_absent(mock_redis):
    mock_redis.exists = AsyncMock(return_value=0)
    token_blocklist.set_redis(mock_redis)
    assert await token_blocklist.is_revoked("abc-jti-789") is False


async def test_revoke_zero_ttl_does_not_set_key(mock_redis):
    token_blocklist.set_redis(mock_redis)
    await token_blocklist.revoke("abc-jti-000", 0)
    mock_redis.setex.assert_not_called()


async def test_revoke_negative_ttl_does_not_set_key(mock_redis):
    token_blocklist.set_redis(mock_redis)
    await token_blocklist.revoke("abc-jti-neg", -1)
    mock_redis.setex.assert_not_called()


async def test_no_redis_is_revoked_fails_closed():
    # A revoked token must never pass just because Redis isn't wired up yet.
    token_blocklist.set_redis(None)
    with pytest.raises(RuntimeError):
        await token_blocklist.is_revoked("any-jti")


async def test_no_redis_revoke_raises():
    # Logout must not report success when the token could not actually be revoked.
    token_blocklist.set_redis(None)
    with pytest.raises(RuntimeError):
        await token_blocklist.revoke("any-jti", 3600)
