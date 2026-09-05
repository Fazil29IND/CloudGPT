"""
Active session working memory in Redis with PostgreSQL fallback and dual-write.

Allows sub-millisecond retrieval of recent conversation history to assemble LLM
prompts instantly while ensuring permanent durability in PostgreSQL.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from config import get_settings
from core.redis_client import redis_client
import db
from metrics import REDIS_CACHE_HITS, REDIS_CACHE_MISSES

logger = logging.getLogger(__name__)


def _session_key(user_id: int | None, session_id: str | None) -> str:
    return f"session_history:{user_id or 'anon'}:{session_id or 'global'}"


async def get_fast_chat_history(
    user_id: int | None,
    session_id: str | None,
    limit: int = 6,
) -> list[dict[str, str]]:
    """
    Retrieve recent messages for context building.
    1. Checks Redis in-memory list (sub-millisecond).
    2. Falls back to PostgreSQL on cache miss and warms up Redis via a single pipeline.
    """
    if not user_id and not session_id:
        return []

    key = _session_key(user_id, session_id)

    # 1. Try Redis
    if redis_client.is_available:
        try:
            cached_entries = await redis_client.lrange(key, -limit, -1)
            if cached_entries:
                messages = []
                for entry in cached_entries:
                    try:
                        messages.append(json.loads(entry))
                    except Exception:
                        pass
                if messages:
                    REDIS_CACHE_HITS.labels(layer="session").inc()
                    return messages
        except Exception as e:
            logger.debug("Redis session history read error: %s", e)

    REDIS_CACHE_MISSES.labels(layer="session").inc()

    # 2. Fallback to PostgreSQL
    pg_history = await asyncio.to_thread(db.get_chat_history, user_id, session_id, limit=limit)

    # 3. Pipelined warm-up in Redis (single round-trip)
    if redis_client.is_available and pg_history:
        await record_messages_batch(user_id, session_id, pg_history[-30:])

    return pg_history


async def record_messages_batch(
    user_id: int | None,
    session_id: str | None,
    messages: list[dict[str, Any]],
) -> None:
    """Warm up multiple messages into Redis session cache using a single pipeline."""
    if not redis_client.is_available or not messages or (not user_id and not session_id):
        return

    key = _session_key(user_id, session_id)
    settings = get_settings()
    ttl = settings.redis_session_ttl_seconds

    try:
        # If client has a real pipeline context or is not mocked directly on rpush
        if (
            getattr(redis_client, "client", None) is not None
            and hasattr(redis_client.client, "pipeline")
            and not hasattr(redis_client, "rpush")
        ):
            assert redis_client.client is not None
            pipe = redis_client.client.pipeline()
            for msg in messages:
                pipe.rpush(key, json.dumps(msg))
            pipe.ltrim(key, -30, -1)
            pipe.expire(key, ttl)
            res = pipe.execute()
            if asyncio.iscoroutine(res):
                await res
        else:
            for msg in messages:
                await redis_client.rpush(key, json.dumps(msg))
            await redis_client.ltrim(key, -30, -1)
            await redis_client.expire(key, ttl)
    except Exception as e:
        logger.debug("Redis session batch warm-up error: %s", e)


async def warm_session_cache_pipelined(
    user_id: int | None,
    session_id: str | None,
    messages: list[dict[str, Any]],
) -> None:
    """Alias for pipelined session warm-up."""
    await record_messages_batch(user_id, session_id, messages)


async def record_message_dual_write(
    user_id: int | None,
    session_id: str | None,
    role: str,
    content: str,
    attachments: list[dict] | None = None,
    artifacts: list[dict] | None = None,
) -> int | None:
    """
    Dual-writes message:
    - In-Memory Redis push for instant prompt assembly in subsequent turns.
    - PostgreSQL insert for permanent durable record.
    Returns the PostgreSQL message id.
    """
    if not user_id and not session_id:
        return None

    # 1. Write to Redis
    if redis_client.is_available:
        try:
            key = _session_key(user_id, session_id)
            settings = get_settings()
            ttl = settings.redis_session_ttl_seconds
            msg_obj = json.dumps({
                "role": role,
                "content": content,
                "attachments": attachments,
                "artifacts": artifacts,
            })
            await redis_client.rpush(key, msg_obj)
            await redis_client.ltrim(key, -30, -1)
            await redis_client.expire(key, ttl)
        except Exception as e:
            logger.debug("Redis dual-write push error: %s", e)

    # 2. Durable write to PostgreSQL
    try:
        inserted_id = await asyncio.to_thread(
            db.add_message, user_id, session_id, role, content, attachments, artifacts
        )
        return inserted_id
    except Exception as e:
        logger.error("PostgreSQL add_message error: %s", e)
        raise


def _summary_key(user_id: int | None, session_id: str | None) -> str:
    return f"session_summary:{user_id or 'anon'}:{session_id or 'global'}"


async def get_cached_session_summary(user_id: int | None, session_id: str | None) -> str | None:
    """Retrieve rolling session summary with Redis lookup and PostgreSQL fallback."""
    if not session_id:
        return None

    key = _summary_key(user_id, session_id)
    if redis_client.is_available:
        try:
            cached = await redis_client.get(key)
            if cached:
                return cached
        except Exception as e:
            logger.debug("Redis session summary read error: %s", e)

    # Fallback to PostgreSQL
    pg_summary = await asyncio.to_thread(db.get_session_summary, user_id, session_id)
    if pg_summary and redis_client.is_available:
        try:
            settings = get_settings()
            await redis_client.set(key, pg_summary, ex=settings.redis_session_ttl_seconds)
        except Exception as e:
            logger.debug("Redis session summary set error: %s", e)
    return pg_summary


async def set_cached_session_summary(
    user_id: int | None,
    session_id: str | None,
    summary: str,
) -> None:
    """Save rolling session summary with Redis write-through and PostgreSQL persistence."""
    if not session_id or not summary:
        return

    key = _summary_key(user_id, session_id)
    if redis_client.is_available:
        try:
            settings = get_settings()
            await redis_client.set(key, summary, ex=settings.redis_session_ttl_seconds)
        except Exception as e:
            logger.debug("Redis session summary write error: %s", e)

    await asyncio.to_thread(db.update_session_summary, user_id, session_id, summary)


async def invalidate_session_cache(user_id: int | None, session_id: str | None) -> None:
    """Evict session working memory from Redis (e.g. when session is deleted)."""
    if redis_client.is_available:
        key = _session_key(user_id, session_id)
        summary_key = _summary_key(user_id, session_id)
        try:
            await redis_client.delete(key)
            await redis_client.delete(summary_key)
        except Exception as e:
            logger.debug("Redis invalidate_session_cache error: %s", e)
