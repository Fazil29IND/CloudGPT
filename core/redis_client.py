"""
Redis connection management and helper utilities for CloudGPT.

Provides async access to Redis with connection pooling, automatic serialization,
and resilient fail-safe error handling (graceful degradation if Redis is down).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError, RedisError, TimeoutError as RedisTimeoutError

from config import get_settings

logger = logging.getLogger(__name__)


class RedisManager:
    """Async Redis manager with automatic reconnection and fallback handling."""

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._custom_url = redis_url
        self.client: Optional[aioredis.Redis] = None
        self._connected: bool = False

    @property
    def redis_url(self) -> str:
        if self._custom_url:
            return self._custom_url
        return get_settings().redis_url

    @property
    def is_enabled(self) -> bool:
        return get_settings().redis_enabled

    @property
    def is_available(self) -> bool:
        return self.is_enabled and self._connected and self.client is not None

    async def connect(self) -> bool:
        """Initialize connection pool to Redis. Returns True if connected."""
        if not self.is_enabled:
            logger.info("Redis is disabled via configuration.")
            return False

        try:
            self.client = aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=50,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
            )
            # Verify connectivity with ping
            await self.client.ping()
            self._connected = True
            logger.info("Successfully connected to Redis at %s", self.redis_url)
            return True
        except Exception as e:
            self._connected = False
            self.client = None
            logger.warning("Redis is unavailable (%s). Falling back to in-memory/PostgreSQL mode.", e)
            return False

    async def close(self) -> None:
        """Close the Redis client pool."""
        if self.client:
            try:
                await self.client.aclose()
            except Exception as e:
                logger.debug("Error closing Redis client: %s", e)
            finally:
                self.client = None
                self._connected = False
                logger.info("Redis connection pool closed.")

    async def ping(self) -> bool:
        """Ping Redis to check connectivity."""
        if not self.client:
            return False
        try:
            return bool(await self.client.ping())
        except Exception:
            self._connected = False
            return False

    def _handle_error(self, e: Exception, action: str, key: str = "") -> None:
        logger.debug("Redis %s error for key %s: %s", action, key, e)
        if isinstance(e, (RedisConnectionError, RedisTimeoutError, ConnectionRefusedError, OSError)):
            self._connected = False

    async def get(self, key: str) -> Optional[str]:
        """Fetch raw string value."""
        if not self.is_available or not self.client:
            return None
        try:
            return await self.client.get(key)
        except (RedisError, OSError) as e:
            self._handle_error(e, "get", key)
            return None

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Set raw string value with optional TTL (seconds)."""
        if not self.is_available or not self.client:
            return False
        try:
            await self.client.set(key, value, ex=ex)
            return True
        except (RedisError, OSError) as e:
            self._handle_error(e, "set", key)
            return False

    async def get_json(self, key: str) -> Optional[Any]:
        """Fetch and deserialize a JSON object."""
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to deserialize JSON for key %s: %s", key, e)
            return None

    async def set_json(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        """Serialize and set a JSON object with optional TTL (seconds)."""
        try:
            serialized = json.dumps(value)
            return await self.set(key, serialized, ex=ex)
        except (TypeError, ValueError) as e:
            logger.warning("Failed to serialize JSON for key %s: %s", key, e)
            return False

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        if not self.is_available or not self.client or not keys:
            return 0
        try:
            return await self.client.delete(*keys)
        except (RedisError, OSError) as e:
            self._handle_error(e, "delete", ",".join(keys))
            return 0

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        """Fetch range from a list."""
        if not self.is_available or not self.client:
            return []
        try:
            return await self.client.lrange(key, start, end)  # pyright: ignore[reportGeneralTypeIssues]
        except (RedisError, OSError) as e:
            self._handle_error(e, "lrange", key)
            return []

    async def rpush(self, key: str, *values: str) -> int:
        """Push values to the tail of a list."""
        if not self.is_available or not self.client or not values:
            return 0
        try:
            return await self.client.rpush(key, *values)  # pyright: ignore[reportGeneralTypeIssues]
        except (RedisError, OSError) as e:
            self._handle_error(e, "rpush", key)
            return 0

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        """Trim list to specified index range."""
        if not self.is_available or not self.client:
            return False
        try:
            await self.client.ltrim(key, start, end)  # pyright: ignore[reportGeneralTypeIssues]
            return True
        except (RedisError, OSError) as e:
            self._handle_error(e, "ltrim", key)
            return False

    async def expire(self, key: str, seconds: int) -> bool:
        """Set TTL on a key."""
        if not self.is_available or not self.client:
            return False
        try:
            return bool(await self.client.expire(key, seconds))
        except (RedisError, OSError) as e:
            self._handle_error(e, "expire", key)
            return False

    async def acquire_lock(self, key: str, owner_token: str, ttl_seconds: int = 20) -> bool:
        """Acquire a single-flight lock via SET key owner_token NX EX ttl_seconds."""
        if not self.is_available or not self.client:
            # If Redis unavailable, allow proceeding without lock
            return True
        try:
            result = await self.client.set(key, owner_token, nx=True, ex=ttl_seconds)
            return bool(result)
        except (RedisError, OSError) as e:
            self._handle_error(e, "acquire_lock", key)
            return True

    async def release_lock(self, key: str, owner_token: str) -> bool:
        """Release lock only if owner_token matches (atomic Lua script)."""
        if not self.is_available or not self.client:
            return True
        lua_release = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            res = await self.client.eval(lua_release, 1, key, owner_token)  # pyright: ignore[reportGeneralTypeIssues]
            return bool(res)
        except (RedisError, OSError) as e:
            self._handle_error(e, "release_lock", key)
            return False


# Global singleton instance
redis_client = RedisManager()
