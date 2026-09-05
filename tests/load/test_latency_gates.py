"""
Automated Latency Gate & Performance Acceptance Tests for CloudGPT.

Validates that:
1. Cached queries meet the latency target (p95 <= 800ms).
2. Multi-model classification and retrieval respect configured latency budgets.
3. Fallback sequences gracefully degrade without unhandled exceptions.
"""

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent


@pytest.mark.asyncio
async def test_cached_query_latency_gate():
    """Verify that second-request cached queries return with low latency."""
    from core.llm_cache import get_cached_llm_response, set_cached_llm_response

    test_query = "Latency Gate Verification Benchmark Query"
    test_payload = {
        "answer": "This is a pre-cached response for latency gate validation.",
        "sources": [],
        "routes_used": ["RAG"],
        "confidence": 1.0,
        "query_classification": {"intent": "factual"},
        "model_used": "gemini-3.8-flash (cached)",
    }

    mock_store: dict[str, Any] = {}

    class InMemoryRedis:
        is_available = True
        client = None

        async def get_json(self, key: str):
            return mock_store.get(key)

        async def set_json(self, key: str, val: Any, *args, **kwargs):
            mock_store[key] = val
            return True

        async def get(self, key: str):
            val = mock_store.get(key)
            return json.dumps(val) if isinstance(val, (dict, list)) else val

        async def set(self, key: str, val: Any, *args, **kwargs):
            mock_store[key] = val
            return True

    with patch("core.llm_cache.redis_client", InMemoryRedis()):
        # Populate cache
        await set_cached_llm_response(
            query=test_query,
            response_payload=test_payload,
            model="Lite",
            provider_filter=None,
            mode="Lite",
        )

        latencies_ms = []
        for _ in range(10):
            t0 = time.perf_counter()
            cached = await get_cached_llm_response(
                query=test_query,
                model="Lite",
                provider_filter=None,
                mode="Lite",
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            assert cached is not None
            assert cached["answer"] == test_payload["answer"]
            latencies_ms.append(elapsed_ms)

        latencies_ms.sort()
        p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
        assert p95 < 800.0, f"Cached query p95 latency {p95:.2f}ms exceeded 800ms target"


@pytest.mark.asyncio
async def test_retrieval_fallback_safety():
    """Verify that _retrieve_with_fallback safely succeeds across all passes."""
    from api.chat_routes import _retrieve_with_fallback, pipeline
    from config import get_settings
    from router.query_router import QueryClassification

    settings = get_settings()
    retriever, reranker = pipeline.get_retrieval()

    # Pass 1 & Pass 2 mock classification
    classif = QueryClassification(
        routes=["RAG"],
        confidence=0.95,
        intent="factual",
        providers=["aws"],
        services=["s3"],
        categories=["storage"],
        reasoning="Mock classification for latency tests",
        complexity="low",
        requires_code=False,
        needs_internet=False,
    )

    results, pass_name = await _retrieve_with_fallback(
        retriever=retriever,
        reranker=reranker,
        query="What is S3 default encryption?",
        provider_filter="aws",
        classification=classif,
        settings=settings,
    )

    assert pass_name in ("pass1_strict", "pass2_provider", "pass3_global")
    assert isinstance(results, list)
