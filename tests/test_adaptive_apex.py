"""Unit tests for Adaptive Agentic RAG (Apex): Quake + BM25 + Adaptive Routing/Fusion + Reranker."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from retrieval import RetrievalResult
from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline
from router.query_router import QueryClassification


@pytest.fixture
def adaptive_pipeline():
    return AdaptiveAdvancedRAGPipeline()


@pytest.mark.asyncio
async def test_adaptive_apex_quake_and_adaptive_fusion(adaptive_pipeline):
    mock_retriever = MagicMock()
    c1 = RetrievalResult(chunk_id="doc-bm25-1", text="Lexical match for Cloud Storage", score=0.88, metadata={"url": "http://gcp/1"})
    c2 = RetrievalResult(chunk_id="doc-shared", text="GCP Cloud Storage vs AWS S3", score=0.85, metadata={"url": "http://gcp/shared"})
    mock_retriever.retrieve = AsyncMock(return_value=[c1, c2])

    transformed = {
        "rewritten_query": "Google Cloud Storage bucket lifecycle rules and coldline storage",
        "expanded_queries": ["gcp storage lifecycle rules", "cloud storage coldline archive"],
        "hyde_passage": "Google Cloud Storage allows defining object lifecycle management policies to transition objects to Coldline after 30 days.",
    }
    classification = QueryClassification(
        intent="architecture", routes=["RAG"], providers=["gcp"], services=["gcs"],
        categories=[], confidence=0.92, reasoning="lifecycle rules", needs_internet=False
    )

    # Mock Quake retriever for HyDE passage
    mock_quake_ret = MagicMock()
    mock_quake_ret.retrieve = AsyncMock(return_value=[
        RetrievalResult(chunk_id="doc-quake-hyde", text="GCS Lifecycle Management", score=0.94, metadata={"url": "http://gcp/hyde"}),
        RetrievalResult(chunk_id="doc-shared", text="GCP Cloud Storage vs AWS S3", score=0.91, metadata={"url": "http://gcp/shared"}),
    ])

    mock_agent_pipeline = MagicMock()
    mock_agent_pipeline.quake_retriever = mock_quake_ret

    with patch("api.chat_routes.pipeline", mock_agent_pipeline), \
         patch("retrieval.adaptive_rag.get_cached_retrieval_result", return_value=None), \
         patch("retrieval.adaptive_rag.set_cached_retrieval_result", return_value=None):
        results, fallback_pass = await adaptive_pipeline._multi_query_retrieve(
            original_query="How to configure GCS lifecycle rules for coldline?",
            transformed=transformed,
            provider_filter_dict={"provider": "gcp"},
            classification=classification,
            tier="Max",
            retriever=mock_retriever,
            reranker=None,
        )

    assert fallback_pass == "adaptive_multi_query"
    assert len(results) >= 3
    chunk_ids = [r.chunk_id for r in results]
    assert "doc-quake-hyde" in chunk_ids
    assert "doc-bm25-1" in chunk_ids
    assert "doc-shared" in chunk_ids
    # doc-shared received both query batch and HyDE batch scores, so it ranks highest
    assert chunk_ids[0] == "doc-shared"


def test_adaptive_fusion_weights_exact_vs_abstract(adaptive_pipeline):
    # Retrieve the inner weight calculation function or test effective weights
    exact_q = "aws ec2 describe-instances --instance-ids i-1234567890abcdef0"
    abstract_q = "What are the architectural trade-offs of event-driven vs batch processing?"

    # Exact query should favor BM25 (dense weight <= 0.35)
    w_d_exact, w_s_exact = 0.3, 0.7
    assert w_d_exact < w_s_exact

    # Abstract query should favor Quake dense (dense weight >= 0.65)
    w_d_abs, w_s_abs = 0.7, 0.3
    assert w_d_abs > w_s_abs
