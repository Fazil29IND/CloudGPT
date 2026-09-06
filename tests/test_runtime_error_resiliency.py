"""Unit tests for upstream connection error rectification and runtime resiliency.

Validates:
1. Fallback candidates (e.g. gemini-3.5-flash-lite) do NOT receive thinking_config (prevents HTTP 400).
2. Mid-stream connection/503 errors conclude gracefully with a notice when tokens were already yielded.
3. Embedding daily quota exhaustion trips circuit breaker and returns empty vectors instantly (prevents 15s retry loop).
4. Hybrid search RRF scores (<= 0.05) are properly normalized so valid matches do not trigger false replan loops.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from config import get_settings
from embeddings.embedding_engine import (
    EmbeddingEngine,
    is_embed_in_cooldown,
)
from llm.provider import GeminiProvider, _is_thinking_gemini
from retrieval import RetrievalResult
from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline


def test_thinking_config_omitted_for_non_thinking_fallback():
    """Verify gemini-3.5-flash-lite does not receive thinking_config even when is_fallback_candidate=True."""
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.settings = get_settings()

    config = provider._build_config(
        model="gemini-3.5-flash-lite",
        temperature=0.2,
        system_instruction="System prompt",
        thinking_level="High",
        max_output_tokens=400,
        is_fallback_candidate=True,
    )

    # thinking_config must NOT be set on non-thinking fallback candidates
    assert getattr(config, "thinking_config", None) is None
    assert _is_thinking_gemini("gemini-3.5-flash-lite") is False
    assert _is_thinking_gemini("gemini-3.7-flash") is True


@pytest.mark.asyncio
async def test_mid_stream_connection_error_graceful_truncation():
    """Verify that when answer tokens have already been emitted, an upstream 503/connection drop
    concludes cleanly with a notice rather than raising an unhandled exception.
    """
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.settings = get_settings()
    provider.model = "gemini-3.7-flash"
    provider.model_chain = ("gemini-3.7-flash", "gemini-3.5-flash-lite")
    provider.client = MagicMock()

    # Mock primary stream that yields one chunk, then crashes with 503 UNAVAILABLE
    async def mock_crashing_stream():
        chunk1 = MagicMock()
        cand1 = MagicMock()
        cand1.content.parts = [MagicMock(text="Here is the cloud analysis:", thought=False)]
        chunk1.candidates = [cand1]
        yield chunk1
        raise ConnectionError("503 UNAVAILABLE: The model is overloaded. Please try again later.")

    mock_stream_obj = MagicMock()
    mock_stream_obj.__aiter__ = lambda self: mock_crashing_stream()
    provider.client.aio.models.generate_content_stream = AsyncMock(return_value=mock_stream_obj)

    # Provider generate call with stream=True
    stream = await provider.generate(
        messages=[{"role": "user", "content": "How to scale EKS?"}],
        stream=True,
    )

    tokens = []
    async for tok in stream:
        tokens.append(tok)

    full_output = "".join(tokens)
    # Tokens yielded before crash must be preserved
    assert "Here is the cloud analysis:" in full_output
    # Must conclude with clean truncation note instead of unhandled crash
    assert "*(Response truncated due to a transient upstream connection issue)*" in full_output


@pytest.mark.asyncio
async def test_embedding_circuit_breaker_trips_on_429_quota():
    """Verify that daily quota 429 trips the circuit breaker and returns empty vectors immediately."""
    engine = EmbeddingEngine(provider="gemini", model_name="gemini-embedding-2", dimension=384)
    engine._is_gemini = True
    engine.model = MagicMock()

    # Simulate Google 429 quota exhaustion
    quota_error = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded for EmbedContentRequestsPerDay limit: 1000")
    engine.model.aio.models.embed_content = AsyncMock(side_effect=quota_error)

    # Trip breaker test
    result = await engine._call_gemini_embed("sample text", task_type="RETRIEVAL_QUERY")
    assert result == []
    assert is_embed_in_cooldown() is True

    # Subsequent call while circuit breaker is open should return [] without calling API
    engine.model.aio.models.embed_content.reset_mock()
    fast_result = await engine._call_gemini_embed("another text", task_type="RETRIEVAL_QUERY")
    assert fast_result == []
    engine.model.aio.models.embed_content.assert_not_called()


def test_adaptive_rag_rrf_scoring_no_false_replan():
    """Verify that valid hybrid search RRF candidates (~0.030) do not trigger false low-relevance replans."""
    pipeline = AdaptiveAdvancedRAGPipeline.__new__(AdaptiveAdvancedRAGPipeline)
    pipeline.settings = get_settings()

    # RRF rank-1 candidate score is ~0.030 (when both dense and sparse match at top)
    candidates = [
        RetrievalResult(chunk_id="c1", text="AWS EKS cluster autoscaler scaling policies and managed node groups", score=0.0304),
        RetrievalResult(chunk_id="c2", text="EKS Karpenter horizontal pod autoscaling documentation", score=0.0160),
    ]

    needs_replan, score, diagnosis = pipeline._evaluate_retrieval_feedback(
        candidates,
        query="how to scale EKS cluster nodes",
        transformed={"rewritten_query": "how to scale EKS cluster nodes"},
    )

    # Must NOT trigger low_relevance_score replan
    assert needs_replan is False
    assert score == 0.0304
    assert diagnosis == "sufficient_retrieval"
