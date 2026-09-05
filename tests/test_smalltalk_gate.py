"""Tests for the small-talk gate (Part 1 of the implementation plan).

Covers:
- Layer 1 regex hits and misses (compound messages must NOT gate)
- Layer 2 cosine gate with a fake embedder, including the fail-open band
- Fail-open on embedder errors / missing embeddings
- Attachments present → never gated
- Pipeline-level bypass: greeting on Max tier answers with zero retrieval,
  web-search, transform, or classify calls; SSE still emits token + done
- Router safety net: rule-based route returns chitchat with routes=[]
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Settings
from llm.provider import GeminiQuotaExceeded  # noqa: F401  (import guard: provider imports cleanly)
from router.query_router import QueryRouter
from router.smalltalk_gate import (
    CANONICAL_SMALLTALK_UTTERANCES,
    SmallTalkGate,
    is_smalltalk_regex_query,
)


# ─── Layer 1: regex ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "hi",
        "Hi!",
        "HI",
        "hii",
        "hey",
        "hey there",
        "hello",
        "Hello?",
        "yo",
        "sup",
        "good morning",
        "Good Morning!",
        "good evening",
        "thanks",
        "thank you",
        "Thank you!",
        "thx",
        "bye",
        "goodbye",
        "good night",
        "ok",
        "okay",
        "cool",
        "great",
        "who are you",
        "who are you?",
        "what can you do",
        "help",
        "how are you",
        "how's it going",
        "what's up",
        "  hi  ",
    ],
)
def test_regex_gate_hits_greetings(query):
    assert is_smalltalk_regex_query(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "hi, what is S3 lifecycle?",
        "What is S3 lifecycle?",
        "help me configure VPC peering",
        "help me write a Lambda function",
        "history of AWS",
        "This is a detailed question about DynamoDB capacity modes",
        "hey, compare S3 and GCS pricing",
        "how are you today? I want to know about EC2",
        "",
    ],
)
def test_regex_gate_misses_real_questions(query):
    assert is_smalltalk_regex_query(query) is False


def test_regex_gate_fails_open_for_long_queries():
    long_greeting = "hi " * 45  # 135 chars, still semantically a greeting
    assert len(long_greeting.strip()) > 120
    assert is_smalltalk_regex_query(long_greeting) is False


# ─── Layer 2: cosine gate ────────────────────────────────────────────────────


class _DictEmbedder:
    """Deterministic fake embedder: maps known texts to fixed vectors."""

    def __init__(self, vectors: dict[str, list[float]], default: list[float]):
        self.vectors = vectors
        self.default = default
        self.calls = 0

    async def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self.vectors.get(text, self.default)


def _gate(embedder) -> SmallTalkGate:
    return SmallTalkGate(embedder=embedder, settings=Settings(smalltalk_gate_enabled=True))


async def test_cosine_gate_hits_canonical_greeting():
    # Canonical utterances all map to [0, 1]; a query vector near [0, 1]
    # is a strong match.
    embedder = _DictEmbedder(vectors={}, default=[0.0, 1.0])
    gate = _gate(embedder)
    assert await gate.is_smalltalk("hola!", [0.1, 0.99]) is True


async def test_cosine_gate_misses_unrelated_embedding():
    embedder = _DictEmbedder(vectors={}, default=[0.0, 1.0])
    gate = _gate(embedder)
    # Orthogonal to every canonical utterance.
    assert await gate.is_smalltalk("explain DynamoDB capacity", [1.0, 0.0]) is False


async def test_cosine_gate_fails_open_in_ambiguous_band():
    embedder = _DictEmbedder(vectors={}, default=[0.0, 1.0])
    gate = _gate(embedder)
    # cosine ~0.85 vs every canonical utterance → inside the 0.80–0.92
    # fail-open band, so the query falls through to the normal pipeline.
    query_vec = [0.5268, 0.85]
    norm = (0.5268**2 + 0.85**2) ** 0.5
    query_vec = [v / norm for v in query_vec]
    assert await gate.is_smalltalk("hola!", query_vec) is False


async def test_cosine_gate_fails_open_on_embedder_error():
    class _BrokenEmbedder:
        async def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("embedder down")

    gate = _gate(_BrokenEmbedder())
    assert await gate.is_smalltalk("hola!", [0.0, 1.0]) is False


async def test_cosine_gate_skips_layer2_without_embedding():
    embedder = _DictEmbedder(vectors={}, default=[0.0, 1.0])
    gate = _gate(embedder)
    assert await gate.is_smalltalk("what is S3 lifecycle?", None) is False
    assert embedder.calls == 0


async def test_gate_disabled_via_settings():
    gate = SmallTalkGate(
        embedder=_DictEmbedder(vectors={}, default=[0.0, 1.0]),
        settings=Settings(smalltalk_gate_enabled=False),
    )
    assert await gate.is_smalltalk("hi", [0.0, 1.0]) is False


def test_override_utterances_file(tmp_path):
    override = tmp_path / "utterances.json"
    override.write_text(json.dumps(["hola", "que tal"]), encoding="utf-8")
    settings = Settings(smalltalk_utterances_override=str(override))
    from router.smalltalk_gate import _load_utterances

    assert _load_utterances(settings) == ("hola", "que tal")
    assert _load_utterances(Settings()) == CANONICAL_SMALLTALK_UTTERANCES


# ─── Router safety net ───────────────────────────────────────────────────────


def test_rule_based_router_returns_chitchat_for_greeting():
    router = QueryRouter(llm_client=None, router_llm_client=None)
    classification = router._rule_based_route("hi")
    assert classification.intent == "chitchat"
    assert classification.routes == []
    assert classification.needs_internet is False


def test_rule_based_router_returns_chitchat_for_thanks():
    router = QueryRouter(llm_client=None, router_llm_client=None)
    classification = router._rule_based_route("thank you!")
    assert classification.intent == "chitchat"
    assert classification.routes == []


async def test_route_query_delegates_to_rule_based_chitchat():
    router = QueryRouter(llm_client=None, router_llm_client=None)
    classification = await router.route_query("hello")
    assert classification.intent == "chitchat"
    assert classification.routes == []


# ─── Pipeline-level bypass ───────────────────────────────────────────────────


class _RecordingEmbedder:
    """Fake embedder where every canonical utterance embeds to [0, 1]."""

    def __init__(self):
        self.queries: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.0, 1.0]


def _mock_chat_pipeline(embedder) -> MagicMock:
    mock = MagicMock()
    mock.settings = Settings(
        smalltalk_gate_enabled=True,
        enable_semantic_cache=True,
        semantic_cache_enabled=True,
        semantic_cache_threshold=0.92,
        enable_structured_stage_events=True,
    )
    mock.get_embeddings.return_value = embedder
    return mock


async def test_greeting_on_max_tier_bypasses_full_pipeline():
    """'Hi' on Apex answers directly: no retrieval, web, transform, classify."""
    embedder = _RecordingEmbedder()

    async def _fake_gwf(**kwargs):
        if kwargs.get("stream"):

            async def _gen():
                yield "Hello! "
                yield "How can I help?"

            return _gen(), "gemini"
        return "Hello! How can I help?", "gemini"

    with patch("api.chat_routes.pipeline", _mock_chat_pipeline(embedder)), \
         patch("api.chat_routes.generate_with_fallback", AsyncMock(side_effect=_fake_gwf)), \
         patch(
             "retrieval.adaptive_rag.AdaptiveAdvancedRAGPipeline.run",
             AsyncMock(side_effect=AssertionError("Adaptive pipeline must not run for greetings")),
         ), \
         patch(
             "retrieval.agentic_rag.AgenticRAGPipeline.run",
             AsyncMock(side_effect=AssertionError("Agentic pipeline must not run for greetings")),
         ):
        from api.chat_routes import execute_agent_pipeline

        result = await execute_agent_pipeline(
            query="Hi",
            tier="Max",
            stream=False,
            thinking_level="High",
        )

    assert result.pipeline_type == "smalltalk"
    assert result.answer == "Hello! How can I help?"
    assert result.routes == []
    assert result.classification == {"intent": "chitchat", "routes": []}
    assert result.sources == []


async def test_greeting_stream_emits_only_generate_stage_events():
    """SSE contract: gate bypass still emits token + done-compatible stream."""
    embedder = _RecordingEmbedder()
    emitted: list[dict] = []

    async def _emit(payload):
        emitted.append(payload)

    async def _fake_gwf(**kwargs):
        async def _gen():
            yield "Hello!"

        return _gen(), "gemini"

    # The token generator is lazy: the generate call happens on first
    # iteration, so the stream must be consumed INSIDE the patch scope.
    with patch("api.chat_routes.pipeline", _mock_chat_pipeline(embedder)), \
         patch("api.chat_routes.generate_with_fallback", AsyncMock(side_effect=_fake_gwf)):
        from api.chat_routes import execute_agent_pipeline

        result = await execute_agent_pipeline(
            query="Hi",
            tier="Max",
            stream=True,
            emit_event=_emit,
        )

        assert result.token_stream is not None
        tokens = [t async for t in result.token_stream]

    assert tokens == ["Hello!"]

    stages = [e.get("stage") for e in emitted if "stage" in e]
    assert all(s in ("generate",) for s in stages), f"unexpected stage events: {stages}"
    assert not any(s in ("retrieve", "rerank", "search", "transform_query") for s in stages)


async def test_compound_message_is_not_gated():
    embedder = _RecordingEmbedder()
    adaptive_result = MagicMock(pipeline_type="adaptive_rag", answer="real answer")
    adaptive_result.sources = []
    adaptive_result.routes = ["RAG"]
    adaptive_result.classification = {"intent": "explain"}
    adaptive_result.pipeline_timings = {}
    adaptive_result.fallback_pass = "none"

    with patch("api.chat_routes.pipeline", _mock_chat_pipeline(embedder)), \
         patch(
             "retrieval.adaptive_rag.AdaptiveAdvancedRAGPipeline.run",
             AsyncMock(return_value=adaptive_result),
         ) as mock_run:
        from api.chat_routes import execute_agent_pipeline

        result = await execute_agent_pipeline(
            query="hi, also what is S3 lifecycle?",
            tier="Max",
            stream=False,
        )

    assert mock_run.called
    assert result.pipeline_type == "adaptive_rag"


async def test_attachments_bypass_the_gate():
    embedder = _RecordingEmbedder()
    adaptive_result = MagicMock(pipeline_type="adaptive_rag", answer="answer with file")
    adaptive_result.sources = []
    adaptive_result.routes = ["RAG"]
    adaptive_result.classification = {"intent": "explain"}
    adaptive_result.pipeline_timings = {}
    adaptive_result.fallback_pass = "none"

    with patch("api.chat_routes.pipeline", _mock_chat_pipeline(embedder)), \
         patch(
             "retrieval.adaptive_rag.AdaptiveAdvancedRAGPipeline.run",
             AsyncMock(return_value=adaptive_result),
         ) as mock_run:
        from api.chat_routes import execute_agent_pipeline

        result = await execute_agent_pipeline(
            query="hi",
            tier="Max",
            attachment_texts=[{"content": "attachment text", "filename": "a.txt"}],
            stream=False,
        )

    assert mock_run.called
    assert result.pipeline_type == "adaptive_rag"


async def test_gate_error_fails_open_to_pipeline():
    """A crashing gate must never take the pipeline down."""
    embedder = _RecordingEmbedder()
    adaptive_result = MagicMock(pipeline_type="adaptive_rag", answer="answer")
    adaptive_result.sources = []
    adaptive_result.routes = ["RAG"]
    adaptive_result.classification = {"intent": "explain"}
    adaptive_result.pipeline_timings = {}
    adaptive_result.fallback_pass = "none"

    with patch("api.chat_routes.pipeline", _mock_chat_pipeline(embedder)), \
         patch("router.smalltalk_gate.SmallTalkGate.is_smalltalk", AsyncMock(side_effect=RuntimeError("boom"))), \
         patch(
             "retrieval.adaptive_rag.AdaptiveAdvancedRAGPipeline.run",
             AsyncMock(return_value=adaptive_result),
         ) as mock_run:
        from api.chat_routes import execute_agent_pipeline

        result = await execute_agent_pipeline(query="Hi", tier="Max", stream=False)

    assert mock_run.called
    assert result.pipeline_type == "adaptive_rag"
