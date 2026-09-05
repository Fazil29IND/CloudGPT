"""Unit tests for Hybrid RAG (Lite): HNSW + BM25 + RRF Fusion + Reranker."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from retrieval import RetrievalResult, Reranker
from retrieval.dense import HNSWRetriever
from retrieval.hnsw_index import HNSWIndex
from retrieval.hybrid import HybridRetriever


def _make_result(chunk_id: str, score: float, url: str = "https://aws.amazon.com") -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        text=f"Content for {chunk_id}",
        score=score,
        metadata={"url": url, "document_type": "official"},
    )


@pytest.mark.asyncio
async def test_hybrid_lite_hnsw_and_bm25_rrf_and_reranker():
    # 1. HNSW dense retriever with mock embeddings
    mock_engine = MagicMock()
    mock_engine.embed_query = AsyncMock(return_value=[1.0, 0.0, 0.0])

    hnsw_index = HNSWIndex(dimension=3)
    hnsw_index.add_item("hnsw-doc-1", [1.0, 0.0, 0.0], text="Amazon S3 Overview", metadata={"url": "https://aws/s3/1", "document_type": "official"})
    hnsw_index.add_item("shared-doc", [0.9, 0.1, 0.0], text="AWS Object Storage", metadata={"url": "https://aws/s3/shared", "document_type": "official"})

    hnsw_retriever = HNSWRetriever(mock_engine, hnsw_index=hnsw_index)

    # 2. Sparse BM25 retriever mock
    sparse_retriever = MagicMock()
    sparse_retriever.retrieve = AsyncMock(return_value=[
        _make_result("sparse-doc-1", 0.95, "https://aws/s3/sparse1"),
        _make_result("shared-doc", 0.88, "https://aws/s3/shared"),
    ])

    # 3. HybridRetriever combining HNSW + BM25 via RRF
    hybrid = HybridRetriever(hnsw_retriever, sparse_retriever)

    results = await hybrid.retrieve("Amazon S3 storage", top_k=5, namespace="services-v2")

    assert len(results) >= 2
    chunk_ids = [r.chunk_id for r in results]
    assert "shared-doc" in chunk_ids
    # shared-doc appears in both HNSW and BM25, so its fused RRF rank should be highest
    assert chunk_ids[0] == "shared-doc"

    # 4. Reranker cross-encoder step
    reranker = Reranker()
    reranker.model = MagicMock()
    # Mock rerank output
    reranker.model.rerank = MagicMock(return_value=[
        {"id": "shared-doc", "text": "AWS Object Storage", "score": 0.98, "meta": {"document_type": "official"}},
        {"id": "hnsw-doc-1", "text": "Amazon S3 Overview", "score": 0.85, "meta": {"document_type": "official"}},
    ])

    reranked = reranker.rerank("Amazon S3 storage", results, top_k=2)
    assert len(reranked) == 2
    assert reranked[0].chunk_id == "shared-doc"
    assert reranked[0].score > 0.9


def test_hybrid_lite_exact_technical_query_adaptive_weights():
    dense_mock = MagicMock()
    sparse_mock = MagicMock()
    hybrid = HybridRetriever(dense_mock, sparse_mock)

    # Exact CLI query -> lowers dense weight to 0.3, increases sparse weight to 0.7
    w_dense, w_sparse = hybrid._get_effective_weights("aws s3api put-bucket-policy --policy file://policy.json")
    assert w_dense <= 0.35
    assert w_sparse >= 0.65

    # Natural language conceptual query -> increases dense weight
    w_dense_nlq, w_sparse_nlq = hybrid._get_effective_weights("What are the architectural trade-offs of microservices?")
    assert w_dense_nlq >= 0.65
    assert w_sparse_nlq <= 0.35
