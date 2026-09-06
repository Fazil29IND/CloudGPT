"""Unit and integration tests for Tiered Cache Policy, Adaptive Cache Router,
Multi-Level Stage-Aware Caching, and Feedback-Driven Policy.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.cache_policy import (
    CachePolicyDecision,
    answer_write_ttl,
    apply_policy_record,
    get_query_feedback_policy,
    record_feedback_policy,
    route_cache_policy,
    skip_answer_cache_for_validation,
)
from core.llm_cache import (
    get_cached_answer,
    get_cached_decision,
    get_cached_retrieval_result,
    get_cached_stage,
    set_cached_answer,
    set_cached_decision,
    set_cached_retrieval_result,
    set_cached_stage,
)
from core.memory_cache import get_memory_cache
from core.tool_cache import get_cached_tool_result, set_cached_tool_result
from generation.compression import compress_chunk
from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline
from retrieval.hybrid import RetrievalResult
from router.query_router import QueryClassification


@pytest.fixture(autouse=True)
def clean_l1_cache():
    """Ensure L1 memory cache is clean before and after each test."""
    get_memory_cache().clear()
    yield
    get_memory_cache().clear()


# ─── Subsystem 1: Adaptive Cache Router Tests ───────────────────────────────

def test_route_cache_policy_defaults():
    decision = route_cache_policy("How to configure AWS S3 bucket?")
    assert isinstance(decision, CachePolicyDecision)
    assert decision.exact_lookup is True
    assert decision.semantic_lookup is True
    assert decision.decision_cache_lookup is True
    assert decision.stage_caches is True
    assert decision.retrieval_cache is True
    assert decision.answer_cache_write is True
    assert decision.answer_ttl_multiplier == 1.0


def test_route_cache_policy_direct_fast():
    decision = route_cache_policy("aws s3 ls --profile dev", strategy="direct_fast")
    assert decision.semantic_threshold == 0.97
    assert any("direct_fast" in r for r in decision.reasons)


def test_route_cache_policy_multi_perspective():
    decision = route_cache_policy(
        "Compare AWS DynamoDB vs Azure Cosmos DB latency", strategy="multi_perspective"
    )
    assert decision.semantic_threshold == 0.95
    assert any("multi_perspective" in r for r in decision.reasons)


def test_route_cache_policy_risk_level():
    cls_high = QueryClassification(
        intent="architecture",
        routes=["RAG"],
        confidence=0.9,
        risk_level="high",
        services=["s3"],
        providers=["aws"],
        categories=["storage"],
        reasoning="Production data resiliency",
    )
    decision = route_cache_policy("Production database failover", classification=cls_high)
    assert decision.answer_cache_write is False
    assert any("risk=" in r for r in decision.reasons)


def test_route_cache_policy_low_confidence():
    cls_low = QueryClassification(
        intent="troubleshooting",
        routes=["RAG"],
        confidence=0.35,
        risk_level="low",
        services=[],
        providers=["aws"],
        categories=["compute"],
        reasoning="Uncertain route",
    )
    decision = route_cache_policy("ambiguous query", classification=cls_low)
    assert decision.answer_cache_write is False
    assert any("low_confidence" in r for r in decision.reasons)


def test_route_cache_policy_attachment_isolation():
    decision = route_cache_policy("analyze this terraform file", has_attachments=True)
    assert decision.exact_lookup is False
    assert decision.semantic_lookup is False
    assert decision.answer_cache_write is False
    assert any("has_attachments" in r for r in decision.reasons)


def test_route_cache_policy_multi_turn_history():
    history = [
        {"role": "user", "content": "What is EC2?"},
        {"role": "assistant", "content": "EC2 is virtual computing."},
        {"role": "user", "content": "How much does it cost?"},
    ]
    decision = route_cache_policy("How much does it cost?", chat_history=history)
    assert decision.exact_lookup is True
    assert decision.semantic_lookup is False
    assert any("multi_turn_history" in r for r in decision.reasons)


# ─── Subsystem 2: Feedback-Driven Cache Policy Tests ────────────────────────

def test_apply_policy_record_penalty():
    decision = CachePolicyDecision()
    penalty_record = {"rating": -1, "reason": "Hallucinated pricing"}
    updated = apply_policy_record(decision, penalty_record)
    assert updated.exact_lookup is False
    assert updated.semantic_lookup is False
    assert updated.answer_cache_write is False
    assert any("feedback_penalty" in r for r in updated.reasons)


def test_apply_policy_record_boost():
    decision = CachePolicyDecision()
    boost_record = {"rating": 1, "reason": "Spot-on explanation"}
    updated = apply_policy_record(decision, boost_record)
    assert updated.exact_lookup is True
    assert updated.semantic_lookup is True
    assert updated.answer_ttl_multiplier == 1.5
    assert any("feedback_boost" in r for r in updated.reasons)


def test_skip_answer_cache_for_validation():
    report_passed = {
        "passed": True,
        "dimensions": {
            "grounding": {"passed": True, "score": 0.95},
        },
    }
    assert skip_answer_cache_for_validation(report_passed) is False

    report_failed = {
        "passed": False,
        "dimensions": {
            "grounding": {"passed": False, "score": 0.2, "details": "unsupported claims"},
        },
    }
    assert skip_answer_cache_for_validation(report_failed) is True


def test_answer_write_ttl_boost():
    base_ttl = 3600
    ttl_normal = answer_write_ttl(base_ttl, "normal query", policy_record=None)
    assert ttl_normal == 3600

    ttl_boosted = answer_write_ttl(
        base_ttl, "boosted query", policy_record={"rating": 1}
    )
    assert ttl_boosted == 5400  # 3600 * 1.5


@pytest.mark.asyncio
async def test_feedback_policy_in_memory_persistence():
    query = "aws eks pod networking cni"
    applied = await record_feedback_policy(query, rating=-1, reason="Stale CNI version")
    assert applied["recorded"] is True
    assert applied["action"] == "penalty"

    read_back = await get_query_feedback_policy(query)
    assert read_back is not None
    assert read_back.get("rating") == -1


# ─── Subsystem 3: Multi-Level Stage-Aware Caching Tests ─────────────────────

@pytest.mark.asyncio
async def test_decision_cache_in_memory_fallback():
    scope = "adaptive_transform"
    query = "gcp cloud run custom domain mapping"
    version = "v1"
    decision = {
        "routing_path": "direct_fast",
        "rewritten_query": "gcp cloud run domain",
        "expanded_queries": ["gcp cloud run dns"],
    }

    # Write without Redis (L1 in-memory fallback)
    success = await set_cached_decision(scope, query, decision, version, ttl_seconds=300)
    assert success is True

    # Read back from L1 in-memory
    read_decision = await get_cached_decision(scope, query, version)
    assert read_decision is not None
    assert read_decision["routing_path"] == "direct_fast"


@pytest.mark.asyncio
async def test_stage_cache_in_memory_fallback():
    stage_key = "rag:v2:stage:rerank:v2:mock_query:mock_candidates"
    stage_data = [
        {"chunk_id": "c1", "text": "chunk 1 content", "score": 0.95, "metadata": {}},
        {"chunk_id": "c2", "text": "chunk 2 content", "score": 0.88, "metadata": {}},
    ]

    success = await set_cached_stage(stage_key, stage_data, ttl_seconds=600)
    assert success is True

    restored = await get_cached_stage(stage_key)
    assert restored is not None
    assert len(restored) == 2
    assert restored[0]["chunk_id"] == "c1"


# ─── Subsystem 4: Adaptive RAG Stage & Pipeline Integration Tests ───────────

@pytest.mark.asyncio
async def test_adaptive_rag_stage1_decision_cache_hit():
    pipeline = AdaptiveAdvancedRAGPipeline()
    query = "azure application gateway waf v2"

    cached_decision = {
        "routing_path": "direct_fast",
        "strategy": "direct_fast",
        "rewritten_query": "Azure Application Gateway WAF v2 configuration",
        "expanded_queries": ["App Gateway WAF CRS rules"],
        "hyde_passage": "Azure Application Gateway WAF v2 provides managed security.",
        "applied_transformations": ["exact_syntax"],
    }

    await set_cached_decision("adaptive_transform", query, cached_decision, "v1")

    # Call _transform_query — should hit decision cache and NOT call the router LLM
    with patch("retrieval.adaptive_rag.get_sub_model_provider") as mock_get_sub:
        mock_router = MagicMock()
        mock_router.classify = AsyncMock(side_effect=AssertionError("LLM should not be called on cache hit"))
        mock_get_sub.return_value = mock_router

        res = await pipeline._transform_query(query, "Max")

    assert res.strategy == "direct_fast"
    assert res.rewritten_query == "Azure Application Gateway WAF v2 configuration"


@pytest.mark.asyncio
async def test_adaptive_rag_stage2_retrieval_cache_rehydration():
    pipeline = AdaptiveAdvancedRAGPipeline()
    query = "azure cosmos db consistency models"
    transformed = {
        "rewritten_query": query,
        "expanded_queries": [query],
        "routing_path": "direct_fast",
    }
    cls_obj = QueryClassification(
        intent="architecture",
        routes=["RAG"],
        confidence=0.95,
        risk_level="low",
        services=["cosmosdb"],
        providers=["azure"],
        categories=["database"],
        reasoning="Azure Cosmos DB architecture",
    )

    # Mock get_cached_retrieval_result returning serialized dicts
    cached_dict_results = [
        {
            "chunk_id": "chunk-cosmos-1",
            "text": "Strong consistency offers linearizability guarantees.",
            "score": 0.96,
            "metadata": {"provider": "azure", "service": "cosmosdb"},
        }
    ]

    with patch("retrieval.adaptive_rag.get_cached_retrieval_result", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = cached_dict_results

        results, source = await pipeline._multi_query_retrieve(
            query,
            transformed,
            provider_filter_dict=None,
            classification=cls_obj,
            tier="Max",
            retriever=MagicMock(),
            reranker=MagicMock(),
        )

    assert source == "cached"
    assert len(results) == 1
    # Verify proper rehydration into RetrievalResult object with chunk_id attribute
    chunk = results[0]
    assert isinstance(chunk, RetrievalResult)
    assert chunk.chunk_id == "chunk-cosmos-1"
    assert "linearizability" in chunk.text
    assert chunk.metadata.get("provider") == "azure"


@pytest.mark.asyncio
async def test_adaptive_rag_stage3_rerank_cache_write_and_hit():
    pipeline = AdaptiveAdvancedRAGPipeline()
    query = "aws lambda reserved concurrency vs provisioned"
    candidates = [
        RetrievalResult(chunk_id="c1", text="Reserved concurrency sets a maximum limit.", score=0.85, metadata={}),
        RetrievalResult(chunk_id="c2", text="Provisioned concurrency pre-warms execution environments.", score=0.92, metadata={}),
    ]
    transformed = {"routing_path": "direct_fast", "sparse_query": query}

    mock_reranker = MagicMock()
    mock_reranker.rerank = MagicMock(return_value=candidates)

    # First call: cache miss -> executes reranker and writes to stage cache
    compressed1 = await pipeline._rerank_and_compress(
        query, candidates, "Max", mock_reranker, transformed=transformed
    )
    assert len(compressed1) == 2
    assert mock_reranker.rerank.call_count == 1

    # Second call with identical candidates: hits stage cache and skips reranker
    mock_reranker.rerank.reset_mock()
    compressed2 = await pipeline._rerank_and_compress(
        query, candidates, "Max", mock_reranker, transformed=transformed
    )
    assert len(compressed2) == 2
    assert mock_reranker.rerank.call_count == 0  # Skipped because of stage cache hit!
    assert compressed2[0].chunk_id == "c1"


@pytest.mark.asyncio
async def test_adaptive_rag_exact_cache_hit_and_penalty_bypass():
    pipeline = AdaptiveAdvancedRAGPipeline()
    query = "what is aws direct connect?"

    cached_payload = {
        "answer": "AWS Direct Connect links your internal network to an AWS Direct Connect location.",
        "sources": [{"title": "AWS Direct Connect", "url": "https://aws.amazon.com/directconnect"}],
        "model": "gemini-3.8-flash",
    }

    cls_mock = QueryClassification(
        intent="factual",
        routes=["RAG"],
        confidence=0.95,
        risk_level="low",
        services=["directconnect"],
        providers=["aws"],
        categories=["networking"],
        reasoning="Direct connect definition",
    )

    mock_embedder = MagicMock()
    mock_embedder.embed_query = AsyncMock(return_value=[0.1] * 384)

    # Case 1: Normal exact cache hit
    with patch("core.llm_cache.get_cached_answer", new_callable=AsyncMock) as mock_get_answer, \
         patch("router.query_router.QueryRouter.route_query", new_callable=AsyncMock) as mock_route, \
         patch.object(pipeline, "_transform_query", new_callable=AsyncMock) as mock_transform:

        mock_get_answer.return_value = cached_payload
        mock_route.return_value = cls_mock
        mock_transform.return_value = {"routing_path": "direct_fast", "rewritten_query": query, "expanded_queries": [query]}

        result = await pipeline.run(query=query, tier="Max", stream=False)

    assert result.pipeline_type == "exact_cache"
    assert "Direct Connect links" in result.answer
    assert result.validation.get("cached") is True

    # Case 2: Feedback penalty rating=-1 bypasses exact cache
    await record_feedback_policy(query, rating=-1, reason="Outdated pricing")

    with patch("core.llm_cache.get_cached_answer", new_callable=AsyncMock) as mock_get_answer, \
         patch("router.query_router.QueryRouter.route_query", new_callable=AsyncMock) as mock_route, \
         patch("api.chat_routes.pipeline.get_embeddings", return_value=mock_embedder), \
         patch.object(pipeline, "_transform_query", new_callable=AsyncMock) as mock_transform, \
         patch.object(pipeline, "_multi_query_retrieve", new_callable=AsyncMock) as mock_retrieve, \
         patch.object(pipeline, "_rerank_and_compress", new_callable=AsyncMock) as mock_rerank, \
         patch.object(pipeline, "_generate", new_callable=AsyncMock) as mock_gen:

        mock_get_answer.return_value = cached_payload
        mock_route.return_value = cls_mock
        mock_transform.return_value = {"routing_path": "direct_fast", "rewritten_query": query, "expanded_queries": [query]}
        mock_retrieve.return_value = ([], "fresh")
        mock_rerank.return_value = []
        mock_gen.return_value = ("Fresh un-cached answer", "test-model")

        result_penalized = await pipeline.run(query=query, tier="Max", stream=False)

    # When penalized, exact answer cache is bypassed
    assert result_penalized.pipeline_type == "adaptive_rag"
    assert result_penalized.answer == "Fresh un-cached answer"


@pytest.mark.asyncio
async def test_canonical_answer_cache_dual_write_and_resolution():
    """Verify that an answer written with model name can be resolved by generic lookup (model="")."""
    query = "What is Amazon S3 Glacier?"
    payload = {"answer": "Amazon S3 Glacier is a secure, durable, and low-cost storage class.", "sources": [], "model": "gemini-2.5-flash"}

    # Write with explicit model
    await set_cached_answer(query=query, response_payload=payload, model="gemini-2.5-flash", ttl_seconds=300)

    # 1. Lookup with model="" (default in exact and semantic lookups)
    hit_generic = await get_cached_answer(query=query)
    assert hit_generic is not None
    assert hit_generic["answer"] == payload["answer"]
    assert hit_generic["model"] == "gemini-2.5-flash"

    # 2. Lookup with specific model
    hit_specific = await get_cached_answer(query=query, model="gemini-2.5-flash")
    assert hit_specific is not None
    assert hit_specific["answer"] == payload["answer"]


@pytest.mark.asyncio
async def test_retrieval_cache_auto_serialization_and_l1_fallback():
    """Verify set_cached_retrieval_result serializes RetrievalResult objects and works via L1."""
    query = "test retrieval key"
    c1 = RetrievalResult(chunk_id="chunk-1", text="Sample AWS text", score=0.95, metadata={"provider": "aws"})
    c2 = RetrievalResult(chunk_id="chunk-2", text="Sample GCP text", score=0.88, metadata={"provider": "gcp"})

    # Write objects directly
    success = await set_cached_retrieval_result(query, [c1, c2], provider_filter="all", ttl_seconds=600)
    assert success is True

    # Read back from cache
    cached = await get_cached_retrieval_result(query, provider_filter="all")
    assert isinstance(cached, list)
    assert len(cached) == 2
    assert cached[0]["chunk_id"] == "chunk-1"
    assert cached[0]["text"] == "Sample AWS text"
    assert cached[0]["metadata"]["provider"] == "aws"


@pytest.mark.asyncio
async def test_tool_cache_l1_in_process_fallback():
    """Verify tool_cache stores and reads from L1 memory cache when Redis is absent.

    With no durable layer available the write is reported as False (honest
    status), but the L1 write still succeeds and reads back correctly."""
    params = {"q": "ec2 m5.large", "providers": "aws"}
    result_data = [{"service": "AmazonEC2", "pricePerHour": 0.096}]

    # Set tool cache — L1-only, so the durable write is reported as not done
    written = await set_cached_tool_result("cloud_pricing", params, result_data, ttl_seconds=60)
    assert written is False

    # Get tool cache — L1 read-back still works
    read_data = await get_cached_tool_result("cloud_pricing", params)
    assert read_data == result_data


def test_compress_chunk_handles_dictionaries_defensively():
    """Verify compress_chunk safely operates on dicts as well as RetrievalResult objects without AttributeError."""
    profile = ("error", "resolve", "fix", "cli")
    dict_chunk = {
        "chunk_id": "c-dict",
        "text": (
            "When you encounter an error, use the CLI fix command to resolve the issue. "
            "This is a second sentence detailing permissions and IAM policies. "
            "This third sentence contains additional general cloud information that is unrelated to fixing the issue. "
            "This fourth sentence is also generic background filler text not matching any profile."
        ),
        "score": 0.9,
        "metadata": {"provider": "aws"},
    }
    result = compress_chunk("fix CLI error", dict_chunk, profile=profile)
    assert isinstance(result, dict)
    assert "compressed" in result["metadata"]
    assert "error" in result["text"]
