"""Prometheus business metrics for CloudGPT.

Custom counters/histograms used across the chat pipeline. The HTTP-layer
metrics (request count/latency per route) come from
prometheus-fastapi-instrumentator, wired in app.py.

If prometheus_client is not installed the module degrades to no-ops so the
application can still boot (metrics are a production requirement, not a
runtime dependency for unit tests).
"""

from __future__ import annotations

import logging

try:
    from prometheus_client import Counter, Histogram, Gauge
    _AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the dependency
    _AVAILABLE = False

    class _NoopMetric:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def labels(self, *args, **kwargs) -> _NoopMetric:
            return self

        def inc(self, amount: float = 1) -> None:
            pass

        def dec(self, amount: float = 1) -> None:
            pass

        def set(self, value: float) -> None:
            pass

        def observe(self, amount: float) -> None:
            pass

        def time(self):
            import contextlib

            return contextlib.nullcontext()

    Counter = _NoopMetric  # type: ignore[assignment,misc]
    Histogram = _NoopMetric  # type: ignore[assignment,misc]
    Gauge = _NoopMetric  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

CHAT_REQUESTS_TOTAL = Counter(
    "cloudgpt_chat_requests_total",
    "Total chat requests processed.",
    labelnames=("tier", "model", "status", "pipeline_type"),
)

PIPELINE_TYPE_GAUGE = Gauge(
    "cloudgpt_pipeline_active_type",
    "Currently active pipeline type request distribution.",
    labelnames=("pipeline_type",),
)

LLM_LATENCY_SECONDS = Histogram(
    "cloudgpt_llm_latency_seconds",
    "End-to-end LLM generation latency in seconds.",
    labelnames=("provider", "role"),
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

LLM_QUOTA_ERRORS_TOTAL = Counter(
    "cloudgpt_llm_quota_errors_total",
    "Upstream LLM quota / rate-limit rejections surfaced to users.",
    labelnames=("tier",),
)

SUBMODEL_CALLS_TOTAL = Counter(
    "cloudgpt_submodel_calls_total",
    "Sub-model (lightweight helper) LLM calls by pipeline role.",
    labelnames=("role",),
)

TOKEN_USAGE_TOTAL = Counter(
    "cloudgpt_token_usage_total",
    "Total tokens consumed by the chat pipeline.",
    labelnames=("tier", "model"),
)

RAG_RESULTS_COUNT = Histogram(
    "cloudgpt_rag_results_count",
    "Number of RAG results injected into context per query.",
    buckets=(0, 1, 2, 5, 10, 20, 50),
)

RAG_STAGE_DURATION_SECONDS = Histogram(
    "cloudgpt_rag_stage_duration_seconds",
    "RAG pipeline stage duration in seconds.",
    labelnames=("stage", "tier"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

CACHE_HITS_TOTAL = Counter(
    "cloudgpt_cache_hits_total",
    "Cache hits (LLM response cache, keyed by cache kind).",
    labelnames=("cache",),
)

CACHE_MISSES_TOTAL = Counter(
    "cloudgpt_cache_misses_total",
    "Cache misses (LLM response cache, keyed by cache kind).",
    labelnames=("cache",),
)

REDIS_CACHE_HITS = Counter(
    "cloudgpt_redis_cache_hits_total",
    "Redis cache hits by layer",
    labelnames=("layer",),
)

REDIS_CACHE_MISSES = Counter(
    "cloudgpt_redis_cache_misses_total",
    "Redis cache misses by layer",
    labelnames=("layer",),
)

REDIS_LOCK_ACQUIRED = Counter(
    "cloudgpt_redis_lock_acquired_total",
    "Single-flight locks acquired",
)

REDIS_LOCK_WAITED = Counter(
    "cloudgpt_redis_lock_waited_total",
    "Requests that waited for a single-flight lock",
)

REDIS_LATENCY = Histogram(
    "cloudgpt_redis_op_duration_seconds",
    "Redis operation latency in seconds",
    labelnames=("op",),
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5, 1.0),
)

INGESTION_DEAD_LETTER_COUNT = Gauge(
    "cloudgpt_ingestion_dead_letter_count",
    "Ingestion dead-letter queue failed chunk count",
)

MEMORY_CACHE_HITS = Counter(
    "cloudgpt_memory_cache_hits_total",
    "L1 in-process memory cache hits",
    labelnames=("layer",),
)

MEMORY_CACHE_MISSES = Counter(
    "cloudgpt_memory_cache_misses_total",
    "L1 in-process memory cache misses",
    labelnames=("layer",),
)

SEMANTIC_CACHE_HITS = Counter(
    "cloudgpt_semantic_cache_hits_total",
    "Semantic similarity cache hits",
)

TTFT_SECONDS = Histogram(
    "cloudgpt_ttft_seconds",
    "Time-to-first-token from request receipt to first SSE token event.",
    labelnames=("tier", "pipeline_type"),
    buckets=(0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0),
)

PROMPT_SECTION_TOKENS = Histogram(
    "cloudgpt_prompt_section_tokens",
    "Tokens assembled into each prompt section.",
    labelnames=("section", "tier"),
    buckets=(25, 50, 100, 250, 500, 1000, 2000, 4000, 8000, 16000, 32000),
)

PROMPT_TOTAL_TOKENS = Histogram(
    "cloudgpt_prompt_total_tokens",
    "Total prompt tokens assembled before LLM invocation.",
    labelnames=("tier", "model"),
    buckets=(250, 500, 1000, 2000, 4000, 8000, 16000, 32000, 64000, 128000),
)

PROMPT_BUDGET_DROPS = Counter(
    "cloudgpt_prompt_budget_drops_total",
    "Context sources, chunks, or items trimmed or dropped due to budget or dedup.",
    labelnames=("section", "reason"),
)

PROMPT_OVERFLOW_TOTAL = Counter(
    "cloudgpt_prompt_overflow_total",
    "Total prompt budget overflow occurrences by tier and section.",
    labelnames=("tier", "section"),
)

SEMANTIC_CACHE_SIMILARITY = Histogram(
    "cloudgpt_semantic_cache_similarity",
    "Cosine similarity scores of queries looked up in semantic cache.",
    labelnames=("intent",),
    buckets=(0.7, 0.8, 0.85, 0.9, 0.92, 0.95, 0.98, 1.0),
)

BILLING_EVENTS_TOTAL = Counter(
    "cloudgpt_billing_events_total",
    "Billing events processed by provider, event type, and status.",
    labelnames=("provider", "event_type", "status"),
)

CONTEXT_U_CURVE_REORDERS = Counter(
    "cloudgpt_context_u_curve_reorders_total",
    "Evidence chunk sets reordered using U-curve attention optimization.",
    labelnames=("tier",),
)

CONTEXT_DYNAMIC_SCALING_TOTAL = Counter(
    "cloudgpt_context_dynamic_scaling_total",
    "Context window dynamic budget expansion invocations.",
    labelnames=("tier", "reason"),
)

CONTEXT_BUILDS_BY_RAG_MODE_TOTAL = Counter(
    "cloudgpt_context_builds_by_rag_mode_total",
    "Specialized context builds executed by RAG mode and tier.",
    labelnames=("rag_mode", "tier"),
)

BILLING_WEBHOOK_ERRORS_TOTAL = Counter(
    "cloudgpt_billing_webhook_errors_total",
    "Billing webhook processing errors by reason.",
    labelnames=("reason",),
)

TASK_FAILURES_TOTAL = Counter(
    "cloudgpt_task_failures_total",
    "ARQ background task execution failures.",
    labelnames=("task",),
)

TASK_SUCCESS_TOTAL = Counter(
    "cloudgpt_task_success_total",
    "ARQ background task execution successes.",
    labelnames=("task",),
)

DB_POOL_EXHAUSTED_TOTAL = Counter(
    "cloudgpt_db_pool_exhausted_total",
    "Times database connection pool was exhausted.",
)

RAG_FALLBACK_TOTAL = Counter(
    "cloudgpt_rag_fallback_total",
    "RAG retrieval fallback occurrences by fallback type.",
    labelnames=("fallback_type",),
)

CLOUD_API_ERRORS_TOTAL = Counter(
    "cloudgpt_cloud_api_errors_total",
    "Cloud provider API errors by provider and failure type.",
    labelnames=("provider", "type"),
)

JWT_ERRORS_TOTAL = Counter(
    "cloudgpt_jwt_errors_total",
    "JWT errors by error type.",
    labelnames=("error_type",),
)

PIPELINE_TIMEOUTS_TOTAL = Counter(
    "cloudgpt_pipeline_timeouts_total",
    "Pipeline timeouts across retrieval, reasoning, search, and generation components.",
    labelnames=("component",),
)

STRUCTURED_OUTPUT_ATTEMPTS_TOTAL = Counter(
    "structured_output_attempts_total",
    "Total structured-output LLM calls attempted",
    labelnames=("role",),  # role: router | grader | transform
)

STRUCTURED_OUTPUT_FAILURES_TOTAL = Counter(
    "structured_output_failures_total",
    "Structured-output LLM calls that returned malformed or schema-invalid JSON",
    labelnames=("role", "failure_reason"),  # failure_reason: json_decode | schema_invalid | timeout | empty
)

OUTPUT_VALIDATION_TOTAL = Counter(
    "cloudgpt_output_validation_total",
    "Multi-dimensional output validation outcomes by tier, dimension, and verdict.",
    labelnames=("tier", "dimension", "outcome"),  # outcome: pass | fail
)

CLAIM_SUPPORT_SCORE = Histogram(
    "cloudgpt_claim_support_score",
    "Aggregate claim-level evidence support score of generated answers.",
    labelnames=("tier",),
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

GENERATION_RETRIES_TOTAL = Counter(
    "cloudgpt_generation_retries_total",
    "Bounded generation retries triggered by output validation failures.",
    labelnames=("tier",),
)

GENERATION_ABSTENTIONS_TOTAL = Counter(
    "cloudgpt_generation_abstentions_total",
    "Policy-based abstentions when validated grounding could not be achieved.",
    labelnames=("tier",),
)

CACHE_CASCADE_HITS = Counter(
    "cloudgpt_cache_cascade_hits_total",
    "Hits per tiered cache cascade layer (exact, semantic, retrieval, decision, stage).",
    labelnames=("tier", "layer"),
)

CACHE_POLICY_EVENTS = Counter(
    "cloudgpt_cache_policy_events_total",
    "Feedback-driven and adaptive cache-policy actions.",
    labelnames=("action",),  # penalty | boost | cache_skip_validation | bypass_serve
)


def metrics_available() -> bool:
    """Whether prometheus_client is installed and metrics are live."""
    return _AVAILABLE


