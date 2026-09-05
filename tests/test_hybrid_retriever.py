"""Unit tests for HybridRetriever fault isolation, fallback behavior, and metrics."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from retrieval import RetrievalResult
from retrieval.hybrid import HybridRetriever


def _make_dummy_result(chunk_id: str, score: float = 0.9, url: str = "https://docs.aws.amazon.com/s3") -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        text=f"Sample text for {chunk_id}",
        score=score,
        metadata={"url": url, "title": "AWS S3"},
    )


@pytest.mark.asyncio
async def test_hybrid_both_succeed():
    dense_mock = MagicMock()
    sparse_mock = MagicMock()
    dense_mock.pinecone_manager.active_namespace.return_value = "services-v1"
    dense_mock.retrieve = AsyncMock(return_value=[_make_dummy_result("chunk-1", 0.95, "https://docs.aws.amazon.com/s3/1")])
    sparse_mock.retrieve = AsyncMock(return_value=[_make_dummy_result("chunk-2", 0.85, "https://docs.aws.amazon.com/s3/2")])

    hybrid = HybridRetriever(dense_mock, sparse_mock)
    results = await hybrid.retrieve("Amazon S3 storage", top_k=5, namespace="services-v1")

    assert len(results) == 2
    chunk_ids = [r.chunk_id for r in results]
    assert "chunk-1" in chunk_ids
    assert "chunk-2" in chunk_ids


@pytest.mark.asyncio
async def test_hybrid_dense_fails_falls_back_to_sparse():
    dense_mock = MagicMock()
    sparse_mock = MagicMock()
    dense_mock.pinecone_manager.active_namespace.return_value = "services-v1"
    dense_mock.retrieve = AsyncMock(side_effect=ConnectionError("Pinecone connection lost"))
    sparse_mock.retrieve = AsyncMock(return_value=[_make_dummy_result("sparse-1", 0.80, "https://docs.aws.amazon.com/ec2")])

    hybrid = HybridRetriever(dense_mock, sparse_mock)
    results = await hybrid.retrieve("EC2 compute instances", top_k=5, namespace="services-v1")

    assert len(results) == 1
    assert results[0].chunk_id == "sparse-1"


@pytest.mark.asyncio
async def test_hybrid_sparse_fails_falls_back_to_dense():
    dense_mock = MagicMock()
    sparse_mock = MagicMock()
    dense_mock.pinecone_manager.active_namespace.return_value = "services-v1"
    dense_mock.retrieve = AsyncMock(return_value=[_make_dummy_result("dense-1", 0.90, "https://cloud.google.com/gke")])
    sparse_mock.retrieve = AsyncMock(side_effect=TimeoutError("Sparse BM25 timed out"))

    hybrid = HybridRetriever(dense_mock, sparse_mock)
    results = await hybrid.retrieve("Google Kubernetes Engine GKE", top_k=5, namespace="services-v1")

    assert len(results) == 1
    assert results[0].chunk_id == "dense-1"


@pytest.mark.asyncio
async def test_hybrid_partial_namespace_failure_continues():
    dense_mock = MagicMock()
    sparse_mock = MagicMock()

    async def mock_dense(query, top_k=50, filters=None, namespace=None):
        if namespace == "broken-ns":
            raise RuntimeError("Namespace broken-ns unavailable")
        return [_make_dummy_result(f"dense-{namespace}", 0.88, f"https://example.com/{namespace}")]

    async def mock_sparse(query, top_k=50, filters=None, namespace=None):
        if namespace == "broken-ns":
            raise TimeoutError("Timeout in broken-ns")
        return [_make_dummy_result(f"sparse-{namespace}", 0.75, f"https://example.com/{namespace}/sparse")]

    dense_mock.retrieve = AsyncMock(side_effect=mock_dense)
    sparse_mock.retrieve = AsyncMock(side_effect=mock_sparse)

    hybrid = HybridRetriever(dense_mock, sparse_mock)
    results = await hybrid.retrieve(
        "cross cloud networking",
        top_k=5,
        namespaces=["healthy-ns", "broken-ns"],
    )

    assert len(results) >= 1
    chunk_ids = [r.chunk_id for r in results]
    assert any("healthy-ns" in cid for cid in chunk_ids)
    assert not any("broken-ns" in cid for cid in chunk_ids)


@pytest.mark.asyncio
async def test_hybrid_both_fail_returns_empty_list():
    dense_mock = MagicMock()
    sparse_mock = MagicMock()
    dense_mock.retrieve = AsyncMock(side_effect=ConnectionError("Dense unreachable"))
    sparse_mock.retrieve = AsyncMock(side_effect=ConnectionError("Sparse unreachable"))

    hybrid = HybridRetriever(dense_mock, sparse_mock)
    results = await hybrid.retrieve("query that fails everywhere", top_k=5, namespace="services-v1")

    assert results == []
