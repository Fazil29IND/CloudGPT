"""Automated verification suite for 2026 enterprise architecture remediations.

Covers:
- Calculator DoS & exponentiation bomb protection
- BoundedTTLCache capacity LRU eviction and TTL expiration
- Attachment IDOR cross-tenant access prevention
- Hybrid RAG dense query vector reuse
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from file_processor import BoundedTTLCache, load_attachments_from_redis
from tools.calculator import CalculatorTool
from retrieval import RetrievalResult
from retrieval.dense import DenseRetriever


def test_calculator_dos_and_exponent_bomb_rejection():
    """Verify CalculatorTool rejects dangerous exponent bombs, AST bombs, and oversized inputs."""
    tool = CalculatorTool()

    # 1. Normal power works
    assert tool.calculate("2 ** 8").result == 256.0
    assert tool.calculate("10 + 5 * 2").result == 20.0

    # 2. Exponent bomb rejected
    with pytest.raises(ValueError, match="exceeds safe calculation limit"):
        tool.calculate("2 ** 1001")

    with pytest.raises(ValueError, match="too large for exponentiation"):
        tool.calculate("9999999999999 ** 2")

    # 3. Input length bomb rejected
    oversized = "1 + " * 300 + "1"  # > 500 characters
    with pytest.raises(ValueError, match="exceeds maximum allowed length"):
        tool.calculate(oversized)

    # 4. AST complexity bomb rejected (60 addition operations > 50 node limit)
    many_nodes = " + ".join(["1"] * 60)
    with pytest.raises(ValueError, match="complexity exceeds maximum"):
        tool.calculate(many_nodes)

    # 5. Division by zero handled cleanly
    with pytest.raises(ValueError):
        tool.calculate("42 / 0")


def test_bounded_ttl_cache_eviction():
    """Verify BoundedTTLCache enforces max capacity and expires stale entries."""
    cache = BoundedTTLCache(maxsize=3, default_ttl=0.2)

    # Capacity enforcement
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") == 1
    assert cache.get("b") == 2
    assert cache.get("c") == 3

    # Add 4th item: least recently used ('a' accessed first, then 'b', 'c', so next insert should evict 'a')
    cache.get("b")
    cache.get("c")
    cache.set("d", 4)
    # 'a' should be evicted because 'b' and 'c' were touched
    assert cache.get("a") is None
    assert cache.get("d") == 4

    # TTL expiration
    time.sleep(0.25)
    assert cache.get("b") is None
    assert cache.get("c") is None
    assert cache.get("d") is None


@pytest.mark.asyncio
async def test_attachment_idor_isolation():
    """Verify load_attachments_from_redis denies access if user_id doesn't match payload owner."""
    staged_payload = {
        "attachment_id": "test-att-idor-1",
        "user_id": 42,
        "filename": "confidential_tax_return.pdf",
        "content_type": "application/pdf",
        "content": "Confidential financial statement",
        "kind": "file",
        "is_image": False,
        "is_audio": False,
        "is_video": False,
        "is_pdf": True,
        "size": 1024,
    }

    with patch("file_processor.redis_client.get_json", new_callable=AsyncMock) as mock_get_json:
        mock_get_json.return_value = staged_payload

        # Case A: Attacker (user_id=99) requests user 42's attachment -> BLOCKED
        blocked = await load_attachments_from_redis(
            [{"attachment_id": "test-att-idor-1"}],
            user_id=99,
        )
        assert blocked is None

        # Case B: Legitimate owner (user_id=42) requests attachment -> ALLOWED
        allowed = await load_attachments_from_redis(
            [{"attachment_id": "test-att-idor-1"}],
            user_id=42,
        )
        assert allowed is not None
        assert len(allowed) == 1
        assert allowed[0]["filename"] == "confidential_tax_return.pdf"


@pytest.mark.asyncio
async def test_dense_retriever_vector_override():
    """Verify vector_override bypasses embed_query in dense retrievers."""
    mock_engine = MagicMock()
    mock_engine.embed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])

    mock_index = MagicMock()
    mock_index.nodes = ["node1"]
    mock_index.search = MagicMock(return_value=[("c1", 0.95, "sample text", {})])

    dense_ret = DenseRetriever(embedding_engine=mock_engine, hnsw_index=mock_index)

    # When vector_override is provided, embed_query must NOT be called
    precomputed = [0.9, 0.8, 0.7]
    results = await dense_ret.retrieve(
        "test query",
        vector_override=precomputed,
    )
    assert len(results) == 1
    assert results[0].chunk_id == "c1"
    # embed_query should NOT have been called because precomputed was passed!
    mock_engine.embed_query.assert_not_called()
    mock_index.search.assert_called_once()
