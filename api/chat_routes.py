"""
CloudGPT Chat API Routes Module.

Orchestrates the unified multi-model AI agent pipeline:
Query Router → Internet Search (gated by classification) + Hybrid RAG /
Pricing / Cloud APIs / Calculator → Context Builder → Multi-Model LLM Chain
(Gemini 3.8/3.7/3.6/3.5 Flash, Claude 3.7 Sonnet, Kimi, Qwen, DeepSeek) →
Citation Manager → SSE Stream.

The agent pipeline (classification → tools → context → LLM) is implemented
exactly once in `execute_agent_pipeline` and is shared by the non-streaming
and SSE streaming endpoints.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
_hashlib = hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Awaitable, Callable, Literal

import db
import structlog
import tiktoken

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field

from config import get_settings
from citations.citation_manager import CitationManager
from cloud_apis.aws_tools import AWSTools
from cloud_apis.azure_tools import AzureTools
from cloud_apis.gcp_tools import GCPTools
from embeddings.embedding_engine import EmbeddingEngine
from embeddings.qdrant_manager import QdrantManager
from embeddings.pinecone_manager import PineconeManager
from llm.context_builder import ContextBuilder
from llm.history_budget import trim_history_by_tokens, compact_history_turns
from llm.provider import (
    GeminiQuotaExceeded,
    get_llm_provider,
    get_sub_model_provider,
    get_evaluator_provider,
    calculate_effective_prompt_budget,
)
from llm.thinking import (
    ThinkingStreamSplitter,
    clamp_thinking_level,
    profile_for,
    split_thinking,
)
from metrics import (
    CACHE_HITS_TOTAL,
    CACHE_MISSES_TOTAL,
    CHAT_REQUESTS_TOTAL,
    LLM_LATENCY_SECONDS,
    LLM_QUOTA_ERRORS_TOTAL,
    PIPELINE_TIMEOUTS_TOTAL,
    RAG_RESULTS_COUNT,
    RAG_STAGE_DURATION_SECONDS,
    REDIS_LOCK_ACQUIRED,
    REDIS_LOCK_WAITED,
    SEMANTIC_CACHE_HITS,
    SUBMODEL_CALLS_TOTAL,
    TOKEN_USAGE_TOTAL,
    TTFT_SECONDS,
)
from core.semantic_cache import get_semantic_cache
from retrieval.bm25 import SparseRetriever
from retrieval.dense import DenseRetriever, HNSWRetriever, QuakeRetriever, get_default_hnsw_index, get_default_quake_index
from retrieval.hybrid import HybridRetriever
from retrieval.reranker import Reranker
from router.query_router import QueryRouter, provider_aliases
from tools.calculator import CalculatorTool
from tools.pricing.aws_pricing import AWSPricingTool
from tools.pricing.azure_pricing import AzurePricingTool
from tools.pricing.gcp_pricing import GCPPricingTool
from tools.pricing import fetch_cloud_pricing
from tools.web_search import WebSearchTool
from core.llm_cache import (
    get_cached_answer,
    get_cached_llm_response,
    get_cached_retrieval_result,
    set_cached_answer,
    set_cached_llm_response,
    set_cached_retrieval_result,
)
from core.rate_limit import rate_limiter
from core.redis_client import redis_client
from core.security import get_request_id, validate_password_rules, sanitize_model_output
from core.session_cache import (
    get_fast_chat_history,
    record_message_dual_write,
    invalidate_session_cache,
    get_cached_session_summary,
    set_cached_session_summary,
)
from core.user_memory import (
    get_user_memories_cached,
    upsert_user_memory_cached,
    delete_user_memory_cached,
    delete_all_user_memory_cached,
    extract_durable_user_facts,
)

import secrets

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


# ─── Request / Response Models ───────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str = Field(..., description="User question or prompt")
    provider_filter: str | None = Field(default=None, description="Optional provider filter (aws, gcp, azure)")
    session_id: str | None = Field(default=None, description="Session tracking ID")
    mode: str | None = Field(default="Lite", description="Requested model mode (Apex, Core, Lite)")
    thinking_level: str | None = Field(default="Medium", description="Thinking depth (Low, Medium, High, Max)")
    attachments: list[dict[str, Any]] | None = Field(default=None, description="Optional attached files metadata (attachment_id references)")
    request_id: str | None = Field(default=None, description="Client-generated request ID for idempotency and quota tracking")
    truncate_from_message_id: int | None = Field(default=None, description="Optional message ID to truncate history from before generating")


class FeedbackRequest(BaseModel):
    message_id: int = Field(..., description="ID of the message to provide feedback for")
    rating: int = Field(..., description="Rating score: 1 (positive) or -1 (negative)")
    reason: str | None = Field(default=None, description="Optional explanation or category for the rating")


class SourceCitation(BaseModel):
    source_number: int
    source_type: str
    url: str
    provider: str
    service: str
    title: str
    section: str


class UpdateProfileRequest(BaseModel):
    name: str = Field(..., max_length=80, description="User display name")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., description="Current password")
    new_password: str = Field(..., description="New password")
    confirm_password: str = Field(..., description="Confirm new password")


class UpdatePreferenceRequest(BaseModel):
    key: str = Field(..., description="Preference key")
    value: Any = Field(..., description="Preference value")



class ChatResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]]
    routes_used: list[str]
    confidence: float
    query_classification: dict[str, Any] = Field(default_factory=dict)
    model_used: str = "gemini-3.8-flash"
    thinking_level: str = "Medium"
    thinking_tokens: int = 0
    thinking_summary: str = ""
    pipeline_timings: dict[str, float] = Field(default_factory=dict)
    fallback_pass: str = "none"
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    bundle_zip_url: str | None = None


# ─── Latency Budget & Pipeline Result ───────────────────────────────────────

@dataclass
class LatencyBudget:
    """Latency budgets per stage (in milliseconds)."""
    classification_ms: int = 300
    retrieval_ms: int = 300
    reranking_ms: int = 400
    web_search_ms: int = 2000
    context_assembly_ms: int = 50


@dataclass
class PipelineResult:
    """Outcome of one agent pipeline execution.

    For `stream=True` the LLM token iterator is exposed via `token_stream`;
    iterating it fills in `answer` and `model_used` incrementally, so callers
    should read result fields only after the stream is exhausted.
    """

    answer: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    classification: dict[str, Any] = field(default_factory=dict)
    model_used: str = "unknown"
    token_stream: AsyncGenerator[str, None] | None = None
    pipeline_timings: dict[str, float] = field(default_factory=dict)
    fallback_pass: str = "none"
    pipeline_type: str = "current_rag"
    context_validator_flags: list[str] = field(default_factory=list)
    cqc_coherence_score: float = 1.0
    # Layer-4 output validation report (claim support, dimensions, claim→source
    # attribution). Populated by the tier pipelines; empty dict when validation
    # is disabled or not applicable.
    validation: dict[str, Any] = field(default_factory=dict)
    # Provider-reported token usage for the main generation call
    # ({"prompt_tokens": int|None, "total_tokens": int|None, ...}). Populated
    # lazily for streams — read only after the token stream is exhausted.
    usage: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    bundle_zip_url: str | None = None


# SSE event types: status, stage, provider_detected, session_title, thinking_token,
# thinking_done, token, memory_updated, error, done, live_verify
# Emit callback: receives a JSON-serializable SSE payload dict mid-pipeline.
EmitEvent = Callable[[dict[str, Any]], Awaitable[None]]


# ─── Pipeline Singleton Container ───────────────────────────────────────────

class AgentPipeline:
    """Encapsulates and lazy-initializes all agent components."""

    def __init__(self) -> None:
        self.settings = get_settings()

        # Model instances (lazy-initialized)
        self._main_llm = None
        self._query_router = None

        self.context_builder = ContextBuilder()
        self.embedding_engine = None
        self.pinecone_manager = None
        self.vector_manager = None
        self.hybrid_retriever = None
        self.reranker = None
        self.hnsw_retriever = None
        self.quake_retriever = None

        # Tools
        self.web_search = WebSearchTool()
        self.calculator = CalculatorTool()
        self.aws_pricing = AWSPricingTool()
        self.gcp_pricing = GCPPricingTool()
        self.azure_pricing = AzurePricingTool()
        self.aws_tools = AWSTools()
        self.gcp_tools = GCPTools()
        self.azure_tools = AzureTools()

    def get_llm(self, tier: str = "Free"):
        """Return a GeminiProvider for the given tier."""
        return get_llm_provider("main", tier)

    def get_main_llm(self, tier: str = "Free"):
        """Get the main LLM (dynamic tier)."""
        return self.get_llm(tier)

    def get_router_llm(self, tier: str = "Free"):
        """Query classification and routing — uses the sub-model."""
        return get_sub_model_provider("router", tier)

    def get_summarizer_llm(self, tier: str = "Free"):
        """Title generation and query transformation — uses the sub-model."""
        return get_sub_model_provider("summarizer", tier)

    def get_verifier_llm(self, tier: str = "Free"):
        """Answer Evaluator — uses dedicated evaluator model."""
        return get_evaluator_provider(tier)

    def get_router(self, tier: str = "Free"):
        """Get the query router backed by the sub-model."""
        llm = self.get_router_llm(tier)
        return QueryRouter(
            llm_client=llm,
            router_llm_client=llm,
        )

    def get_embeddings(self):
        if self.embedding_engine is None:
            self.get_retrieval()
        return self.embedding_engine

    def get_quake_retriever(self):
        if getattr(self, "quake_retriever", None) is None:
            if self.embedding_engine is None:
                return None
            self.quake_retriever = QuakeRetriever(
                embedding_engine=self.embedding_engine,
                quake_index=get_default_quake_index(self.settings),
                pinecone_manager=self.pinecone_manager,
            )
        return self.quake_retriever

    def get_retrieval(self):
        if self.hybrid_retriever is None:
            self.embedding_engine = EmbeddingEngine(
                provider=self.settings.embedding_provider,
                model_name=self.settings.embedding_model,
                dimension=self.settings.embedding_dimension
            )
            if hasattr(self.settings, "has_qdrant") and self.settings.has_qdrant:
                self.vector_manager = QdrantManager(self.settings)
            elif getattr(self.settings, "pinecone_api_key", None):
                self.vector_manager = PineconeManager(self.settings)
            else:
                self.vector_manager = QdrantManager(self.settings)

            self.pinecone_manager = self.vector_manager
            self.hnsw_retriever = HNSWRetriever(
                embedding_engine=self.embedding_engine,
                hnsw_index=get_default_hnsw_index(self.settings),
                vector_manager=self.vector_manager,
            )
            self.quake_retriever = QuakeRetriever(
                embedding_engine=self.embedding_engine,
                quake_index=get_default_quake_index(self.settings),
                vector_manager=self.vector_manager,
            )
            dense = DenseRetriever(
                self.embedding_engine,
                vector_manager=self.vector_manager,
                hnsw_index=self.hnsw_retriever.hnsw_index,
            )
            sparse = SparseRetriever(self.embedding_engine, self.vector_manager)
            self.hybrid_retriever = HybridRetriever(
                dense_retriever=dense,
                sparse_retriever=sparse,
                dense_weight=self.settings.dense_weight,
                sparse_weight=self.settings.sparse_weight
            )
            self.reranker = Reranker(model_name=self.settings.reranker_model)
        return self.hybrid_retriever, self.reranker

    async def generate_chat_title(
        self, query: str, answer: str, tier: str = "Free", current_title: str | None = None
    ) -> str:
        """Generate or refine a concise, relatable 3 to 6 word title in Title Case."""
        has_current = bool(
            current_title
            and current_title.strip()
            and current_title.strip().lower() not in ("new conversation", "untitled", "new chat")
        )
        if has_current:
            system_content = (
                "You are an intelligent conversation title evaluator. "
                f"The current conversation title is: '{current_title.strip()}'. "
                "Analyze the latest user query and assistant summary. "
                "If the current title still accurately reflects the overall discussion, output the current title unchanged. "
                "If the conversation has shifted or evolved into a specific new topic, generate an updated 3 to 6 word Title Case heading. "
                "Output ONLY the final title text with no quotes, prefixes, or markdown."
            )
            user_content = (
                f"Current Title: {current_title.strip()}\n"
                f"Latest User Query: {query[:300]}\n"
                f"Latest Assistant Summary: {answer[:300]}\n"
                "Title:"
            )
        else:
            system_content = (
                "You are a concise conversation heading generator. Summarize the user query into a clean, "
                "informative title of 3 to 6 words in Title Case. "
                "Output ONLY the title text. Do NOT include quotes, prefixes, markdown, or trailing punctuation."
            )
            user_content = f"User Query: {query[:300]}\nAssistant Summary: {answer[:300]}\nTitle:"

        prompt_messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        try:
            llm = self.get_summarizer_llm(tier)
            SUBMODEL_CALLS_TOTAL.labels(role="title").inc()
            title_text = await asyncio.wait_for(
                llm.generate(messages=prompt_messages, stream=False, temperature=0.3),
                timeout=4.0,
            )
            cleaned = re.sub(r'[\r\n"\'`]+', '', title_text).strip()
            cleaned = re.sub(r'^[#\-\*\s]+', '', cleaned)
            cleaned = re.sub(r'^(final title|title|heading):\s*', '', cleaned, flags=re.IGNORECASE).strip()
            words = cleaned.split()
            if 1 <= len(words) <= 8:
                return " ".join(words[:6])
        except Exception as e:
            logger.warning("AI heading generation fallback", error=str(e))

        if has_current:
            return current_title.strip()

        # Fallback snippet title
        words = re.sub(r'[^\w\s]', '', query).split()
        if words:
            return " ".join(words[:5]).title()
        return "New Conversation"


pipeline = AgentPipeline()


def _calculator_context(query: str) -> dict[str, Any]:
    """Evaluate the first safe arithmetic expression found in a query."""
    expression_pattern = re.compile(r"[\d\.\s\+\-\*\/\(\)%]{3,}")
    for match in expression_pattern.findall(query):
        expression = match.strip()
        if not re.search(r"\d", expression):
            continue
        try:
            return pipeline.calculator.calculate(expression).model_dump()
        except Exception:
            continue
    return {"note": "Calculator ready for mathematical breakdown"}


# ─── Multi-Model Answer Generation with Fallback ───────────────────────────

# Token usage of the most recent generate_with_fallback call in the current
# request context. A ContextVar so concurrent requests don't cross over, and
# so lazy token-stream generators (which run in the endpoint's context) report
# their usage where it is read.
_last_gen_usage: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "last_gen_usage", default=None
)


def _tier_primary_model(mode: str) -> str:
    """Map a requested model mode (Max/Pro/Free) to its configured primary Gemini model."""
    settings = pipeline.settings
    m = (mode or "Free").strip().lower()
    if m in ("max", "apex", "developer", "admin"):
        return settings.gemini_model_apex
    if m in ("pro", "core"):
        return settings.gemini_model_core
    return settings.gemini_model_lite


def _true_input_tokens(result: Any, fallback: int) -> int:
    """True prompt tokens for billing: provider-reported when available, else
    the tiktoken estimate captured at generation time, else the query-only count."""
    usage = dict(getattr(result, "usage", {}) or {})
    prompt = usage.get("prompt_tokens")
    if not prompt:
        prompt = (_last_gen_usage.get() or {}).get("prompt_tokens")
    try:
        return int(prompt) if prompt else fallback
    except (TypeError, ValueError):
        return fallback


def _quota_error_message(requested_model: str) -> str:
    """User-facing message for upstream Gemini quota exhaustion.

    Distinct from the CloudGPT-side pre-flight messages ("Token limit exceeded
    for your tier…" / "Token quota exhausted…") so users can tell their own
    plan window apart from a Google-side API rejection.
    """
    return (
        f"Gemini Free Tier Quota has Reached its Limit for the {requested_model} model. "
        "Please try again after the quota window resets."
    )


async def generate_with_fallback(
    messages: list[dict],
    stream: bool = False,
    temperature: float | None = None,
    tier: str = "Free",
    thinking_level: str | None = None,
    max_output_tokens: int | None = None,
    usage_out: dict[str, Any] | None = None,
):
    """Generate a response using the Gemini provider for the given tier.

    ``temperature`` is tier-calibrated if not explicitly passed:
      - Free / Lite -> 0.10 (strict factual grounding, zero extrapolation)
      - Pro / Core  -> 0.15 (structured diagnostic reasoning)
      - Max / Apex  -> 0.20 (architectural synthesis)
    ``thinking_level`` is forwarded to GeminiProvider which passes it to
    ``thinking_config`` in GenerateContentConfig.
    ``max_output_tokens`` is forwarded to GeminiProvider, ensuring thinking tiers
    have sufficient token budget headroom.
    ``usage_out`` optionally receives provider-reported token usage
    (``prompt_tokens`` / ``total_tokens``) after a successful generation so
    callers can bill true input tokens instead of query-length estimates.
    """
    if temperature is None:
        _t = (tier or "Free").strip().lower()
        if _t in ("max", "apex", "developer", "admin"):
            temperature = 0.20
        elif _t in ("pro", "core"):
            temperature = 0.15
        else:
            temperature = 0.10

    try:
        llm = pipeline.get_main_llm(tier)
        if max_output_tokens is None:
            base_tokens = getattr(pipeline.settings, "chat_max_output_tokens", 4096)
            _t_norm = (tier or "Free").strip().lower()
            if _t_norm in ("max", "apex", "developer", "admin"):
                base_tokens = max(base_tokens, 16384)
            elif _t_norm in ("pro", "core"):
                base_tokens = max(base_tokens, 8192)
            if thinking_level:
                from llm.thinking import profile_for

                profile = profile_for(thinking_level)
                if profile.enabled:
                    max_output_tokens = max(base_tokens, profile.budget_tokens + base_tokens)
                else:
                    max_output_tokens = base_tokens
            else:
                max_output_tokens = base_tokens
        elif thinking_level:
            from llm.thinking import profile_for

            profile = profile_for(thinking_level)
            if profile.enabled:
                max_output_tokens = max(max_output_tokens, profile.budget_tokens + max_output_tokens)

        result = await llm.generate(
            messages=messages,
            stream=stream,
            temperature=temperature,
            thinking_level=thinking_level,
            max_output_tokens=max_output_tokens,
        )
        usage = dict(getattr(llm, "last_usage", {}) or {})
        if not usage.get("prompt_tokens"):
            # Fallback estimate: tiktoken over the assembled prompt (system +
            # context + history + attachments + query) — the true input cost
            # is 5-20x the query length, so billing query-only undercounts.
            try:
                usage["prompt_tokens"] = sum(
                    _count_tokens(str(m.get("content") or "")) for m in messages
                )
                usage["prompt_tokens_estimated"] = True
            except Exception:
                pass
        if usage_out is not None:
            usage_out.update(usage)
        _last_gen_usage.set(usage)
        return result, "gemini"
    except GeminiQuotaExceeded:
        # Propagate untouched: the SSE/non-stream endpoints surface a dedicated
        # quota message instead of the generic generation-failure wrapper.
        raise
    except Exception as exc:
        logger.exception(
            "generate_with_fallback.failed",
            tier=tier,
            thinking_level=thinking_level,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise RuntimeError(
            f"Gemini generation failed (tier={tier}, thinking={thinking_level}). "
            f"Verify GEMINI_API_KEY and model availability. Cause: {exc}"
        ) from exc


# ─── Unified Agent Pipeline ─────────────────────────────────────────────────

_WEB_SEARCH_DOMAIN_MAP = {
    "aws": ["docs.aws.amazon.com", "aws.amazon.com"],
    "gcp": ["cloud.google.com"],
    "azure": ["learn.microsoft.com", "azure.microsoft.com"],
}

_PROBLEM_SOLVING_INTENTS = ("troubleshooting", "error_fix", "problem_solving")
_FRESHNESS_INTENTS = ("recent_info", "live_resource", "incident")


def _stage_event(
    stage: str,
    label: str,
    status: Literal["start", "complete", "skipped"],
    elapsed_ms: float | None = None,
) -> dict:
    """Build a structured pipeline stage SSE payload."""
    ev: dict = {
        "stage": stage,
        "label": label,
        "status": label,
        "stage_status": status,
    }
    if elapsed_ms is not None:
        ev["elapsed_ms"] = round(elapsed_ms, 1)
    return ev


def _build_strict_filter(provider_filter: str | None, classification) -> dict | None:
    if getattr(classification, "confidence", 0.0) >= 0.85 and len(getattr(classification, "services", [])) == 1:
        prov = provider_filter or (classification.providers[0] if len(getattr(classification, "providers", [])) == 1 else None)
        if prov:
            aliases = provider_aliases(prov)
            prov_val = aliases if len(aliases) > 1 else prov.lower()
            return {"provider": prov_val, "service": classification.services[0]}
    return None


def _build_provider_filter(provider_filter: str | None, classification) -> dict | None:
    prov = provider_filter or (classification.providers[0] if len(getattr(classification, "providers", [])) == 1 else None)
    if prov:
        aliases = provider_aliases(prov)
        prov_val = aliases if len(aliases) > 1 else prov.lower()
        return {"provider": prov_val}
    return None


async def _retrieve_with_fallback(
    retriever,
    reranker,
    query: str,
    provider_filter: str | None,
    classification,
    settings,
    top_k_override: int | None = None,
    expand_to_parents: bool = True,
    chat_history: list[dict] | None = None,
) -> tuple[list, str]:
    """Execute 3-pass retrieval sequence: strict -> provider -> global with async reranking and parent resolution."""
    retrieval_k = top_k_override or settings.retrieval_top_k
    retrieve_args = {"expand_to_parents": expand_to_parents}

    async def _safe_retrieve(filters):
        try:
            return await retriever.retrieve(query=query, top_k=retrieval_k, filters=filters, chat_history=chat_history, **retrieve_args)
        except TypeError:
            try:
                return await retriever.retrieve(query=query, top_k=retrieval_k, filters=filters, **retrieve_args)
            except TypeError:
                return await retriever.retrieve(query=query, top_k=retrieval_k, filters=filters)

    rerank_query = query
    if chat_history and getattr(settings, "enable_contextual_query_rewriting", True):
        try:
            from retrieval.query_processor import rewrite_contextual_query
            _ctx = rewrite_contextual_query(query, chat_history=chat_history)
            rerank_query = _ctx.effective_dense_query
        except Exception:
            rerank_query = query

    strict_f = _build_strict_filter(provider_filter, classification)
    if strict_f:
        cands = await _safe_retrieve(strict_f)
        if len(cands) >= 5:
            reranked = await asyncio.to_thread(
                reranker.rerank, query=rerank_query, results=cands, top_k=settings.rerank_top_k
            )
            return reranked, "pass1_strict"

    prov_f = _build_provider_filter(provider_filter, classification)
    if prov_f:
        cands = await _safe_retrieve(prov_f)
        if len(cands) >= 5:
            reranked = await asyncio.to_thread(
                reranker.rerank, query=rerank_query, results=cands, top_k=settings.rerank_top_k
            )
            return reranked, "pass2_provider"

    cands = await _safe_retrieve(None)
    if cands:
        reranked = await asyncio.to_thread(
            reranker.rerank, query=rerank_query, results=cands, top_k=settings.rerank_top_k
        )
    else:
        reranked = []
    return reranked, "pass3_global"


async def _gather_pipeline_context(
    query: str,
    provider_filter: str | None,
    tier: str,
    emit_event: EmitEvent | None = None,
    chat_history: list[dict] | None = None,
) -> tuple:
    """Classify the query and run routed tools with latency budgets and per-stage metrics."""
    router_instance = pipeline.get_router(tier)
    citation_mgr = CitationManager()
    timings: dict[str, float] = {}
    fallback_pass = "none"

    if emit_event:
        if getattr(pipeline.settings, "enable_structured_stage_events", True):
            await emit_event(_stage_event("classify", "Analyzing query intent", "start"))

    # 1. Classify the user query with latency budget enforcement
    t0_cls = time.perf_counter()
    budget_cls = getattr(pipeline.settings, "budget_classification_ms", 300) / 1000.0
    try:
        classification = await asyncio.wait_for(router_instance.route_query(query), timeout=budget_cls)
    except asyncio.TimeoutError:
        logger.warning("Classification stage timed out, using fast rule-based router")
        classification = router_instance._rule_based_route(query)

    cls_sec = time.perf_counter() - t0_cls
    timings["classification"] = round(cls_sec * 1000, 2)
    RAG_STAGE_DURATION_SECONDS.labels(stage="classification", tier=tier).observe(cls_sec)

    if emit_event and getattr(pipeline.settings, "enable_structured_stage_events", True):
        await emit_event(_stage_event("classify", "Query analyzed", "complete", elapsed_ms=timings["classification"]))

    # ── Context Validator — query gate (all tiers, shared path) ──────────────
    if getattr(pipeline.settings, "enable_context_validator", True):
        from core.context_validator import ContextValidator
        _cv = ContextValidator(pipeline.settings)
        _query_vr = _cv.validate_query(query)
        if not _query_vr.is_valid:
            logger.warning("context_validator.query_rejected", tier=tier, issues=_query_vr.issues)
            return (
                classification, [], [], [], [], [], None,
                citation_mgr, timings, "context_validator_rejected"
            )

    # Risk gate: medium/high risk forces the pipeline to annotate reasoning for disclaimer
    _risk_level = getattr(classification, "risk_level", "low")
    if _risk_level in ("medium", "high") and tier in ("Free", "Lite"):
        logger.info("risk_gate.escalation", risk_level=_risk_level, original_tier=tier)
        classification.reasoning += f" [risk_gate={_risk_level}]"

    routes = classification.routes.copy()

    # Apply user filter if specified
    if provider_filter and provider_filter.lower() in ["aws", "gcp", "azure"]:
        if provider_filter.lower() not in classification.providers:
            classification.providers.append(provider_filter.lower())

    detected_provider = None
    if len(classification.providers) == 1:
        detected_provider = classification.providers[0].lower()
    if emit_event and detected_provider:
        await emit_event({"provider_detected": detected_provider})

    rag_results: list[dict[str, Any]] = []
    web_results: list[dict[str, Any]] = []
    internet_results: list[dict[str, Any]] = []
    pricing_data: list[dict[str, Any]] = []
    calc_results: dict[str, Any] | None = None

    # 2. Parallel, Fault-Isolated Context Gathering with return_exceptions=True
    should_search = (
        pipeline.settings.always_web_search
        or classification.needs_internet
        or "INTERNET" in routes
        or classification.intent in _PROBLEM_SOLVING_INTENTS
        or classification.intent in _FRESHNESS_INTENTS
    )

    async def _safe_internet() -> list[dict[str, Any]]:
        t0_web = time.perf_counter()
        results: list[dict[str, Any]] = []
        if not should_search:
            return results

        if emit_event:
            if getattr(pipeline.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("search", "Searching live internet", "start"))
            else:
                await emit_event({"status": "Searching documentation & live internet..."})

        budget_web = getattr(pipeline.settings, "budget_web_search_ms", 2000) / 1000.0
        try:
            search_query = query
            search_domains = None
            if provider_filter and provider_filter.lower() in ("aws", "gcp", "azure"):
                pf = provider_filter.lower()
                search_domains = _WEB_SEARCH_DOMAIN_MAP.get(pf)
                if pf not in query.lower():
                    search_query = f"{pf.upper()} {query}"

            async def _do_search():
                if classification.intent in _PROBLEM_SOLVING_INTENTS:
                    return await pipeline.web_search.search_for_problem_solving(
                        query=search_query, max_results=pipeline.settings.web_search_max_results
                    )
                else:
                    return await pipeline.web_search.search(
                        query=search_query, max_results=pipeline.settings.web_search_max_results, domains=search_domains
                    )

            search_items = await asyncio.wait_for(_do_search(), timeout=budget_web)
            for item in search_items:
                results.append({
                    "title": item.title,
                    "url": item.url,
                    "content": item.content,
                    "source_engine": item.source_engine,
                })
            if results and getattr(pipeline.settings, "enable_context_validator", True):
                from core.context_validator import ContextValidator
                _cv = ContextValidator(pipeline.settings)
                _cv.validate_web_results(results, tier=tier)
            if "INTERNET" not in routes:
                routes.append("INTERNET")
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("Web search stage timed out against budget")
            PIPELINE_TIMEOUTS_TOTAL.labels(component="internet_search").inc()
        except (ConnectionError, OSError) as e:
            logger.warning("Internet search connection error", error=str(e))
        except Exception as e:
            logger.warning("Internet search error", error=str(e))
        finally:
            web_sec = time.perf_counter() - t0_web
            timings["web_search"] = round(web_sec * 1000, 2)
            RAG_STAGE_DURATION_SECONDS.labels(stage="web_search", tier=tier).observe(web_sec)
            if emit_event and getattr(pipeline.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("search", "Search complete", "complete", elapsed_ms=timings["web_search"]))
        return results

    async def _safe_rag() -> tuple[list[dict[str, Any]], str]:
        t0_rag = time.perf_counter()
        results: list[dict[str, Any]] = []
        fb_pass = "none"
        if "RAG" not in routes:
            return results, fb_pass

        if emit_event:
            if getattr(pipeline.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("retrieve", "Consulting documentation & service cards", "start"))
            else:
                await emit_event({"status": "Consulting RAG vector database & service cards..."})

        budget_rag = (
            getattr(pipeline.settings, "budget_retrieval_ms", 300)
            + getattr(pipeline.settings, "budget_reranking_ms", 400)
        ) / 1000.0

        try:
            from metrics import CACHE_CASCADE_HITS

            cached_candidates = await get_cached_retrieval_result(query, provider_filter, pipeline.settings)
            # ③ Hybrid retrieval cache — multi-turn queries retrieve under the
            # contextual rewrite, so consult that representation's key too.
            if cached_candidates is None and chat_history and getattr(
                pipeline.settings, "enable_contextual_query_rewriting", True
            ):
                try:
                    from retrieval.query_processor import rewrite_contextual_query

                    _ctx = rewrite_contextual_query(query, chat_history=chat_history)
                    _eff = (_ctx.effective_search_query or "").strip()
                    if _eff and _eff.lower() != query.strip().lower():
                        cached_candidates = await get_cached_retrieval_result(
                            _eff, provider_filter, pipeline.settings
                        )
                except Exception as rewrite_err:
                    logger.debug("retrieval_cache.rewrite_lookup_skipped error=%s", rewrite_err)

            pf_tag = provider_filter or "all"
            neg_hash = _hashlib.sha256(f"{query}:{pf_tag}".encode("utf-8")).hexdigest()[:24]
            neg_cache_key = f"rag:v2:negative:{neg_hash}"
            is_negative = await redis_client.get(neg_cache_key) if redis_client else None

            if is_negative:
                top_results = []
                fb_pass = "negative_cached"
            elif cached_candidates is not None:
                from retrieval.hybrid import RetrievalResult

                top_results = [
                    RetrievalResult(
                        chunk_id=str(r.get("chunk_id", "")),
                        text=str(r.get("text", r.get("content", ""))),
                        score=float(r.get("score", 1.0)),
                        metadata=dict(r.get("metadata", {}) or {}),
                    )
                    if isinstance(r, dict)
                    else r
                    for r in cached_candidates
                ]
                fb_pass = "cached"
                CACHE_CASCADE_HITS.labels(tier="Free", layer="retrieval").inc()
            else:
                adaptive_top_k = pipeline.settings.retrieval_top_k
                if (
                    getattr(pipeline.settings, "enable_adaptive_top_k", True)
                    and getattr(pipeline.settings, "adaptive_retrieval_top_k", True)
                    and getattr(classification, "confidence", 0.0) >= 0.85
                    and len(getattr(classification, "services", [])) == 1
                ):
                    adaptive_top_k = max(20, pipeline.settings.retrieval_top_k // 2)

                retriever, reranker = pipeline.get_retrieval()
                top_results, fb_pass = await asyncio.wait_for(
                    _retrieve_with_fallback(
                        retriever,
                        reranker,
                        query,
                        provider_filter,
                        classification,
                        pipeline.settings,
                        top_k_override=adaptive_top_k,
                        chat_history=chat_history,
                    ),
                    timeout=budget_rag,
                )
                if not top_results and redis_client:
                    await redis_client.set(neg_cache_key, "1", ex=30)
                elif top_results:
                    await set_cached_retrieval_result(query, top_results, provider_filter, pipeline.settings, ttl_seconds=21600)
                    # Cache under the rewritten representation too so future
                    # multi-turn lookups hit either key.
                    if chat_history and getattr(pipeline.settings, "enable_contextual_query_rewriting", True):
                        try:
                            from retrieval.query_processor import rewrite_contextual_query as _rq

                            _wctx = _rq(query, chat_history=chat_history)
                            _weff = (_wctx.effective_search_query or "").strip()
                            if _weff and _weff.lower() != query.strip().lower():
                                await set_cached_retrieval_result(
                                    _weff, top_results, provider_filter, pipeline.settings, ttl_seconds=21600
                                )
                        except Exception:
                            pass

            if getattr(pipeline.settings, "enable_context_validator", True) and top_results:
                from core.context_validator import ContextValidator
                _cv = ContextValidator(pipeline.settings)
                chunk_vr = _cv.validate_retrieved_chunks(top_results, tier=tier)
                # staleness_flags are carried forward in result metadata for CQC
                for chunk in top_results:
                    cid = chunk.chunk_id if hasattr(chunk, "chunk_id") else (chunk.get("chunk_id", "") if isinstance(chunk, dict) else "")
                    c_meta = chunk.metadata if hasattr(chunk, "metadata") else (chunk.get("metadata", {}) if isinstance(chunk, dict) else {})
                    if cid in chunk_vr.staleness_flags:
                        c_meta["stale"] = True
                if not chunk_vr.is_valid:
                    logger.warning("context_validator.chunks_rejected", issues=chunk_vr.issues, tier=tier)
                    top_results = []

            # Lite Layer 3 — conditional extractive/contextual compression +
            # fixed evidence-aware ordering (stale demotion, score order).
            if top_results and getattr(pipeline.settings, "enable_lite_evidence_compression", True):
                try:
                    from generation.compression import compress_evidence
                    from generation.assembly import order_fixed_evidence

                    top_results = compress_evidence(
                        query, top_results, policy="lite", settings=pipeline.settings
                    )
                    top_results = order_fixed_evidence(top_results)
                except Exception as comp_err:
                    logger.warning("lite_evidence_compression.failed", error=str(comp_err))

            for res in top_results:
                m_data = res.metadata if hasattr(res, "metadata") else (res.get("metadata", {}) if isinstance(res, dict) else {})
                txt = res.text if hasattr(res, "text") else (res.get("content", "") if isinstance(res, dict) else "")
                cid = res.chunk_id if hasattr(res, "chunk_id") else (res.get("chunk_id", "") if isinstance(res, dict) else "")
                results.append({
                    "chunk_id": cid,
                    "provider": m_data.get("provider", "cloud"),
                    "service": m_data.get("service", ""),
                    "section": m_data.get("section", ""),
                    "url": m_data.get("url", ""),
                    "content": txt,
                    "title": m_data.get("title", ""),
                    "stale": m_data.get("stale", False),
                    "parent_chunk_id": m_data.get("parent_chunk_id"),
                    "hierarchy_level": m_data.get("hierarchy_level", 1),
                    "is_coalesced_parent": m_data.get("is_coalesced_parent", False),
                })
            RAG_RESULTS_COUNT.observe(len(results))
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("RAG retrieval stage timed out against budget")
            PIPELINE_TIMEOUTS_TOTAL.labels(component="retrieval").inc()
            fb_pass = "timeout_fallback"
        except (ConnectionError, OSError) as e:
            logger.warning("RAG retrieval connection error", error=str(e))
            fb_pass = "connection_error_fallback"
        except Exception as e:
            logger.warning("RAG retrieval fallback", error=str(e))
            fb_pass = "error_fallback"
        finally:
            rag_sec = time.perf_counter() - t0_rag
            timings["retrieval"] = round(rag_sec * 1000, 2)
            RAG_STAGE_DURATION_SECONDS.labels(stage="retrieval", tier=tier).observe(rag_sec)
            if emit_event and getattr(pipeline.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("retrieve", "Documentation retrieved", "complete", elapsed_ms=timings["retrieval"]))
        return results, fb_pass

    async def _safe_pricing() -> list[dict[str, Any]]:
        t0_price = time.perf_counter()
        results: list[dict[str, Any]] = []
        if "PRICING" not in routes:
            return results
        try:
            results = await fetch_cloud_pricing(
                query=query,
                providers=classification.get("providers", []),
                aws_tool=pipeline.aws_pricing,
                azure_tool=pipeline.azure_pricing,
                gcp_tool=pipeline.gcp_pricing,
                timeout=2.5,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("Pricing lookup timed out")
            PIPELINE_TIMEOUTS_TOTAL.labels(component="pricing").inc()
        except Exception as e:
            logger.warning("Pricing lookup fallback", error=str(e))
        finally:
            timings["pricing"] = round((time.perf_counter() - t0_price) * 1000, 2)
        return results

    async def _safe_web() -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if "WEB" not in routes:
            return results
        try:
            web_items = await asyncio.wait_for(
                pipeline.web_search.search(query=query, max_results=3),
                timeout=float(getattr(pipeline.settings, "web_search_timeout_seconds", 5.0)),
            )
            for item in web_items:
                results.append({
                    "title": item.title,
                    "url": item.url,
                    "snippet": item.content
                })
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("Web search tool execution timed out")
            PIPELINE_TIMEOUTS_TOTAL.labels(component="web_search").inc()
        except Exception as e:
            logger.warning("Web search tool execution error", error=str(e))
        return results

    # Run context gathering with complete isolation and partial failure resilience
    gather_results = await asyncio.gather(
        _safe_internet(),
        _safe_rag(),
        _safe_pricing(),
        _safe_web(),
        return_exceptions=True,
    )

    # Re-raise CancelledError immediately to uphold cooperative cancellation
    for r in gather_results:
        if isinstance(r, asyncio.CancelledError):
            raise r

    failed = [r for r in gather_results if isinstance(r, BaseException)]
    if failed:
        logger.warning(
            "pipeline_partial_failure",
            failed_count=len(failed),
            errors=[type(e).__name__ for e in failed],
        )

    # Safely unpack participants
    internet_items = gather_results[0] if not isinstance(gather_results[0], BaseException) else []
    rag_items, rag_fallback = gather_results[1] if not isinstance(gather_results[1], BaseException) else ([], "error_fallback")
    pricing_items = gather_results[2] if not isinstance(gather_results[2], BaseException) else []
    web_items = gather_results[3] if not isinstance(gather_results[3], BaseException) else []

    internet_results.extend(internet_items)
    rag_results.extend(rag_items)
    fallback_pass = rag_fallback
    pricing_data.extend(pricing_items)

    # Register citations deterministically (chunk_id links sources to the
    # retrieval chunk for claim-level source-to-chunk attribution)
    for r in rag_results:
        citation_mgr.register_source(
            source_type="rag",
            url=r.get("url", "https://docs.aws.amazon.com/"),
            provider=r.get("provider", "cloud"),
            service=r.get("service", ""),
            title=r.get("title", "Cloud Documentation"),
            section=r.get("section", ""),
            chunk_id=r.get("chunk_id") or None,
        )

    for item in internet_results:
        citation_mgr.register_source(
            source_type="internet",
            url=item.get("url", ""),
            provider=provider_filter or "web",
            service="",
            title=item.get("title", ""),
            section=f"Internet Search ({item.get('source_engine', 'web')})"
        )

    for item in web_items:
        if not any(r.get("url") == item.get("url") for r in internet_results):
            web_results.append(item)
            citation_mgr.register_source(
                source_type="web",
                url=item.get("url", ""),
                provider="web",
                service="",
                title=item.get("title", ""),
                section="Web Search Result"
            )

    # 6. Calculator route
    if "CALCULATOR" in routes:
        calc_results = _calculator_context(query)

    return (
        classification,
        routes,
        rag_results,
        web_results,
        internet_results,
        pricing_data,
        calc_results,
        citation_mgr,
        timings,
        fallback_pass,
    )


async def resolve_chat_history(
    user_id: int | None,
    session_id: str | None,
    limit: int | None = None,
    emit_event: EmitEvent | None = None,
    tier: str = "Free",
) -> tuple[list[dict], str | None]:
    """
    Retrieve chat history using configured max_history_messages,
    trim according to history_token_budget (tier-aware), and trigger rolling compaction if enabled.
    Returns (trimmed_history, session_summary).
    """
    if not user_id and not session_id:
        return [], None

    effective_limit = limit or pipeline.settings.max_history_messages
    raw_history = await get_fast_chat_history(user_id, session_id, limit=effective_limit)

    tier_norm = (tier or "Free").capitalize()
    if tier_norm == "Pro":
        token_budget = getattr(pipeline.settings, "history_token_budget_pro", 4000)
    elif tier_norm in ("Max", "Developer"):
        token_budget = getattr(pipeline.settings, "history_token_budget_max", 8000)
    else:
        token_budget = getattr(pipeline.settings, "history_token_budget", 1500)

    kept_history, dropped_history = trim_history_by_tokens(raw_history, max_tokens=token_budget, tier=tier_norm)

    # Retrieve cached or persistent session summary
    session_summary = await get_cached_session_summary(user_id, session_id)

    # Trigger rolling compaction if older turns were dropped and threshold reached
    if (
        dropped_history
        and getattr(pipeline.settings, "history_compaction_enabled", True)
        and len(raw_history) >= getattr(pipeline.settings, "history_compaction_threshold_messages", 10)
    ):
        if emit_event:
            try:
                if getattr(pipeline.settings, "enable_structured_stage_events", True):
                    await emit_event(_stage_event("compacting", "Compacting older chat history...", "start"))
                else:
                    await emit_event({"status": "Compacting older chat history..."})
            except Exception:
                pass

        new_summary = await compact_history_turns(dropped_history, session_summary)
        if new_summary:
            session_summary = new_summary
            await set_cached_session_summary(user_id, session_id, new_summary)

        if emit_event and getattr(pipeline.settings, "enable_structured_stage_events", True):
            try:
                await emit_event(_stage_event("compacting", "Session history compacted", "complete"))
            except Exception:
                pass

    return kept_history, session_summary


def _build_pipeline_messages(
    query: str,
    classification,
    rag_results: list[dict[str, Any]],
    web_results: list[dict[str, Any]],
    internet_results: list[dict[str, Any]],
    pricing_data: list[dict[str, Any]],
    calc_results: dict[str, Any] | None,
    provider_filter: str | None,
    chat_history: list[dict] | None,
    attachment_texts: list[dict[str, Any]] | None = None,
    max_context_tokens: int | None = None,
    session_summary: str | None = None,
    user_memories: list[dict[str, Any]] | None = None,
    tier: str = "Free",
    model: str = "unknown",
    policy_digest: str | None = None,
    rag_mode: str | None = None,
    **kwargs: Any,
) -> list[dict]:
    """Assemble the LLM message list from gathered context + history + attachments + summary + user memories."""
    cls_dump = classification.model_dump() if hasattr(classification, "model_dump") else classification
    messages = pipeline.context_builder.build_context(
        query=query,
        classification=cls_dump,
        rag_results=rag_results,
        web_results=web_results,
        internet_results=internet_results,
        pricing_data=pricing_data,
        calc_results=calc_results,
        provider_filter=provider_filter,
        attachment_texts=attachment_texts,
        max_context_tokens=max_context_tokens,
        user_memories=user_memories,
        tier=tier,
        model=model,
        policy_digest=policy_digest,
        rag_mode=rag_mode,
        **kwargs,
    )
    system_msg = messages[0]
    user_msg = messages[1]

    if attachment_texts:
        user_msg["attachments"] = attachment_texts

    if session_summary:
        summary_annotation = f"<session_summary>\n{session_summary.strip()}\n</session_summary>\n\n"
        if getattr(pipeline.settings, "enable_kv_cache_prefix_optimization", True):
            # Invariant prefix: keep system_msg untouched so KV prompt caching hits 100%
            user_msg = {"role": "user", "content": summary_annotation + user_msg["content"]}
        else:
            system_msg = {"role": "system", "content": system_msg["content"] + "\n\n" + summary_annotation.strip()}

    if chat_history:
        messages = [system_msg] + chat_history + [user_msg]
    else:
        messages = [system_msg, user_msg]
    return messages


async def execute_agent_pipeline(
    query: str,
    provider_filter: str | None = None,
    tier: str = "Free",
    chat_history: list[dict] | None = None,
    attachment_texts: list[dict[str, Any]] | None = None,
    emit_event: EmitEvent | None = None,
    stream: bool = False,
    thinking_level: str | None = None,
    session_summary: str | None = None,
    user_memories: list[dict[str, Any]] | None = None,
) -> PipelineResult:
    """Execute the full agent pipeline with per-stage timings, token budgeting, and single-flight lock integration."""

    # Normalise tier string to canonical values before any dispatch
    _t = (tier or "Free").strip().lower()
    if _t in ("max", "apex", "developer", "admin"):
        tier = "Max"
    elif _t in ("pro", "core"):
        tier = "Pro"
    elif _t in ("lite",):
        tier = "Free"

    # ── Small-Talk Gate (Layer 1 regex + Layer 2 cosine) ─────────────────────
    # Runs before the semantic cache so a greeting never pays for retrieval or
    # the full pipeline. Shares one query embedding with the cache lookup below
    # so a first-turn greeting costs a single embedding call.
    _gate_candidate = (
        getattr(pipeline.settings, "smalltalk_gate_enabled", True)
        and not attachment_texts
        and len(query.strip()) <= getattr(pipeline.settings, "smalltalk_max_query_chars", 120)
    )
    # Apex adaptive cache router: when enabled, cache-level decisions for Max
    # requests are made post-transform inside AdaptiveAdvancedRAGPipeline, so
    # the pre-dispatch semantic layer is bypassed for Max.
    _max_cache_router = (
        tier == "Max" and getattr(pipeline.settings, "enable_adaptive_cache_router", True)
    )
    _semcache_candidate = (
        not _max_cache_router
        and getattr(pipeline.settings, "enable_semantic_cache", True)
        and getattr(pipeline.settings, "semantic_cache_enabled", True)
        and not attachment_texts
        and not (chat_history and len(chat_history) > 1)
    )
    # Lite Layer 1 — exact answer cache (L1 memory → Redis) consulted before
    # any embedding work. History is differentiated via the same hash the
    # endpoint uses, so multi-turn queries cannot false-hit.
    _exact_candidate = (
        tier == "Free"
        and getattr(pipeline.settings, "enable_lite_exact_cache", True)
        and not attachment_texts
    )

    def _history_cache_hash(history: list[dict] | None) -> str | None:
        if not history:
            return None
        return _hashlib.sha256(
            "|".join(f"{m.get('role', '')}:{m.get('content', '')[:500]}" for m in history).encode()
        ).hexdigest()[:16]

    _history_hash = _history_cache_hash(chat_history)

    query_embedding: list[float] | None = None
    if _gate_candidate or _semcache_candidate:
        try:
            query_embedding = await asyncio.wait_for(
                pipeline.get_embeddings().embed_query(query),
                timeout=float(getattr(pipeline.settings, "embedding_timeout_seconds", 2.0)),
            )
        except Exception as e:
            logger.warning("query_embedding.unavailable", error=str(e))
            query_embedding = None

    if _gate_candidate:
        from router.smalltalk_gate import SmallTalkGate

        gate = SmallTalkGate(embedder=pipeline.get_embeddings() if query_embedding else None)
        try:
            if await gate.is_smalltalk(query, query_embedding):
                logger.info("smalltalk.bypass", tier=tier, query=query[:60])
                return await run_smalltalk_pipeline(
                    query=query,
                    tier=tier,
                    stream=stream,
                    thinking_level=thinking_level,
                    emit_event=emit_event,
                    chat_history=chat_history,
                    session_summary=session_summary,
                    user_memories=user_memories,
                )
        except Exception as e:
            logger.warning("smalltalk.gate_error", error=str(e))

    # ── Lite Cache Cascade: ① Exact → ② Semantic → ③ Hybrid Retrieval ────────
    # Feedback-driven policy: a thumbs-down penalty record bypasses both
    # answer-cache layers for this query (retrieval cache is unaffected).
    _feedback_penalty = False
    if (_exact_candidate or _semcache_candidate) and getattr(
        pipeline.settings, "enable_feedback_cache_policy", True
    ):
        try:
            from core.cache_policy import get_query_feedback_policy

            _policy = await get_query_feedback_policy(query)
            if _policy and _policy.get("rating") == -1:
                _feedback_penalty = True
                logger.info("cache_policy.answer_layers_bypassed", query=query[:60])
        except Exception as e:
            logger.warning("cache_policy.policy_lookup_error", error=str(e))

    # ── ① Exact Cache Check ───────────────────────────────────────────────────
    if _exact_candidate and not _feedback_penalty:
        try:
            _exact_ans = await get_cached_answer(
                query=query,
                provider_filter=provider_filter,
                history_hash=_history_hash,
            )
            if isinstance(_exact_ans, dict) and _exact_ans.get("answer"):
                from metrics import CACHE_CASCADE_HITS

                CACHE_CASCADE_HITS.labels(tier="Free", layer="exact").inc()
                logger.info("exact_cache.hit", query=query[:60])
                if isinstance(_exact_ans, dict):
                    raw_exact = _exact_ans.get("answer", "")
                    ans_text = str(raw_exact.get("answer", "") if isinstance(raw_exact, dict) else (raw_exact or ""))
                    ans_sources = _exact_ans.get("sources", []) if isinstance(_exact_ans.get("sources"), list) else []
                    ans_model = str(_exact_ans.get("model", "exact-cache") or "exact-cache")
                else:
                    ans_text = str(_exact_ans or "")
                    ans_sources = []
                    ans_model = "exact-cache"

                async def _exact_cached_stream() -> AsyncGenerator[str, None]:
                    chunk_size = 32
                    for i in range(0, len(ans_text), chunk_size):
                        yield ans_text[i:i + chunk_size]
                        await asyncio.sleep(0.005)

                return PipelineResult(
                    answer=ans_text,
                    token_stream=_exact_cached_stream() if stream else None,
                    routes=["RAG"],
                    confidence=1.0,
                    classification={"intent": "exact_cached"},
                    sources=ans_sources,
                    model_used=ans_model,
                    pipeline_timings={"exact_cache": 1.0},
                    pipeline_type="exact_cache",
                )
        except Exception as e:
            logger.warning("exact_cache.lookup_error", error=str(e))

    # ── ② Semantic Cache Check ────────────────────────────────────────────────
    if _semcache_candidate and not _feedback_penalty:
        sem_cache = get_semantic_cache()
        try:
            if query_embedding is not None:
                cached_answer_key, sim = sem_cache.get(
                    query_embedding, threshold=pipeline.settings.semantic_cache_threshold
                )
                if cached_answer_key:
                    cached_ans = await get_cached_answer(
                        query=cached_answer_key, provider_filter=provider_filter
                    )
                    if cached_ans:
                        from metrics import CACHE_CASCADE_HITS

                        SEMANTIC_CACHE_HITS.inc()
                        CACHE_CASCADE_HITS.labels(
                            tier="Free" if tier == "Free" else tier, layer="semantic"
                        ).inc()
                        logger.info("semantic_cache.hit", similarity=round(sim, 3), query=query[:60])
                        if isinstance(cached_ans, dict):
                            raw_ans = cached_ans.get("answer", "")
                            ans_text = str(raw_ans.get("answer", "") if isinstance(raw_ans, dict) else (raw_ans or ""))
                            ans_sources = cached_ans.get("sources", []) if isinstance(cached_ans.get("sources"), list) else []
                            ans_model = str(cached_ans.get("model", "semantic-cache") or "semantic-cache")
                        else:
                            ans_text = str(cached_ans or "")
                            ans_sources = []
                            ans_model = "semantic-cache"

                        if stream:
                            async def _cached_stream() -> AsyncGenerator[str, None]:
                                chunk_size = 32
                                for i in range(0, len(ans_text), chunk_size):
                                    yield ans_text[i:i + chunk_size]
                                    await asyncio.sleep(0.005)
                            return PipelineResult(
                                answer=ans_text,
                                token_stream=_cached_stream(),
                                routes=["RAG"],
                                confidence=0.98,
                                classification={"intent": "semantic_cached"},
                                sources=ans_sources,
                                model_used=ans_model,
                                pipeline_timings={"semantic_cache": 1.0},
                                pipeline_type="semantic_cache",
                            )
                        return PipelineResult(
                            answer=ans_text,
                            token_stream=None,
                            routes=["RAG"],
                            confidence=0.98,
                            classification={"intent": "semantic_cached"},
                            sources=ans_sources,
                            model_used=ans_model,
                            pipeline_timings={"semantic_cache": 1.0},
                            pipeline_type="semantic_cache",
                        )
        except Exception as e:
            logger.warning("semantic_cache.lookup_error", error=str(e))

    # ── Tier-Based Pipeline Dispatch ──────────────────────────────────────────
    # Pro tier  → Agentic RAG  (Plan & Route, Grade Evidence, Self-Critique)
    # Max / Dev → Adaptive RAG (Transform Query, HyDE, Rerank & Compress)
    # Free      → falls through to existing _gather_pipeline_context() path
    # ─────────────────────────────────────────────────────────────────────────
    if tier == "Pro":
        from retrieval.agentic_rag import AgenticRAGPipeline
        res = await AgenticRAGPipeline().run(
            query=query,
            provider_filter=provider_filter,
            tier=tier,
            chat_history=chat_history,
            attachment_texts=attachment_texts,
            stream=stream,
            thinking_level=thinking_level,
            emit_event=emit_event,
            session_summary=session_summary,
            user_memories=user_memories,
        )
        res.usage = dict(_last_gen_usage.get() or {})
        _has_attachments = bool(attachment_texts)
        _is_multiturn = bool(chat_history and len(chat_history) > 1)
        if (
            not _has_attachments
            and query_embedding
            and getattr(pipeline.settings, "enable_semantic_cache", True)
            and getattr(pipeline.settings, "semantic_cache_enabled", True)
        ):
            try:
                from core.cache_policy import skip_answer_cache_for_validation

                if not _is_multiturn:
                    get_semantic_cache().set(query_embedding, query)
                # Feedback-driven policy: validation-failed answers are never cached.
                _cache_ok = not skip_answer_cache_for_validation(res.validation, pipeline.settings)
                if res.answer and not stream and _cache_ok:
                    await set_cached_answer(
                        query=query,
                        response_payload={"answer": res.answer, "sources": res.sources, "model": res.model_used},
                        model=res.model_used,
                        provider_filter=provider_filter,
                        history_hash=_history_hash,
                        ttl_seconds=getattr(pipeline.settings, "redis_cache_ttl_seconds", 3600),
                    )
            except Exception:
                pass
        return res

    if tier in ("Max", "Developer", "Apex", "admin"):
        from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline
        res = await AdaptiveAdvancedRAGPipeline().run(
            query=query,
            provider_filter=provider_filter,
            tier=tier,
            chat_history=chat_history,
            attachment_texts=attachment_texts,
            stream=stream,
            thinking_level=thinking_level,
            emit_event=emit_event,
            session_summary=session_summary,
            user_memories=user_memories,
        )
        res.usage = dict(_last_gen_usage.get() or {})
        # Note: AdaptiveAdvancedRAGPipeline manages its own Stage 4 answer-cache
        # write (respecting adaptive cache router policy, feedback-boosted TTL,
        # attachment isolation, and Layer-4 validation gating).
        return res

    # ── Free Tier: context assembly and generation ────────────────────────────
    try:
        (
            classification,
            routes,
            rag_results,
            web_results,
            internet_results,
            pricing_data,
            calc_results,
            citation_mgr,
            stage_timings,
            fallback_pass,
        ) = await _gather_pipeline_context(query, provider_filter, tier, emit_event=emit_event, chat_history=chat_history)
    except asyncio.CancelledError:
        logger.info("pipeline_cancelled", query=query[:40], tier=tier, reason="client_disconnect")
        raise

    sources = [s.model_dump() for s in citation_mgr.get_sources()]
    result = PipelineResult(
        routes=routes,
        confidence=classification.confidence,
        classification=classification.model_dump(),
        sources=sources,
        pipeline_timings=stage_timings,
        fallback_pass=fallback_pass,
        pipeline_type="current_rag",
    )

    tier_prompt_budget = {
        "Free": getattr(pipeline.settings, "prompt_budget_free", pipeline.settings.context_tokens_free),
        "Pro": getattr(pipeline.settings, "prompt_budget_pro", pipeline.settings.context_tokens_pro),
        "Max": getattr(pipeline.settings, "prompt_budget_max", pipeline.settings.context_tokens_max),
    }.get(tier, getattr(pipeline.settings, "prompt_budget_free", 4000)) if getattr(pipeline.settings, "enable_context_budget", True) else None

    if tier_prompt_budget:
        has_attachments = bool(attachment_texts and len(attachment_texts) > 0)
        is_deep_workload = bool((chat_history and len(chat_history) >= 4))
        generator_model = getattr(pipeline.settings, "gemini_model_generator", None)
        tier_prompt_budget = calculate_effective_prompt_budget(
            tier_budget=tier_prompt_budget,
            model_name=generator_model,
            max_output_tokens=getattr(pipeline.settings, "chat_max_output_tokens", 4096),
            tier=tier,
            has_attachments=has_attachments,
            is_deep_workload=is_deep_workload,
        )

    t0_ctx = time.perf_counter()
    messages = _build_pipeline_messages(
        query=query,
        classification=classification,
        rag_results=rag_results,
        web_results=web_results,
        internet_results=internet_results,
        pricing_data=pricing_data,
        calc_results=calc_results,
        provider_filter=provider_filter,
        chat_history=chat_history,
        attachment_texts=attachment_texts,
        max_context_tokens=tier_prompt_budget,
        session_summary=session_summary,
        user_memories=user_memories,
        tier=tier,
        rag_mode="lite",
    )
    ctx_sec = time.perf_counter() - t0_ctx
    result.pipeline_timings["context_assembly"] = round(ctx_sec * 1000, 2)
    RAG_STAGE_DURATION_SECONDS.labels(stage="context_assembly", tier=tier).observe(ctx_sec)

    if stream:
        if emit_event:
            if getattr(pipeline.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("generate", "Synthesizing answer with AI...", "start"))
            else:
                await emit_event({"status": "Synthesizing answer with AI..."})

        async def token_stream() -> AsyncGenerator[str, None]:
            t0_gen = time.perf_counter()
            usage_info: dict[str, Any] = {}
            token_iter, provider_name = await generate_with_fallback(
                messages=messages, stream=True, tier=tier, thinking_level=thinking_level,
                usage_out=usage_info,
            )
            result.usage = usage_info
            result.model_used = provider_name
            async for token in token_iter:
                result.answer += token
                yield token
            result.answer = sanitize_model_output(result.answer)
            gen_sec = time.perf_counter() - t0_gen
            result.pipeline_timings["llm_generate"] = round(gen_sec * 1000, 2)
            RAG_STAGE_DURATION_SECONDS.labels(stage="llm_generate", tier=tier).observe(gen_sec)
            # Post-hoc Lite validation on the assembled stream (deterministic
            # only — the SSE contract is untouched, stored answer is annotated).
            try:
                from generation.validator import apply_lite_validation
                apply_lite_validation(result, query, rag_results, citation_mgr, pipeline.settings)
            except Exception as e:
                logger.warning("lite_validation.stream_wiring_error", error=str(e))
            _has_attachments = bool(attachment_texts)
            _is_multiturn = bool(chat_history and len(chat_history) > 1)
            if (
                not _has_attachments
                and query_embedding
                and getattr(pipeline.settings, "enable_semantic_cache", True)
                and getattr(pipeline.settings, "semantic_cache_enabled", True)
                and result.answer
            ):
                try:
                    from core.cache_policy import skip_answer_cache_for_validation

                    if not skip_answer_cache_for_validation(result.validation, pipeline.settings):
                        if not _is_multiturn:
                            get_semantic_cache().set(query_embedding, query)
                        await set_cached_answer(
                            query=query,
                            response_payload={"answer": result.answer, "sources": sources, "model": provider_name},
                            model=provider_name,
                            provider_filter=provider_filter,
                            history_hash=_history_hash,
                            ttl_seconds=getattr(pipeline.settings, "redis_cache_ttl_seconds", 3600),
                        )
                except Exception:
                    pass

        result.token_stream = token_stream()
        return result

    t0_gen = time.perf_counter()
    usage_info: dict[str, Any] = {}
    with LLM_LATENCY_SECONDS.labels(provider="chain", role="main").time():
        raw_answer, model_used = await generate_with_fallback(
            messages=messages, stream=False, tier=tier, thinking_level=thinking_level,
            usage_out=usage_info,
        )
    result.usage = usage_info
    gen_sec = time.perf_counter() - t0_gen
    result.pipeline_timings["llm_generate"] = round(gen_sec * 1000, 2)
    RAG_STAGE_DURATION_SECONDS.labels(stage="llm_generate", tier=tier).observe(gen_sec)

    result.answer = sanitize_model_output(raw_answer)
    result.model_used = model_used

    # Lite Layer 4 — validated grounded generation: deterministic cleanup,
    # claim-level evidence entailment, claim→source citation mapping, and
    # multi-dimensional output validation (zero added LLM latency).
    try:
        from generation.validator import apply_lite_validation
        apply_lite_validation(result, query, rag_results, citation_mgr, pipeline.settings)
    except Exception as e:
        logger.warning("lite_validation.wiring_error", error=str(e))

    _has_attachments = bool(attachment_texts)
    _is_multiturn = bool(chat_history and len(chat_history) > 1)
    if (
        not _has_attachments
        and query_embedding
        and getattr(pipeline.settings, "enable_semantic_cache", True)
        and getattr(pipeline.settings, "semantic_cache_enabled", True)
        and result.answer
    ):
        try:
            from core.cache_policy import skip_answer_cache_for_validation

            if not skip_answer_cache_for_validation(result.validation, pipeline.settings):
                if not _is_multiturn:
                    get_semantic_cache().set(query_embedding, query)
                await set_cached_answer(
                    query=query,
                    response_payload={"answer": result.answer, "sources": sources, "model": model_used},
                    model=model_used,
                    provider_filter=provider_filter,
                    history_hash=_history_hash,
                    ttl_seconds=getattr(pipeline.settings, "redis_cache_ttl_seconds", 3600),
                )
        except Exception:
            pass

    return result


async def run_smalltalk_pipeline(
    query: str,
    tier: str = "Free",
    stream: bool = False,
    thinking_level: str | None = None,
    emit_event: EmitEvent | None = None,
    chat_history: list[dict] | None = None,
    session_summary: str | None = None,
    user_memories: list[dict] | None = None,
    provider_filter: str | None = None,
) -> PipelineResult:
    """Direct LLM answer for greetings / small talk — no retrieval, no tools.

    Used by the small-talk gate at the top of ``execute_agent_pipeline`` and as
    the chitchat safety-net branch inside the Agentic / Adaptive RAG pipelines.
    Emits only the standard `generate` stage events; no retrieve/rerank/search/
    transform events ever fire on this path.
    """
    from llm.system_prompts import SMALLTALK_SYSTEM_PROMPT

    messages = [{"role": "system", "content": SMALLTALK_SYSTEM_PROMPT}]
    # A few recent turns keep follow-ups ("thanks!") coherent without pulling
    # the whole history/memories apparatus into the prompt.
    for msg in (chat_history or [])[-4:]:
        role = msg.get("role")
        if role in ("user", "assistant") and msg.get("content"):
            messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": query})

    result = PipelineResult(
        routes=[],
        confidence=1.0,
        classification={"intent": "chitchat", "routes": []},
        sources=[],
        pipeline_timings={"smalltalk": 0.0},
        fallback_pass="none",
        pipeline_type="smalltalk",
    )

    if stream:
        if emit_event and getattr(pipeline.settings, "enable_structured_stage_events", True):
            await emit_event(_stage_event("generate", "Synthesizing answer with AI...", "start"))

        async def token_stream() -> AsyncGenerator[str, None]:
            usage_info: dict[str, Any] = {}
            token_iter, provider_name = await generate_with_fallback(
                messages=messages, stream=True, tier=tier,
                thinking_level=thinking_level, usage_out=usage_info,
            )
            result.usage = usage_info
            result.model_used = provider_name
            async for token in token_iter:
                result.answer += token
                yield token
            result.answer = sanitize_model_output(result.answer)
            if emit_event and getattr(pipeline.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("generate", "Answer ready", "complete"))

        result.token_stream = token_stream()
        return result

    usage_info: dict[str, Any] = {}
    with LLM_LATENCY_SECONDS.labels(provider="chain", role="main").time():
        raw_answer, model_used = await generate_with_fallback(
            messages=messages, stream=False, tier=tier,
            thinking_level=thinking_level, usage_out=usage_info,
        )
    result.usage = usage_info
    result.answer = sanitize_model_output(raw_answer)
    result.model_used = model_used
    if emit_event and getattr(pipeline.settings, "enable_structured_stage_events", True):
        await emit_event(_stage_event("generate", "Answer ready", "complete"))
    return result


async def run_agent_workflow(
    query: str,
    provider_filter: str | None = None,
    tier: str = "Free",
    chat_history: list[dict] | None = None
) -> tuple[str, list[dict[str, Any]], list[str], float, dict[str, Any], str]:
    """Backward-compatible wrapper around `execute_agent_pipeline`.

    Returns:
        Tuple of (answer, sources, routes, confidence, classification, model_used)
    """
    result = await execute_agent_pipeline(
        query=query,
        provider_filter=provider_filter,
        tier=tier,
        chat_history=chat_history,
        stream=False,
    )
    return (
        result.answer,
        result.sources,
        result.routes,
        result.confidence,
        result.classification,
        result.model_used,
    )


# ─── Shared Endpoint Helpers ────────────────────────────────────────────────

def _count_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        return len(text) // 4


async def _load_attachment_texts(
    attachments: list[dict[str, Any]] | None,
    user_id: int | None = None,
) -> list[dict[str, Any]] | None:
    """Resolve attachment_id references from Redis into context-ready payloads with user isolation."""
    if not attachments:
        return None
    try:
        from file_processor import load_attachments_from_redis
        return await load_attachments_from_redis(attachments, user_id=user_id)
    except Exception as e:
        logger.warning("Failed to load attachments, continuing without them", error=str(e))
        return None


# ─── API Endpoints ──────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(payload: ChatRequest, request: Request) -> ChatResponse:
    """Non-streaming chat endpoint."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    session_id = payload.session_id
    request_id = payload.request_id or get_request_id(request)
    from db import get_token_usage, increment_token_usage, get_user_by_id, get_active_subscription, reserve_usage, release_reservation, settle_usage, QuotaExceeded
    from core.entitlements import resolve_entitlements
    user = await asyncio.to_thread(get_user_by_id, user_id) if user_id else None
    user_email = user.get("email") if user else None
    usage = await asyncio.to_thread(get_token_usage, user_id)
    subscription = await asyncio.to_thread(get_active_subscription, user_id) if user_id else None
    entitlements = resolve_entitlements(usage.get("tier"), subscription, user_email=user_email)

    # Chat rate limiting (Redis sliding window) - Developer/Unlimited tier enjoys unrestricted access
    settings = get_settings()
    is_developer_exempt = (entitlements.unlimited or entitlements.plan_key == "Developer") and getattr(settings, "enable_developer_unlimited_bypass", True)
    if not is_developer_exempt:
        if not await rate_limiter.allowed_async(f"chat:{user_id}", settings.chat_rate_limit_per_minute):
            raise HTTPException(status_code=429, detail="Chat rate limit exceeded. Please wait a moment.")

    # Mode validation (supports Apex, Core, Lite)
    raw_mode = (payload.mode or "Lite").strip().lower()
    if raw_mode in ("apex", "max"):
        requested_model = "Apex"
        requested_mode = "Max"
    elif raw_mode in ("core", "pro"):
        requested_model = "Core"
        requested_mode = "Pro"
    else:
        requested_model = "Lite"
        requested_mode = "Free"

    if not entitlements.unlimited:
        if "Apex" not in entitlements.allowed_models and requested_model == "Apex":
            if "Core" in entitlements.allowed_models:
                requested_model = "Core"
                requested_mode = "Pro"
            else:
                requested_model = "Lite"
                requested_mode = "Free"
        if "Core" not in entitlements.allowed_models and requested_model == "Core":
            requested_model = "Lite"
            requested_mode = "Free"

    from core.entitlements import get_allowed_thinking_for_model
    from llm.thinking import default_thinking_for_tier

    allowed_for_model = get_allowed_thinking_for_model(entitlements.model_tier, requested_model)
    effective_allowed = tuple(
        lvl for lvl in entitlements.allowed_thinking_levels if lvl in allowed_for_model
    ) or ("Low",)
    default_lvl = default_thinking_for_tier(requested_mode)
    thinking_level = clamp_thinking_level(
        payload.thinking_level or default_lvl, effective_allowed
    )
    thinking_profile = profile_for(thinking_level)

    # Token checking across all four quota windows
    limit_day = float('inf') if entitlements.tokens_day is None else entitlements.tokens_day
    limit_month = float('inf') if entitlements.tokens_month is None else entitlements.tokens_month
    limit_5h = float('inf') if entitlements.tokens_5h is None else entitlements.tokens_5h
    limit_week = float('inf') if entitlements.tokens_week is None else entitlements.tokens_week

    input_tokens = _count_tokens(payload.query)

    if (
        usage.get("tokens_used_day", 0) + input_tokens > limit_day
        or usage.get("tokens_used_month", 0) + input_tokens > limit_month
        or usage.get("tokens_used_5h", 0) + input_tokens > limit_5h
        or usage.get("tokens_used_week", 0) + input_tokens > limit_week
    ):
        CHAT_REQUESTS_TOTAL.labels(tier=entitlements.plan_key, model=requested_mode, status="quota_exceeded", pipeline_type="unknown").inc()
        raise HTTPException(status_code=429, detail="Token limit exceeded for your tier across active rolling windows.")

    try:
        # Reserve worst-case quota scaled by the thinking level multiplier.
        if user_id:
            try:
                await asyncio.to_thread(
                    reserve_usage,
                    user_id,
                    request_id,
                    entitlements,
                    int(input_tokens * 3 * thinking_profile.quota_multiplier),
                )
            except QuotaExceeded:
                CHAT_REQUESTS_TOTAL.labels(tier=entitlements.plan_key, model=requested_mode, status="quota_exceeded", pipeline_type="unknown").inc()
                raise HTTPException(status_code=429, detail="Token quota exhausted. Please wait for your quota to reset.")

        # Compute history hash for cache key differentiation
        history_for_hash = await get_fast_chat_history(user_id, session_id, limit=6)
        history_hash = hashlib.sha256(
            "|".join(f"{m.get('role', '')}:{m.get('content', '')[:500]}" for m in history_for_hash).encode()
        ).hexdigest()[:16] if history_for_hash else None

        # ── Check LLM Response Cache ─────────────────────────────────────────
        cached_data = await get_cached_llm_response(
            query=payload.query,
            model=requested_mode,
            provider_filter=payload.provider_filter,
            mode=requested_mode,
            history_hash=history_hash,
        )
        if cached_data:
            CACHE_HITS_TOTAL.labels(cache="llm_response").inc()
            answer = cached_data.get("answer", "")
            # Dual-write message history (Redis memory + Postgres durable table)
            await record_message_dual_write(user_id, session_id, "user", payload.query)
            await record_message_dual_write(user_id, session_id, "assistant", answer)

            output_tokens = _count_tokens(answer)
            total_tokens = input_tokens + output_tokens
            if user_id:
                await asyncio.to_thread(increment_token_usage, user_id, total_tokens)
                # Settle quota reservation with actual usage. Cached answers cost
                # nothing upstream, so the estimated cost is zero.
                try:
                    await asyncio.to_thread(
                        settle_usage, user_id, request_id, entitlements,
                        model=cached_data.get('model_used', requested_mode),
                        input_tokens=input_tokens, output_tokens=output_tokens, status="settled",
                        estimated_cost=0.0,
                    )
                except Exception as settle_err:
                    logger.warning("Failed to settle cached usage", error=str(settle_err))

            TOKEN_USAGE_TOTAL.labels(tier=entitlements.plan_key, model=cached_data.get('model_used', requested_mode)).inc(total_tokens)
            CHAT_REQUESTS_TOTAL.labels(tier=entitlements.plan_key, model=requested_mode, status="cache_hit", pipeline_type="cached").inc()
            return ChatResponse(
                answer=answer,
                sources=cached_data.get("sources", []),
                routes_used=cached_data.get("routes_used", []),
                confidence=cached_data.get("confidence", 1.0),
                query_classification=cached_data.get("query_classification", {}),
                model_used=f"{cached_data.get('model_used', requested_mode)} (cached)",
                pipeline_timings={"cached": 0.0},
                fallback_pass="cached",
            )

        CACHE_MISSES_TOTAL.labels(cache="llm_response").inc()

        # Single-flight lock acquisition
        answer_hash = _hashlib.sha256(
            f"{requested_mode}:{payload.provider_filter or 'all'}:{payload.query.strip().lower()}:{history_hash or 'none'}".encode()
        ).hexdigest()[:24]
        lock_key = f"rag:v2:lock:{answer_hash}"
        lock_token = secrets.token_hex(8)
        lock_acquired = False

        if redis_client:
            lock_acquired = await redis_client.acquire_lock(lock_key, lock_token, ttl_seconds=20)
            if lock_acquired:
                REDIS_LOCK_ACQUIRED.inc()
            else:
                REDIS_LOCK_WAITED.inc()
                for _ in range(15):
                    await asyncio.sleep(1.0)
                    cached_data = await get_cached_llm_response(
                        query=payload.query,
                        model=requested_mode,
                        provider_filter=payload.provider_filter,
                        mode=requested_mode,
                        history_hash=history_hash,
                    )
                    if cached_data:
                        answer = cached_data.get("answer", "")
                        await record_message_dual_write(user_id, session_id, "user", payload.query)
                        await record_message_dual_write(user_id, session_id, "assistant", answer)
                        # Bill and settle like a normal cache hit — otherwise this
                        # early return orphans the quota reservation until the reaper.
                        output_tokens = _count_tokens(answer)
                        total_tokens = input_tokens + output_tokens
                        if user_id:
                            await asyncio.to_thread(increment_token_usage, user_id, total_tokens)
                            try:
                                await asyncio.to_thread(
                                    settle_usage, user_id, request_id, entitlements,
                                    model=cached_data.get('model_used', requested_mode),
                                    input_tokens=input_tokens, output_tokens=output_tokens,
                                    status="settled", estimated_cost=0.0,
                                )
                            except Exception as settle_err:
                                logger.warning("Failed to settle lock-wait usage", error=str(settle_err))
                        return ChatResponse(
                            answer=answer,
                            sources=cached_data.get("sources", []),
                            routes_used=cached_data.get("routes_used", []),
                            confidence=cached_data.get("confidence", 1.0),
                            query_classification=cached_data.get("query_classification", {}),
                            model_used=f"{cached_data.get('model_used', requested_mode)} (cached)",
                            pipeline_timings={"lock_waited": 1000.0},
                            fallback_pass="lock_cached",
                        )

        try:
            is_first_prompt = False
            if user_id and session_id:
                existing_history = await get_fast_chat_history(user_id, session_id, limit=1)
                is_first_prompt = (len(existing_history) == 0)

            history, session_summary = await resolve_chat_history(user_id, session_id, tier=entitlements.plan_key)
            user_memories = None
            if user_id and getattr(entitlements, "has_user_memory", False):
                user_memories = await get_user_memories_cached(user_id)
                for fact in extract_durable_user_facts(payload.query):
                    await upsert_user_memory_cached(
                        user_id=user_id,
                        key=fact["key"],
                        value=fact["value"],
                        category=fact["category"],
                    )

            attachment_texts = await _load_attachment_texts(payload.attachments, user_id=user_id)

            result = await execute_agent_pipeline(
                query=payload.query,
                provider_filter=payload.provider_filter,
                tier=requested_mode,
                chat_history=history,
                attachment_texts=attachment_texts,
                stream=False,
                thinking_level=thinking_level,
                session_summary=session_summary,
                user_memories=user_memories,
            )
            raw_answer, sources, routes = result.answer, result.sources, result.routes
            confidence, classification, model_used = result.confidence, result.classification, result.model_used

            # Separate reasoning (<think>) content from the visible answer.
            thinking_text, answer = split_thinking(raw_answer)
            thinking_tokens = _count_tokens(thinking_text) if thinking_text else 0
            thinking_summary = thinking_text[:500].strip() if thinking_text else ""

            logger.info(
                "chat.pipeline_timings",
                request_id=request_id,
                user_id=user_id,
                session_id=session_id,
                model=model_used,
                tier=entitlements.plan_key,
                routes=routes,
                input_tokens=input_tokens,
                thinking_level=thinking_level,
                thinking_tokens=thinking_tokens,
                fallback_pass=result.fallback_pass,
                timings=result.pipeline_timings,
            )

            # Save messages to history (dual-write to fast Redis + durable Postgres)
            await record_message_dual_write(user_id, session_id, "user", payload.query)
            await record_message_dual_write(user_id, session_id, "assistant", answer)

            # Cache LLM Response for subsequent identical queries
            await set_cached_llm_response(
                query=payload.query,
                response_payload={
                    "answer": answer,
                    "sources": [s if isinstance(s, dict) else s.model_dump() for s in sources] if sources else [],
                    "routes_used": routes,
                    "confidence": confidence,
                    "query_classification": classification,
                    "model_used": model_used,
                },
                model=requested_mode,
                provider_filter=payload.provider_filter,
                mode=requested_mode,
                history_hash=history_hash,
            )

            if user_id and session_id:
                try:
                    from db import get_session_title, set_session_title
                    current_title = None
                    if not is_first_prompt:
                        current_title = await asyncio.to_thread(get_session_title, user_id, session_id)
                    generated_title = await pipeline.generate_chat_title(
                        payload.query, answer, requested_mode, current_title=current_title
                    )
                    if generated_title and (is_first_prompt or generated_title != current_title):
                        await asyncio.to_thread(set_session_title, user_id, session_id, generated_title)
                except Exception as title_err:
                    logger.warning("Failed setting chat title", error=str(title_err))

            # Count output tokens (answer + reasoning both consume quota) and
            # bill the TRUE prompt size (provider-reported or estimated), not
            # just the raw query — the assembled prompt is 5-20x larger.
            output_tokens = _count_tokens(answer)
            true_input_tokens = _true_input_tokens(result, input_tokens)
            total_tokens = true_input_tokens + output_tokens + thinking_tokens

            if user_id:
                await asyncio.to_thread(increment_token_usage, user_id, total_tokens)
                try:
                    await asyncio.to_thread(
                        settle_usage, user_id, request_id, entitlements,
                        model=model_used, input_tokens=true_input_tokens,
                        output_tokens=output_tokens, status="settled",
                        thinking_tokens=thinking_tokens,
                        estimated_cost=get_settings().estimate_llm_cost_usd(
                            _tier_primary_model(requested_mode),
                            true_input_tokens, output_tokens, thinking_tokens,
                        ),
                    )
                except Exception as settle_err:
                    logger.warning("Failed to settle usage", error=str(settle_err))

            TOKEN_USAGE_TOTAL.labels(tier=entitlements.plan_key, model=model_used).inc(total_tokens)
            CHAT_REQUESTS_TOTAL.labels(tier=entitlements.plan_key, model=model_used, status="ok", pipeline_type=result.pipeline_type).inc()
            return ChatResponse(
                answer=answer,
                sources=sources,
                routes_used=routes,
                confidence=confidence,
                query_classification=classification,
                model_used=model_used,
                thinking_level=thinking_level,
                thinking_tokens=thinking_tokens,
                thinking_summary=thinking_summary,
                pipeline_timings=result.pipeline_timings,
                fallback_pass=result.fallback_pass,
            )
        finally:
            if redis_client and lock_acquired:
                await redis_client.release_lock(lock_key, lock_token)

    except HTTPException:
        raise
    except GeminiQuotaExceeded:
        # Upstream Gemini quota exhausted (429 / RESOURCE_EXHAUSTED): fail with
        # a dedicated quota message instead of the generic 500.
        CHAT_REQUESTS_TOTAL.labels(tier=entitlements.plan_key, model=requested_mode, status="quota_error", pipeline_type="unknown").inc()
        LLM_QUOTA_ERRORS_TOTAL.labels(tier=entitlements.plan_key).inc()
        if user_id:
            try:
                await asyncio.to_thread(release_reservation, user_id, request_id)
            except Exception:
                pass
        raise HTTPException(status_code=429, detail=_quota_error_message(requested_model))
    except Exception:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.exception("Error in chat endpoint", request_id=request_id)
        CHAT_REQUESTS_TOTAL.labels(tier=entitlements.plan_key, model=requested_mode, status="error", pipeline_type="unknown").inc()
        if user_id:
            try:
                await asyncio.to_thread(release_reservation, user_id, request_id)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail="An internal error occurred")


@router.post("/chat/stream")
async def chat_stream_endpoint(payload: ChatRequest, request: Request) -> StreamingResponse:
    """SSE streaming chat endpoint with multi-model fallback."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    session_id = payload.session_id
    request_id = payload.request_id or get_request_id(request)
    from db import get_token_usage, get_user_by_id, get_active_subscription, reserve_usage, release_reservation, QuotaExceeded
    from core.entitlements import resolve_entitlements
    user = await asyncio.to_thread(get_user_by_id, user_id) if user_id else None
    user_email = user.get("email") if user else None
    usage = await asyncio.to_thread(get_token_usage, user_id)
    subscription = await asyncio.to_thread(get_active_subscription, user_id) if user_id else None
    entitlements = resolve_entitlements(usage.get("tier"), subscription, user_email=user_email)

    # Chat rate limiting (Redis sliding window) - Developer/Unlimited tier enjoys unrestricted access
    settings = get_settings()
    is_developer_exempt = (entitlements.unlimited or entitlements.plan_key == "Developer") and getattr(settings, "enable_developer_unlimited_bypass", True)
    if not is_developer_exempt:
        if not await rate_limiter.allowed_async(f"chat:{user_id}", settings.chat_rate_limit_per_minute):
            raise HTTPException(status_code=429, detail="Chat rate limit exceeded. Please wait a moment.")

    # Mode validation (supports Apex, Core, Lite)
    raw_mode = (payload.mode or "Lite").strip().lower()
    if raw_mode in ("apex", "max"):
        requested_model = "Apex"
        requested_mode = "Max"
    elif raw_mode in ("core", "pro"):
        requested_model = "Core"
        requested_mode = "Pro"
    else:
        requested_model = "Lite"
        requested_mode = "Free"

    if not entitlements.unlimited:
        if "Apex" not in entitlements.allowed_models and requested_model == "Apex":
            if "Core" in entitlements.allowed_models:
                requested_model = "Core"
                requested_mode = "Pro"
            else:
                requested_model = "Lite"
                requested_mode = "Free"
        if "Core" not in entitlements.allowed_models and requested_model == "Core":
            requested_model = "Lite"
            requested_mode = "Free"

    from core.entitlements import get_allowed_thinking_for_model
    from llm.thinking import default_thinking_for_tier

    allowed_for_model = get_allowed_thinking_for_model(entitlements.model_tier, requested_model)
    effective_allowed = tuple(
        lvl for lvl in entitlements.allowed_thinking_levels if lvl in allowed_for_model
    ) or ("Low",)
    default_lvl = default_thinking_for_tier(requested_mode)
    thinking_level = clamp_thinking_level(
        payload.thinking_level or default_lvl, effective_allowed
    )
    thinking_profile = profile_for(thinking_level)

    # Token checking across all four quota windows
    limit_day = float('inf') if entitlements.tokens_day is None else entitlements.tokens_day
    limit_month = float('inf') if entitlements.tokens_month is None else entitlements.tokens_month
    limit_5h = float('inf') if entitlements.tokens_5h is None else entitlements.tokens_5h
    limit_week = float('inf') if entitlements.tokens_week is None else entitlements.tokens_week

    input_tokens = _count_tokens(payload.query)

    if (
        usage.get("tokens_used_day", 0) + input_tokens > limit_day
        or usage.get("tokens_used_month", 0) + input_tokens > limit_month
        or usage.get("tokens_used_5h", 0) + input_tokens > limit_5h
        or usage.get("tokens_used_week", 0) + input_tokens > limit_week
    ):
        async def rate_limit_error():
            yield "data: {\"error\": \"Token limit exceeded for your tier across active rolling windows. Please upgrade or wait for reset.\"}\n\n"
        return StreamingResponse(rate_limit_error(), media_type="text/event-stream")

    # Reserve worst-case quota scaled by the thinking level multiplier.
    if user_id:
        try:
            await asyncio.to_thread(
                reserve_usage,
                user_id,
                request_id,
                entitlements,
                int(input_tokens * 3 * thinking_profile.quota_multiplier),
            )
        except QuotaExceeded:
            async def quota_error():
                yield "data: {\"error\": \"Token quota exhausted for your tier. Please wait for your quota to reset.\"}\n\n"
            return StreamingResponse(quota_error(), media_type="text/event-stream")

    async def event_generator() -> AsyncGenerator[str, None]:
        event_queue: asyncio.Queue = asyncio.Queue()
        pipeline_task: asyncio.Task | None = None
        try:
            async def emit_event(event_payload: dict) -> None:
                await event_queue.put(event_payload)

            is_first_prompt = False
            if user_id and session_id:
                if payload.truncate_from_message_id:
                    from db import truncate_messages_from
                    await asyncio.to_thread(truncate_messages_from, user_id, session_id, payload.truncate_from_message_id)
                    await invalidate_session_cache(user_id, session_id)

                existing_msgs = await get_fast_chat_history(user_id, session_id, limit=1)
                is_first_prompt = (len(existing_msgs) == 0)

            history, session_summary = await resolve_chat_history(user_id, session_id, emit_event=emit_event, tier=entitlements.plan_key)
            history_hash = hashlib.sha256(
                "|".join(f"{m.get('role', '')}:{m.get('content', '')[:500]}" for m in (history or [])[-6:]).encode()
            ).hexdigest()[:16] if history else None
            user_memories = None
            if user_id and getattr(entitlements, "has_user_memory", False):
                user_memories = await get_user_memories_cached(user_id)
                for fact in extract_durable_user_facts(payload.query):
                    await upsert_user_memory_cached(
                        user_id=user_id,
                        key=fact["key"],
                        value=fact["value"],
                        category=fact["category"],
                    )
                    await emit_event({"type": "memory_updated", "key": fact["key"], "value": fact["value"]})

            attachment_texts = await _load_attachment_texts(payload.attachments, user_id=user_id)

            stream_start_time = time.monotonic()
            pipeline_task = asyncio.create_task(
                execute_agent_pipeline(
                    query=payload.query,
                    provider_filter=payload.provider_filter,
                    tier=requested_mode,
                    chat_history=history,
                    attachment_texts=attachment_texts,
                    emit_event=emit_event,
                    stream=True,
                    thinking_level=thinking_level,
                    session_summary=session_summary,
                    user_memories=user_memories,
                )
            )

            result = None
            while True:
                getter = asyncio.create_task(event_queue.get())
                await asyncio.wait(
                    {pipeline_task, getter}, return_when=asyncio.FIRST_COMPLETED
                )
                if getter.done():
                    yield f"data: {json.dumps(getter.result())}\n\n"
                else:
                    getter.cancel()
                if pipeline_task.done():
                    exc = pipeline_task.exception()
                    if exc is not None:
                        raise exc
                    result = pipeline_task.result()
                    break

            splitter = ThinkingStreamSplitter()
            thinking_started_at: float | None = None
            thinking_finished_at: float | None = None
            thinking_emitted = False
            first_token_emitted = False
            thinking_char_count: int = 0
            thinking_seq: int = 0
            async for raw_token in result.token_stream:
                for kind, text in splitter.feed(raw_token):
                    if kind == "thinking":
                        if thinking_started_at is None:
                            thinking_started_at = time.monotonic()
                        thinking_emitted = True
                        thinking_char_count += len(text)
                        thinking_seq += 1
                        yield f"data: {json.dumps({'thinking_token': text, 'seq': thinking_seq, 'chars': thinking_char_count})}\n\n"
                    else:
                        if not first_token_emitted:
                            first_token_emitted = True
                            ttft = time.monotonic() - stream_start_time
                            try:
                                _ptype = getattr(result, "pipeline_type", "unknown") or "unknown"
                                TTFT_SECONDS.labels(tier=entitlements.plan_key, pipeline_type=_ptype).observe(ttft)
                            except Exception as _m_err:
                                logger.debug("TTFT metric observe error: %s", _m_err)
                        if thinking_emitted and thinking_finished_at is None:
                            thinking_finished_at = time.monotonic()
                            elapsed = round(
                                (thinking_finished_at - (thinking_started_at or thinking_finished_at)), 1
                            )
                            yield f"data: {json.dumps({'thinking_done': True, 'elapsed': elapsed, 'preview': splitter.thinking_text[-120:].strip()})}\n\n"
                        yield f"data: {json.dumps({'token': text})}\n\n"
            thinking_text, full_answer = splitter.flush()
            if thinking_emitted and thinking_finished_at is None:
                thinking_finished_at = time.monotonic()
                elapsed = round(
                    (thinking_finished_at - (thinking_started_at or thinking_finished_at)), 1
                )
                yield f"data: {json.dumps({'thinking_done': True, 'elapsed': elapsed, 'preview': splitter.thinking_text[-120:].strip()})}\n\n"
            model_used = result.model_used
            routes = result.routes
            classification = result.classification
            sources_dump = result.sources
            thinking_tokens = _count_tokens(thinking_text) if thinking_text else 0

            logger.info(
                "chat.pipeline_timings",
                user_id=user_id,
                session_id=session_id,
                model=model_used,
                tier=entitlements.plan_key,
                routes=routes,
                input_tokens=input_tokens,
                thinking_level=thinking_level,
                thinking_tokens=thinking_tokens,
                fallback_pass=result.fallback_pass,
                timings=result.pipeline_timings,
            )

            # Save messages to history (dual-write to fast Redis + durable Postgres)
            user_msg_id = await record_message_dual_write(
                user_id, session_id, "user", payload.query, attachments=payload.attachments
            )
            assistant_msg_id = await record_message_dual_write(
                user_id, session_id, "assistant", full_answer
            )

            # Cache completed response in Redis
            await set_cached_llm_response(
                query=payload.query,
                response_payload={
                    "answer": full_answer,
                    "sources": sources_dump,
                    "routes_used": routes,
                    "confidence": result.confidence,
                    "query_classification": classification,
                    "model_used": model_used,
                },
                model=requested_mode,
                provider_filter=payload.provider_filter,
                mode=requested_mode,
                history_hash=history_hash,
            )

            # Dual-write to Layer 3 Answer Cache and Semantic Vector Cache
            _has_attachments = bool(payload.attachments)
            _is_multiturn = bool(history and len(history) > 1)
            if (
                full_answer
                and not _has_attachments
                and getattr(pipeline.settings, "enable_semantic_cache", True)
                and getattr(pipeline.settings, "semantic_cache_enabled", True)
            ):
                try:
                    from core.cache_policy import skip_answer_cache_for_validation
                    from core.llm_cache import set_cached_answer
                    from core.semantic_cache import get_semantic_cache

                    _val_dict = getattr(result, "validation", {}) or {}
                    if not skip_answer_cache_for_validation(_val_dict, pipeline.settings):
                        await set_cached_answer(
                            query=payload.query,
                            response_payload={"answer": full_answer, "sources": sources_dump, "model": model_used},
                            model=model_used,
                            provider_filter=payload.provider_filter,
                            history_hash=history_hash,
                            ttl_seconds=getattr(pipeline.settings, "redis_cache_ttl_seconds", 3600),
                        )
                        if not _is_multiturn:
                            try:
                                emb = await pipeline.get_embeddings().embed_query(payload.query)
                                if emb:
                                    get_semantic_cache().set(
                                        emb,
                                        payload.query,
                                        intent=classification.get("intent") if isinstance(classification, dict) else None,
                                    )
                            except Exception:
                                pass
                except Exception as _c_err:
                    logger.debug("chat_stream.write_through_cache_error: %s", _c_err)

            # Generate and stream session title (initial title on first prompt, or refine if topic shifted)
            generated_title = None
            if user_id and session_id:
                try:
                    from db import get_session_title, set_session_title
                    current_title = None
                    if not is_first_prompt:
                        current_title = await asyncio.to_thread(get_session_title, user_id, session_id)
                    generated_title = await pipeline.generate_chat_title(
                        payload.query, full_answer, requested_mode, current_title=current_title
                    )
                    if generated_title and (is_first_prompt or generated_title != current_title):
                        await asyncio.to_thread(set_session_title, user_id, session_id, generated_title)
                        yield f"data: {json.dumps({'type': 'session_title', 'session_id': session_id, 'title': generated_title})}\n\n"
                except Exception as title_err:
                    logger.warning("Failed generating/setting stream session title", error=str(title_err))

            # Update token usage and settle reservation (true prompt size +
            # answer + reasoning)
            output_tokens = _count_tokens(full_answer)
            true_input_tokens = _true_input_tokens(result, input_tokens)
            total_consumed = true_input_tokens + output_tokens + thinking_tokens
            if user_id:
                from db import increment_token_usage, settle_usage
                await asyncio.to_thread(increment_token_usage, user_id, total_consumed)
                try:
                    await asyncio.to_thread(
                        settle_usage, user_id, request_id, entitlements,
                        model=model_used, input_tokens=true_input_tokens,
                        output_tokens=output_tokens, status="settled",
                        thinking_tokens=thinking_tokens,
                        estimated_cost=get_settings().estimate_llm_cost_usd(
                            _tier_primary_model(requested_mode),
                            true_input_tokens, output_tokens, thinking_tokens,
                        ),
                    )
                except Exception as settle_err:
                    logger.warning("Failed to settle stream usage", error=str(settle_err))

            TOKEN_USAGE_TOTAL.labels(tier=entitlements.plan_key, model=model_used).inc(total_consumed)
            CHAT_REQUESTS_TOTAL.labels(tier=entitlements.plan_key, model=model_used, status="ok", pipeline_type=result.pipeline_type).inc()

            # Final completion event with source metadata + thinking summary + pipeline timings + message IDs
            usage_after = await asyncio.to_thread(get_token_usage, user_id) if user_id else {}
            thinking_elapsed = (
                round((thinking_finished_at - thinking_started_at), 1)
                if thinking_started_at and thinking_finished_at
                else None
            )
            yield f"data: {json.dumps({'done': True, 'message_id': assistant_msg_id, 'user_message_id': user_msg_id, 'title': generated_title, 'session_id': session_id, 'sources': sources_dump, 'route': routes, 'classification': classification, 'model_used': model_used, 'pipeline_timings': result.pipeline_timings, 'fallback_pass': result.fallback_pass, 'thinking': {'level': thinking_level, 'thinking_tokens': thinking_tokens, 'elapsed': thinking_elapsed, 'budget': thinking_profile.budget_tokens}, 'usage': {'input': true_input_tokens, 'output': output_tokens, 'thinking': thinking_tokens, 'total_consumed': total_consumed, 'tokens_used_day': usage_after.get('tokens_used_day', 0), 'limit_day': entitlements.tokens_day if entitlements.tokens_day is not None else 'Unlimited', 'tokens_used_month': usage_after.get('tokens_used_month', 0), 'limit_month': entitlements.tokens_month if entitlements.tokens_month is not None else 'Unlimited', 'tokens_used_5h': usage_after.get('tokens_used_5h', 0), 'tokens_used_week': usage_after.get('tokens_used_week', 0), 'is_unlimited': entitlements.unlimited}})}\n\n"

        except GeminiQuotaExceeded as quota_exc:
            # Upstream Gemini quota exhausted (429 / RESOURCE_EXHAUSTED).
            # Dedicated additive event fields (`quota`, `tier`); the existing
            # `error` event shape is unchanged so old clients still render it.
            logger.error(
                "Streaming quota exhausted",
                request_id=request_id,
                model=quota_exc.model_name,
                tier=entitlements.plan_key,
            )
            CHAT_REQUESTS_TOTAL.labels(tier=entitlements.plan_key, model=requested_mode, status="quota_error", pipeline_type="unknown").inc()
            LLM_QUOTA_ERRORS_TOTAL.labels(tier=entitlements.plan_key).inc()
            if user_id:
                try:
                    await asyncio.to_thread(release_reservation, user_id, request_id)
                except Exception:
                    pass
            yield f"data: {json.dumps({'error': _quota_error_message(requested_model), 'quota': True, 'tier': requested_mode, 'recoverable': False})}\n\n"
        except Exception as exc:
            # `request_id` is deliberately NOT reassigned here: assigning it
            # inside this closure would shadow the endpoint-scope request ID
            # and break the happy-path settle call (UnboundLocalError).
            logger.exception("chat.streaming_error", error=str(exc), request_id=request_id)
            CHAT_REQUESTS_TOTAL.labels(tier=entitlements.plan_key, model=requested_mode, status="error", pipeline_type="unknown").inc()
            if user_id:
                try:
                    await asyncio.to_thread(release_reservation, user_id, request_id)
                except Exception:
                    pass
            exc_str = str(exc).lower()
            if any(k in exc_str for k in ("503", "unavailable", "high demand", "overloaded")):
                err_msg = "Cloud AI models are temporarily experiencing high demand. Please try again in a moment."
            else:
                err_msg = "An internal error occurred. Please try again."
            yield f"data: {json.dumps({'error': err_msg, 'recoverable': True})}\n\n"
        finally:
            if pipeline_task is not None and not pipeline_task.done():
                pipeline_task.cancel()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/upload")
async def upload_attachment(request: Request) -> dict[str, Any]:
    """Accept a file upload, extract its text, and stage it for chat context.

    Requires an authenticated session. Enforces the per-tier `max_file_bytes`
    and `max_files` entitlements, an extension allow-list, and magic-byte
    content sniffing. Returns an attachment_id usable in ChatRequest.attachments.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    from db import get_active_subscription, get_token_usage, get_user_by_id
    from core.entitlements import resolve_entitlements
    from file_processor import (
        AttachmentError,
        FileTooLargeError,
        UnsupportedFileTypeError,
        stage_attachment,
    )

    user = await asyncio.to_thread(get_user_by_id, user_id)
    user_email = user.get("email") if user else None
    usage = await asyncio.to_thread(get_token_usage, user_id)
    subscription = await asyncio.to_thread(get_active_subscription, user_id) if user_id else None
    entitlements = resolve_entitlements(usage.get("tier"), subscription, user_email=user_email)

    settings = get_settings()
    is_developer_exempt = (entitlements.unlimited or entitlements.plan_key == "Developer") and getattr(settings, "enable_developer_unlimited_bypass", True)
    if not is_developer_exempt:
        upload_limit = getattr(settings, "upload_rate_limit_per_minute", 20)
        if not await rate_limiter.allowed_async(f"upload:{user_id}", upload_limit):
            raise HTTPException(status_code=429, detail="Upload rate limit exceeded. Please wait a moment.")

    async with request.form() as form:
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise HTTPException(status_code=400, detail="Missing 'file' field in multipart form.")
        data = await upload.read()

        if not data:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        filename = upload.filename or "upload"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        from file_processor import IMAGE_EXTENSIONS, AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
        max_bytes = entitlements.max_file_bytes
        if ext in IMAGE_EXTENSIONS:
            max_bytes = max(entitlements.max_file_bytes, getattr(entitlements, "max_image_bytes", 5_242_880))
        elif ext in AUDIO_EXTENSIONS:
            max_bytes = max(entitlements.max_file_bytes, getattr(entitlements, "max_audio_bytes", 10_485_760))
        elif ext in VIDEO_EXTENSIONS:
            if not getattr(settings, "enable_video_input", True):
                raise HTTPException(status_code=400, detail="Video upload is currently disabled.")
            max_bytes = max(entitlements.max_file_bytes, 26_214_400)

        if len(data) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds your plan's {max_bytes} byte limit.",
            )

        # Enforce the per-tier attachment count across staged attachments.
        staged_count = await _count_staged_attachments(user_id)
        if staged_count >= entitlements.max_files:
            raise HTTPException(
                status_code=429,
                detail=f"Your plan allows at most {entitlements.max_files} attachment(s) per chat.",
            )

        try:
            metadata = await stage_attachment(
                user_id=user_id,
                filename=filename,
                declared_content_type=upload.content_type or "application/octet-stream",
                data=data,
                max_file_bytes=max_bytes,
            )
        except UnsupportedFileTypeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc))
        except AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    logger.info(
        "upload.staged",
        user_id=user_id,
        attachment_id=metadata.get("attachment_id"),
        size=metadata.get("size"),
        content_type=metadata.get("content_type"),
    )
    return {
        "attachment_id": metadata["attachment_id"],
        "filename": metadata["filename"],
        "size": metadata["size"],
        "content_type": metadata["content_type"],
        "chars_extracted": metadata["chars_extracted"],
        "kind": metadata.get("kind", "file"),
    }


async def _count_staged_attachments(user_id: int) -> int:
    """Best-effort count of the user's currently staged attachments in Redis."""
    try:
        from file_processor import ATTACHMENT_KEY_PREFIX
        if not redis_client.is_available or not redis_client.client:
            return 0
        keys = []
        async for key in redis_client.client.scan_iter(match=f"{ATTACHMENT_KEY_PREFIX}*"):
            keys.append(key)
        count = 0
        for key in keys:
            payload = await redis_client.get_json(key)
            if payload and payload.get("user_id") == user_id:
                count += 1
        return count
    except Exception as e:
        logger.warning("Failed counting staged attachments: %s", e)
        return 0


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(attachment_id: str, request: Request) -> Response:
    """Download a staged user attachment by ID."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    from file_processor import get_staged_attachment_bytes
    res = await get_staged_attachment_bytes(attachment_id, user_id=user_id)
    if not res:
        raise HTTPException(status_code=404, detail="Attachment not found or has expired.")

    data, filename, content_type = res
    if content_type in ("text/html", "image/svg+xml", "application/xhtml+xml"):
        content_type = "application/octet-stream"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": content_type,
    }
    return Response(content=data, media_type=content_type, headers=headers)


@router.post("/feedback")
async def message_feedback_endpoint(payload: FeedbackRequest, request: Request) -> dict[str, Any]:
    """Record user feedback (rating 1 or -1) for an assistant message.

    A thumbs-down additionally writes a feedback-driven cache-policy penalty
    (answer caches bypass that query's results), and a thumbs-up writes a
    boost record extending answer-cache freshness — see core/cache_policy.py.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    from db import save_message_feedback
    try:
        feedback = await asyncio.to_thread(
            save_message_feedback,
            user_id=user_id,
            message_id=payload.message_id,
            rating=payload.rating,
            reason=payload.reason,
        )
        # Feedback-driven cache policy: resolve the prompting query for the
        # rated message and record the cache-policy consequence.
        cache_policy_action: dict[str, Any] = {"recorded": False, "action": "none"}
        if getattr(pipeline.settings, "enable_feedback_cache_policy", True):
            try:
                from db import get_message_feedback_context
                from core.cache_policy import record_feedback_policy

                context = await asyncio.to_thread(
                    get_message_feedback_context, payload.message_id, user_id
                )
                query_text = (context or {}).get("query", "")
                if query_text:
                    cache_policy_action = await record_feedback_policy(
                        query_text, payload.rating, payload.reason
                    )
            except Exception as policy_err:
                logger.warning("cache_policy.feedback_wiring_failed", error=str(policy_err))
        return {"status": "ok", "feedback": feedback, "cache_policy": cache_policy_action}
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        logger.error("Failed to save feedback", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to record message feedback.")


@router.get("/sessions")
async def list_sessions(request: Request, limit: int = 100, q: str | None = None) -> list[dict[str, Any]]:
    """List the signed-in user's recent chat sessions with optional search query."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")
    from db import get_user_sessions
    return await asyncio.to_thread(get_user_sessions, user_id, limit=max(1, min(limit, 500)), query=q)


@router.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str, request: Request) -> list[dict[str, Any]]:
    """Return messages for a session owned by the signed-in user."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")
    from db import get_chat_history
    msgs = await asyncio.to_thread(get_chat_history, user_id, session_id, limit=1000)
    # Return messages in chronological order (oldest to newest) for client UI
    return list(reversed(msgs))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    """Delete all messages for a specific session owned by the signed-in user."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")
    from core.security import validate_csrf
    validate_csrf(request)
    from db import delete_user_session
    deleted = delete_user_session(user_id, session_id)
    await invalidate_session_cache(user_id, session_id)
    return {"status": "ok", "deleted": deleted, "session_id": session_id}


class TruncateSessionRequest(BaseModel):
    message_id: int = Field(..., description="Message ID from which to truncate history (inclusive)")


@router.post("/sessions/{session_id}/truncate")
async def truncate_session_endpoint(session_id: str, payload: TruncateSessionRequest, request: Request) -> dict[str, Any]:
    """Truncate messages from a specific message ID onward for the current session."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")
    from db import truncate_messages_from
    from core.session_cache import invalidate_session_cache
    deleted = await asyncio.to_thread(truncate_messages_from, user_id, session_id, payload.message_id)
    await invalidate_session_cache(user_id, session_id)
    return {"status": "ok", "deleted": deleted, "session_id": session_id}


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Check health of vector database, configuration and agent services."""
    settings = get_settings()
    vdb = getattr(pipeline, "vector_manager", None) or getattr(pipeline, "pinecone_manager", None)
    vector_status = "uninitialized"
    try:
        if vdb:
            ok = await vdb.health_check()
            vector_status = "connected" if ok else "unhealthy"
        else:
            vector_status = f"collection: {getattr(settings, 'qdrant_collection', 'cloud-docs')} (ready)"
    except Exception as e:
        vector_status = f"error: {e}"

    from core.redis_client import redis_client
    redis_status = "connected" if redis_client.is_available else ("disabled" if not settings.redis_enabled else "unreachable")

    arq_failed_jobs = 0
    if redis_client.is_available and getattr(redis_client, "client", None) is not None:
        try:
            keys = await redis_client.client.keys("arq:retry:*")
            arq_failed_jobs = len(keys) if keys else 0
        except Exception:
            arq_failed_jobs = 0

    tier_lower = settings.user_tier.lower()
    if tier_lower in ("max", "apex", "developer", "admin"):
        active_model = settings.gemini_model_apex
    elif tier_lower in ("pro", "core"):
        active_model = settings.gemini_model_core
    else:
        active_model = settings.gemini_model_lite

    return {
        "status": "healthy",
        "redis": redis_status,
        "arq_failed_jobs": arq_failed_jobs,
        "main_model": "Gemini 3.8 Flash (all tiers)",
        "main_model_name": active_model,
        "sub_models": {
            "tier": settings.user_tier,
            "router_model": active_model,
        },
        "always_web_search": settings.always_web_search,
        "qdrant": vector_status,
        "vector_db": vector_status,
        "pinecone": vector_status,
        "embedding_model": settings.embedding_model,
    }


@router.get("/sources/stats")
async def source_stats() -> dict[str, Any]:
    """Retrieve indexed documentation collection metrics."""
    try:
        vdb = getattr(pipeline, "vector_manager", None) or getattr(pipeline, "pinecone_manager", None)
        if vdb:
            stats = await vdb.get_collection_stats()
            return {"status": "ok", "stats": stats}
    except Exception as e:
        logger.warning("Error fetching stats", error=str(e))
    return {
        "status": "ok",
        "collection": getattr(get_settings(), "qdrant_collection", "cloud-docs"),
        "points_count": 0,
    }


@router.get("/user/memory")
async def get_user_memory_endpoint(request: Request) -> dict[str, Any]:
    """Retrieve all durable preferences and remembered facts for current user."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    memories = await get_user_memories_cached(user_id)
    return {"memories": memories}


@router.delete("/user/memory/{memory_id}")
async def delete_user_memory_endpoint(memory_id: int, request: Request) -> dict[str, Any]:
    """Delete a durable memory fact for current user."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    success = await delete_user_memory_cached(user_id, memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory fact not found")
    return {"success": True}


@router.delete("/user/memory")
async def clear_all_user_memory(request: Request) -> dict[str, Any]:
    """Clear all durable memory preferences for current user."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    deleted = await delete_all_user_memory_cached(user_id)
    return {"success": True, "deleted": deleted}


@router.post("/user/profile")
async def update_user_profile_endpoint(data: UpdateProfileRequest, request: Request) -> dict[str, Any]:
    """Update display name for the current user."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    name = data.name.strip()[:80]
    if not name:
        raise HTTPException(status_code=422, detail="Name cannot be empty")
    await asyncio.to_thread(db.update_user_profile, user_id, name, True)
    return {"success": True, "name": name}


@router.post("/user/change-password")
async def change_password_endpoint(data: ChangePasswordRequest, request: Request) -> dict[str, Any]:
    """Change account password for password-authenticated users."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await asyncio.to_thread(db.get_user_by_id, user_id)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=400, detail="Password change not available for OAuth accounts")
    if not db.verify_password(user, data.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    valid, msg = validate_password_rules(data.new_password, data.confirm_password, user.get("email", ""))
    if not valid:
        raise HTTPException(status_code=422, detail=msg)
    await asyncio.to_thread(db.set_user_password, user_id, data.new_password)
    return {"success": True}


ALLOWED_PREF_KEYS = {
    "response_language",
    "default_thinking",
    "show_thinking",
    "show_token_usage",
    "show_pipeline_stages",
    "always_web_search",
    "compact_messages",
    "default_model",
    "primary_cloud",
    "preferred_iac",
    "preferred_region",
    "custom_instructions",
    "rag_search_mode",
    "code_line_numbers",
    "stream_speed",
    "theme_mode",
    "audio_autoplay",
    "live_cloud_tools",
}


@router.post("/user/preferences")
async def update_user_preference(data: UpdatePreferenceRequest, request: Request) -> dict[str, Any]:
    """Update a specific UI / workflow preference in settings_json."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if data.key not in ALLOWED_PREF_KEYS:
        raise HTTPException(status_code=422, detail="Unknown preference key")
    success = await asyncio.to_thread(db.update_user_setting, user_id, data.key, data.value)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update preference")
    return {"success": True}


