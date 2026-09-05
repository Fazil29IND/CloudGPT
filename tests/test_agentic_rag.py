import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from retrieval.agentic_rag import AgenticRAGPipeline
from retrieval.hybrid import RetrievalResult
from router.query_router import QueryClassification


@pytest.fixture
def agentic_pipeline():
    return AgenticRAGPipeline()


# ── Stage 1: Plan & Route ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_plan_and_route_parses_valid_llm_json(agentic_pipeline):
    mock_router = MagicMock()
    valid_json = json.dumps({
        "intent": "compare",
        "routes": ["RAG", "INTERNET"],
        "retrieval_strategy": "multi-hop",
        "sub_queries": ["AWS EKS config", "GKE config"],
        "providers": ["aws", "gcp"],
        "needs_internet": True,
        "confidence": 0.95,
    })
    mock_router.classify = AsyncMock(return_value=valid_json)

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        plan = await agentic_pipeline._plan_and_route("Compare EKS and GKE", "Pro")

    assert plan["intent"] == "compare"
    assert plan["retrieval_strategy"] == "multi-hop"
    assert plan["sub_queries"] == ["AWS EKS config", "GKE config"]
    assert plan["providers"] == ["aws", "gcp"]
    assert plan["needs_internet"] is True
    assert plan["confidence"] == 0.95


@pytest.mark.asyncio
async def test_plan_and_route_falls_back_on_llm_failure(agentic_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(side_effect=RuntimeError("Provider offline"))

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        plan = await agentic_pipeline._plan_and_route("What is S3?", "Pro")

    assert plan["intent"] == "explain"
    assert plan["retrieval_strategy"] == "broad"
    assert plan["sub_queries"] == []
    assert plan["confidence"] == 0.7


@pytest.mark.asyncio
async def test_plan_and_route_falls_back_on_timeout(agentic_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(side_effect=asyncio.TimeoutError())

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        plan = await agentic_pipeline._plan_and_route("What is S3?", "Pro")

    assert plan["retrieval_strategy"] == "broad"
    assert plan["sub_queries"] == []


@pytest.mark.asyncio
async def test_plan_and_route_falls_back_on_invalid_json(agentic_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(return_value="Not a JSON string")

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        plan = await agentic_pipeline._plan_and_route("What is S3?", "Pro")

    assert plan["retrieval_strategy"] == "broad"


@pytest.mark.asyncio
async def test_plan_retrieval_strategy_validated_to_allowed_values(agentic_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(return_value=json.dumps({"retrieval_strategy": "invalid_strat"}))

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        plan = await agentic_pipeline._plan_and_route("What is S3?", "Pro")

    assert plan["retrieval_strategy"] == "broad"


@pytest.mark.asyncio
async def test_sub_queries_cleared_for_non_multi_hop_strategy(agentic_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(return_value=json.dumps({
        "retrieval_strategy": "broad",
        "sub_queries": ["sub1", "sub2"],
    }))

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        plan = await agentic_pipeline._plan_and_route("What is S3?", "Pro")

    assert plan["sub_queries"] == []


@pytest.mark.asyncio
async def test_sub_queries_capped_at_3(agentic_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(return_value=json.dumps({
        "retrieval_strategy": "multi-hop",
        "sub_queries": ["q1", "q2", "q3", "q4", "q5"],
    }))

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        plan = await agentic_pipeline._plan_and_route("Complex query", "Pro")

    assert len(plan["sub_queries"]) == 3


# ── Stage 2: Hybrid Retrieve (Multi-Hop RRF) ────────────────────────────────

@pytest.mark.asyncio
async def test_hybrid_retrieve_multi_hop_merges_by_rrf(agentic_pipeline):
    mock_retriever = MagicMock()
    res1 = RetrievalResult(chunk_id="c1", text="text1", score=0.9, metadata={"url": "http://1"})
    res2 = RetrievalResult(chunk_id="c2", text="text2", score=0.8, metadata={"url": "http://2"})
    res3 = RetrievalResult(chunk_id="c1", text="text1", score=0.7, metadata={"url": "http://1"})
    res4 = RetrievalResult(chunk_id="c3", text="text3", score=0.6, metadata={"url": "http://3"})

    async def fake_retrieve(query, top_k=10, filters=None):
        if "eks" in query:
            return [res1, res2]
        return [res3, res4]

    mock_retriever.retrieve = AsyncMock(side_effect=fake_retrieve)
    mock_reranker = MagicMock()
    mock_reranker.rerank = MagicMock(side_effect=lambda q, cands, top_k: cands[:top_k])

    classification = QueryClassification(
        intent="compare", routes=["RAG"], providers=["aws", "gcp"],
        services=[], categories=[], confidence=0.9, reasoning="r", needs_internet=False
    )

    with patch("retrieval.agentic_rag.get_cached_retrieval_result", return_value=None), \
         patch("retrieval.agentic_rag.set_cached_retrieval_result", return_value=None):
        results, fallback_pass = await agentic_pipeline._hybrid_retrieve(
            query="Compare EKS and GKE",
            sub_queries=["eks query", "gke query"],
            retrieval_strategy="multi-hop",
            provider_filter=None,
            classification=classification,
            tier="Pro",
            retriever=mock_retriever,
            reranker=mock_reranker,
        )

    assert fallback_pass == "agentic_multi_hop"
    # c1 appeared in both batches, so it should have higher RRF score
    chunk_ids = [r.chunk_id for r in results]
    assert "c1" in chunk_ids
    assert chunk_ids[0] == "c1"


@pytest.mark.asyncio
async def test_hybrid_retrieve_failed_sub_query_skipped_not_raised(agentic_pipeline):
    mock_retriever = MagicMock()
    res1 = RetrievalResult(chunk_id="c1", text="text1", score=0.9, metadata={})

    async def fake_retrieve(query, top_k=10, filters=None):
        if "fail" in query:
            raise RuntimeError("Pinecone timeout")
        return [res1]

    mock_retriever.retrieve = AsyncMock(side_effect=fake_retrieve)
    mock_reranker = MagicMock()
    mock_reranker.rerank = MagicMock(side_effect=lambda q, cands, top_k: cands[:top_k])

    classification = QueryClassification(
        intent="compare", routes=["RAG"], providers=[], services=[],
        categories=[], confidence=0.9, reasoning="", needs_internet=False
    )

    with patch("retrieval.agentic_rag.get_cached_retrieval_result", return_value=None), \
         patch("retrieval.agentic_rag.set_cached_retrieval_result", return_value=None):
        results, _ = await agentic_pipeline._hybrid_retrieve(
            query="test",
            sub_queries=["good query", "fail query"],
            retrieval_strategy="multi-hop",
            provider_filter=None,
            classification=classification,
            tier="Pro",
            retriever=mock_retriever,
            reranker=mock_reranker,
        )

    assert len(results) == 1
    assert results[0].chunk_id == "c1"


# ── Stage 3: Grade Evidence ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_grade_evidence_filters_chunks_below_0_4(agentic_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(return_value="[0.9, 0.2, 0.7]")

    c1 = RetrievalResult(chunk_id="c1", text="good text", score=0.9, metadata={})
    c2 = RetrievalResult(chunk_id="c2", text="irrelevant noise", score=0.8, metadata={})
    c3 = RetrievalResult(chunk_id="c3", text="fair text", score=0.7, metadata={})

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        graded = await agentic_pipeline._grade_evidence("query", [c1, c2, c3], "Pro")

    assert len(graded) == 2
    assert graded[0].chunk_id == "c1"
    assert graded[1].chunk_id == "c3"
    assert graded[0].metadata["grade_score"] == 0.9


@pytest.mark.asyncio
async def test_grade_evidence_keeps_top3_when_all_below_threshold(agentic_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(return_value="[0.1, 0.2, 0.3, 0.05]")

    chunks = [
        RetrievalResult(chunk_id=f"c{i}", text=f"text{i}", score=float(i), metadata={})
        for i in range(4)
    ]

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        graded = await agentic_pipeline._grade_evidence("query", chunks, "Pro")

    assert len(graded) == 3


@pytest.mark.asyncio
async def test_grade_evidence_safe_on_json_parse_failure(agentic_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(return_value="Error during grading")

    chunks = [RetrievalResult(chunk_id="c1", text="text1", score=0.9, metadata={})]

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        graded = await agentic_pipeline._grade_evidence("query", chunks, "Pro")

    assert len(graded) == 1
    assert graded[0].chunk_id == "c1"


# ── Stage 4: Generate & Self-Critique ────────────────────────────────────────

@pytest.mark.asyncio
async def test_self_critique_returns_original_on_no_revision_needed(agentic_pipeline):
    async def fake_generate(messages, stream=False, tier="Pro", thinking_level=None, **kwargs):
        return "Initial drafted answer", "claude-sonnet"

    mock_evaluator = MagicMock()
    mock_evaluator.generate = AsyncMock(return_value="NO_REVISION_NEEDED")

    timings = {}
    with patch("api.chat_routes.generate_with_fallback", side_effect=fake_generate), \
         patch("llm.provider.get_evaluator_provider", return_value=mock_evaluator), \
         patch("api.chat_routes._build_pipeline_messages", return_value=[{"role": "user", "content": "q"}]):
        answer, model = await agentic_pipeline._generate_and_verify(
            query="test query",
            graded_chunks=[],
            classification_obj=MagicMock(),
            internet_results=[],
            provider_filter=None,
            chat_history=None,
            attachment_texts=None,
            tier="Pro",
            thinking_level="Medium",
            stream=False,
            timings=timings,
            emit_event=None,
        )

    assert answer == "Initial drafted answer"
    assert model == "claude-sonnet"
    assert "generate" in timings
    assert "self_critique" in timings


@pytest.mark.asyncio
async def test_self_critique_extracts_revised_answer(agentic_pipeline):
    async def fake_generate(messages, stream=False, tier="Pro", thinking_level=None, **kwargs):
        return "Initial answer with flaw", "claude-sonnet"

    mock_evaluator = MagicMock()
    mock_evaluator.generate = AsyncMock(return_value="REVISED ANSWER:\nCorrected and updated answer")

    timings = {}
    with patch("api.chat_routes.generate_with_fallback", side_effect=fake_generate), \
         patch("llm.provider.get_evaluator_provider", return_value=mock_evaluator), \
         patch("api.chat_routes._build_pipeline_messages", return_value=[{"role": "user", "content": "q"}]):
        answer, model = await agentic_pipeline._generate_and_verify(
            query="test query",
            graded_chunks=[],
            classification_obj=MagicMock(),
            internet_results=[],
            provider_filter=None,
            chat_history=None,
            attachment_texts=None,
            tier="Pro",
            thinking_level="Medium",
            stream=False,
            timings=timings,
            emit_event=None,
        )

    assert answer == "Corrected and updated answer"


@pytest.mark.asyncio
async def test_full_pipeline_run_returns_pipeline_result(agentic_pipeline):
    mock_plan = {
        "intent": "explain", "routes": ["RAG"], "retrieval_strategy": "broad",
        "sub_queries": [], "providers": ["aws"], "needs_internet": False,
        "confidence": 0.9, "_timing_ms": 10.0,
    }

    chunks = [RetrievalResult(chunk_id="c1", text="aws s3 doc", score=0.9, metadata={"provider": "aws", "url": "http://s3"})]

    with patch.object(agentic_pipeline, "_plan_and_route", return_value=mock_plan), \
         patch.object(agentic_pipeline, "_hybrid_retrieve", return_value=(chunks, "pass_1")), \
         patch.object(agentic_pipeline, "_grade_evidence", return_value=chunks), \
         patch.object(agentic_pipeline, "_generate_and_verify", return_value=("Agentic answer", "claude-sonnet")), \
         patch("api.chat_routes.pipeline.get_retrieval", return_value=(MagicMock(), MagicMock())):
        result = await agentic_pipeline.run(
            query="How does S3 work?",
            tier="Pro",
        )

    assert result.pipeline_type == "agentic_rag"
    assert result.answer == "Agentic answer"
    assert result.model_used == "claude-sonnet"
    assert len(result.sources) == 1
    assert "plan_route" in result.pipeline_timings
    assert "hybrid_retrieve" in result.pipeline_timings
    assert "grade_evidence" in result.pipeline_timings


def test_build_plan_guided_representation(agentic_pipeline):
    """Test asymmetric generation of dense and sparse representations guided by the plan."""
    plan = {
        "intent": "troubleshooting",
        "providers": ["aws"],
        "sub_queries": ["What is Lambda concurrency error 429?"],
        "retrieval_modality": "hybrid",
    }
    raw_query = "Can you please tell me how to fix RequestLimitExceeded error --max-concurrency?"

    rep = agentic_pipeline._build_plan_guided_representation(raw_query, plan=plan)

    # Dense representation: includes troubleshooting anchors and provider prefix
    assert "troubleshooting" in rep.dense_query
    assert "AWS" in rep.dense_query
    assert "RequestLimitExceeded" in rep.dense_query

    # Sparse representation: stripped boilerplate, preserved exact error and flag
    assert not rep.sparse_query.lower().startswith("can you please tell me")
    assert "RequestLimitExceeded" in rep.sparse_query
    assert "--max-concurrency" in rep.sparse_query

    # Sub-query representations
    assert len(rep.sub_query_representations) == 1
    sq_dense, sq_sparse = rep.sub_query_representations[0]
    assert "troubleshooting" in sq_dense
    assert "429" in sq_sparse


@pytest.mark.asyncio
async def test_hybrid_retrieve_uses_plan_guided_representations(agentic_pipeline):
    """Test that _hybrid_retrieve dispatches plan_repr.dense_query to dense retriever and plan_repr.sparse_query to sparse retriever."""
    mock_retriever = MagicMock()
    mock_dense = MagicMock()
    mock_sparse = MagicMock()

    mock_dense.retrieve = AsyncMock(return_value=[RetrievalResult(chunk_id="d1", text="lambda doc", score=0.9)])
    mock_sparse.retrieve = AsyncMock(return_value=[RetrievalResult(chunk_id="s1", text="error trace", score=0.85)])

    mock_retriever.dense_retriever = mock_dense
    mock_retriever.sparse_retriever = mock_sparse

    mock_reranker = MagicMock()
    mock_reranker.rerank = MagicMock(side_effect=lambda q, cands, top_k: cands[:top_k])

    classification = QueryClassification(
        intent="troubleshooting",
        routes=["RAG"],
        providers=["aws"],
        services=[],
        categories=[],
        confidence=0.9,
        reasoning="",
        needs_internet=False,
    )
    plan = {
        "intent": "troubleshooting",
        "providers": ["aws"],
        "sub_queries": [],
        "retrieval_strategy": "broad",
        "retrieval_modality": "hybrid",
    }

    raw_query = "Could you explain why I get RequestLimitExceeded --timeout=30?"

    with patch("retrieval.agentic_rag.get_cached_retrieval_result", return_value=None), \
         patch("retrieval.agentic_rag.set_cached_retrieval_result", return_value=None):
        results, fallback = await agentic_pipeline._hybrid_retrieve(
            query=raw_query,
            sub_queries=[],
            retrieval_strategy="broad",
            provider_filter="aws",
            classification=classification,
            tier="Pro",
            retriever=mock_retriever,
            reranker=mock_reranker,
            retrieval_modality="hybrid",
            plan=plan,
        )

    # Verify dense retriever got semantic framing with AWS
    dense_call_arg = mock_dense.retrieve.call_args[0][0]
    assert "troubleshooting" in dense_call_arg
    assert "AWS" in dense_call_arg

    # Verify sparse retriever got high-signal query without conversational boilerplate
    sparse_call_arg = mock_sparse.retrieve.call_args[0][0]
    assert not sparse_call_arg.lower().startswith("could you explain")
    assert "--timeout=30" in sparse_call_arg
    assert fallback == "agentic_hybrid_rrf"

