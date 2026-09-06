import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from api.chat_routes import _gather_pipeline_context, execute_agent_pipeline
from app import app


@pytest.mark.asyncio
async def test_gather_context_partial_failure_rag_fails_internet_succeeds():
    """When RAG fails, Internet search results are still preserved."""
    fake_classification = MagicMock(
        routes=["RAG", "INTERNET"],
        providers=[],
        needs_internet=True,
        intent="general",
        confidence=0.9,
    )
    fake_router = MagicMock(route_query=AsyncMock(return_value=fake_classification))

    fake_web_item = MagicMock(
        title="Web Title",
        url="https://aws.amazon.com/ec2",
        content="EC2 overview",
        source_engine="searxng",
    )

    with patch("api.chat_routes.pipeline.get_router", return_value=fake_router), \
         patch("api.chat_routes.pipeline.web_search.search", AsyncMock(return_value=[fake_web_item])), \
         patch("api.chat_routes._retrieve_with_fallback", AsyncMock(side_effect=TimeoutError("RAG timeout"))):

        (
            classification,
            routes,
            rag_results,
            web_results,
            internet_results,
            pricing_data,
            api_data,
            calc_results,
            citation_mgr,
            timings,
            fallback_pass,
        ) = await _gather_pipeline_context("What is EC2?", None, "Free")

        assert len(internet_results) == 1
        assert internet_results[0]["title"] == "Web Title"
        assert rag_results == []
        assert fallback_pass in ("timeout_fallback", "error_fallback")
        sources = citation_mgr.get_sources()
        assert len(sources) == 1
        assert sources[0].source_type == "internet"


@pytest.mark.asyncio
async def test_gather_context_partial_failure_internet_fails_rag_succeeds():
    """When Internet search fails with ConnectionError, RAG results are still preserved."""
    fake_classification = MagicMock(
        routes=["RAG", "INTERNET"],
        providers=[],
        needs_internet=True,
        intent="general",
        confidence=0.9,
    )
    fake_router = MagicMock(route_query=AsyncMock(return_value=fake_classification))

    fake_rag_result = MagicMock(
        text="S3 documentation content",
        metadata={"provider": "aws", "service": "s3", "title": "Amazon S3", "url": "https://aws.amazon.com/s3"},
    )

    with patch("api.chat_routes.pipeline.get_router", return_value=fake_router), \
         patch("api.chat_routes.pipeline.web_search.search", AsyncMock(side_effect=ConnectionError("DNS failure"))), \
         patch("api.chat_routes._retrieve_with_fallback", AsyncMock(return_value=([fake_rag_result], "dense_sparse"))):

        (
            classification,
            routes,
            rag_results,
            web_results,
            internet_results,
            pricing_data,
            api_data,
            calc_results,
            citation_mgr,
            timings,
            fallback_pass,
        ) = await _gather_pipeline_context("What is S3?", None, "Free")

        assert len(rag_results) == 1
        assert rag_results[0]["service"] == "s3"
        assert internet_results == []
        assert fallback_pass == "dense_sparse"
        sources = citation_mgr.get_sources()
        assert len(sources) == 1
        assert sources[0].source_type == "rag"


@pytest.mark.asyncio
async def test_gather_context_all_subtasks_fail_gracefully():
    """When both RAG and Internet search fail, an empty context is returned without crashing."""
    fake_classification = MagicMock(
        routes=["RAG", "INTERNET"],
        providers=[],
        needs_internet=True,
        intent="general",
        confidence=0.9,
    )
    fake_router = MagicMock(route_query=AsyncMock(return_value=fake_classification))

    with patch("api.chat_routes.pipeline.get_router", return_value=fake_router), \
         patch("api.chat_routes.pipeline.web_search.search", AsyncMock(side_effect=Exception("Search crash"))), \
         patch("api.chat_routes._retrieve_with_fallback", AsyncMock(side_effect=Exception("RAG crash"))):

        (
            classification,
            routes,
            rag_results,
            web_results,
            internet_results,
            pricing_data,
            api_data,
            calc_results,
            citation_mgr,
            timings,
            fallback_pass,
        ) = await _gather_pipeline_context("Query", None, "Free")

        assert rag_results == []
        assert internet_results == []
        assert fallback_pass == "error_fallback"
        assert citation_mgr.get_sources() == []


@pytest.mark.asyncio
async def test_gather_context_cancelled_error_propagates():
    """When a task is cancelled, asyncio.CancelledError must be re-raised."""
    fake_classification = MagicMock(
        routes=["RAG"],
        providers=[],
        needs_internet=False,
        intent="general",
        confidence=0.9,
    )
    fake_router = MagicMock(route_query=AsyncMock(return_value=fake_classification))

    with patch("api.chat_routes.pipeline.get_router", return_value=fake_router), \
         patch("api.chat_routes._retrieve_with_fallback", AsyncMock(side_effect=asyncio.CancelledError)):

        with pytest.raises(asyncio.CancelledError):
            await _gather_pipeline_context("Query", None, "Free")


@pytest.mark.asyncio
async def test_execute_agent_pipeline_cancelled_error_reraises():
    """execute_agent_pipeline must re-raise CancelledError on disconnect."""
    with patch("api.chat_routes._gather_pipeline_context", AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await execute_agent_pipeline("Query", tier="Free")


def test_api_health_includes_arq_failed_jobs():
    """Verify /api/health endpoint includes arq_failed_jobs key."""
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "arq_failed_jobs" in data
    assert isinstance(data["arq_failed_jobs"], int)
