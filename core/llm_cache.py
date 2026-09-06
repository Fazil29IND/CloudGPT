"""
LLM Prompt and Response Multi-Layer In-Memory Caching using Redis.

Three explicit versioned layers:
- Layer 1 (Plan): Query classification / routing plans
- Layer 2 (Retrieval): Hybrid retrieval + reranked chunk lists
- Layer 3 (Answer): Final synthesized LLM responses with source citations
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from typing import Any, Optional

from config import get_settings
from core.memory_cache import get_memory_cache
from core.redis_client import redis_client
from metrics import (
    MEMORY_CACHE_HITS,
    MEMORY_CACHE_MISSES,
    REDIS_CACHE_HITS,
    REDIS_CACHE_MISSES,
    REDIS_LATENCY,
)

logger = logging.getLogger(__name__)


def _jitter_ttl(base_ttl: int, jitter_pct: float = 0.15) -> int:
    """Add a small randomized jitter to TTL to prevent cache stampedes."""
    if base_ttl <= 0:
        return base_ttl
    jitter = random.randint(0, max(1, int(base_ttl * jitter_pct)))
    return base_ttl + jitter


# ─── Layer 1: Query Plan (Classification) ───────────────────────────────────

def _plan_key(query_normalized: str, router_version: str) -> str:
    digest = hashlib.sha256(query_normalized.encode("utf-8")).hexdigest()[:24]
    return f"rag:v2:plan:{router_version}:{digest}"


async def get_cached_query_plan(
    query: str,
    settings: Any = None,
) -> Optional[dict[str, Any]]:
    """Retrieve cached query classification plan."""
    if not redis_client.is_available:
        return None

    settings = settings or get_settings()
    router_version = getattr(settings, "cache_router_version", "v1")
    key = _plan_key(query.strip().lower(), router_version)

    t0 = time.perf_counter()
    data = await redis_client.get_json(key)
    elapsed = time.perf_counter() - t0
    REDIS_LATENCY.labels(op="get_plan").observe(elapsed)

    if data is not None:
        REDIS_CACHE_HITS.labels(layer="plan").inc()
        logger.debug("Plan Cache Hit for key: %s", key)
    else:
        REDIS_CACHE_MISSES.labels(layer="plan").inc()

    return data


async def set_cached_query_plan(
    query: str,
    plan: dict[str, Any],
    settings: Any = None,
    ttl_seconds: int = 900,
) -> bool:
    """Cache query classification plan in Redis."""
    if not redis_client.is_available:
        return False

    settings = settings or get_settings()
    router_version = getattr(settings, "cache_router_version", "v1")
    key = _plan_key(query.strip().lower(), router_version)
    actual_ttl = _jitter_ttl(ttl_seconds)

    t0 = time.perf_counter()
    success = await redis_client.set_json(key, plan, ex=actual_ttl)
    elapsed = time.perf_counter() - t0
    REDIS_LATENCY.labels(op="set_plan").observe(elapsed)

    return success


# ─── Layer 2: Retrieval Result ──────────────────────────────────────────────

def _retrieval_key(
    query_normalized: str,
    corpus_version: str,
    embedding_model: str,
    provider_filter: Optional[str] = None,
) -> str:
    if query_normalized.startswith(("agentic:", "adaptive:", "rag:v2:retrieval:")):
        return query_normalized
    payload = f"{corpus_version}:{embedding_model}:{provider_filter or 'all'}:{query_normalized}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"rag:v2:retrieval:{digest}"


async def get_cached_retrieval_result(
    query: str,
    provider_filter: Optional[str] = None,
    settings: Any = None,
) -> Optional[list[dict[str, Any]]]:
    """Retrieve cached retrieval + reranking candidate results with L1 memory fallback."""
    settings = settings or get_settings()
    corpus_version = getattr(settings, "cache_corpus_version", "v1")
    embedding_model = getattr(settings, "embedding_model", "default")
    key = _retrieval_key(query.strip().lower(), corpus_version, embedding_model, provider_filter)

    # 1. Check L1 in-process memory cache first
    l1 = get_memory_cache()
    if getattr(settings, "enable_l1_cache", True):
        l1_result = l1.get(key)
        if l1_result is not None:
            REDIS_CACHE_HITS.labels(layer="retrieval").inc()
            logger.debug("L1 Retrieval Cache Hit for key: %s", key)
            return l1_result

    if not redis_client.is_available:
        return None

    t0 = time.perf_counter()
    data = await redis_client.get_json(key)
    elapsed = time.perf_counter() - t0
    REDIS_LATENCY.labels(op="get_retrieval").observe(elapsed)

    if data is not None:
        REDIS_CACHE_HITS.labels(layer="retrieval").inc()
        logger.debug("Retrieval Cache Hit for key: %s", key)
        if getattr(settings, "enable_l1_cache", True):
            l1_ttl = getattr(settings, "memory_cache_ttl_seconds", 60)
            l1.set(key, data, ttl=l1_ttl)
    else:
        REDIS_CACHE_MISSES.labels(layer="retrieval").inc()

    return data


async def set_cached_retrieval_result(
    query: str,
    results: list[Any],
    provider_filter: Optional[str] = None,
    settings: Any = None,
    ttl_seconds: int = 21600,
) -> bool:
    """Cache retrieval results in L1 memory and Redis (default TTL: 6 hours with jitter)."""
    settings = settings or get_settings()
    corpus_version = getattr(settings, "cache_corpus_version", "v1")
    embedding_model = getattr(settings, "embedding_model", "default")
    key = _retrieval_key(query.strip().lower(), corpus_version, embedding_model, provider_filter)

    # Ensure all elements are clean JSON-serializable dictionaries
    serializable_results: list[dict[str, Any]] = [
        r
        if isinstance(r, dict)
        else {
            "chunk_id": str(getattr(r, "chunk_id", "")),
            "text": str(getattr(r, "text", "")),
            "score": float(getattr(r, "score", 1.0)),
            "metadata": dict(getattr(r, "metadata", {}) or {}),
        }
        for r in results
    ]

    # Write to L1 memory cache
    if getattr(settings, "enable_l1_cache", True):
        l1_ttl = getattr(settings, "memory_cache_ttl_seconds", 60)
        get_memory_cache().set(key, serializable_results, ttl=l1_ttl)

    if not redis_client.is_available:
        return True

    actual_ttl = _jitter_ttl(ttl_seconds)
    t0 = time.perf_counter()
    success = await redis_client.set_json(key, serializable_results, ex=actual_ttl)
    elapsed = time.perf_counter() - t0
    REDIS_LATENCY.labels(op="set_retrieval").observe(elapsed)

    return success


# ─── Layer 3: Final Answer ──────────────────────────────────────────────────

def _answer_key(
    query_normalized: str,
    model: str,
    mode: str,
    corpus_version: str,
    prompt_version: str,
    provider_filter: Optional[str] = None,
    history_hash: Optional[str] = None,
) -> str:
    payload = f"{prompt_version}:{model}:{mode}:{corpus_version}:{provider_filter or 'all'}:{query_normalized}:{history_hash or 'none'}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"rag:v2:answer:{digest}"


async def get_cached_answer(
    query: str,
    model: str = "",
    mode: str = "",
    provider_filter: Optional[str] = None,
    history_hash: Optional[str] = None,
    settings: Any = None,
) -> Optional[dict[str, Any]]:
    """Retrieve cached final LLM answer response with L1 fallback and canonical key resolution."""
    settings = settings or get_settings()
    corpus_version = getattr(settings, "cache_corpus_version", "v1")
    prompt_version = getattr(settings, "cache_prompt_version", "v1")
    normalized_query = query.strip().lower()

    key = _answer_key(
        query_normalized=normalized_query,
        model=model,
        mode=mode or "default",
        corpus_version=corpus_version,
        prompt_version=prompt_version,
        provider_filter=provider_filter,
        history_hash=history_hash,
    )

    # 1. Check L1 in-process memory cache first
    l1 = get_memory_cache()
    if getattr(settings, "enable_l1_cache", True):
        l1_result = l1.get(key)
        if l1_result is not None:
            MEMORY_CACHE_HITS.labels(layer="l1_answer").inc()
            logger.debug("L1 Answer Cache Hit for key: %s", key)
            return l1_result
        # If specific model requested but missed in L1, check canonical key
        if model:
            canonical_key = _answer_key(
                query_normalized=normalized_query,
                model="",
                mode=mode or "default",
                corpus_version=corpus_version,
                prompt_version=prompt_version,
                provider_filter=provider_filter,
                history_hash=history_hash,
            )
            l1_canonical = l1.get(canonical_key)
            if l1_canonical is not None:
                MEMORY_CACHE_HITS.labels(layer="l1_answer").inc()
                return l1_canonical
        MEMORY_CACHE_MISSES.labels(layer="l1_answer").inc()

    if not redis_client.is_available:
        return None

    # 2. Check L2 Redis cache
    t0 = time.perf_counter()
    data = await redis_client.get_json(key)
    elapsed = time.perf_counter() - t0
    REDIS_LATENCY.labels(op="get_answer").observe(elapsed)

    # If specific model key missed in Redis, check canonical model-agnostic key
    if data is None and model:
        canonical_key = _answer_key(
            query_normalized=normalized_query,
            model="",
            mode=mode or "default",
            corpus_version=corpus_version,
            prompt_version=prompt_version,
            provider_filter=provider_filter,
            history_hash=history_hash,
        )
        data = await redis_client.get_json(canonical_key)

    # Fallback to v1 legacy key for zero-downtime transition if not found in v2
    if data is None:
        legacy_key = generate_llm_cache_key(
            query=query, model=model, provider_filter=provider_filter, mode=mode, history_hash=history_hash
        )
        data = await redis_client.get_json(legacy_key)

    if data is not None:
        REDIS_CACHE_HITS.labels(layer="answer").inc()
        logger.debug("Answer Cache Hit for key: %s", key)
        # Backfill L1
        if getattr(settings, "enable_l1_cache", True):
            l1_ttl = getattr(settings, "memory_cache_ttl_seconds", 60)
            l1.set(key, data, ttl=l1_ttl)
    else:
        REDIS_CACHE_MISSES.labels(layer="answer").inc()

    return data


async def set_cached_answer(
    query: str,
    response_payload: dict[str, Any],
    model: str = "",
    mode: str = "",
    provider_filter: Optional[str] = None,
    history_hash: Optional[str] = None,
    settings: Any = None,
    ttl_seconds: Optional[int] = None,
) -> bool:
    """Cache final LLM answer in L1 memory and Redis with TTL jitter and canonical dual-write."""
    settings = settings or get_settings()
    corpus_version = getattr(settings, "cache_corpus_version", "v1")
    prompt_version = getattr(settings, "cache_prompt_version", "v1")
    normalized_query = query.strip().lower()

    key = _answer_key(
        query_normalized=normalized_query,
        model=model,
        mode=mode or "default",
        corpus_version=corpus_version,
        prompt_version=prompt_version,
        provider_filter=provider_filter,
        history_hash=history_hash,
    )

    # Determine keys to write: always write the specified key, plus canonical key if model was given
    keys_to_write = [key]
    if model:
        canonical_key = _answer_key(
            query_normalized=normalized_query,
            model="",
            mode=mode or "default",
            corpus_version=corpus_version,
            prompt_version=prompt_version,
            provider_filter=provider_filter,
            history_hash=history_hash,
        )
        if canonical_key not in keys_to_write:
            keys_to_write.append(canonical_key)

    # Write to L1 memory cache
    if getattr(settings, "enable_l1_cache", True):
        l1_ttl = getattr(settings, "memory_cache_ttl_seconds", 60)
        l1 = get_memory_cache()
        for k in keys_to_write:
            l1.set(k, response_payload, ttl=l1_ttl)

    if not redis_client.is_available:
        return True

    base_ttl = ttl_seconds if ttl_seconds is not None else settings.redis_cache_ttl_seconds
    actual_ttl = _jitter_ttl(base_ttl)

    t0 = time.perf_counter()
    success = True
    for k in keys_to_write:
        res = await redis_client.set_json(k, response_payload, ex=actual_ttl)
        if not res:
            success = False
    elapsed = time.perf_counter() - t0
    REDIS_LATENCY.labels(op="set_answer").observe(elapsed)

    return success


async def invalidate_corpus_cache(new_corpus_version: str = "v2") -> int:
    """Optionally evict stale Layer-2 (retrieval) and Layer-3 (answer) keys early.

    Natural invalidation occurs via CACHE_CORPUS_VERSION bump in Settings, but this
    allows an operator to free memory immediately without touching session or tool keys.
    """
    if not redis_client.is_available:
        return 0

    client = getattr(redis_client, "_client", None)
    if client is None:
        return 0

    evicted = 0
    patterns = ["rag:v2:retrieval:*", "rag:v2:answer:*", "rag:v2:decision:*", "rag:v2:stage:*", "rag:v2:policy:*", "semcache:*"]
    for pattern in patterns:
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                await client.delete(*keys)
                evicted += len(keys)
            if cursor == 0:
                break
    logger.info(
        "llm_cache.corpus_cache_invalidated new_version=%s evicted_keys=%s",
        new_corpus_version,
        evicted,
    )
    return evicted


# ─── Structured Decision Cache (per-scope stage decisions) ──────────────────

_DECISION_SCOPES = ("agentic_plan", "adaptive_transform", "router_classification")


def _decision_key(scope: str, query_normalized: str, producer_version: str) -> str:
    digest = hashlib.sha256(f"{scope}:{query_normalized}".encode("utf-8")).hexdigest()[:24]
    return f"rag:v2:decision:{scope}:{producer_version}:{digest}"


async def get_cached_decision(
    scope: str,
    query: str,
    producer_version: str,
    settings: Any = None,
) -> Optional[dict[str, Any]]:
    """Retrieve a cached structured stage decision (e.g. agentic plan, adaptive transform).

    ``scope`` separates producers that emit different schemas for the same
    query (agentic_plan, adaptive_transform, router_classification).
    ``producer_version`` should be the prompt/version pointer that invalidates
    the decision when its producer changes (e.g. cache_router_version).
    Degrades safely to L1 in-process memory cache when Redis is unavailable.
    """
    if scope not in _DECISION_SCOPES:
        return None

    settings = settings or get_settings()
    key = _decision_key(scope, query.strip().lower(), producer_version)

    # 1. Check L1 in-process memory cache
    l1 = get_memory_cache()
    if getattr(settings, "enable_l1_cache", True):
        l1_val = l1.get(key)
        if l1_val is not None:
            REDIS_CACHE_HITS.labels(layer="decision").inc()
            logger.debug("L1 Decision Cache Hit for key: %s", key)
            return l1_val

    if not redis_client.is_available:
        return None

    t0 = time.perf_counter()
    data = await redis_client.get_json(key)
    elapsed = time.perf_counter() - t0
    REDIS_LATENCY.labels(op="get_decision").observe(elapsed)

    if data is not None:
        REDIS_CACHE_HITS.labels(layer="decision").inc()
        logger.debug("Decision Cache Hit for key: %s", key)
        if getattr(settings, "enable_l1_cache", True):
            l1.set(key, data, ttl=getattr(settings, "memory_cache_ttl_seconds", 60))
    else:
        REDIS_CACHE_MISSES.labels(layer="decision").inc()
    return data


async def set_cached_decision(
    scope: str,
    query: str,
    decision: dict[str, Any],
    producer_version: str,
    settings: Any = None,
    ttl_seconds: int = 900,
) -> bool:
    """Cache a structured stage decision with TTL jitter and L1 memory fallback."""
    if scope not in _DECISION_SCOPES:
        return False

    settings = settings or get_settings()
    key = _decision_key(scope, query.strip().lower(), producer_version)

    # Write to L1 in-process memory cache
    if getattr(settings, "enable_l1_cache", True):
        l1 = get_memory_cache()
        l1.set(key, decision, ttl=float(ttl_seconds))

    if not redis_client.is_available:
        return True

    t0 = time.perf_counter()
    success = await redis_client.set_json(key, decision, ex=_jitter_ttl(ttl_seconds))
    elapsed = time.perf_counter() - t0
    REDIS_LATENCY.labels(op="set_decision").observe(elapsed)
    return success


# ─── Multi-Level Stage-Aware Cache Helpers (Apex) ──────────────────────────

async def get_cached_stage(stage_key: str, settings: Any = None) -> Optional[Any]:
    """Retrieve a cached pipeline stage output (e.g. rerank+compress) with L1 fallback."""
    settings = settings or get_settings()
    l1 = get_memory_cache()
    if getattr(settings, "enable_l1_cache", True):
        l1_res = l1.get(stage_key)
        if l1_res is not None:
            return l1_res

    if not redis_client.is_available:
        return None

    try:
        data = await redis_client.get_json(stage_key)
        if data is not None and getattr(settings, "enable_l1_cache", True):
            l1.set(stage_key, data, ttl=getattr(settings, "memory_cache_ttl_seconds", 60))
        return data
    except Exception as e:
        logger.debug("stage_cache_read_failed error=%s", e)
        return None


async def set_cached_stage(
    stage_key: str,
    data: Any,
    ttl_seconds: int = 1800,
    settings: Any = None,
) -> bool:
    """Cache a pipeline stage output in L1 memory and Redis with TTL jitter."""
    settings = settings or get_settings()
    if getattr(settings, "enable_l1_cache", True):
        l1 = get_memory_cache()
        l1.set(stage_key, data, ttl=float(ttl_seconds))

    if not redis_client.is_available:
        return True

    try:
        actual_ttl = _jitter_ttl(ttl_seconds)
        return await redis_client.set_json(stage_key, data, ex=actual_ttl)
    except Exception as e:
        logger.debug("stage_cache_write_failed error=%s", e)
        return False


# ─── Feedback-Driven Policy Records ─────────────────────────────────────────

def policy_record_key(query_normalized: str) -> str:
    digest = hashlib.sha256(query_normalized.encode("utf-8")).hexdigest()[:24]
    return f"rag:v2:policy:{digest}"


async def get_cached_query_policy(query: str, settings: Any = None) -> Optional[dict[str, Any]]:
    """Read the feedback-driven policy record for a query, if any (L1 + Redis)."""
    normalized = query.strip().lower()
    key = policy_record_key(normalized)
    l1 = get_memory_cache()
    l1_val = l1.get(key)
    if l1_val is not None:
        return l1_val

    if not redis_client.is_available:
        return None
    try:
        data = await redis_client.get_json(key)
        if data is not None:
            l1.set(key, data, ttl=60.0)
        return data
    except Exception as e:
        logger.debug("policy_record_read_failed error=%s", e)
        return None


async def set_cached_query_policy(
    query: str, record: dict[str, Any], ttl_seconds: int, settings: Any = None
) -> bool:
    """Write a feedback-driven policy record for a query (L1 + Redis)."""
    normalized = query.strip().lower()
    key = policy_record_key(normalized)
    l1 = get_memory_cache()
    l1.set(key, record, ttl=float(ttl_seconds))

    if not redis_client.is_available:
        return True
    try:
        return await redis_client.set_json(key, record, ex=max(1, int(ttl_seconds)))
    except Exception as e:
        logger.debug("policy_record_write_failed error=%s", e)
        return False


async def delete_cached_query_policy(query: str) -> int:
    normalized = query.strip().lower()
    key = policy_record_key(normalized)
    get_memory_cache().invalidate(key)

    if not redis_client.is_available:
        return 1
    try:
        return await redis_client.delete(key)
    except Exception as e:
        logger.debug("policy_record_delete_failed error=%s", e)
        return 0


# ─── Backward Compatibility Shims ──────────────────────────────────────────

def generate_llm_cache_key(
    query: str,
    model: str = "",
    provider_filter: Optional[str] = None,
    mode: str = "",
    history_hash: Optional[str] = None,
) -> str:
    """Legacy deterministic SHA-256 cache key for an LLM query."""
    normalized_query = query.strip().lower()
    payload = f"{model}:{mode or 'default'}:{provider_filter or 'all'}:{normalized_query}:{history_hash or 'none'}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"llm_cache:{digest}"


async def get_cached_llm_response(
    query: str,
    model: str = "",
    provider_filter: Optional[str] = None,
    mode: str = "",
    history_hash: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Backward-compatible shim calling get_cached_answer."""
    return await get_cached_answer(
        query=query,
        model=model,
        mode=mode,
        provider_filter=provider_filter,
        history_hash=history_hash,
    )


async def set_cached_llm_response(
    query: str,
    response_payload: dict[str, Any],
    model: str = "",
    provider_filter: Optional[str] = None,
    mode: str = "",
    history_hash: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> bool:
    """Backward-compatible shim calling set_cached_answer."""
    return await set_cached_answer(
        query=query,
        response_payload=response_payload,
        model=model,
        mode=mode,
        provider_filter=provider_filter,
        history_hash=history_hash,
        ttl_seconds=ttl_seconds,
    )
