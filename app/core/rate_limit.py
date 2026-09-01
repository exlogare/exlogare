from __future__ import annotations

import time

from app.core.redis import get_redis


class RateLimitExceeded(Exception):
    pass


async def check_rate_limit(key: str, limit: int, window_seconds: int = 60) -> None:
    """Fixed-window rate limit using Redis INCR + EXPIRE."""
    if limit <= 0:
        return
    redis_client = get_redis()
    bucket = int(time.time() // window_seconds)
    redis_key = f"rl:{key}:{bucket}"
    count = await redis_client.incr(redis_key)
    if count == 1:
        await redis_client.expire(redis_key, window_seconds)
    if count > limit:
        raise RateLimitExceeded(f"Rate limit exceeded for {key}")
