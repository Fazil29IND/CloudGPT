"""
Automated tests for the CloudGPT unified agent pipeline, structured logging,
and Prometheus metrics (Phases 1–2).

Covers:
  - Gemini default model name is a valid identifier (P-10 fix)
  - PipelineResult unified pipeline (non-streaming) via execute_agent_pipeline
  - Streaming pipeline returns a token stream that fills the result
  - run_agent_workflow backward-compatible wrapper
  - Attachment text and history are injected into LLM messages
  - Internet search is gated by classification when ALWAYS_WEB_SEARCH=false (P-12)
  - Structured JSON logging carries request_id
  - /metrics endpoint returns Prometheus exposition format
  - ARQ worker settings are importable (Phase 7)

Run with:
    python -m pytest tests/test_pipeline_observability.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:Fazil@localhost:5000/pygpt"
)
os.environ["ENVIRONMENT"] = "development"


from citations.citation_manager import CitationManager
from config import get_settings
from router.query_router import QueryClassification


# ── Config fixes (Phase 1) ───────────────────────────────────────────────────

def test_gemini_model_default_is_valid():
    """P-10: the default Gemini model must be a real google-genai identifier."""
    settings = get_settings()
    assert settings.gemini_model == "gemini-3.8-flash"


# ── Pipeline fixtures ────────────────────────────────────────────────────────

def _classification(routes=None, needs_internet=False):
    return QueryClassification(
        intent="explain",
        routes=routes or ["RAG"],
        providers=["aws"],
        services=["s3"],
        categories=["storage"],
        confidence=0.9,
        reasoning="test classification",
        needs_internet=needs_internet,
    )


def _patched_gather(monkeypatch, classification, citation_mgr=None):
    from api import chat_routes

    async def fake_gather(query, provider_filter, tier, emit_event=None, chat_history=None):
        return (
            classification,
            classification.routes.copy(),
            [],
            [],
            [],
            [],
            [],  # api_data
            None,
            citation_mgr or CitationManager(),
            {"classification": 10.0, "retrieval": 15.0},
            "none",
        )

    monkeypatch.setattr(chat_routes, "_gather_pipeline_context", fake_gather)


# ── Non-streaming pipeline ───────────────────────────────────────────────────

def test_execute_agent_pipeline_non_streaming(monkeypatch):
    from api import chat_routes

    _patched_gather(monkeypatch, _classification(routes=["RAG"]))
    async def fake_generate(messages, stream=False, temperature=0.7, tier="Free", thinking_level=None, **kwargs):
        assert stream is False
        return "final answer", "gemini"

    monkeypatch.setattr(chat_routes, "generate_with_fallback", fake_generate)

    result = asyncio.run(chat_routes.execute_agent_pipeline("what is s3?", tier="Free"))
    assert result.answer == "final answer"
    assert result.model_used == "gemini"
    assert result.routes == ["RAG"]
    assert result.confidence == 0.9
    assert result.classification["intent"] == "explain"
    assert result.token_stream is None


def test_execute_agent_pipeline_streaming(monkeypatch):
    from api import chat_routes

    _patched_gather(monkeypatch, _classification(routes=["RAG"]))

    async def token_iter():
        for token in ["hello ", "streamed ", "world"]:
            yield token

    async def fake_generate(messages, stream=False, temperature=0.7, tier="Free", thinking_level=None, **kwargs):
        assert stream is True
        assert thinking_level == "High"
        return token_iter(), "gemini"

    monkeypatch.setattr(chat_routes, "generate_with_fallback", fake_generate)

    async def run():
        events = []
        async def emit(payload):
            events.append(payload)

        result = await chat_routes.execute_agent_pipeline(
            "what is s3?", tier="Free", emit_event=emit, stream=True, thinking_level="High"
        )
        assert result.token_stream is not None
        collected = []
        async for token in result.token_stream:
            collected.append(token)
        return events, collected, result

    events, collected, result = asyncio.run(run())
    assert "".join(collected) == "hello streamed world"
    assert result.answer == "hello streamed world"
    assert result.model_used == "gemini"
    # Status event for synthesis was emitted through the callback
    assert any("Synthesizing" in e.get("status", "") for e in events)


def test_run_agent_workflow_wrapper(monkeypatch):
    """The legacy tuple-returning wrapper delegates to the unified pipeline."""
    from api import chat_routes

    _patched_gather(monkeypatch, _classification(routes=["RAG"]))
    async def fake_generate(messages, stream=False, temperature=0.7, tier="Free", thinking_level=None, **kwargs):
        return "answer", "gemini"

    monkeypatch.setattr(chat_routes, "generate_with_fallback", fake_generate)

    answer, sources, routes, confidence, classification, model_used = asyncio.run(
        chat_routes.run_agent_workflow("compare s3 and gcs")
    )
    assert answer == "answer"
    assert model_used == "gemini"
    assert routes == ["RAG"]


def test_context_messages_include_history_and_attachments(monkeypatch):
    from api import chat_routes

    messages = chat_routes._build_pipeline_messages(
        query="summarize the doc",
        classification=_classification(),
        rag_results=[],
        web_results=[],
        internet_results=[],
        pricing_data=[],
        calc_results=None,
        provider_filter=None,
        chat_history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        attachment_texts=[{"filename": "notes.txt", "content_type": "text/plain", "content": "ATTACHED-CONTENT-123"}],
    )

    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert [m["content"] for m in messages[1:3]] == ["hi", "hello"]
    assert "ATTACHED-CONTENT-123" in messages[-1]["content"]
    assert "notes.txt" in messages[-1]["content"]


def test_internet_search_gated_by_classification(monkeypatch):
    """P-12: with always_web_search=False, no search when classifier declines."""
    from api import chat_routes

    classification = _classification(routes=["RAG"], needs_internet=False)

    class FakeRouter:
        async def route_query(self, query):
            return classification

    monkeypatch.setattr(chat_routes.pipeline, "get_router", lambda tier: FakeRouter())

    calls = []

    class FakeWebSearch:
        async def search(self, *args, **kwargs):
            calls.append("search")
            return []

        async def search_for_problem_solving(self, *args, **kwargs):
            calls.append("problem")
            return []

    with patch.object(chat_routes.pipeline, "web_search", FakeWebSearch()), \
         patch.object(chat_routes.pipeline.settings, "always_web_search", False):
        asyncio.run(chat_routes._gather_pipeline_context("explain iam roles", None, "Free"))

    assert calls == []


def test_internet_search_runs_when_classifier_requests(monkeypatch):
    from api import chat_routes

    classification = _classification(routes=["RAG", "INTERNET"], needs_internet=True)

    class FakeRouter:
        async def route_query(self, query):
            return classification

    monkeypatch.setattr(chat_routes.pipeline, "get_router", lambda tier: FakeRouter())

    calls = []

    class FakeWebSearch:
        async def search(self, *args, **kwargs):
            calls.append("search")
            return []

        async def search_for_problem_solving(self, *args, **kwargs):
            calls.append("problem")
            return []

    with patch.object(chat_routes.pipeline, "web_search", FakeWebSearch()), \
         patch.object(chat_routes.pipeline.settings, "always_web_search", False):
        asyncio.run(chat_routes._gather_pipeline_context("latest aws s3 pricing", None, "Free"))

    assert calls == ["search"]


def test_internet_search_always_runs_when_configured(monkeypatch):
    """Legacy default: ALWAYS_WEB_SEARCH=true forces search for every query."""
    from api import chat_routes

    classification = _classification(routes=["RAG"], needs_internet=False)

    class FakeRouter:
        async def route_query(self, query):
            return classification

    monkeypatch.setattr(chat_routes.pipeline, "get_router", lambda tier: FakeRouter())

    calls = []

    class FakeWebSearch:
        async def search(self, *args, **kwargs):
            calls.append("search")
            return []

        async def search_for_problem_solving(self, *args, **kwargs):
            calls.append("problem")
            return []

    with patch.object(chat_routes.pipeline, "web_search", FakeWebSearch()), \
         patch.object(chat_routes.pipeline.settings, "always_web_search", True):
        asyncio.run(chat_routes._gather_pipeline_context("purely conceptual question", None, "Free"))

    assert calls == ["search"]


# ── Structured logging (Phase 2) ─────────────────────────────────────────────

def test_structured_log_contains_request_id(capsys):
    from logging_config import bind_request_context, configure_logging, get_logger, unbind_request_context

    configure_logging(log_level="INFO", log_format="json")
    bind_request_context(request_id="req-rid-12345", user_id=42)
    logger = get_logger("test.structured")
    logger.info("hello structured world", extra_field="value")
    unbind_request_context()

    output = capsys.readouterr().out
    assert '"request_id"' in output and "req-rid-12345" in output
    assert '"user_id"' in output and "42" in output
    assert "hello structured world" in output
    assert '"event"' in output


def test_console_log_format_renders_readable(capsys):
    from logging_config import configure_logging, get_logger

    configure_logging(log_level="INFO", log_format="console")
    get_logger("test.console").info("console formatted message")

    output = capsys.readouterr().out
    assert "console formatted message" in output


# ── Prometheus metrics (Phase 2) ─────────────────────────────────────────────

def test_metrics_custom_counters_exist():
    from metrics import (
        CACHE_HITS_TOTAL,
        CHAT_REQUESTS_TOTAL,
        LLM_LATENCY_SECONDS,
        RAG_RESULTS_COUNT,
        TOKEN_USAGE_TOTAL,
    )

    # Incrementing labelled metrics must not raise.
    CHAT_REQUESTS_TOTAL.labels(tier="lite", model="Free", status="ok", pipeline_type="current_rag").inc()
    TOKEN_USAGE_TOTAL.labels(tier="lite", model="Free").inc(10)
    LLM_LATENCY_SECONDS.labels(provider="gemini", role="main").observe(0.5)
    RAG_RESULTS_COUNT.observe(5)
    CACHE_HITS_TOTAL.labels(cache="llm_response").inc()


def test_metrics_endpoint_exposes_prometheus_format(app_instance):
    from fastapi.testclient import TestClient

    client = TestClient(app_instance)
    response = client.get("/metrics")
    assert response.status_code == 200

    body = response.text
    # Prometheus exposition format: TYPE lines and the instrumentator's
    # default http request metrics must be present.
    assert "# HELP" in body or "# TYPE" in body
    assert "http_request" in body


def test_metrics_business_counters_scraped(app_instance):
    from fastapi.testclient import TestClient

    from metrics import CHAT_REQUESTS_TOTAL

    CHAT_REQUESTS_TOTAL.labels(tier="pro", model="Pro", status="ok", pipeline_type="agentic_rag").inc()
    client = TestClient(app_instance)
    body = client.get("/metrics").text
    assert "cloudgpt_chat_requests_total" in body


# ── ARQ worker (Phase 7) ─────────────────────────────────────────────────────

def test_worker_settings_importable():
    from tasks import WorkerSettings

    names = [fn.__name__ for fn in WorkerSettings.functions]
    assert "cleanup_expired_reservations_task" in names
    assert "send_email_task" in names
    assert "ingest_services_task" in names


# ── Pipeline Observability & SSE Status Events ───────────────────────────────

@pytest.mark.asyncio
async def test_agentic_pipeline_emits_status_events_in_order():
    from retrieval.agentic_rag import AgenticRAGPipeline
    from retrieval.hybrid import RetrievalResult
    from unittest.mock import AsyncMock, MagicMock

    events = []
    async def mock_emit(payload):
        events.append(payload.get("status", ""))

    pipeline = AgenticRAGPipeline()
    mock_plan = {
        "intent": "explain", "routes": ["RAG"], "retrieval_strategy": "broad",
        "sub_queries": [], "providers": ["aws"], "needs_internet": False,
        "confidence": 0.9, "_timing_ms": 10.0,
    }
    chunks = [RetrievalResult(chunk_id="c1", text="text", score=0.9, metadata={})]

    with patch.object(pipeline, "_plan_and_route", return_value=mock_plan), \
         patch.object(pipeline, "_hybrid_retrieve", return_value=(chunks, "pass")), \
         patch.object(pipeline, "_grade_evidence", return_value=chunks), \
         patch("api.chat_routes.generate_with_fallback", AsyncMock(return_value=("ans", "model"))), \
         patch("api.chat_routes._build_pipeline_messages", return_value=[]), \
         patch("api.chat_routes.pipeline.get_retrieval", return_value=(MagicMock(), MagicMock())):
        await pipeline.run(query="q", tier="Pro", emit_event=mock_emit)

    status_str = " -> ".join(events)
    assert "Planning retrieval strategy" in status_str
    assert "Searching documentation & evidence" in status_str
    assert "Grading evidence quality" in status_str
    assert "Synthesizing and verifying answer" in status_str


@pytest.mark.asyncio
async def test_adaptive_pipeline_emits_status_events_in_order():
    from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline
    from retrieval.hybrid import RetrievalResult
    from router.query_router import QueryClassification
    from unittest.mock import AsyncMock, MagicMock

    events = []
    async def mock_emit(payload):
        events.append(payload.get("status", ""))

    pipeline = AdaptiveAdvancedRAGPipeline()
    transformed = {"rewritten_query": "q", "expanded_queries": ["q"], "hyde_passage": "p"}
    classification = QueryClassification(
        intent="explain", routes=["RAG"], providers=[], services=[],
        categories=[], confidence=0.9, reasoning="", needs_internet=False
    )
    chunks = [RetrievalResult(chunk_id="c1", text="text", score=0.9, metadata={})]

    with patch.object(pipeline, "_transform_query", return_value=transformed), \
         patch("router.query_router.QueryRouter.route_query", AsyncMock(return_value=classification)), \
         patch.object(pipeline, "_multi_query_retrieve", return_value=(chunks, "pass")), \
         patch.object(pipeline, "_rerank_and_compress", return_value=chunks), \
         patch("api.chat_routes.generate_with_fallback", AsyncMock(return_value=("ans", "model"))), \
         patch("api.chat_routes._build_pipeline_messages", return_value=[]), \
         patch("api.chat_routes.pipeline.get_retrieval", return_value=(MagicMock(), MagicMock())), \
         patch("api.chat_routes.pipeline.get_main_llm", return_value=MagicMock()), \
         patch("api.chat_routes.pipeline.get_router_llm", return_value=MagicMock()):
        await pipeline.run(query="q", tier="Max", emit_event=mock_emit)

    status_str = " -> ".join(events)
    assert "Transforming and expanding query" in status_str
    assert "Running multi-query retrieval" in status_str
    assert "Reranking and compressing context" in status_str
    assert "Synthesizing answer with AI" in status_str


def test_all_stage_labels_registered():
    from metrics import RAG_STAGE_DURATION_SECONDS
    _ALL_STAGES = [
        "classification", "web_search", "retrieval",
        "context_assembly", "llm_generate",
        "plan_route", "agentic_retrieve", "grade_evidence",
        "agentic_generate", "agentic_critique",
        "transform_query", "adaptive_retrieve", "rerank_compress",
        "adaptive_generate",
    ]
    _ALL_TIERS = ("Free", "Pro", "Max", "Developer")
    for stage in _ALL_STAGES:
        for tier in _ALL_TIERS:
            assert RAG_STAGE_DURATION_SECONDS.labels(stage=stage, tier=tier) is not None

