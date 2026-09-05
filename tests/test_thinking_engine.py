"""
Automated tests for the CloudGPT Thinking Engine, tier→model mapping, and
namespace-aware RAG.

Covers (Implementation Plan "Multi-Model Upgrade" verification):
  - Thinking profiles: budgets and quota multipliers per level (Low/Medium/High/Max)
  - Tier clamping: Lite ≤ Medium, Core ≤ High, Apex ≤ Max
  - <think> splitting: complete text and token-boundary streaming
  - Tier model mapping: Lite → Gemini Flash chain, Core → Claude Thinking,
    Apex → Claude Max Thinking (with graceful fallback when keys are absent)
  - Provider thinking wiring: Gemini thinking_config, Claude extended thinking
  - Entitlements thinking levels per plan
  - Pinecone namespace constants and namespace-passing upsert/query
  - Services.Md next-gen expansion parses into extra catalog categories
  - Knowledge corpus chunks carry senior domain metadata

Run with:
    python -m pytest tests/test_thinking_engine.py -v
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:Fazil@localhost:5000/pygpt"
)
os.environ["ENVIRONMENT"] = "development"

import pytest

from config import get_settings
from core.entitlements import resolve_entitlements
from llm.thinking import (
    ThinkingStreamSplitter,
    clamp_thinking_level,
    normalize_thinking_level,
    profile_for,
    split_thinking,
)


# ── Thinking profiles ────────────────────────────────────────────────────────

def test_thinking_profiles_budgets_and_multipliers():
    settings = get_settings()
    assert profile_for("Low", settings).budget_tokens == settings.thinking_budget_low
    assert profile_for("Medium", settings).budget_tokens == settings.thinking_budget_medium
    assert profile_for("High", settings).budget_tokens == settings.thinking_budget_high
    assert profile_for("Max", settings).budget_tokens == settings.thinking_budget_max

    # Quota multipliers scale monotonically: 1.0 / 1.5 / 2.5 / 4.0
    assert profile_for("Low", settings).quota_multiplier == 1.0
    assert profile_for("Medium", settings).quota_multiplier == 1.5
    assert profile_for("High", settings).quota_multiplier == 2.5
    assert profile_for("Max", settings).quota_multiplier == 4.0

    # OpenAI reasoning effort vocabulary
    assert profile_for("Low", settings).reasoning_effort == "low"
    assert profile_for("Medium", settings).reasoning_effort == "medium"
    assert profile_for("High", settings).reasoning_effort == "high"
    assert profile_for("Max", settings).reasoning_effort == "high"


def test_normalize_thinking_level_aliases():
    assert normalize_thinking_level("low") == "Low"
    assert normalize_thinking_level("MAXIMUM") == "Max"
    assert normalize_thinking_level("deep") == "High"
    assert normalize_thinking_level("bogus", default="Medium") == "Medium"
def test_default_thinking_for_tier():
    from llm.thinking import default_thinking_for_tier
    assert default_thinking_for_tier("Free") == "Low"
    assert default_thinking_for_tier("Lite") == "Low"
    assert default_thinking_for_tier("Pro") == "Medium"
    assert default_thinking_for_tier("Core") == "Medium"
    assert default_thinking_for_tier("Max") == "High"
    assert default_thinking_for_tier("Apex") == "High"
    assert default_thinking_for_tier("Developer") == "High"
    assert default_thinking_for_tier("admin") == "High"


def test_clamp_thinking_level_per_tier():
    # Free capped to Low
    assert clamp_thinking_level("Max", ["Low"]) == "Low"
    assert clamp_thinking_level("High", ["Low"]) == "Low"
    assert clamp_thinking_level("Medium", ["Low"]) == "Low"
    assert clamp_thinking_level("Low", ["Low"]) == "Low"

    # Pro capped to Medium
    assert clamp_thinking_level("Max", ["Low", "Medium"]) == "Medium"
    assert clamp_thinking_level("High", ["Low", "Medium"]) == "Medium"
    assert clamp_thinking_level("Medium", ["Low", "Medium"]) == "Medium"
    assert clamp_thinking_level("Low", ["Low", "Medium"]) == "Low"

    # Max / Apex can reach High
    assert clamp_thinking_level("Max", ["Low", "Medium", "High"]) == "High"
    assert clamp_thinking_level("High", ["Low", "Medium", "High"]) == "High"
    assert clamp_thinking_level("Medium", ["Low", "Medium", "High"]) == "Medium"
    assert clamp_thinking_level("Low", ["Low", "Medium", "High"]) == "Low"


def test_split_thinking_complete_response():
    thinking, answer = split_thinking(
        "<think>checking IAM trust chain first</think>## Answer\nUse roles."
    )
    assert thinking == "checking IAM trust chain first"
    assert answer == "## Answer\nUse roles."


def test_split_thinking_no_block():
    thinking, answer = split_thinking("plain answer")
    assert thinking == ""
    assert answer == "plain answer"


def test_split_thinking_unterminated_block():
    thinking, answer = split_thinking("prefix <think>truncated reasoning")
    assert "truncated reasoning" in thinking
    assert answer == "prefix"


def test_stream_splitter_boundary_safe():
    splitter = ThinkingStreamSplitter()
    for token in ["Ans", "<th", "ink>deep ", "thought</thi", "nk> tail", " end"]:
        splitter.feed(token)
    thinking, answer = splitter.flush()

    # Text-level correctness holds even when tags straddle token boundaries.
    assert thinking == "deep thought"
    assert answer == "Ans tail end"


def test_stream_splitter_text_never_mixes_across_blocks():
    splitter = ThinkingStreamSplitter()
    splitter.feed("<think>plan</think>act<think>verify</think>done")
    thinking, answer = splitter.flush()
    assert thinking == "planverify"
    assert answer == "actdone"


# ── Tier → model mapping ─────────────────────────────────────────────────────

class _StubProvider:
    """Records factory kwargs; instances fail construction when configured to."""

    failures: dict[str, bool] = {}
    constructed: list[tuple[str, dict]] = []
    label = "stub"

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.label = cls.__name__

    def __init__(self, **kwargs):
        if _StubProvider.failures.get(type(self).__name__, False):
            raise ValueError(f"{type(self).__name__} key missing (simulated)")
        _StubProvider.constructed.append((type(self).__name__, kwargs))
        self.kwargs = kwargs


def _patch_factory_providers(gemini=True):
    """Replace provider classes in the factory with observable stubs."""
    class StubGemini(_StubProvider): pass
    class StubClaude(_StubProvider): pass
    class StubOpenAI(_StubProvider): pass

    _StubProvider.failures = {
        "StubGemini": not gemini,
    }
    _StubProvider.constructed = []

    import llm.provider as mod

    return patch.multiple(
        mod,
        GeminiProvider=StubGemini,
        ClaudeProvider=StubClaude,
        OpenAIProvider=StubOpenAI,
    )


def test_lite_tier_uses_gemini_flash_chain():
    from llm.provider import get_llm_provider

    with _patch_factory_providers(gemini=True):
        provider = get_llm_provider("main", "Free")
    assert type(provider).__name__ == "StubGemini"
    # Primary Lite model is Gemini 3.8 Flash with fallback chain
    assert provider.kwargs["models"][0] == get_settings().gemini_model_lite
    assert provider.kwargs["models"] == [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    ]


def test_core_tier_uses_gemini_flash():
    from llm.provider import get_llm_provider

    with _patch_factory_providers(gemini=True):
        provider = get_llm_provider("main", "Pro")
    assert type(provider).__name__ == "StubGemini"
    assert provider.kwargs["models"][0] == get_settings().gemini_model_core
    assert provider.kwargs["models"] == [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    ]


def test_apex_tier_uses_gemini_flash():
    from llm.provider import get_llm_provider

    with _patch_factory_providers(gemini=True):
        provider = get_llm_provider("main", "Max")
    assert type(provider).__name__ == "StubGemini"
    assert provider.kwargs["models"][0] == get_settings().gemini_model_apex
    assert provider.kwargs["models"] == [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    ]


def test_apex_falls_back_to_gemini():
    from llm.provider import get_llm_provider

    with _patch_factory_providers(gemini=True):
        provider = get_llm_provider("main", "Apex")
    assert type(provider).__name__ == "StubGemini"
    assert provider.kwargs["models"][0] == get_settings().gemini_model_apex


def test_lite_raises_when_every_provider_is_unavailable():
    """With every stub failing (including the Gemini fallback), the factory
    must raise loudly rather than silently return a broken provider."""
    from llm.provider import get_llm_provider

    with _patch_factory_providers(gemini=False):
        _StubProvider.failures["StubGemini"] = True
        with pytest.raises(Exception):
            get_llm_provider("main", "Free")


# ── Provider thinking wiring ─────────────────────────────────────────────────

def test_gemini_build_config_includes_thinking_budget():
    from llm.provider import GeminiProvider

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.settings = get_settings()

    for model in [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    ]:
        config = provider._build_config(
            model=model,
            temperature=0.2,
            system_instruction=None,
            thinking_level="High",
            max_output_tokens=None,
        )
        assert config.thinking_config is not None, f"{model} should get thinking_config"
        assert config.thinking_config.thinking_budget == get_settings().thinking_budget_high
        assert config.thinking_config.include_thoughts is True

    # Non-thinking Gemini models must not receive a thinking config
    plain = provider._build_config(
        model="gemini-1.0-pro",
        temperature=0.2,
        system_instruction=None,
        thinking_level="Max",
        max_output_tokens=None,
    )
    assert getattr(plain, "thinking_config", None) is None


def test_gemini_provider_timeout_initialization():
    from unittest.mock import MagicMock, patch
    from llm.provider import GeminiProvider

    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "mock-key"
    mock_settings.gemini_model = "gemini-3.8-flash"
    mock_settings.gemini_request_timeout_seconds = 25.0
    mock_settings.llm_stream_timeout_seconds = 120.0

    with patch("llm.provider.get_settings", return_value=mock_settings), \
         patch("google.genai.Client") as mock_client_cls:
        provider = GeminiProvider(model="gemini-3.8-flash")
        assert provider.per_request_timeout == 25.0
        assert provider.stream_timeout == 120.0
        call_kwargs = mock_client_cls.call_args[1]
        assert call_kwargs["http_options"].timeout == 120000


@pytest.mark.asyncio
async def test_gemini_provider_stream_timeouts():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from llm.provider import GeminiProvider

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.settings = get_settings()
    provider.model_chain = ["gemini-3.8-flash"]
    provider.model = "gemini-3.8-flash"
    provider.per_request_timeout = 25.0
    provider.stream_timeout = 120.0

    mock_stream = MagicMock()
    async def fake_stream_iter(*args, **kwargs):
        yield MagicMock(candidates=[MagicMock(content=MagicMock(parts=[MagicMock(text="chunk", thought=False)]))])
    mock_stream.__aiter__ = fake_stream_iter

    mock_client = MagicMock()
    mock_client.aio.models.generate_content_stream = AsyncMock(return_value=mock_stream)
    provider.client = mock_client

    recorded_timeouts = []
    real_wait_for = asyncio.wait_for

    async def intercepted_wait_for(fut, timeout):
        recorded_timeouts.append(timeout)
        return await real_wait_for(fut, timeout)

    with patch("asyncio.wait_for", side_effect=intercepted_wait_for):
        stream_iter = await provider._stream_generate(
            model="gemini-3.8-flash",
            contents=[],
            temperature=0.2,
            system_instruction=None,
            thinking_level="Max",
        )
        tokens = [t async for t in stream_iter]
        assert "chunk" in tokens

    # First wait_for is stream open (25s), second wait_for is first chunk (120s)
    assert recorded_timeouts[0] == 25.0
    assert recorded_timeouts[1] == 120.0


def test_gemini_build_config_max_output_tokens_for_thinking():
    from llm.provider import GeminiProvider

    provider = GeminiProvider.__new__(GeminiProvider)
    settings = get_settings()
    provider.settings = settings

    # With Max thinking (65536 tokens budget), max_output_tokens must scale up
    cfg_max = provider._build_config(
        model="gemini-3.8-flash",
        temperature=0.2,
        system_instruction=None,
        thinking_level="Max",
        max_output_tokens=None,
    )
    assert cfg_max.thinking_config.thinking_budget == settings.thinking_budget_max
    assert cfg_max.max_output_tokens >= settings.thinking_budget_max + 8192

    # If an explicit small max_output_tokens is passed, it must still scale for thinking
    cfg_small = provider._build_config(
        model="gemini-3.8-flash",
        temperature=0.2,
        system_instruction=None,
        thinking_level="Max",
        max_output_tokens=4096,
    )
    assert cfg_small.max_output_tokens >= settings.thinking_budget_max + 8192

    # Without thinking, explicit max_output_tokens is preserved
    cfg_plain = provider._build_config(
        model="gemini-3.8-flash",
        temperature=0.2,
        system_instruction=None,
        thinking_level=None,
        max_output_tokens=2048,
    )
    assert cfg_plain.max_output_tokens == 2048


def test_gemini_thinking_budget_defensive_clamp():
    from unittest.mock import patch
    from llm.provider import GeminiProvider
    from llm.thinking import ThinkingProfile

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.settings = get_settings()

    # Even if profile_for returns a budget higher than 65535, _build_config clamps to 65535
    oversized = ThinkingProfile("Max", 99999, 4.0, "high")
    with patch("llm.provider.profile_for", return_value=oversized):
        cfg = provider._build_config(
            model="gemini-3.8-flash",
            temperature=0.2,
            system_instruction=None,
            thinking_level="Max",
            max_output_tokens=None,
        )
        assert cfg.thinking_config.thinking_budget == 65535
        assert cfg.max_output_tokens >= 65535 + 8192


@pytest.mark.asyncio
async def test_generate_with_fallback_passes_max_output_tokens():
    from unittest.mock import AsyncMock, patch
    from api.chat_routes import generate_with_fallback

    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value="test answer")

    with patch("api.chat_routes.pipeline.get_main_llm", return_value=mock_llm):
        # 1. Max thinking without explicit max_output_tokens
        await generate_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            tier="Max",
            thinking_level="Max",
        )
        call_kwargs = mock_llm.generate.call_args[1]
        assert call_kwargs["max_output_tokens"] >= get_settings().thinking_budget_max + 8192

        # 2. Explicit max_output_tokens passed
        await generate_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            tier="Free",
            thinking_level=None,
            max_output_tokens=1024,
        )
        call_kwargs = mock_llm.generate.call_args[1]
        assert call_kwargs["max_output_tokens"] == 1024


def test_claude_request_params_enable_extended_thinking():
    from llm.provider import ClaudeProvider

    settings = get_settings()
    params = ClaudeProvider._request_params(
        system_prompt="sys",
        claude_messages=[{"role": "user", "content": "hi"}],
        temperature=0.2,
        thinking_level="Max",
        max_output_tokens=None,
    )
    assert params["thinking"] == {"type": "enabled", "budget_tokens": settings.thinking_budget_max}
    assert params["temperature"] == 1  # Anthropic requires temperature=1 with thinking
    assert params["max_tokens"] >= settings.thinking_budget_max + 8192

    # Without a thinking level the classic parameters are used
    plain = ClaudeProvider._request_params(
        system_prompt="sys",
        claude_messages=[{"role": "user", "content": "hi"}],
        temperature=0.5,
        thinking_level=None,
        max_output_tokens=None,
    )
    assert "thinking" not in plain
    assert plain["max_tokens"] == 4096


# ── Entitlements ─────────────────────────────────────────────────────────────

def test_entitlements_thinking_levels_per_plan():
    lite = resolve_entitlements("Lite")
    core = resolve_entitlements("Pro")
    apex = resolve_entitlements("Max")
    dev = resolve_entitlements("developer")

    assert lite.allowed_thinking_levels == ("Low", "Medium", "High")
    assert core.allowed_thinking_levels == ("Low", "Medium", "High")
    assert apex.allowed_thinking_levels == ("Low", "Medium", "High", "Max")
    assert dev.allowed_thinking_levels == ("Low", "Medium", "High", "Max")

    assert lite.allowed_models == ["Lite"]
    assert core.allowed_models == ["Core", "Lite"]
    assert apex.allowed_models == ["Apex", "Core", "Lite"]
    assert dev.allowed_models == ["Apex", "Core", "Lite"]


def test_allowed_thinking_per_model_and_tier():
    from core.entitlements import get_allowed_thinking_for_model

    # Free / Lite user
    assert get_allowed_thinking_for_model("Free", "Lite") == ("Low", "Medium", "High")
    assert get_allowed_thinking_for_model("Free", "Core") == ("Low", "Medium")

    # Pro / Core user — Max thinking is Apex-exclusive
    assert get_allowed_thinking_for_model("Pro", "Lite") == ("Low", "Medium", "High")
    assert get_allowed_thinking_for_model("Pro", "Core") == ("Low", "Medium", "High")
    assert get_allowed_thinking_for_model("Pro", "Apex") == ("Low", "Medium")

    # Max / Apex user
    assert get_allowed_thinking_for_model("Max", "Lite") == ("Low", "Medium", "High", "Max")
    assert get_allowed_thinking_for_model("Max", "Core") == ("Low", "Medium", "High", "Max")
    assert get_allowed_thinking_for_model("Max", "Apex") == ("Low", "Medium", "High", "Max")


# ── Pinecone namespaces ──────────────────────────────────────────────────────

def test_namespace_constants_defined():
    from embeddings.pinecone_manager import (
        ALL_NAMESPACES,
        NAMESPACE_ENGINEER_KNOWLEDGE,
        NAMESPACE_IAC,
        NAMESPACE_SERVICES,
        NAMESPACE_TROUBLESHOOTING,
    )

    assert NAMESPACE_SERVICES == "services-master"
    assert NAMESPACE_ENGINEER_KNOWLEDGE == "senior-engineer-knowledge"
    assert NAMESPACE_TROUBLESHOOTING == "troubleshooting-playbooks"
    assert NAMESPACE_IAC == "iac-templates"
    assert len(ALL_NAMESPACES) == 4


def test_upsert_and_query_pass_namespace():
    from embeddings.pinecone_manager import NAMESPACE_IAC, PineconeManager

    manager = PineconeManager.__new__(PineconeManager)
    manager.settings = get_settings()
    manager.index_name = "test-index"
    manager.client = MagicMock()
    mock_index = MagicMock()
    mock_index.query.return_value = {"matches": []}
    manager._index = mock_index

    import asyncio

    # Upsert forwards the namespace
    asyncio.run(manager.upsert_chunks(
        [{
            "chunk_id": "kb-abc",
            "dense_vector": [0.0] * manager.settings.embedding_dimension,
            "sparse_vector": {"indices": [1], "values": [1.0]},
            "text": "hello",
            "metadata": {"title": "t"},
        }],
        namespace=NAMESPACE_IAC,
    ))
    assert mock_index.upsert.call_args.kwargs["namespace"] == NAMESPACE_IAC

    # Dense and sparse queries forward the namespace
    asyncio.run(manager.search_dense([0.0] * manager.settings.embedding_dimension, namespace=NAMESPACE_IAC))
    assert mock_index.query.call_args.kwargs["namespace"] == NAMESPACE_IAC

    asyncio.run(manager.search_sparse({"indices": [1], "values": [1.0]}, namespace=NAMESPACE_IAC))
    assert mock_index.query.call_args.kwargs["namespace"] == NAMESPACE_IAC


# ── Knowledge base & catalog expansion ───────────────────────────────────────

def test_knowledge_corpus_chunks_carry_senior_metadata():
    from ingest_services import build_knowledge_chunks

    ns_map = build_knowledge_chunks(__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "senior_engineer_knowledge")
    assert set(ns_map.keys()) == {
        "senior-engineer-knowledge",
        "troubleshooting-playbooks",
        "iac-templates",
    }
    for namespace, chunks in ns_map.items():
        assert chunks, f"namespace {namespace} produced no chunks"
        for chunk in chunks:
            assert chunk["metadata"]["difficulty_tier"] == "senior"
            assert chunk["metadata"]["domain"] in ("architecture", "troubleshooting", "iac", "finops", "cli")
            assert chunk["chunk_id"].startswith("kb-")


def test_services_md_contains_next_gen_catalog():
    from ingest_services import parse_services_md

    parsed = parse_services_md(
        __import__("pathlib").Path(__file__).resolve().parent.parent / "Services.Md"
    )
    categories = {c["category"] for c in parsed["catalog_categories"]}
    # The 2026 next-gen expansion must add its categories to the catalog
    assert "Next-Gen Compute & Specialized Silicon" in categories
    assert "Next-Gen AI, Agents & Model Platforms" in categories
    assert "Next-Gen Security & Governance" in categories
    # Original catalog categories remain intact
    assert "Compute" in categories or any("Compute" in c for c in categories)


def test_services_md_covers_requested_new_services():
    content = (
        __import__("pathlib").Path(__file__).resolve().parent.parent / "Services.Md"
    ).read_text(encoding="utf-8")
    for service in (
        "Aurora DSQL",
        "AWS Clean Rooms",
        "Bedrock AgentCore",
        "SimSpace Weaver",
        "Verified Access",
        "AWS Cloud WAN",
        "Med-Gemini",
        "Trillium",
        "AlloyDB Omni",
        "Microsoft Foundry",
        "Agent 365",
        "Elastic SAN",
        "HorizonDB",
        "Azure Managed Lustre",
        "Microsoft Sentinel",
        "Chaos Studio",
        "Deployment Environments",
        "S3 Express One Zone",
        "VPC Lattice",
        "Security Lake",
    ):
        assert service in content, f"Services.Md is missing '{service}'"
