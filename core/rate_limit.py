"""
Sliding window rate limiter with Redis backend and in-memory fallback.

Provides atomic distributed rate limiting when Redis is active, and falls back
to process-local sliding window deque when Redis is not configured.
"""

from __future__ import annotations

import time
import logging
from collections import defaultdict, deque

from core.redis_client import redis_client

logger = logging.getLogger(__name__)


_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local maximum = tonumber(ARGV[3])
local cutoff = now - window

redis.call('ZREMRANGEBYSCORE', key, 0, cutoff)
local current_count = redis.call('ZCARD', key)

if current_count < maximum then
    local seq = redis.call('INCR', key .. ':seq')
    redis.call('ZADD', key, now, tostring(now) .. ':' .. tostring(seq))
    redis.call('EXPIRE', key, math.ceil(window) + 5)
    return 1
else
    return 0
end
"""


class SlidingWindowRateLimiter:
    """Sliding window rate limiter with Redis support and local fallback."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allowed(self, key: str, maximum: int, window_seconds: int = 60) -> bool:
        """Process-local synchronous sliding window check."""
        now = time.monotonic()
        events = self._events[key]
        cutoff = now - window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= maximum:
            return False
        events.append(now)
        return True

    async def allowed_async(self, key: str, maximum: int, window_seconds: int = 60) -> bool:
        """
        Async rate limit check:
        Uses Redis atomic sorted set if available, else local in-memory window.
        """
        if redis_client.is_available and redis_client.client:
            if hasattr(self, "_fallback_since"):
                delattr(self, "_fallback_since")
                self.reset()
                logger.info("rate_limiter_redis_reconnected_reset_local_state")

            redis_key = f"ratelimit:{key}"
            now = time.time()
            try:
                allowed = await redis_client.client.eval(
                    _SLIDING_WINDOW_LUA,
                    1,
                    redis_key,
                    str(now),
                    str(window_seconds),
                    str(maximum),
                )
                return bool(int(allowed) == 1)
            except Exception as e:
                logger.warning("Redis rate limiter error, falling back to local: %s", e)
                if not hasattr(self, "_fallback_since"):
                    self._fallback_since = time.monotonic()

        else:
            if not hasattr(self, "_fallback_since"):
                self._fallback_since = time.monotonic()
                logger.warning("rate_limiter_redis_fallback")

        # Fallback to local memory limiter
        return self.allowed(key, maximum, window_seconds)

    def reset(self) -> None:
        """Clear all in-memory windows (used by the test suite for isolation)."""
        self._events.clear()


rate_limiter = SlidingWindowRateLimiter()
