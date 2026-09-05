import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline
from retrieval.hybrid import RetrievalResult
from router.query_router import QueryClassification


@pytest.fixture
def adaptive_pipeline():
    return AdaptiveAdvancedRAGPipeline()


# ── Stage 1: Transform Query ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transform_query_parses_all_fields(adaptive_pipeline):
    mock_router = MagicMock()
    valid_json = json.dumps({
        "rewritten_query": "AWS VPC peering configuration with GCP",
        "expanded_queries": ["AWS GCP VPC peering steps", "Cross cloud private network connection"],
        "hyde_passage": "To connect AWS VPC to GCP VPC privately, use Cloud Interconnect with Direct Connect.",
    })
    mock_router.classify = AsyncMock(return_value=valid_json)

    with patch("retrieval.adaptive_rag.get_sub_model_provider", return_value=mock_router):
        res = await adaptive_pipeline._transform_query("vpc peering aws gcp", "Max")

    assert res["rewritten_query"] == "AWS VPC peering configuration with GCP"
    assert len(res["expanded_queries"]) == 2
    assert "Direct Connect" in res["hyde_passage"]


@pytest.mark.asyncio
async def test_transform_query_identity_fallback_on_llm_failure(adaptive_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(side_effect=RuntimeError("Submodel failure"))

    with patch("retrieval.adaptive_rag.get_sub_model_provider", return_value=mock_router):
        res = await adaptive_pipeline._transform_query("simple query", "Max")

    assert res["rewritten_query"] == "simple query"
    assert res["expanded_queries"] == ["simple query"]
    assert res["hyde_passage"] == "simple query"


@pytest.mark.asyncio
async def test_transform_query_identity_fallback_on_invalid_json(adaptive_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(return_value="Plain text instead of json")

    with patch("retrieval.adaptive_rag.get_sub_model_provider", return_value=mock_router):
        res = await adaptive_pipeline._transform_query("simple query", "Max")

    assert res["rewritten_query"] == "simple query"


@pytest.mark.asyncio
async def test_transform_query_identity_fallback_on_timeout(adaptive_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(side_effect=asyncio.TimeoutError())

    with patch("retrieval.adaptive_rag.get_sub_model_provider", return_value=mock_router):
        res = await adaptive_pipeline._transform_query("simple query", "Max")

    assert res["rewritten_query"] == "simple query"


@pytest.mark.asyncio
async def test_transform_query_expanded_queries_capped_at_3(adaptive_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(return_value=json.dumps({
        "rewritten_query": "q",
        "expanded_queries": ["1", "2", "3", "4", "5"],
        "hyde_passage": "h",
    }))

    with patch("retrieval.adaptive_rag.get_sub_model_provider", return_value=mock_router):
        res = await adaptive_pipeline._transform_query("q", "Max")

    assert len(res["expanded_queries"]) == 3


# ── Stage 2: Multi-Query Retrieve (+ HyDE) ──────────────────────────────────

@pytest.mark.asyncio
async def test_multi_query_retrieve_rrf_and_hyde_merge(adaptive_pipeline):
    mock_retriever = MagicMock()
    c1 = RetrievalResult(chunk_id="c1", text="text1", score=0.9, metadata={"url": "http://1"})
    c2 = RetrievalResult(chunk_id="c2", text="text2", score=0.8, metadata={"url": "http://2"})

    mock_retriever.retrieve = AsyncMock(return_value=[c1, c2])

    transformed = {
        "rewritten_query": "rewritten q",
        "expanded_queries": ["expanded q1", "expanded q2"],
        "hyde_passage": "hypothetical answer",
    }
    classification = QueryClassification(
        intent="explain", routes=["RAG"], providers=[], services=[],
        categories=[], confidence=0.9, reasoning="", needs_internet=False
    )

    mock_emb = MagicMock()
    mock_emb.embed_query = AsyncMock(return_value=[0.1] * 1536)
    mock_pinecone = MagicMock()
    mock_pinecone.active_namespace = MagicMock(return_value="services")
    mock_pinecone.search_dense = AsyncMock(return_value=[
        {"id": "c3", "score": 0.85, "metadata": {"text": "hyde match", "url": "http://3"}}
    ])

    with patch("api.chat_routes.pipeline.embedding_engine", mock_emb), \
         patch("api.chat_routes.pipeline.pinecone_manager", mock_pinecone), \
         patch("retrieval.adaptive_rag.get_cached_retrieval_result", return_value=None), \
         patch("retrieval.adaptive_rag.set_cached_retrieval_result", return_value=None):
        results, fallback_pass = await adaptive_pipeline._multi_query_retrieve(
            original_query="orig q",
            transformed=transformed,
            provider_filter_dict=None,
            classification=classification,
            tier="Max",
            retriever=mock_retriever,
            reranker=None,
        )

    assert fallback_pass == "adaptive_multi_query"
    ids = [r.chunk_id for r in results]
    assert "c1" in ids
    assert "c2" in ids
    assert "c3" in ids


@pytest.mark.asyncio
async def test_multi_query_retrieve_url_deduplication(adaptive_pipeline):
    mock_retriever = MagicMock()
    c1 = RetrievalResult(chunk_id="c1", text="text1", score=0.9, metadata={"url": "http://same.url"})
    c2 = RetrievalResult(chunk_id="c2", text="text2", score=0.8, metadata={"url": "http://same.url"})

    mock_retriever.retrieve = AsyncMock(return_value=[c1, c2])

    transformed = {
        "rewritten_query": "q",
        "expanded_queries": [],
        "hyde_passage": "q",
    }
    classification = QueryClassification(
        intent="explain", routes=["RAG"], providers=[], services=[],
        categories=[], confidence=0.9, reasoning="", needs_internet=False
    )

    with patch("api.chat_routes.pipeline.embedding_engine", None), \
         patch("api.chat_routes.pipeline.pinecone_manager", None), \
         patch("retrieval.adaptive_rag.get_cached_retrieval_result", return_value=None), \
         patch("retrieval.adaptive_rag.set_cached_retrieval_result", return_value=None):
        results, _ = await adaptive_pipeline._multi_query_retrieve(
            original_query="q",
            transformed=transformed,
            provider_filter_dict=None,
            classification=classification,
            tier="Max",
            retriever=mock_retriever,
            reranker=None,
        )

    assert len(results) == 1
    assert results[0].chunk_id == "c1"


# ── Stage 3: Rerank & Compress ──────────────────────────────────────────────

def test_compress_chunk_keeps_relevant_sentences_and_first_two(adaptive_pipeline):
    query = "configure s3 bucket lifecycle policy"
    text = (
        "Amazon S3 is object storage. "
        "It provides high durability and availability. "
        "Irrelevant sentence about databases and computing instances that has no overlap. "
        "Another random unrelated sentence about lambda functions. "
        "You can configure an S3 bucket lifecycle policy in the management console."
    )
    chunk = RetrievalResult(chunk_id="c1", text=text, score=0.85, metadata={})
    compressed = adaptive_pipeline._compress_chunk(query, chunk)

    assert compressed.metadata["compressed"] is True
    assert "Amazon S3 is object storage." in compressed.text
    assert "It provides high durability and availability." in compressed.text
    assert "configure an S3 bucket lifecycle policy" in compressed.text
    assert "Irrelevant sentence about databases" not in compressed.text


def test_compress_chunk_low_score_kept_full(adaptive_pipeline):
    query = "s3 bucket"
    text = "Sentence one. Sentence two. Sentence three. Sentence four."
    chunk = RetrievalResult(chunk_id="c1", text=text, score=0.3, metadata={})
    res = adaptive_pipeline._compress_chunk(query, chunk)

    assert res.metadata["compressed"] is False
    assert res.text == text


# ── Stage 4: Generate & Orchestration ───────────────────────────────────────

@pytest.mark.asyncio
async def test_full_pipeline_run_returns_pipeline_result(adaptive_pipeline):
    transformed = {
        "rewritten_query": "clean query",
        "expanded_queries": ["clean query"],
        "hyde_passage": "passage",
    }
    classification = QueryClassification(
        intent="explain", routes=["RAG"], providers=["aws"],
        services=["s3"], categories=["storage"], confidence=0.9, reasoning="", needs_internet=False
    )
    chunks = [RetrievalResult(chunk_id="c1", text="chunk text", score=0.9, metadata={"provider": "aws", "url": "http://1"})]

    with patch.object(adaptive_pipeline, "_transform_query", return_value=transformed), \
         patch("router.query_router.QueryRouter.route_query", AsyncMock(return_value=classification)), \
         patch.object(adaptive_pipeline, "_multi_query_retrieve", return_value=(chunks, "multi_query_pass")), \
         patch.object(adaptive_pipeline, "_rerank_and_compress", return_value=chunks), \
         patch("api.chat_routes.generate_with_fallback", AsyncMock(return_value=("Adaptive answer", "claude-max-thinking"))), \
         patch("api.chat_routes._build_pipeline_messages", return_value=[{"role": "user", "content": "q"}]), \
         patch("api.chat_routes.pipeline.get_retrieval", return_value=(MagicMock(), MagicMock())), \
         patch("api.chat_routes.pipeline.get_main_llm", return_value=MagicMock()), \
         patch("api.chat_routes.pipeline.get_router_llm", return_value=MagicMock()):
        result = await adaptive_pipeline.run(
            query="Deep cloud query",
            tier="Max",
        )

    assert result.pipeline_type == "adaptive_rag"
    assert result.answer == "Adaptive answer"
    assert result.model_used == "claude-max-thinking"
    assert "transform_query" in result.pipeline_timings
    assert "multi_query_retrieve" in result.pipeline_timings
    assert "rerank_compress" in result.pipeline_timings
    assert "generate" in result.pipeline_timings
