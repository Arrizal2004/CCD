"""
Redis-backed login lockout — protects individual accounts from brute-force password guessing.

Keyed by username, not client IP: behind Tailscale Funnel/Serve the backend never sees the real
internet-facing client IP (traffic arrives looking like it's from localhost), so IP-based limiting
would either be useless or wrongly lock out every visitor together. Username-based keying works
regardless of the network path and directly protects the thing an attacker is actually guessing.

Same wiring pattern as token_blocklist.py: set_redis() is called once in main.py's lifespan with
the plain (undecorated) redis client.
"""
import redis.asyncio as aioredis

_redis: aioredis.Redis | None = None

MAX_ATTEMPTS = 5           # failed attempts allowed within the window
FAIL_WINDOW_SECONDS = 300  # window to accumulate failures (5 min)
LOCKOUT_SECONDS = 300      # lockout duration once the threshold is hit (5 min)


def set_redis(r: aioredis.Redis) -> None:
    global _redis
    _redis = r


def _norm(username: str) -> str:
    return (username or "").strip().lower()


async def seconds_locked(username: str) -> int:
    """0 if the account isn't locked, else seconds remaining. Fails open (0) if Redis is down —
    a brute-force window during a Redis outage is a smaller risk than locking everyone out."""
    if not _redis:
        return 0
    try:
        ttl = await _redis.ttl(f"login:lock:{_norm(username)}")
    except Exception:
        return 0
    return ttl if ttl and ttl > 0 else 0


async def record_failure(username: str) -> None:
    if not _redis:
        return
    try:
        key = f"login:fail:{_norm(username)}"
        n = await _redis.incr(key)
        if n == 1:
            await _redis.expire(key, FAIL_WINDOW_SECONDS)
        if n >= MAX_ATTEMPTS:
            await _redis.setex(f"login:lock:{_norm(username)}", LOCKOUT_SECONDS, "1")
            await _redis.delete(key)
    except Exception:
        pass


async def record_success(username: str) -> None:
    if not _redis:
        return
    try:
        await _redis.delete(f"login:fail:{_norm(username)}")
    except Exception:
        pass
