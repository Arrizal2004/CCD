"""
Redis-backed JWT blocklist.
Tokens are revoked by storing their jti claim until the token's natural expiry.
"""
import redis.asyncio as aioredis

_redis: aioredis.Redis | None = None


def set_redis(r: aioredis.Redis) -> None:
    global _redis
    _redis = r


async def revoke(jti: str, ttl_seconds: int) -> None:
    if not _redis:
        raise RuntimeError("Redis tidak tersedia — token tidak dapat di-revoke")
    if ttl_seconds > 0:
        await _redis.setex(f"jwt:revoked:{jti}", ttl_seconds, "1")


async def is_revoked(jti: str) -> bool:
    if not _redis:
        raise RuntimeError("Redis tidak tersedia — tidak dapat memverifikasi revokasi token")
    return bool(await _redis.exists(f"jwt:revoked:{jti}"))
