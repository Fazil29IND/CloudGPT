"""
Sliding window rate limiter with Redis backend and in-memory fallback.

The Redis path is a single atomic Lua script (trim expired entries → count →
conditionally add → expire) so concurrent requests at the window boundary can
never over-admit. When Redis is not configured or errors, it falls back to a
process-local sliding window deque.
"""

from __future__ import annotations

import inspect
import time
import logging
import uuid
from collections import defaultdict, deque

from core.redis_client import redis_client

logger = logging.getLogger(__name__)

# Atomic sliding-window allowance. Returns 1 when the request is admitted
# (after adding its timestamp), 0 when the window is full.
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local maximum = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= maximum then
    redis.call('EXPIRE', key, window + 5)
    return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window + 5)
return 1
"""


class SlidingWindowRateLimiter:
    """Sliding window rate limiter with Redis support and local fallback."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._script = None
        self._script_client = None

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

    def _get_script(self, client):
        """Register the Lua script once per Redis client instance (re-registered
        transparently if the client object is replaced by a reconnect)."""
        if self._script is None or self._script_client is not client:
            self._script = client.register_script(_SLIDING_WINDOW_LUA)
            self._script_client = client
        return self._script

    async def allowed_async(self, key: str, maximum: int, window_seconds: int = 60) -> bool:
        """
        Async rate limit check:
        Uses an atomic Redis Lua sliding window if available, else the local
        in-memory window.
        """
        if redis_client.is_available and redis_client.client:
            if hasattr(self, "_fallback_since"):
                delattr(self, "_fallback_since")
                self.reset()
                logger.info("rate_limiter_redis_reconnected_reset_local_state")

            redis_key = f"ratelimit:{key}"
            now = time.time()
            # Unique member per request: identical timestamps must not overwrite
            # each other's ZADD entries (that would undercount concurrent hits).
            member = f"{now:.6f}:{uuid.uuid4().hex}"
            try:
                script = self._get_script(redis_client.client)
                result = script(keys=[redis_key], args=[now, window_seconds, maximum, member])
                if inspect.isawaitable(result):
                    result = await result
                return bool(int(result))
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
