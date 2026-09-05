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
from core.redis_client import redis_client

logger = logging.getLogger(__name__)


def generate_tool_cache_key(tool_name: str, params: dict[str, Any]) -> str:
    """Generate a stable SHA-256 cache key from tool name and sorted parameters."""
    serialized_params = json.dumps(params, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized_params.encode("utf-8")).hexdigest()
    return f"tool_cache:{tool_name}:{digest}"


async def get_cached_tool_result(tool_name: str, params: dict[str, Any]) -> Optional[Any]:
    """Fetch cached tool result if available."""
    if not redis_client.is_available:
        return None

    key = generate_tool_cache_key(tool_name, params)
    return await redis_client.get_json(key)


async def set_cached_tool_result(
    tool_name: str,
    params: dict[str, Any],
    result: Any,
    ttl_seconds: Optional[int] = None,
) -> bool:
    """Cache tool result with TTL."""
    if not redis_client.is_available:
        return False

    settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.redis_tool_ttl_seconds
    key = generate_tool_cache_key(tool_name, params)
    return await redis_client.set_json(key, result, ex=ttl)
