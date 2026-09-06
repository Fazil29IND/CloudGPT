"""Live smoke verification — the only path from 'Implemented' to 'Production Verified'.

Skipped unless LIVE_API_TESTS=1 and real credentials are present. Runs a
minimal roundtrip against each *live* dependency:

  1. Gemini chat generation (one tiny call through GeminiProvider)
  2. Pinecone index reachability + non-empty stats
  3. Azure Retail Prices API (keyless public endpoint)

Never run in the default hermetic suite; wire into a nightly CI job with
secrets injected.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("LIVE_API_TESTS") != "1",
    reason="Live verification suite — set LIVE_API_TESTS=1 with real credentials",
)


@pytest.mark.asyncio
async def test_live_gemini_chat_generation():
    """A real Gemini call must be accepted by the API (proves model IDs are live)."""
    from config import get_settings

    settings = get_settings()
    if not settings.has_gemini:
        pytest.skip("GEMINI_API_KEY not configured")

    from llm.provider import get_llm_provider

    provider = get_llm_provider("main", "Free")
    answer = await provider.generate(
        messages=[{"role": "user", "content": "Reply with the single word: pong"}],
        stream=False,
        thinking_level="Low",
        max_output_tokens=64,
    )
    assert isinstance(answer, str) and len(answer) > 0
    assert provider.last_usage.get("prompt_tokens") is not None


@pytest.mark.asyncio
async def test_live_pinecone_roundtrip():
    """The Pinecone index must be reachable (real describe_index_stats ping)."""
    from config import get_settings

    settings = get_settings()
    if not settings.pinecone_api_key:
        pytest.skip("PINECONE_API_KEY not configured")

    from embeddings.pinecone_manager import PineconeManager

    pm = PineconeManager(settings)
    assert await pm.health_check(), "Pinecone health check failed — index unreachable"


@pytest.mark.asyncio
async def test_live_azure_retail_pricing():
    """The keyless public Azure Retail Prices API must return real price data."""
    from tools.pricing.azure_pricing import AzurePricingTool

    tool = AzurePricingTool()
    result = await tool.get_vm_price(sku="Standard_D2s_v5")
    assert result.success, f"Azure pricing failed: {result.error_message}"
    assert result.unit_price > 0
    assert result.estimated is False
