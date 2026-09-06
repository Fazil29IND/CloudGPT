"""
Tool & Retrieval Result In-Memory Caching using Redis.

Caches deterministic tool executions such as Cloud Pricing lookups (AWS, GCP, Azure)
and external Web Searches to minimize redundant API latency.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from config import get_settings
from core.memory_cache import get_memory_cache
from core.redis_client import redis_client

logger = logging.getLogger(__name__)


def generate_tool_cache_key(tool_name: str, params: dict[str, Any]) -> str:
    """Generate a stable SHA-256 cache key from tool name and sorted parameters."""
    serialized_params = json.dumps(params, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized_params.encode("utf-8")).hexdigest()
    return f"tool_cache:{tool_name}:{digest}"


async def get_cached_tool_result(tool_name: str, params: dict[str, Any]) -> Optional[Any]:
    """Fetch cached tool result if available (L1 memory cache + Redis)."""
    key = generate_tool_cache_key(tool_name, params)
    settings = get_settings()

    # 1. Check L1 in-process memory cache
    if getattr(settings, "enable_l1_cache", True):
        l1 = get_memory_cache()
        l1_res = l1.get(key)
        if l1_res is not None:
            return l1_res

    if not redis_client.is_available:
        return None

    try:
        data = await redis_client.get_json(key)
        if data is not None and getattr(settings, "enable_l1_cache", True):
            l1 = get_memory_cache()
            l1.set(key, data, ttl=getattr(settings, "memory_cache_ttl_seconds", 60))
        return data
    except Exception as e:
        logger.debug("tool_cache_read_failed error=%s", e)
        return None


async def set_cached_tool_result(
    tool_name: str,
    params: dict[str, Any],
    result: Any,
    ttl_seconds: Optional[int] = None,
) -> bool:
    """Cache tool result with TTL (L1 memory cache + Redis)."""
    settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else getattr(settings, "redis_tool_ttl_seconds", 1800)
    key = generate_tool_cache_key(tool_name, params)

    # Write to L1 in-process memory cache
    if getattr(settings, "enable_l1_cache", True):
        l1 = get_memory_cache()
        l1.set(key, result, ttl=float(ttl))

    if not redis_client.is_available:
        return True

    try:
        return await redis_client.set_json(key, result, ex=ttl)
    except Exception as e:
        logger.debug("tool_cache_write_failed error=%s", e)
        return False
