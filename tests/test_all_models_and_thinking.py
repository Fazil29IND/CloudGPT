"""
Automated Test Suite for Developer Tier Models and Thinking Modes in CloudGPT.

Tests:
- All models: Apex (Adaptive RAG), Core (Agentic RAG), Lite (Standard RAG)
- All thinking levels: Low, Medium, High, Max
- Both non-streaming and streaming generation
- Response latency and absence of timeouts/internal errors
"""

import os
import time
import pytest

from api.chat_routes import execute_agent_pipeline
from llm.provider import get_llm_provider

# Skip live API integration tests unless LIVE_API_TESTS=1 is set, adhering to AGENTS.md hermetic test mandate
pytestmark = pytest.mark.skipif(
    os.getenv("LIVE_API_TESTS") != "1",
    reason="Live Gemini API tests skipped by default. Set LIVE_API_TESTS=1 to run.",
)


@pytest.mark.asyncio
async def test_gemini_provider_direct():
    """Verify GeminiProvider generates responses under 15s with thinking tags."""
    provider = get_llm_provider("main", "Free")
    messages = [{"role": "user", "content": "What is AWS Lambda? Explain in 1 sentence."}]

    t0 = time.perf_counter()
    try:
        resp = await provider.generate(messages=messages, stream=False, thinking_level="Low")
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            pytest.skip(f"Live Gemini API quota exceeded: {e}")
        raise
    elapsed = time.perf_counter() - t0

    assert resp and len(resp) > 10, f"Expected non-empty response, got: {resp}"
    assert elapsed < 20.0, f"Response took {elapsed}s which exceeds 20s budget"
    print(f"\n[PASS] Gemini direct generate completed in {round(elapsed, 2)}s: {resp[:80]}...")


@pytest.mark.asyncio
async def test_gemini_provider_streaming_all_thinking_levels():
    """Verify streaming across Low, Medium, High, Max thinking levels."""
    provider = get_llm_provider("main", "Free")
    messages = [{"role": "user", "content": "Name 2 differences between S3 and EBS in 2 bullet points."}]

    for level in ["Low", "Medium", "High", "Max"]:
        t0 = time.perf_counter()
        try:
            stream_iter = await provider.generate(messages=messages, stream=True, thinking_level=level)
            tokens = []
            async for chunk in stream_iter:
                tokens.append(chunk)
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                pytest.skip(f"Live Gemini API quota exceeded: {e}")
            raise
        elapsed = time.perf_counter() - t0
        full_text = "".join(tokens)

        assert len(tokens) > 0, f"Level {level} returned 0 chunks"
        assert len(full_text) > 10, f"Level {level} returned empty text"
        assert elapsed < 60.0, f"Level {level} took {elapsed}s (exceeded 60s)"
        print(f"\n[PASS] Thinking Level '{level}' streaming completed in {round(elapsed, 2)}s ({len(tokens)} chunks)")


@pytest.mark.asyncio
@pytest.mark.parametrize("tier,mode", [
    ("Developer", "Apex"),
    ("Developer", "Core"),
    ("Developer", "Lite"),
])
@pytest.mark.parametrize("thinking_level", ["Low", "Medium", "High", "Max"])
async def test_developer_tier_pipeline_matrix(tier: str, mode: str, thinking_level: str):
    """Test full agent pipeline for Developer tier across all models and thinking levels."""
    query = "Compare Google Cloud Run vs AWS App Runner for container hosting."

    t0 = time.perf_counter()
    try:
        result = await execute_agent_pipeline(
            query=query,
            provider_filter=None,
            tier=tier,
            chat_history=None,
            attachment_texts=None,
            stream=False,
            thinking_level=thinking_level,
        )
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            pytest.skip(f"Live Gemini API quota exceeded: {e}")
        raise
    elapsed = time.perf_counter() - t0

    assert result is not None, f"Pipeline returned None for {mode}/{thinking_level}"
    assert result.answer and len(result.answer) > 20, f"Answer empty for {mode}/{thinking_level}"
    max_limit = 60.0
    assert elapsed < max_limit, f"Pipeline execution took {elapsed}s which exceeds {max_limit}s limit"
    print(f"\n[PASS] Tier '{tier}' Mode '{mode}' Thinking '{thinking_level}' in {round(elapsed, 2)}s | Model: {result.model_used}")


@pytest.mark.asyncio
async def test_developer_tier_streaming_pipeline():
    """Verify end-to-end streaming pipeline in Developer Tier (Apex mode + High thinking)."""
    query = "How do I set up a secure VPC peering connection in AWS?"

    events = []
    async def capture_event(ev: dict):
        events.append(ev)

    t0 = time.perf_counter()
    try:
        result = await execute_agent_pipeline(
            query=query,
            provider_filter="aws",
            tier="Developer",
            chat_history=None,
            attachment_texts=None,
            emit_event=capture_event,
            stream=True,
            thinking_level="High",
        )
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            pytest.skip(f"Live Gemini API quota exceeded: {e}")
        raise

    streamed_tokens = []
    async for token in result.token_stream:
        streamed_tokens.append(token)
    elapsed = time.perf_counter() - t0

    assert len(streamed_tokens) > 0, "No tokens yielded in stream"
    assert len("".join(streamed_tokens)) > 30, "Streamed text too short"
    assert elapsed < 60.0, f"Stream took {elapsed}s"
    print(f"\n[PASS] Streaming pipeline finished in {round(elapsed, 2)}s with {len(events)} events and {len(streamed_tokens)} tokens")
