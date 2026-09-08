"""Task 9: Test cache streaming correctness — verify double-unwrap fix for exact and semantic cache."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.chat_routes import execute_agent_pipeline


@pytest.mark.asyncio
async def test_exact_cache_hit_returns_clean_string():
    """Verify that exact cache hit unwrap returns the plain string answer without double-unwrap failure."""
    cached_payload = {
        "answer": "Complete standalone Terraform code for AWS VPC.",
        "sources": [{"title": "AWS VPC Docs", "url": "https://docs.aws.amazon.com"}],
        "model": "exact-cache",
    }

    with patch("api.chat_routes.get_cached_answer", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = cached_payload

        res = await execute_agent_pipeline(
            query="how to configure aws vpc",
            tier="Free",
            stream=False,
        )

        assert res.answer == "Complete standalone Terraform code for AWS VPC."
        assert res.pipeline_type == "exact_cache"
        assert len(res.sources) == 1


@pytest.mark.asyncio
async def test_semantic_cache_hit_returns_clean_string():
    """Verify that semantic cache hit unwrap returns the plain string answer without double-unwrap failure."""
    cached_payload = {
        "answer": "GCP Cloud Run deployment configuration.",
        "sources": [{"title": "Cloud Run Docs", "url": "https://cloud.google.com"}],
        "model": "semantic-cache",
    }

    mock_sem_cache = MagicMock()
    mock_sem_cache.get.return_value = ("canonical-query-key", 0.98)

    with patch("api.chat_routes.get_cached_answer", new_callable=AsyncMock) as mock_get_cache, \
         patch("api.chat_routes.get_semantic_cache", return_value=mock_sem_cache):

        # First call (exact cache) returns None, second call (semantic cache key) returns cached_payload
        mock_get_cache.side_effect = [None, cached_payload]

        res = await execute_agent_pipeline(
            query="how to deploy to gcp cloud run",
            tier="Free",
            stream=False,
        )

        assert res.answer == "GCP Cloud Run deployment configuration."
        assert res.model_used == "semantic-cache"
