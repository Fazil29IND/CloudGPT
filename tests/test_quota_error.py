"""Tests for upstream Gemini quota (429 / RESOURCE_EXHAUSTED) handling.

Covers the Part 2 implementation plan:
- quota error classification checked BEFORE generic model errors
- fail-fast: a 429 does NOT walk the fallback chain (one API key powers it)
- cooldown is tripped so subsequent requests skip the doomed retry loop
- generate_with_fallback propagates GeminiQuotaExceeded instead of wrapping it
- generic errors keep the existing RuntimeError wrapper
- provider-reported usage capture for true-input billing
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import llm.provider as provider_module
from api.chat_routes import _quota_error_message, generate_with_fallback
from config import get_settings
from llm.provider import GeminiProvider, GeminiQuotaExceeded, _model_in_cooldown


@pytest.fixture(autouse=True)
def _clear_model_cooldowns():
    """Keep the module-global cooldown map isolated between tests."""
    provider_module._model_cooldowns.clear()
    yield
    provider_module._model_cooldowns.clear()


def _make_provider() -> GeminiProvider:
    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "mock"
    mock_settings.gemini_model = "gemini-3.8-flash"
    mock_settings.gemini_request_timeout_seconds = 5.0
    mock_settings.llm_stream_timeout_seconds = 10.0
    mock_settings.gemini_model_lite = "gemini-3.8-flash"
    mock_settings.gemini_model_core = "gemini-3.8-flash"
    mock_settings.gemini_model_apex = "gemini-3.8-flash"
    mock_settings.gemini_model_fallback_1 = "gemini-3.7-flash"
    mock_settings.gemini_model_fallback_2 = "gemini-3.6-flash"
    mock_settings.gemini_model_fallback_3 = "gemini-3.5-flash"
    with patch("llm.provider.get_settings", return_value=mock_settings):
        return GeminiProvider()


# ─── Classification ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "429 RESOURCE_EXHAUSTED. You exceeded your current quota.",
        "Resource has been exhausted (e.g. check quota).",
        "You exceeded your current quota, please check your plan and billing details.",
        "Rate limit exceeded for the requested model.",
        "HTTP 429 Too Many Requests",
    ],
)
def test_is_quota_error_matches_quota_markers(message):
    assert GeminiProvider._is_quota_error(Exception(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "503 Service Unavailable: model overloaded",
        "404 model gemini-x not found",
        "internal error 500",
    ],
)
def test_is_quota_error_ignores_non_quota_errors(message):
    assert GeminiProvider._is_quota_error(Exception(message)) is False


def test_quota_error_checked_before_model_error():
    # A 429 payload also matches generic model-error markers ("quota", "429");
    # the quota classifier must win so the chain is not walked.
    exc = Exception("429 quota exceeded")
    assert GeminiProvider._is_quota_error(exc) is True
    assert GeminiProvider._is_model_error(exc) is True


# ─── Fail-fast behavior ──────────────────────────────────────────────────────


async def test_quota_error_fails_fast_without_chain_walk():
    provider = _make_provider()
    calls = 0

    async def _raise_quota(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise Exception("429 RESOURCE_EXHAUSTED. You exceeded your current quota.")

    provider.client = MagicMock()
    provider.client.aio.models.generate_content = _raise_quota

    with pytest.raises(GeminiQuotaExceeded):
        await provider.generate(messages=[{"role": "user", "content": "hi"}])

    assert calls == 1  # exactly one provider attempt — no fallback walk


async def test_quota_error_trips_model_cooldown():
    provider = _make_provider()
    assert _model_in_cooldown("gemini-3.8-flash") is False

    async def _raise_quota(*args, **kwargs):
        raise Exception("429 RESOURCE_EXHAUSTED. You exceeded your current quota.")

    provider.client = MagicMock()
    provider.client.aio.models.generate_content = _raise_quota

    with pytest.raises(GeminiQuotaExceeded):
        await provider.generate(messages=[{"role": "user", "content": "hi"}])

    assert _model_in_cooldown("gemini-3.8-flash") is True


async def test_stream_quota_error_fails_fast():
    provider = _make_provider()
    calls = 0

    async def _raise_stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise Exception("Resource has been exhausted (e.g. check quota).")

    provider.client = MagicMock()
    provider.client.aio.models.generate_content_stream = _raise_stream

    with pytest.raises(GeminiQuotaExceeded):
        stream = await provider.generate(
            messages=[{"role": "user", "content": "hi"}], stream=True
        )
        async for _ in stream:
            pass

    assert calls == 1


def test_quota_exception_carries_model_name():
    exc = GeminiQuotaExceeded("gemini-3.8-flash")
    assert exc.model_name == "gemini-3.8-flash"
    assert "Gemini Free Tier Quota has Reached its Limit" in str(exc)


async def test_non_quota_model_error_still_walks_chain():
    provider = _make_provider()
    attempted: list[str] = []

    async def _raise_not_found(*args, **kwargs):
        attempted.append(kwargs.get("model"))
        raise Exception("404 model not found")

    provider.client = MagicMock()
    provider.client.aio.models.generate_content = _raise_not_found

    with pytest.raises(Exception, match="404"):
        await provider.generate(messages=[{"role": "user", "content": "hi"}])

    # All four chain models were attempted for a non-quota error.
    assert attempted == [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    ]


# ─── generate_with_fallback propagation ─────────────────────────────────────


def _mock_pipeline(llm):
    mp = MagicMock()
    mp.settings = get_settings()
    mp.get_main_llm.return_value = llm
    return mp


async def test_generate_with_fallback_propagates_quota_error():
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(side_effect=GeminiQuotaExceeded("gemini-3.8-flash"))
    mock_llm.last_usage = {"prompt_tokens": None, "total_tokens": None}

    with patch("api.chat_routes.pipeline", _mock_pipeline(mock_llm)):
        with pytest.raises(GeminiQuotaExceeded):
            await generate_with_fallback(
                messages=[{"role": "user", "content": "x"}], tier="Free"
            )


async def test_generate_with_fallback_wraps_generic_errors():
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(side_effect=Exception("500 internal error"))
    mock_llm.last_usage = {"prompt_tokens": None, "total_tokens": None}

    with patch("api.chat_routes.pipeline", _mock_pipeline(mock_llm)):
        with pytest.raises(RuntimeError, match="Gemini generation failed"):
            await generate_with_fallback(
                messages=[{"role": "user", "content": "x"}], tier="Free"
            )


async def test_generate_with_fallback_exposes_provider_usage():
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value="answer")
    mock_llm.last_usage = {"prompt_tokens": 1234, "total_tokens": 2000}
    usage_out: dict = {}

    with patch("api.chat_routes.pipeline", _mock_pipeline(mock_llm)):
        _, provider_name = await generate_with_fallback(
            messages=[{"role": "user", "content": "x"}],
            tier="Free",
            usage_out=usage_out,
        )

    assert provider_name == "gemini"
    assert usage_out == {"prompt_tokens": 1234, "total_tokens": 2000}


# ─── Provider usage capture ──────────────────────────────────────────────────


async def test_generate_records_usage_metadata():
    provider = _make_provider()
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="hello", thought=False)]))
        ],
        text="hello",
        usage_metadata=SimpleNamespace(prompt_token_count=42, total_token_count=100),
    )

    async def _ok(*args, **kwargs):
        return response

    provider.client = MagicMock()
    provider.client.aio.models.generate_content = _ok

    answer = await provider.generate(messages=[{"role": "user", "content": "hi"}])

    assert answer == "hello"
    assert provider.last_usage == {"prompt_tokens": 42, "total_tokens": 100, "thoughts_tokens": None}


# ─── User-facing message ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "requested_model",
    ["Apex", "Core", "Lite"],
)
def test_quota_error_message_names_requested_model(requested_model):
    message = _quota_error_message(requested_model)
    assert "Gemini Free Tier Quota has Reached its Limit" in message
    assert f"for the {requested_model} model" in message
    assert "quota window resets" in message
