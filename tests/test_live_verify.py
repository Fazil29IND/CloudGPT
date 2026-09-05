"""Unit tests for Live Verify escalation in AdaptiveAdvancedRAGPipeline."""

from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline
from retrieval import RetrievalResult
from tools.web_search import WebSearchResult


@pytest.fixture
def adaptive_pipeline():
    return AdaptiveAdvancedRAGPipeline()


@pytest.mark.asyncio
async def test_live_verify_executes_tool_search(adaptive_pipeline):
    mock_results = [
        WebSearchResult(
            title="AWS S3 Updates",
            url="https://docs.aws.amazon.com/s3/latest",
            content="Latest Amazon S3 features for 2026",
            score=0.95,
            source_engine="mock",
        )
    ]
    with patch("api.chat_routes.pipeline.web_search.search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = mock_results
        results = await adaptive_pipeline._live_verify(
            query="S3 cross-region replication",
            providers=["aws"],
            tier="Max",
        )
        assert len(results) == 1
        assert results[0]["title"] == "AWS S3 Updates"
        assert results[0]["live_verified"] is True
        assert results[0]["provider"] == "aws"


@pytest.mark.asyncio
async def test_live_verify_trigger_on_stale_chunks(adaptive_pipeline):
    stale_chunk = RetrievalResult(
        chunk_id="c_stale",
        text="Stale data",
        score=0.9,
        metadata={"stale": True, "provider": "aws"},
    )
    with patch.object(adaptive_pipeline, "_live_verify", new_callable=AsyncMock) as mock_lv, \
         patch.object(adaptive_pipeline, "_transform_query", new_callable=AsyncMock) as mock_tq, \
         patch.object(adaptive_pipeline, "_multi_query_retrieve", new_callable=AsyncMock) as mock_mq, \
         patch.object(adaptive_pipeline, "_rerank_and_compress", new_callable=AsyncMock) as mock_rc, \
         patch.object(adaptive_pipeline, "_generate", new_callable=AsyncMock) as mock_gen, \
         patch("api.chat_routes.pipeline.get_retrieval", return_value=(MagicMock(), MagicMock())):

        mock_tq.return_value = {"intent": "explain", "expanded_queries": [], "hyde_passage": ""}
        mock_mq.return_value = ([stale_chunk], "none")
        mock_rc.return_value = [stale_chunk]
        mock_lv.return_value = [{"title": "Verified doc", "url": "https://aws.amazon.com", "content": "fresh"}]
        mock_gen.return_value = ("Answer", "gemini-3.8-flash")

        result = await adaptive_pipeline.run(query="What is S3?", tier="Max")
        assert mock_lv.called
        assert "live_verify" in result.pipeline_timings
