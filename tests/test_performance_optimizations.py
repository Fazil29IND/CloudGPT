"""Tests for CloudGPT Latency Optimization Features.

Covers:
- Context token budgeting and dynamic chunk trimming
- Structured stage events schema
- L1 memory cache integration in llm_cache
- Pro tier confidence gate to skip self-critique
"""

from unittest.mock import AsyncMock, patch
import pytest

from llm.context_builder import ContextBuilder
from api.chat_routes import _stage_event
from core.llm_cache import get_cached_answer, set_cached_answer
from config import get_settings


def test_stage_event_helper():
    event = _stage_event(stage="retrieve", label="Searching docs", status="start")
    assert event["stage"] == "retrieve"
    assert event["label"] == "Searching docs"
    assert event["stage_status"] == "start"
    assert "elapsed_ms" not in event

    event_done = _stage_event(stage="retrieve", label="Docs found", status="complete", elapsed_ms=123.456)
    assert event_done["stage_status"] == "complete"
    assert event_done["elapsed_ms"] == 123.5


def test_context_builder_token_budget_trimming():
    builder = ContextBuilder()
    classification = {"intent": "general", "providers": ["aws"], "services": ["s3"]}

    # Create 5 large mock RAG results (~500 tokens each)
    large_text = "AWS S3 object storage is designed for 99.999999999% durability. " * 30
    rag_results = [
        {"provider": "aws", "service": "s3", "section": f"sec_{i}", "url": f"https://aws.com/{i}", "content": large_text}
        for i in range(5)
    ]

    # Without cap: all 5 chunks should be present
    full_ctx = builder.build_context(
        query="Explain S3 durability",
        classification=classification,
        rag_results=rag_results,
        max_context_tokens=None,
    )
    user_content_full = full_ctx[1]["content"]
    assert "SOURCE 5" in user_content_full

    # With cap (e.g. 600 tokens ~ 1-2 chunks):
    capped_ctx = builder.build_context(
        query="Explain S3 durability",
        classification=classification,
        rag_results=rag_results,
        max_context_tokens=400,
    )
    user_content_capped = capped_ctx[1]["content"]
    assert "SOURCE 1" in user_content_capped
    # Sources beyond budget should be truncated/trimmed
    assert "SOURCE 5" not in user_content_capped


@pytest.mark.asyncio
async def test_llm_cache_l1_and_l2_flow():
    settings = get_settings()

    # Clear memory cache first
    from core.memory_cache import get_memory_cache
    get_memory_cache().clear()

    # When Redis is absent/mocked, L1 cache should still work in-process
    test_query = "Unique latency test query 12345"
    test_payload = {"answer": "This is a cached answer from L1 memory cache.", "sources": []}

    # Cache should miss initially
    res = await get_cached_answer(query=test_query, model="test-model", mode="Free", provider_filter="aws", settings=settings)
    assert res is None

    # Set cache
    await set_cached_answer(
        query=test_query,
        response_payload=test_payload,
        model="test-model",
        mode="Free",
        provider_filter="aws",
        settings=settings,
        ttl_seconds=60,
    )

    # Cache should hit from L1
    cached_hit = await get_cached_answer(query=test_query, model="test-model", mode="Free", provider_filter="aws", settings=settings)
    assert cached_hit == test_payload


@pytest.mark.asyncio
async def test_agentic_rag_confidence_skip_self_critique():
    from retrieval.agentic_rag import AgenticRAGPipeline
    from retrieval.hybrid import RetrievalResult
    from router.query_router import QueryClassification

    pipeline = AgenticRAGPipeline()

    # Mock 3 chunks with high grade scores (>= 0.7)
    chunks = [
        RetrievalResult(
            chunk_id=f"c_{i}",
            text=f"High quality S3 replication evidence part {i}",
            score=0.9,
            source="hybrid",
            metadata={"grade_score": 0.85, "provider": "aws", "service": "s3", "title": "S3 Guide", "url": "https://aws.com", "section": "Replication"}
        )
        for i in range(3)
    ]

    classification = QueryClassification(
        intent="general",
        routes=["RAG"],
        providers=["aws"],
        services=["s3"],
        categories=[],
        confidence=0.95,
        reasoning="test",
        needs_internet=False,
    )

    timings = {}
    events = []

    async def mock_emit(event):
        events.append(event)

    with patch("api.chat_routes.generate_with_fallback", new=AsyncMock(return_value=("Direct high quality answer", "mock-gpt-4o"))):
        answer, model = await pipeline._generate_and_verify(
            query="How does S3 replication work?",
            graded_chunks=chunks,
            classification_obj=classification,
            internet_results=[],
            provider_filter="aws",
            chat_history=None,
            attachment_texts=None,
            tier="Pro",
            thinking_level="Low",
            stream=False,
            timings=timings,
            emit_event=mock_emit,
        )

    assert answer == "Direct high quality answer"
    # self_critique should be 0.0 (skipped)
    assert timings.get("self_critique") == 0.0
    # Skipped event was emitted
    skipped_events = [e for e in events if e.get("stage") == "self_critique" and e.get("stage_status") == "skipped"]
    assert len(skipped_events) == 1
