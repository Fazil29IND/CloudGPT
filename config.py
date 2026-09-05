"""
CloudGPT Configuration Module

Centralized configuration using pydantic-settings.
All settings are loaded from environment variables or .env file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ─── Project Paths ──────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SOURCES_DIR = BASE_DIR / "sources"
RAW_DIR = DATA_DIR / "raw"
CLEANED_DIR = DATA_DIR / "cleaned"
CHUNKS_DIR = DATA_DIR / "chunks"
METADATA_DIR = DATA_DIR / "metadata"


class Settings(BaseSettings):
    """CloudGPT application settings.

    All values can be overridden via environment variables or a .env file
    placed in the project root.
    """

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App Environment ─────────────────────────────────────────────────
    environment: str = Field(
        default="development",
        description="Application environment (development | production)",
    )

    # ── Existing: Database & Auth ────────────────────────────────────────
    database_url: str = Field(
        default="postgresql://postgres:Fazil@localhost:5000/pygpt",
        description="PostgreSQL connection URI",
    )
    db_pool_min_conn: int = Field(
        default=2,
        ge=1,
        le=50,
        description="Minimum idle connections in PostgreSQL ThreadedConnectionPool",
    )
    db_pool_max_conn: int = Field(
        default=30,
        ge=2,
        le=100,
        description="Maximum total connections in PostgreSQL ThreadedConnectionPool",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URI for caching and temporary data",
    )
    redis_enabled: bool = Field(
        default=True,
        description="Enable Redis caching layer (falls back gracefully if unavailable)",
    )
    redis_cache_ttl_seconds: int = Field(
        default=86400,
        description="TTL for cached LLM prompt/response outputs in seconds (default 24h)",
    )
    redis_session_ttl_seconds: int = Field(
        default=7200,
        description="TTL for active session working memory in seconds (default 2h)",
    )
    redis_tool_ttl_seconds: int = Field(
        default=3600,
        description="TTL for cached tool and pricing queries in seconds (default 1h)",
    )
    secret_key: str = Field(
        default="change-me-in-production",
        description="Secret key for session cookie signing",
    )
    google_client_id: str = Field(default="", description="Google OAuth 2.0 Client ID")
    google_client_secret: str = Field(
        default="", description="Google OAuth 2.0 Client Secret"
    )

    # ── User Tier Configuration ─────────────────────────────────────────
    user_tier: str = Field(
        default="Max",
        description="User subscription tier: Max | Pro | Free",
    )
    admin_email: str = Field(
        default="",
        description="Admin email address for Max tier privileges (set via ADMIN_EMAIL env var)",
    )
    developer_emails: str = Field(
        default="",
        description="Comma-separated internal accounts that receive the Max developer override",
    )
    seed_demo_accounts: bool = Field(
        default=False,
        description="Seed demo accounts on startup (dev only). Never enable in production.",
    )

    # ── Public plans, quotas, and billing ───────────────────────────────
    # Plan names are deliberately normalized in core.entitlements. Prices are
    # Stripe price IDs, never amount values, so deployments choose pricing.
    lite_tokens_day: int = Field(default=25_000, ge=0)
    lite_tokens_month: int = Field(default=750_000, ge=0)
    pro_tokens_day: int = Field(default=150_000, ge=0)
    pro_tokens_month: int = Field(default=4_500_000, ge=0)
    max_tokens_day: int = Field(default=500_000, ge=0)
    max_tokens_month: int = Field(default=15_000_000, ge=0)
    lite_tokens_5h: int = Field(default=50_000, ge=0)
    lite_tokens_week: int = Field(default=300_000, ge=0)
    pro_tokens_5h: int = Field(default=250_000, ge=0)
    pro_tokens_week: int = Field(default=2_000_000, ge=0)
    max_tokens_5h: int = Field(default=500_000, ge=0)
    max_tokens_week: int = Field(default=15_000_000, ge=0)
    pricing_currency: str = Field(default="INR", description="Display currency for pricing")
    pro_price_inr: int = Field(default=2999, description="Pro plan price in INR")
    max_price_inr: int = Field(default=7999, description="Max plan price in INR")
    chat_max_input_chars: int = Field(default=16_000, ge=100, le=200_000)
    chat_max_output_tokens: int = Field(default=4_096, ge=64, le=32_768)
    max_history_messages: int = Field(
        default=20, ge=0, le=100, description="Maximum conversation history messages to retrieve"
    )
    billing_enabled: bool = Field(default=False)
    payment_provider: str = Field(
        default="razorpay",
        description="Payment provider: razorpay (INR orders) or stripe",
    )
    # RazorPay (INR checkout; amounts derived from pro/max_price_inr in paise)
    razorpay_key_id: str | None = Field(default=None, description="RazorPay key id (public)")
    razorpay_key_secret: str | None = Field(default=None, description="RazorPay key secret (server-only)")
    razorpay_webhook_secret: str | None = Field(default=None, description="RazorPay webhook HMAC secret")
    stripe_secret_key: str | None = Field(default=None)
    stripe_publishable_key: str | None = Field(default=None)
    stripe_webhook_secret: str | None = Field(default=None)
    stripe_price_lite_monthly: str | None = Field(default=None)
    stripe_price_lite_yearly: str | None = Field(default=None)
    stripe_price_pro_monthly: str | None = Field(default=None)
    stripe_price_pro_yearly: str | None = Field(default=None)
    stripe_price_max_monthly: str | None = Field(default=None, description="Stripe price ID for Max plan monthly")
    stripe_price_max_yearly: str | None = Field(default=None, description="Stripe price ID for Max plan yearly")
    billing_success_url: str = Field(default="http://localhost:5001/billing?checkout=success")
    billing_cancel_url: str = Field(default="http://localhost:5001/billing?checkout=cancelled")
    billing_grace_period_hours: int = Field(default=72, ge=0, le=720)

    # ── Security and operational controls ───────────────────────────────
    cors_origins: str = Field(default="http://localhost:5001")
    trusted_hosts: str = Field(default="localhost,127.0.0.1,testserver")
    csrf_enabled: bool = Field(default=True)
    csrf_header_name: str = Field(default="X-CSRF-Token")
    auth_rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)
    chat_rate_limit_per_minute: int = Field(default=30, ge=1, le=1000)
    billing_rate_limit_per_minute: int = Field(default=20, ge=1, le=1000)
    upload_rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)
    enable_cloud_api_tools: bool = Field(default=False)

    # ── Email (password reset and transactional mail) ────────────────────
    email_enabled: bool = Field(
        default=False,
        description="Send real email via SMTP; when false, email content is logged instead",
    )
    smtp_host: str = Field(default="", description="SMTP server hostname")
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = Field(default="", description="SMTP username")
    smtp_password: str | None = Field(default=None, description="SMTP password")
    smtp_from_email: str = Field(default="noreply@cloudgpt.local")
    smtp_tls: bool = Field(default=True, description="Use STARTTLS when connecting to SMTP")
    password_reset_token_ttl_minutes: int = Field(default=60, ge=1, le=1440)

    # ── Multimodal Feature Flags ─────────────────────────────────────────
    enable_vision: bool = Field(default=True, description="Enable multimodal image input (vision)")
    enable_audio_input: bool = Field(default=True, description="Enable audio file input and transcription")
    enable_video_input: bool = Field(default=True, description="Enable multimodal video file input")
    enable_tts: bool = Field(default=True, description="Enable text-to-speech audio output")
    enable_artifacts: bool = Field(default=True, description="Enable downloadable output artifacts")
    max_image_dimension: int = Field(default=2048, description="Maximum image width/height in pixels")

    # ── File attachments ─────────────────────────────────────────────────
    attachment_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    attachment_allowed_extensions: str = Field(
        default="txt,md,csv,pdf,docx,xlsx,png,jpg,jpeg,webp,gif,mp3,wav,m4a,ogg,webm,mp4,mov,avi,mkv,py,js,ts,json,yaml,yml,tf,sh,sql,html,css,xml,env,dockerfile,toml,ini,c,cpp,h,rs,go,log,rtf",
        description="Comma-separated allow-list of attachment file extensions",
    )

    # ── Observability ────────────────────────────────────────────────────
    log_format: str = Field(
        default="console",
        description="Log output format: console (development) | json (production)",
    )
    metrics_enabled: bool = Field(
        default=True,
        description="Expose Prometheus metrics at /metrics",
    )

    # OpenAI (alternative)
    openai_api_key: str | None = Field(default=None)
    openai_model: str = Field(default="gpt-4o")

    # Gemini (primary)
    gemini_api_key: str | None = Field(default=None)
    gemini_model: str = Field(
        default="gemini-3.8-flash",
        description="Universal Gemini model identifier (used when no tier override is set)",
    )
    gemini_request_timeout_seconds: float = Field(
        default=25.0,
        description="Client-side timeout for Gemini API requests in seconds",
    )
    gemini_first_chunk_timeout_seconds: float = Field(
        default=6.0,
        ge=1.0,
        le=30.0,
        description="Fail-fast timeout for stream initiation/first chunk before cascading to fallback model",
    )
    gemini_total_fallback_deadline_seconds: float = Field(
        default=25.0,
        ge=5.0,
        le=120.0,
        description="Total maximum time budget across all model fallback attempts in seconds",
    )
    llm_stream_timeout_seconds: float = Field(
        default=120.0,
        description="Timeout in seconds for LLM streaming response generation",
    )

    # ── Tier primary assignments — all three use 3.8-flash ──────────────────
    # Thinking level (Low / Medium / High) is set per-request, not per-model.
    gemini_model_lite: str = Field(
        default="gemini-3.8-flash",
        description="Free-tier primary model — thinking level: Low",
    )
    gemini_model_core: str = Field(
        default="gemini-3.8-flash",
        description="Pro-tier primary model — thinking level: Medium",
    )
    gemini_model_apex: str = Field(
        default="gemini-3.8-flash",
        description="Max-tier primary model — thinking level: High",
    )
    enable_tier_model_specialization: bool = Field(
        default=False,
        description="When true, dynamically specializes model tiers: Flash-Lite for Free, Flash-2.5 for Pro, Pro-2.5 for Apex",
    )
    specialized_model_lite: str = Field(
        default="gemini-2.0-flash-lite",
        description="Specialized low-latency model for Free/Lite tier when specialization is active",
    )
    specialized_model_core: str = Field(
        default="gemini-2.5-flash",
        description="Specialized high-precision model for Pro/Core tier when specialization is active",
    )
    specialized_model_apex: str = Field(
        default="gemini-2.5-pro",
        description="Specialized frontier reasoning model for Max/Apex tier when specialization is active",
    )

    # ── Fallback chain (same for every tier) ────────────────────────────────
    # If 3.8-flash fails, the provider walks this chain in order.
    # Thinking level is preserved across all fallbacks.
    gemini_model_fallback_1: str = Field(
        default="gemini-3.7-flash",
        description="First fallback — used when primary is unavailable",
    )
    gemini_model_fallback_2: str = Field(
        default="gemini-3.6-flash",
        description="Second fallback",
    )
    gemini_model_fallback_3: str = Field(
        default="gemini-3.5-flash",
        description="Third fallback — lightest model, last resort",
    )
    gemini_model_safety_net: str = Field(
        default="gemini-3.5-flash-lite",
        description="Fourth fallback safety net if previous flash models fail",
    )
    fallback_max_output_tokens: int = Field(
        default=400,
        description="Hard token budget cap for fallback candidate models to prevent runaway verbosity and latency",
    )
    fallback_disable_thinking: bool = Field(
        default=True,
        description="Disable thinking budget on fallback candidate models for fast TTFT during failover",
    )
    fallback_enforce_support_persona: bool = Field(
        default=True,
        description="Inject low-latency support triage prompt when fallback models are engaged",
    )

    # ── Model Roles & Version Pinning ────────────────────────────────────────────
    # Sub-model used for structured-judgment tasks: query routing, evidence grading,
    # query transformation, and title generation. Defaults to the lightest fallback
    # so it is fast and cheap. Override in .env to pin a specific version.
    gemini_model_sub: str = Field(
        default="gemini-3.5-flash",
        description="Model for sub-model roles: router, grader, title, query-transform. "
                    "Deliberately lighter than the generation models.",
    )

    # Separate evaluator model for the self-critique / Answer Evaluator stage.
    # Using a different model than the generator avoids self-preference bias.
    # Defaults to gemini-3.7-flash — capable enough for fact-checking, different
    # from the 3.8-flash generator.
    gemini_model_evaluator: str = Field(
        default="gemini-3.7-flash",
        description="Model for the Answer Evaluator (self-critique) stage. "
                    "Must differ from gemini_model_apex/core to avoid self-preference bias.",
    )

    # Temperature overrides per pipeline role.
    # These centralise the per-stage sampling params so they can be tuned without
    # touching call sites.
    temperature_generation: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for main answer generation.",
    )
    temperature_evaluator: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Sampling temperature for the Answer Evaluator (self-critique). "
                    "Low = deterministic fact-checking behavior.",
    )
    temperature_sub_model: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Sampling temperature for sub-model structured-judgment tasks "
                    "(router, grader, transform). 0.0 = fully deterministic.",
    )

    # Structured output retry config.
    structured_output_max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Number of retry attempts when a structured-output LLM call returns "
                    "malformed or schema-invalid JSON.",
    )

    # Schema-validity rate metric tracking window (rolling count).
    schema_validity_window: int = Field(
        default=1000,
        ge=100,
        le=100_000,
        description="Rolling window size for the schema_validity_rate Prometheus counter.",
    )

    # ── Model Capability Benchmark ────────────────────────────────────────────────
    model_eval_golden_set_path: str = Field(
        default="evaluation/golden_set.json",
        description="Path to the shared golden set used by both retrieval_eval and model_eval.",
    )
    model_eval_sample_size: int = Field(
        default=50,
        ge=10,
        le=500,
        description="Number of questions sampled from the golden set for the model-capability slice. "
                    "Sampled deterministically by index so results are reproducible.",
    )
    model_eval_accuracy_baseline: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Accuracy baseline set by the first model_eval run. "
                    "Subsequent runs flag a regression if accuracy drops more than 5 pp below this.",
    )

    self_critique_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=120.0,
        description="Timeout in seconds for the self-critique (Answer Evaluator) LLM call.",
    )

    # ── Thinking Mode (Low | Medium | High | Max) ────────────────────────
    # Reasoning token budgets handed to thinking-capable providers.
    thinking_budget_low: int = Field(default=2048, ge=0, le=65535)
    thinking_budget_medium: int = Field(default=8192, ge=0, le=65535)
    thinking_budget_high: int = Field(default=24576, ge=0, le=65535)
    thinking_budget_max: int = Field(default=65535, ge=0, le=65535)
    # Quota multipliers applied to reserved tokens per thinking level.
    thinking_mult_low: float = Field(default=1.0, ge=0.5, le=10.0)
    thinking_mult_medium: float = Field(default=1.5, ge=0.5, le=10.0)
    thinking_mult_high: float = Field(default=2.5, ge=0.5, le=10.0)
    thinking_mult_max: float = Field(default=4.0, ge=0.5, le=10.0)

    # ── LLM Price Table (USD per million tokens) ─────────────────────────
    # Populates usage_events.estimated_cost at settle time so margin can be
    # measured directly from the usage ledger. Refresh as Google pricing
    # changes; unknown models price at the most expensive listed rate.
    llm_price_input_per_m: dict[str, float] = Field(
        default_factory=lambda: {
            "gemini-3.8-flash": 0.50,
            "gemini-3.7-flash": 0.40,
            "gemini-3.6-flash": 0.30,
            "gemini-3.5-flash": 0.20,
            "gemini-3.5-flash-lite": 0.10,
        },
        description="Input price in USD per million tokens, keyed by model name.",
    )
    llm_price_output_per_m: dict[str, float] = Field(
        default_factory=lambda: {
            "gemini-3.8-flash": 1.50,
            "gemini-3.7-flash": 1.20,
            "gemini-3.6-flash": 0.90,
            "gemini-3.5-flash": 0.60,
            "gemini-3.5-flash-lite": 0.30,
        },
        description="Output price in USD per million tokens, keyed by model name.",
    )
    llm_price_thinking_per_m: dict[str, float] = Field(
        default_factory=lambda: {
            "gemini-3.8-flash": 3.00,
            "gemini-3.7-flash": 2.40,
            "gemini-3.6-flash": 1.80,
            "gemini-3.5-flash": 1.20,
            "gemini-3.5-flash-lite": 0.60,
        },
        description="Thinking/reasoning price in USD per million tokens, keyed by model name.",
    )

    # Claude (alternative)
    anthropic_api_key: str | None = Field(default=None)
    anthropic_model: str = Field(default="claude-sonnet-4-20250514")

    # ── Embedding ───────────────────────────────────────────────────────
    embedding_provider: str = Field(
        default="gemini",
        description="Embedding provider: gemini | local | openai | voyage",
    )
    embedding_model: str = Field(
        default="gemini-embedding-2",
        description="Embedding model name (e.g. gemini-embedding-2, gemini-embedding-001, BAAI/bge-small-en-v1.5)",
    )
    embedding_dimension: int = Field(
        default=384,
        description="Embedding vector dimension (384 for drop-in Pinecone compatibility, or 768 / 1536 / 3072 via MRL)",
    )

    # ── Pinecone ────────────────────────────────────────────────────────
    pinecone_api_key: str | None = Field(
        default=None, description="Pinecone API key"
    )
    pinecone_index_name: str = Field(
        default="cloud-docs", description="Pinecone index name"
    )
    pinecone_cloud: str = Field(
        default="aws", description="Pinecone serverless cloud provider (aws | gcp | azure)"
    )
    pinecone_region: str = Field(
        default="us-east-1", description="Pinecone serverless region"
    )
    pinecone_metric: str = Field(
        default="dotproduct", description="Pinecone distance metric (cosine | dotproduct | euclidean)"
    )

    # ── Retrieval ───────────────────────────────────────────────────────
    retrieval_top_k: int = Field(
        default=50, description="Number of candidates from initial retrieval"
    )
    rerank_top_k: int = Field(
        default=10, description="Number of results after reranking"
    )
    dense_weight: float = Field(
        default=0.6, description="Dense retrieval weight in RRF"
    )
    sparse_weight: float = Field(
        default=0.4, description="Sparse/BM25 retrieval weight in RRF"
    )
    dense_weight_nlq: float = Field(
        default=0.7, description="Dense weight for natural language questions"
    )
    dense_weight_exact: float = Field(
        default=0.3, description="Dense weight for exact technical queries (CLI, API, error codes)"
    )
    reranker_model: str = Field(
        default="ms-marco-MiniLM-L-12-v2", description="FlashRank reranker model"
    )
    rerank_provider: str = Field(
        default="flashrank",
        description="Reranker provider: flashrank (local CPU) | pinecone (serverless inference)",
    )
    sparse_provider: str = Field(
        default="bm25s",
        description="Sparse vector representation provider: bm25s | inference",
    )
    adaptive_retrieval_top_k: bool = Field(
        default=True,
        description="Scale retrieval_top_k down for high-confidence single-service queries",
    )
    retrieval_timeout_seconds: float = Field(
        default=8.0,
        description="Timeout in seconds for hybrid RAG retrieval stage",
    )
    agentic_rag_timeout_seconds: float = Field(
        default=15.0,
        description="Timeout in seconds for agentic RAG planning and sub-query execution",
    )
    adaptive_rag_timeout_seconds: float = Field(
        default=10.0,
        description="Timeout in seconds for adaptive RAG query transformation and execution",
    )

    # ── Cache & Corpus Versioning ───────────────────────────────────────
    cache_corpus_version: str = Field(
        default="v2",
        description="Increment to invalidate retrieval/answer cache when corpus changes",
    )
    cache_prompt_version: str = Field(
        default="v1",
        description="Increment to invalidate answer cache when system prompts change",
    )
    cache_router_version: str = Field(
        default="v1",
        description="Increment to invalidate query plan cache when router prompt changes",
    )
    active_corpus_version: str = Field(
        default="v2",
        description="Active Pinecone namespace version pointer",
    )

    # ── L1 Memory & Semantic Cache ─────────────────────────────────────
    memory_cache_max_entries: int = Field(
        default=200,
        description="Maximum entries in the L1 in-process answer cache",
    )
    memory_cache_ttl_seconds: int = Field(
        default=60,
        description="TTL for L1 in-process cache entries in seconds",
    )
    semantic_cache_enabled: bool = Field(
        default=True,
        description="Enable semantic similarity cache for near-duplicate query detection",
    )
    semantic_cache_threshold: float = Field(
        default=0.92,
        description="Minimum cosine similarity to count as a cache hit (0.0–1.0)",
    )
    semantic_cache_threshold_pricing: float = Field(
        default=0.95,
        description="Minimum cosine similarity for pricing/cost query cache hit (0.0–1.0)",
    )
    semantic_cache_threshold_troubleshooting: float = Field(
        default=0.95,
        description="Minimum cosine similarity for troubleshooting/error query cache hit (0.0–1.0)",
    )
    semantic_cache_threshold_conceptual: float = Field(
        default=0.90,
        description="Minimum cosine similarity for conceptual/overview query cache hit (0.0–1.0)",
    )
    semantic_cache_max_entries: int = Field(
        default=500,
        description="Maximum entries in the semantic cache",
    )

    # ── Small-Talk Gate ─────────────────────────────────────────────────
    smalltalk_gate_enabled: bool = Field(
        default=True,
        description="Bypass the full agent pipeline for greetings/small talk "
                    "(Layer 1 full-match regex + Layer 2 embedding cosine gate).",
    )
    smalltalk_max_query_chars: int = Field(
        default=120,
        ge=1,
        le=1000,
        description="Skip the small-talk gate (fail open) for queries longer than this many characters.",
    )
    smalltalk_similarity_threshold: float = Field(
        default=0.92,
        ge=0.0,
        le=1.0,
        description="Cosine similarity vs canonical small-talk utterances for a Layer 2 gate hit. "
                    "Calibrated against the local bge-small embedder: paraphrase greetings score "
                    ">= 0.93, unrelated short queries <= 0.91.",
    )
    smalltalk_failopen_band: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Fail-open band floor: cosine scores between this and the "
                    "smalltalk_similarity_threshold fall through to the normal pipeline.",
    )
    smalltalk_gemini_similarity_threshold: float = Field(
        default=0.78,
        ge=0.0,
        le=1.0,
        description="Cosine similarity vs canonical utterances for Layer 2 gate when using Gemini embeddings "
                    "(calibrated: greetings >= 0.80, technical queries <= 0.60).",
    )
    smalltalk_gemini_failopen_band: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Fail-open band floor for Gemini embeddings.",
    )
    smalltalk_utterances_override: str | None = Field(
        default=None,
        description="Optional path to a JSON file containing a custom canonical "
                    "small-talk utterance list (one JSON array of strings).",
    )

    # ── Context Token Budgets & Tier Gates ─────────────────────────────
    context_tokens_free: int = Field(
        default=3000,
        description="Max context tokens for RAG/internet content in Free tier prompts",
    )
    context_tokens_pro: int = Field(
        default=5000,
        description="Max context tokens for RAG/internet content in Pro tier prompts",
    )
    context_tokens_max: int = Field(
        default=8000,
        description="Max context tokens for RAG/internet content in Max tier prompts",
    )
    prompt_budget_free: int = Field(
        default=4000,
        description="Global prompt token budget for Free tier across all sections",
    )
    prompt_budget_pro: int = Field(
        default=7000,
        description="Global prompt token budget for Pro tier across all sections",
    )
    prompt_budget_max: int = Field(
        default=12000,
        description="Global prompt token budget for Max tier across all sections",
    )
    enable_global_context_budget: bool = Field(
        default=True,
        description="Feature flag for global prompt token budgeting across all sections",
    )
    prompt_budget_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "rag": 0.45,
            "internet": 0.15,
            "web": 0.10,
            "tools": 0.10,
            "attachments": 0.20,
        },
        description="Section token allocation weights for global context budgeting",
    )
    history_token_budget: int = Field(
        default=1500,
        description="Maximum token budget allocated for chat history turns in prompt",
    )
    history_compaction_enabled: bool = Field(
        default=True,
        description="Enable rolling LLM compaction of older chat history turns",
    )
    history_compaction_threshold_messages: int = Field(
        default=10,
        description="Trigger compaction when conversation history message count reaches this threshold",
    )
    enable_user_memory: bool = Field(
        default=True,
        description="Enable cross-session persistent user preference memory",
    )
    user_memory_max_tokens: int = Field(
        default=300,
        description="Maximum token budget for injected user preferences block",
    )
    semantic_cache_threshold_pricing: float = Field(
        default=0.95,
        description="Semantic cache similarity threshold for pricing queries",
    )
    semantic_cache_threshold_troubleshooting: float = Field(
        default=0.95,
        description="Semantic cache similarity threshold for troubleshooting queries",
    )
    semantic_cache_threshold_conceptual: float = Field(
        default=0.90,
        description="Semantic cache similarity threshold for conceptual queries",
    )
    skip_self_critique_threshold: float = Field(
        default=0.7,
        description="Minimum evidence grade score to skip the Pro tier self-critique pass",
    )

    # ── Optimization Feature Flags ─────────────────────────────────────
    enable_l1_cache: bool = Field(
        default=True,
        description="Feature flag for L1 memory cache",
    )
    enable_semantic_cache: bool = Field(
        default=True,
        description="Feature flag for semantic similarity cache",
    )
    enable_context_budget: bool = Field(
        default=True,
        description="Feature flag for context token budgeting",
    )
    enable_structured_stage_events: bool = Field(
        default=True,
        description="Feature flag for structured pipeline stage SSE events",
    )
    enable_adaptive_top_k: bool = Field(
        default=True,
        description="Feature flag for adaptive top_k scaling",
    )

    # ── Context Validator ─────────────────────────────────────────────────────
    enable_context_validator: bool = Field(
        default=True,
        description="Enable the Context Validator gate (injection detection, secret scrub, staleness flagging).",
    )
    context_validator_fail_closed: bool = Field(
        default=True,
        description="If True, validation failures reject the input. If False, log-and-continue.",
    )
    context_staleness_threshold_days: int = Field(
        default=90,
        description="Services.md chunks older than this many days are flagged as stale by the Context Validator.",
    )
    context_validator_secret_patterns: list[str] = Field(
        default_factory=lambda: [
            r"AKIA[0-9A-Z]{16}",                   # AWS access key
            r"(?i)secret[_\-]?access[_\-]?key",
            r"(?i)\"type\"\s*:\s*\"service_account\"",  # GCP SA JSON
            r"(?i)accountkey=",                    # Azure storage
            r"(?i)password\s*=\s*\S+",
            r"(?i)conn(?:ection)?[_\-]?str(?:ing)?",
        ],
        description="Regex patterns used by the Context Validator to detect and scrub secrets.",
    )

    # ── Context Quality Controller ────────────────────────────────────────────
    enable_context_quality_controller: bool = Field(
        default=True,
        description="Enable the Context Quality Controller (CQC) dedup, diversity, and coherence check.",
    )
    cqc_contradiction_check_tiers: list[str] = Field(
        default_factory=lambda: ["Pro", "Max"],
        description="Tiers on which the CQC runs cross-source contradiction detection.",
    )
    cqc_coherence_threshold: float = Field(
        default=0.55,
        description="Minimum context_coherence_score. Below this, escalation is triggered in Core/Apex.",
    )
    cqc_max_provider_duplication: float = Field(
        default=0.7,
        description="Maximum fraction of evidence chunks that may come from a single cloud provider. "
                    "Above this, diversity rebalancing is applied.",
    )

    # ── Live Verify (Apex only) ────────────────────────────────────────────────
    enable_live_verify: bool = Field(
        default=True,
        description="Enable synchronous Live Verify escalation in the Apex pipeline.",
    )
    live_verify_staleness_trigger: bool = Field(
        default=True,
        description="Trigger Live Verify when the Context Validator flags retrieved chunks as stale.",
    )
    live_verify_confidence_trigger: float = Field(
        default=0.45,
        description="Combined confidence score below which Apex triggers Live Verify instead of retrying retrieval.",
    )
    live_verify_max_results: int = Field(
        default=3,
        description="Maximum number of provider-doc results fetched by a single Live Verify call.",
    )
    live_verify_provider_domains: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "aws": ["aws.amazon.com/about-aws/whats-new", "docs.aws.amazon.com"],
            "gcp": ["cloud.google.com/release-notes", "cloud.google.com/docs"],
            "azure": ["azure.microsoft.com/en-us/updates", "learn.microsoft.com"],
        },
        description="Per-provider domain allow-lists used by the Live Verify tool call.",
    )

    # ── Recommendation Routing ────────────────────────────────────────────────
    recommendation_comparison_bias: bool = Field(
        default=True,
        description="When True, the Query Analyzer up-weights complexity and multi-hop for "
                    "recommendation-shaped queries (requires_provider_comparison=True).",
    )
    risk_gate_keywords: list[str] = Field(
        default_factory=lambda: [
            "cost", "pricing", "budget", "compliance", "hipaa", "gdpr", "pci",
            "migration", "migrate", "production", "disaster recovery", "dr ",
            "deprecated", "end of life", "eol",
        ],
        description="Keywords that force risk_level=medium and route through Answer Evaluator "
                    "regardless of complexity score.",
    )

    # ── Latency Budgets (ms) ────────────────────────────────────────────
    budget_classification_ms: int = Field(
        default=300, description="Latency budget for query classification stage in ms"
    )
    budget_retrieval_ms: int = Field(
        default=300, description="Latency budget for retrieval stage in ms"
    )
    budget_reranking_ms: int = Field(
        default=400, description="Latency budget for reranking stage in ms"
    )
    budget_web_search_ms: int = Field(
        default=2000, description="Latency budget for web search stage in ms"
    )

    # ── Web Search ──────────────────────────────────────────────────────
    searxng_url: str | None = Field(
        default=None,
        description="SearXNG metasearch instance URL (e.g. http://localhost:8080 or https://searx.yourdomain.com)",
    )
    tavily_api_key: str | None = Field(
        default=None, description="Optional Tavily web search API key (legacy)"
    )
    web_search_max_results: int = Field(
        default=5, description="Maximum web search results"
    )
    always_web_search: bool = Field(
        default=False,
        description="Always perform web search for every query",
    )
    web_search_min_confidence: float = Field(
        default=0.80,
        description="Minimum classifier confidence for auto-gated web searches",
    )
    duckduckgo_fallback: bool = Field(
        default=True,
        description="Use DuckDuckGo as fallback when SearXNG is unavailable",
    )
    duckduckgo_direct_search: bool = Field(
        default=True,
        description="Use native resilient direct DuckDuckGo HTML/Lite parser without external dependency failures",
    )
    duckduckgo_html_endpoint: str = Field(
        default="https://html.duckduckgo.com/html/",
        description="Direct DuckDuckGo HTML search endpoint",
    )
    web_search_timeout_seconds: float = Field(
        default=5.0,
        description="Timeout in seconds for external web search queries",
    )

    # ── Cloud Provider Credentials ──────────────────────────────────────
    # AWS
    aws_access_key_id: str | None = Field(default=None, description="AWS Access Key ID")
    aws_secret_access_key: str | None = Field(
        default=None, description="AWS Secret Access Key"
    )
    aws_default_region: str = Field(
        default="us-east-1", description="Default AWS region"
    )

    # GCP
    gcp_project_id: str | None = Field(default=None, description="GCP Project ID")
    gcp_credentials_path: str | None = Field(
        default=None, description="Path to GCP service account JSON"
    )

    # Azure
    azure_subscription_id: str | None = Field(
        default=None, description="Azure Subscription ID"
    )
    azure_tenant_id: str | None = Field(default=None, description="Azure Tenant ID")
    azure_client_id: str | None = Field(
        default=None, description="Azure Service Principal Client ID"
    )
    azure_client_secret: str | None = Field(
        default=None, description="Azure Service Principal Client Secret"
    )

    # ── Crawler ─────────────────────────────────────────────────────────
    crawler_rate_limit: float = Field(
        default=2.0,
        description="Maximum requests per second per domain",
    )
    crawler_max_concurrent: int = Field(
        default=5, description="Maximum concurrent crawl requests"
    )
    crawler_timeout: int = Field(
        default=30, description="HTTP request timeout in seconds"
    )
    crawler_max_retries: int = Field(
        default=3, description="Maximum retry attempts per URL"
    )

    # ── Chunking ────────────────────────────────────────────────────────
    chunk_size: int = Field(
        default=800, description="Target chunk size in tokens"
    )
    chunk_overlap: int = Field(
        default=120, description="Chunk overlap in tokens"
    )

    # ── Application ─────────────────────────────────────────────────────
    app_host: str = Field(default="0.0.0.0", description="Application host")
    app_port: int = Field(default=5001, description="Application port")
    debug: bool = Field(default=False, description="Debug mode")
    log_level: str = Field(default="INFO", description="Logging level")

    # ── Validators ──────────────────────────────────────────────────────

    @field_validator("user_tier")
    @classmethod
    def validate_user_tier(cls, v: str) -> str:
        allowed = {"max", "pro", "free", "lite"}
        if v.lower() not in allowed:
            raise ValueError(f"user_tier must be one of {allowed}, got '{v}'")
        return v.title()

    @field_validator("payment_provider")
    @classmethod
    def validate_payment_provider(cls, v: str) -> str:
        if v.lower() not in {"razorpay", "stripe", "none"}:
            raise ValueError("payment_provider must be razorpay, stripe, or none")
        return v.lower()

    @field_validator("embedding_provider")
    @classmethod
    def validate_embedding_provider(cls, v: str) -> str:
        allowed = {"gemini", "google", "local", "openai", "voyage"}
        if v.lower() not in allowed:
            raise ValueError(f"embedding_provider must be one of {allowed}, got '{v}'")
        return v.lower()

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        if v.lower() not in {"console", "json"}:
            raise ValueError("log_format must be console or json")
        return v.lower()

    @field_validator("pinecone_metric")
    @classmethod
    def validate_pinecone_metric(cls, v: str) -> str:
        allowed = {"cosine", "dotproduct", "euclidean"}
        if v.lower() not in allowed:
            raise ValueError(f"pinecone_metric must be one of {allowed}, got '{v}'")
        return v.lower()

    # ── Convenience Properties ──────────────────────────────────────────

    def estimate_llm_cost_usd(
        self,
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        thinking_tokens: int = 0,
    ) -> float | None:
        """Estimated USD cost of one generation from the configured price table.

        Model lookup falls back to the most expensive listed rate when the
        generating model is not in the table (conservative for margin math).
        Returns None when no price table is configured.
        """
        input_table = self.llm_price_input_per_m or {}
        output_table = self.llm_price_output_per_m or {}
        thinking_table = self.llm_price_thinking_per_m or {}
        if not input_table or not output_table:
            return None

        name = (model or "").strip().lower()
        matched = [k for k in input_table if name.startswith(k.lower())] if name else []
        if not matched and name:
            matched = [k for k in output_table if name.startswith(k.lower())]

        def _rate(table: dict[str, float]) -> float | None:
            if not table:
                return None
            if matched:
                for key in matched:
                    if key in table:
                        return float(table[key])
            return max(float(v) for v in table.values())

        input_rate = _rate(input_table)
        output_rate = _rate(output_table)
        thinking_rate = _rate(thinking_table)
        cost = 0.0
        if input_rate is not None:
            cost += (max(0, input_tokens) / 1_000_000) * input_rate
        if output_rate is not None:
            cost += (max(0, output_tokens) / 1_000_000) * output_rate
        if thinking_rate is not None:
            cost += (max(0, thinking_tokens) / 1_000_000) * thinking_rate
        return round(cost, 8)

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_searxng(self) -> bool:
        return bool(self.searxng_url)

    @property
    def has_tavily(self) -> bool:
        return bool(self.tavily_api_key)


    @property
    def has_aws(self) -> bool:
        return bool(self.aws_access_key_id and self.aws_secret_access_key)

    @property
    def has_gcp(self) -> bool:
        return bool(self.gcp_project_id)

    @property
    def has_azure(self) -> bool:
        return bool(self.azure_subscription_id)

    @property
    def has_pinecone(self) -> bool:
        return bool(self.pinecone_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]

    @property
    def developer_email_set(self) -> set[str]:
        configured = {email.strip().lower() for email in self.developer_emails.split(",") if email.strip()}
        if self.admin_email:
            configured.add(self.admin_email.strip().lower())
        return configured

    @property
    def attachment_allowed_extension_list(self) -> list[str]:
        return [ext.strip().lstrip(".").lower() for ext in self.attachment_allowed_extensions.split(",") if ext.strip()]

    def validate_startup(self) -> None:
        """Reject unsafe configuration and aggregate all configuration issues without revealing secrets."""
        problems: list[str] = []

        if self.environment.lower() == "production":
            if not self.gemini_api_key and not self.openai_api_key and not self.anthropic_api_key:
                problems.append("At least one LLM API key is required in production (GEMINI/OPENAI/ANTHROPIC)")
            if not self.secret_key or self.secret_key == "change-me-in-production":
                problems.append("SECRET_KEY must be set to a strong production value")
            if not self.database_url:
                problems.append("DATABASE_URL is required")
            if "*" in self.cors_origin_list:
                problems.append("CORS_ORIGINS must contain exact origins, never *")
            if self.billing_enabled:
                if self.payment_provider == "stripe":
                    required = {
                        "STRIPE_SECRET_KEY": self.stripe_secret_key,
                        "STRIPE_WEBHOOK_SECRET": self.stripe_webhook_secret,
                        "STRIPE_PRICE_PRO_MONTHLY": self.stripe_price_pro_monthly,
                    }
                    problems.extend(
                        f"{name} is required when billing is enabled with stripe"
                        for name, value in required.items()
                        if not value
                    )
                elif self.payment_provider == "razorpay":
                    required = {
                        "RAZORPAY_KEY_ID": self.razorpay_key_id,
                        "RAZORPAY_KEY_SECRET": self.razorpay_key_secret,
                    }
                    problems.extend(
                        f"{name} is required when billing is enabled with razorpay"
                        for name, value in required.items()
                        if not value
                    )

        if problems:
            raise ValueError("Invalid configuration:\n  • " + "\n  • ".join(problems))


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings singleton."""
    return Settings()
