"""
Verification test suite for prioritized P0, P1, P2, and P3 defect fixes.
"""

import pytest
from starlette.testclient import TestClient

from config import get_settings
from tools.pricing.gcp_pricing import GCPPricingTool


def test_llm_tiering_preserved():
    """Verify explicit constraint: LLM Tiering remains gemini-3.8-flash across all tiers."""
    settings = get_settings()
    assert settings.gemini_model_lite == "gemini-3.8-flash"
    assert settings.gemini_model_core == "gemini-3.8-flash"
    assert settings.gemini_model_apex == "gemini-3.8-flash"


def test_csp_headers_contain_stripe_and_no_razorpay(app_instance):
    """Verify CSP header permits Stripe checkout scripts/frames and excludes Razorpay."""
    client = TestClient(app_instance, raise_server_exceptions=False)
    resp = client.get("/login")
    csp = resp.headers.get("content-security-policy", "")
    assert "https://js.stripe.com" in csp
    assert "https://api.stripe.com" in csp
    assert "razorpay" not in csp.lower()


@pytest.mark.asyncio
async def test_gcp_pricing_tool_realistic_catalog():
    """Verify GCP Pricing Tool serves the static baseline catalog with honest
    estimated provenance, and never invents prices for unknown SKUs."""
    tool = GCPPricingTool()

    # Compute — catalog fallback is explicitly flagged estimated.
    micro_res = await tool.get_compute_price("e2-micro")
    assert micro_res.success is True
    assert micro_res.unit_price == 0.0084
    assert micro_res.unit == "Hour"
    assert micro_res.estimated is True
    assert micro_res.source == "static_catalog"

    std4_res = await tool.get_compute_price("e2-standard-4")
    assert std4_res.success is True
    assert std4_res.unit_price == 0.1344
    assert std4_res.estimated is True

    # Storage
    storage_res = await tool.get_cloud_storage_price("standard")
    assert storage_res.success is True
    assert storage_res.unit_price == 0.020
    assert storage_res.unit == "GiBy.mo"
    assert storage_res.estimated is True

    # SQL — catalog-backed, always labelled estimated (no per-tier public SKU).
    sql_res = await tool.get_cloud_sql_price("db-custom-2-7680")
    assert sql_res.success is True
    assert sql_res.unit_price == 0.1030
    assert sql_res.estimated is True

    # Unknown SKUs must fail honestly instead of returning an invented default.
    unknown_vm = await tool.get_compute_price("x9-totally-unknown-99")
    assert unknown_vm.success is False
    assert unknown_vm.error_message is not None

    unknown_sql = await tool.get_cloud_sql_price("db-does-not-exist")
    assert unknown_sql.success is False

    # Bounded prefix matching still resolves extended machine types
    # (custom shapes derived from a catalog base type).
    extended = await tool.get_compute_price("e2-standard-2-custom-4-8192")
    assert extended.success is True
    assert extended.estimated is True


def test_serve_cached_answer_dict_slicing_safety():
    """Verify _serve_cached_answer handles nested dicts without slice crash."""
    from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline

    pipeline = AdaptiveAdvancedRAGPipeline()
    cached_dict = {
        "answer": {"answer": "This is a nested answer string for testing"},
        "sources": [{"title": "AWS S3"}],
        "model": "gemini-3.8-flash",
    }
    result = pipeline._serve_cached_answer(
        cached=cached_dict,
        query="what is s3?",
        tier="Lite",
        stream=False,
        emit_event=None,
        pipeline_type="semantic_cache",
        timings={},
    )
    assert isinstance(result.answer, str)
    assert "nested answer string" in result.answer
    assert result.pipeline_type == "semantic_cache"


def test_file_processor_bounded_cache():
    """Verify BoundedTTLCache maxsize is capped to protect RAM."""
    from file_processor import _MEMORY_STAGED

    assert _MEMORY_STAGED._maxsize == 50


@pytest.mark.asyncio
async def test_get_current_user_profile_cache():
    """Verify get_current_user uses in-memory session cache."""
    from app import get_current_user, _USER_CACHE, invalidate_user_cache
    from unittest.mock import MagicMock, patch

    mock_request = MagicMock()
    mock_request.session = {"user_id": 99999}

    invalidate_user_cache(99999)
    assert 99999 not in _USER_CACHE

    with patch("db.get_user_by_id", return_value={"id": 99999, "email": "test@cloudgpt.local"}) as mock_db:
        user1 = await get_current_user(mock_request)
        assert user1["email"] == "test@cloudgpt.local"
        assert mock_db.call_count == 1

        # Second call must hit cache and NOT call db
        user2 = await get_current_user(mock_request)
        assert user2["email"] == "test@cloudgpt.local"
        assert mock_db.call_count == 1  # Still 1!

    invalidate_user_cache(99999)
    assert 99999 not in _USER_CACHE
