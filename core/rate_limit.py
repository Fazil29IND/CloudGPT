"""
Sliding window rate limiter with Redis backend and in-memory fallback.

Provides atomic distributed rate limiting when Redis is active, and falls back
to process-local sliding window deque when Redis is not configured.
"""

from __future__ import annotations

import inspect
import time
import logging
from collections import defaultdict, deque

from core.redis_client import redis_client

logger = logging.getLogger(__name__)


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
            cutoff = now - window_seconds
            try:
                pipe = redis_client.client.pipeline()
                pipe.zremrangebyscore(redis_key, 0, cutoff)
                pipe.zcard(redis_key)
                pipe.zadd(redis_key, {str(now): now})
                pipe.expire(redis_key, window_seconds + 5)
                exec_res = pipe.execute()
                results = await exec_res if inspect.isawaitable(exec_res) else exec_res

                current_count = results[1]
                if current_count >= maximum:
                    # Clean up the tentatively added timestamp to eliminate lockout amplification
                    try:
                        rem_task = redis_client.client.zrem(redis_key, str(now))
                        if inspect.isawaitable(rem_task):
                            await rem_task
                    except Exception:
                        pass
                    return False
                return True
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
