"""Unit tests for Agentic RAG (Core): HNSW + BM25 + Agentic Retrieval Routing + RRF + Reranker."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from retrieval import RetrievalResult
from retrieval.agentic_rag import AgenticRAGPipeline
from router.query_router import QueryClassification


@pytest.fixture
def agentic_pipeline():
    return AgenticRAGPipeline()


@pytest.mark.asyncio
async def test_agentic_routing_dense_only_skips_bm25_and_rrf(agentic_pipeline):
    dense_mock = MagicMock()
    sparse_mock = MagicMock()

    dense_res = [RetrievalResult(chunk_id="hnsw-1", text="Cloud Architecture Pattern", score=0.92, metadata={})]
    dense_mock.retrieve = AsyncMock(return_value=dense_res)
    sparse_mock.retrieve = AsyncMock(return_value=[])

    retriever = MagicMock()
    retriever.dense_retriever = dense_mock
    retriever.sparse_retriever = sparse_mock

    reranker = MagicMock()
    reranker.rerank = MagicMock(side_effect=lambda q, cands, top_k: cands[:top_k])

    classification = QueryClassification(
        intent="architecture", routes=["RAG"], providers=["aws"], services=[],
        categories=[], confidence=0.9, reasoning="high-level design", needs_internet=False
    )

    with patch("retrieval.agentic_rag.get_cached_retrieval_result", return_value=None), \
         patch("retrieval.agentic_rag.set_cached_retrieval_result", return_value=None):
        results, fallback_pass = await agentic_pipeline._hybrid_retrieve(
            query="High-level multi-region disaster recovery architecture",
            sub_queries=[],
            retrieval_strategy="broad",
            provider_filter="aws",
            classification=classification,
            tier="Pro",
            retriever=retriever,
            reranker=reranker,
            retrieval_modality="dense",
        )

    # Dense only was executed
    assert fallback_pass == "agentic_dense_hnsw"
    assert dense_mock.retrieve.called
    assert not sparse_mock.retrieve.called
    assert len(results) == 1
    assert results[0].chunk_id == "hnsw-1"


@pytest.mark.asyncio
async def test_agentic_routing_sparse_only_skips_hnsw_and_rrf(agentic_pipeline):
    dense_mock = MagicMock()
    sparse_mock = MagicMock()

    sparse_res = [RetrievalResult(chunk_id="bm25-1", text="aws s3api put-bucket-policy syntax", score=0.95, metadata={})]
    dense_mock.retrieve = AsyncMock(return_value=[])
    sparse_mock.retrieve = AsyncMock(return_value=sparse_res)

    retriever = MagicMock()
    retriever.dense_retriever = dense_mock
    retriever.sparse_retriever = sparse_mock

    reranker = MagicMock()
    reranker.rerank = MagicMock(side_effect=lambda q, cands, top_k: cands[:top_k])

    classification = QueryClassification(
        intent="how_to", routes=["RAG"], providers=["aws"], services=["s3"],
        categories=[], confidence=0.95, reasoning="cli command", needs_internet=False
    )

    with patch("retrieval.agentic_rag.get_cached_retrieval_result", return_value=None), \
         patch("retrieval.agentic_rag.set_cached_retrieval_result", return_value=None):
        results, fallback_pass = await agentic_pipeline._hybrid_retrieve(
            query="aws s3api put-bucket-policy --bucket my-bucket --policy file://policy.json",
            sub_queries=[],
            retrieval_strategy="narrow",
            provider_filter="aws",
            classification=classification,
            tier="Pro",
            retriever=retriever,
            reranker=reranker,
            retrieval_modality="sparse",
        )

    # Sparse only was executed
    assert fallback_pass == "agentic_sparse_bm25"
    assert sparse_mock.retrieve.called
    assert not dense_mock.retrieve.called
    assert len(results) == 1
    assert results[0].chunk_id == "bm25-1"


@pytest.mark.asyncio
async def test_agentic_routing_hybrid_executes_both_and_applies_rrf(agentic_pipeline):
    dense_mock = MagicMock()
    sparse_mock = MagicMock()

    dense_res = [
        RetrievalResult(chunk_id="doc-dense-only", text="Lambda scale", score=0.88, metadata={}),
        RetrievalResult(chunk_id="doc-both", text="Lambda concurrency limits", score=0.91, metadata={}),
    ]
    sparse_res = [
        RetrievalResult(chunk_id="doc-sparse-only", text="ReservedConcurrentExecutions", score=0.85, metadata={}),
        RetrievalResult(chunk_id="doc-both", text="Lambda concurrency limits", score=0.89, metadata={}),
    ]
    dense_mock.retrieve = AsyncMock(return_value=dense_res)
    sparse_mock.retrieve = AsyncMock(return_value=sparse_res)

    retriever = MagicMock()
    retriever.dense_retriever = dense_mock
    retriever.sparse_retriever = sparse_mock

    reranker = MagicMock()
    reranker.rerank = MagicMock(side_effect=lambda q, cands, top_k: cands[:top_k])

    classification = QueryClassification(
        intent="troubleshooting", routes=["RAG"], providers=["aws"], services=["lambda"],
        categories=[], confidence=0.9, reasoning="hybrid query", needs_internet=False
    )

    with patch("retrieval.agentic_rag.get_cached_retrieval_result", return_value=None), \
         patch("retrieval.agentic_rag.set_cached_retrieval_result", return_value=None):
        results, fallback_pass = await agentic_pipeline._hybrid_retrieve(
            query="AWS Lambda throttling with ReservedConcurrentExecutions",
            sub_queries=[],
            retrieval_strategy="broad",
            provider_filter="aws",
            classification=classification,
            tier="Pro",
            retriever=retriever,
            reranker=reranker,
            retrieval_modality="hybrid",
        )

    # Hybrid was executed with RRF
    assert fallback_pass == "agentic_hybrid_rrf"
    assert dense_mock.retrieve.called
    assert sparse_mock.retrieve.called
    assert len(results) == 3
    # doc-both appears in both lists, so its fused RRF rank is highest
    assert results[0].chunk_id == "doc-both"
