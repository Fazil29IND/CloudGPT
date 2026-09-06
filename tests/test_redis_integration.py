"""
Unit and integration tests for Redis caching layer and PostgreSQL hybrid architecture.

Tests:
1. Redis client manager connectivity and operations.
2. Graceful fallback when Redis is offline/disabled.
3. LLM Prompt/Response cache hit and miss flows.
4. Active session working memory with dual-write to PostgreSQL.
5. Tool and pricing cache.
6. Redis-backed sliding window rate limiter.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.redis_client import RedisManager
from core.llm_cache import (
    generate_llm_cache_key,
    get_cached_llm_response,
    set_cached_llm_response,
)
from core.session_cache import (
    get_fast_chat_history,
    record_message_dual_write,
    invalidate_session_cache,
)
from core.tool_cache import (
    get_cached_tool_result,
    set_cached_tool_result,
)
from core.rate_limit import SlidingWindowRateLimiter


# ─── 1. Redis Client Manager Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_manager_disabled():
    with patch("core.redis_client.get_settings") as mock_settings:
        mock_settings.return_value.redis_enabled = False
        manager = RedisManager()
        connected = await manager.connect()
        assert connected is False
        assert manager.is_available is False
        assert await manager.get("any_key") is None
        assert await manager.set("any_key", "value") is False


@pytest.mark.asyncio
async def test_redis_manager_connection_failure():
    with patch("core.redis_client.aioredis.from_url") as mock_from_url:
        mock_client = AsyncMock()
        mock_client.ping.side_effect = Exception("Connection refused")
        mock_from_url.return_value = mock_client

        manager = RedisManager(redis_url="redis://localhost:6379/9")
        connected = await manager.connect()
        assert connected is False
        assert manager.is_available is False


@pytest.mark.asyncio
async def test_redis_manager_crud_operations():
    mock_client = AsyncMock()
    mock_client.ping.return_value = True
    mock_client.get.return_value = json.dumps({"status": "ok"})
    mock_client.set.return_value = True
    mock_client.delete.return_value = 1
    mock_client.lrange.return_value = [json.dumps({"role": "user", "content": "hello"})]
    mock_client.rpush.return_value = 1

    mock_settings = MagicMock()
    mock_settings.redis_enabled = True
    mock_settings.redis_url = "redis://localhost:6379/0"

    with patch("core.redis_client.get_settings", return_value=mock_settings), \
         patch("core.redis_client.aioredis.from_url", return_value=mock_client):
        manager = RedisManager()
        await manager.connect()
        assert manager.is_available is True

        # Test get_json & set_json
        val = await manager.get_json("test_key")
        assert val == {"status": "ok"}
        mock_client.get.assert_called_with("test_key")

        ok = await manager.set_json("test_key", {"status": "ok"}, ex=60)
        assert ok is True
        mock_client.set.assert_called_with("test_key", json.dumps({"status": "ok"}), ex=60)

        # Test lrange & rpush
        items = await manager.lrange("test_list", 0, -1)
        assert len(items) == 1
        mock_client.lrange.assert_called_with("test_list", 0, -1)

        await manager.close()
        assert manager.is_available is False


# ─── 2. LLM Prompt & Response Cache Tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_llm_cache_key_generation():
    key1 = generate_llm_cache_key("What is AWS S3?", model="Max", provider_filter="aws")
    key2 = generate_llm_cache_key("  what is aws s3?  ", model="Max", provider_filter="aws")
    # Normalized query should produce identical hash key
    assert key1 == key2

    key3 = generate_llm_cache_key("What is AWS S3?", model="Pro", provider_filter="aws")
    assert key1 != key3


@pytest.mark.asyncio
async def test_llm_cache_hit_and_miss():
    with patch("core.llm_cache.redis_client") as mock_rc:
        # Cache Miss
        mock_rc.is_available = True
        mock_rc.get_json = AsyncMock(return_value=None)
        mock_rc.set_json = AsyncMock(return_value=True)

        res = await get_cached_llm_response("Explain DynamoDB", model="Max")
        assert res is None

        # Cache Set & Hit
        expected_response = {
            "answer": "DynamoDB is a managed NoSQL database.",
            "sources": [],
            "model_used": "gemini",
        }
        await set_cached_llm_response("Explain DynamoDB", expected_response, model="Max")
        assert mock_rc.set_json.call_count == 2  # Model-specific + canonical dual write

        mock_rc.get_json.return_value = expected_response
        hit = await get_cached_llm_response("Explain DynamoDB", model="Max")
        assert hit == expected_response


# ─── 3. Session Working Memory & Dual-Write Tests ────────────────────────────

@pytest.mark.asyncio
async def test_session_fast_history_redis_hit():
    with patch("core.session_cache.redis_client") as mock_rc, \
         patch("core.session_cache.db.get_chat_history") as mock_db_history:
        mock_rc.is_available = True
        cached_msgs = [
            json.dumps({"role": "user", "content": "Hi"}),
            json.dumps({"role": "assistant", "content": "Hello! How can I help?"}),
        ]
        mock_rc.lrange = AsyncMock(return_value=cached_msgs)

        history = await get_fast_chat_history(user_id=1, session_id="sess-100", limit=6)
        assert len(history) == 2
        assert history[0]["content"] == "Hi"
        assert history[1]["content"] == "Hello! How can I help?"
        # Database should NOT be called on Redis hit
        mock_db_history.assert_not_called()


@pytest.mark.asyncio
async def test_session_fast_history_redis_miss_pg_fallback_and_warmup():
    with patch("core.session_cache.redis_client") as mock_rc, \
         patch("core.session_cache.db.get_chat_history") as mock_db_history:
        mock_rc.is_available = True
        # Redis miss
        mock_rc.lrange = AsyncMock(return_value=[])
        mock_rc.rpush = AsyncMock(return_value=1)
        mock_rc.ltrim = AsyncMock(return_value=True)
        mock_rc.expire = AsyncMock(return_value=True)

        pg_messages = [{"role": "user", "content": "What is GCP Cloud Run?"}]
        mock_db_history.return_value = pg_messages

        history = await get_fast_chat_history(user_id=1, session_id="sess-100", limit=6)
        assert history == pg_messages
        mock_db_history.assert_called_once_with(1, "sess-100", limit=6)
        # Verify Redis was warmed up with PG results
        mock_rc.rpush.assert_called_once()
        mock_rc.expire.assert_called_once()


@pytest.mark.asyncio
async def test_record_message_dual_write():
    with patch("core.session_cache.redis_client") as mock_rc, \
         patch("core.session_cache.db.add_message") as mock_add_message:
        mock_rc.is_available = True
        mock_rc.rpush = AsyncMock(return_value=1)
        mock_rc.ltrim = AsyncMock(return_value=True)
        mock_rc.expire = AsyncMock(return_value=True)

        await record_message_dual_write(
            user_id=1,
            session_id="sess-100",
            role="user",
            content="Compare AWS EC2 vs Lambda",
        )

        # 1. Pushed to Redis
        mock_rc.rpush.assert_called_once()
        mock_rc.ltrim.assert_called_once()
        mock_rc.expire.assert_called_once()

        # 2. Persisted to PostgreSQL
        mock_add_message.assert_called_once_with(1, "sess-100", "user", "Compare AWS EC2 vs Lambda", None, None)


@pytest.mark.asyncio
async def test_invalidate_session_cache():
    with patch("core.session_cache.redis_client") as mock_rc:
        mock_rc.is_available = True
        mock_rc.delete = AsyncMock(return_value=1)

        await invalidate_session_cache(user_id=1, session_id="sess-100")
        assert mock_rc.delete.call_count == 2
        mock_rc.delete.assert_any_call("session_history:1:sess-100")
        mock_rc.delete.assert_any_call("session_summary:1:sess-100")


# ─── 4. Tool & Pricing Cache Tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_cache():
    with patch("core.tool_cache.redis_client") as mock_rc:
        mock_rc.is_available = True
        mock_rc.get_json = AsyncMock(return_value=None)
        mock_rc.set_json = AsyncMock(return_value=True)

        params = {"service": "ec2", "instance_type": "t3.medium", "region": "us-east-1"}
        cached = await get_cached_tool_result("aws_pricing", params)
        assert cached is None

        pricing_data = {"hourly_rate": 0.0416, "currency": "USD"}
        await set_cached_tool_result("aws_pricing", params, pricing_data)
        mock_rc.set_json.assert_called_once()


# ─── 5. Rate Limiter Tests ───────────────────────────────────────────────────

def test_sliding_window_rate_limiter_sync():
    limiter = SlidingWindowRateLimiter()
    key = "user_123"
    # Allow 2 requests in 60s
    assert limiter.allowed(key, maximum=2, window_seconds=60) is True
    assert limiter.allowed(key, maximum=2, window_seconds=60) is True
    assert limiter.allowed(key, maximum=2, window_seconds=60) is False


@pytest.mark.asyncio
async def test_sliding_window_rate_limiter_async_with_redis():
    limiter = SlidingWindowRateLimiter()
    with patch("core.rate_limit.redis_client") as mock_rc:
        mock_rc.is_available = True
        # Atomic Lua sliding window: script returns 1 (admitted) or 0 (denied).
        mock_script = MagicMock()
        mock_script.return_value = 1
        mock_rc.client.register_script.return_value = mock_script

        allowed = await limiter.allowed_async("user_test", maximum=2, window_seconds=60)
        assert allowed is True

        # Now test when the window is full
        mock_script.return_value = 0
        allowed_exceeded = await limiter.allowed_async("user_test", maximum=2, window_seconds=60)
        assert allowed_exceeded is False
