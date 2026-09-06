"""Tiered cache policy engine — adaptive cache router + feedback-driven policy.

Implements the cache-control plane for the 3-tier RAG cache architecture:

- **Adaptive Cache Router (Apex):** turns runtime query state (routing
  strategy, risk level, confidence, complexity) into a CachePolicyDecision —
  which cache levels to consult, the semantic-similarity threshold to apply,
  and whether stage/answer writes are permitted at all.

- **Feedback-Driven Cache Policy (all tiers):** user feedback (thumbs down /
  up) writes short-lived policy records; the exact + semantic answer layers
  consult them before serving. A thumbs-down penalty makes the answer layers
  bypass that query (retrieval cache is unaffected — the evidence was fine,
  the synthesis wasn't). A thumbs-up boost extends the next answer-cache
  write TTL. Validation failures gate answer-cache writes at the source.

Redis is optional throughout: every helper degrades to a neutral no-op when
``redis_client.is_available`` is False, mirroring ``core/llm_cache.py``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from config import get_settings
from core.llm_cache import get_cached_query_policy, set_cached_query_policy
from core.memory_cache import get_memory_cache
from metrics import CACHE_POLICY_EVENTS

logger = structlog.get_logger(__name__)


@dataclass
class CachePolicyDecision:
    """Which cache levels a request may consult / populate."""

    exact_lookup: bool = True
    semantic_lookup: bool = True
    semantic_threshold: float | None = None  # None → tier/intent default
    decision_cache_lookup: bool = True
    stage_caches: bool = True
    retrieval_cache: bool = True
    answer_cache_write: bool = True
    answer_ttl_multiplier: float = 1.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exact_lookup": self.exact_lookup,
            "semantic_lookup": self.semantic_lookup,
            "semantic_threshold": self.semantic_threshold,
            "decision_cache_lookup": self.decision_cache_lookup,
            "stage_caches": self.stage_caches,
            "retrieval_cache": self.retrieval_cache,
            "answer_cache_write": self.answer_cache_write,
            "answer_ttl_multiplier": self.answer_ttl_multiplier,
            "reasons": self.reasons,
        }


def route_cache_policy(
    query: str,
    *,
    strategy: str | None = None,
    classification: Any = None,
    tier: str = "Max",
    risk_level: str | None = None,
    has_attachments: bool = False,
    chat_history: list[dict] | None = None,
    settings: Any = None,
) -> CachePolicyDecision:
    """Decide cache usage for one request from its runtime state (Apex router).

    Rules:
    - ``has_attachments`` → answer caching disabled completely (exact + semantic
      lookups and answer writes are bypassed to avoid cross-tenant context leaks).
    - Multi-turn conversation (len(chat_history) > 1) → semantic lookup bypassed
      (exact lookup preserved with history hash).
    - ``direct_fast`` (exact CLI/syntax) → semantic lookup only at the strict
      direct threshold: near-duplicate CLI questions that differ by a single
      flag must never cross-match an answer.
    - ``multi_perspective`` (comparisons) → strict comparison threshold.
    - ``semantic_hyde`` → tier/intent defaults.
    - High-risk queries → answer cache stays read-only for writes this round
      (production/financial answers should be freshly reasoned).
    - Low router confidence → answer cache write skipped.
    - Feedback penalty (thumbs down) → answer layers bypassed entirely.
    """
    settings = settings or get_settings()
    decision = CachePolicyDecision()

    if has_attachments:
        decision.exact_lookup = False
        decision.semantic_lookup = False
        decision.answer_cache_write = False
        decision.reasons.append("has_attachments→answer caches bypassed and writes disabled")

    if chat_history and len(chat_history) > 1:
        decision.semantic_lookup = False
        decision.reasons.append("multi_turn_history→semantic lookup bypassed")

    if strategy == "direct_fast":
        decision.semantic_threshold = float(
            getattr(settings, "adaptive_semantic_threshold_direct", 0.97)
        )
        decision.reasons.append(f"direct_fast→strict semantic threshold {decision.semantic_threshold}")
    elif strategy == "multi_perspective":
        decision.semantic_threshold = float(
            getattr(settings, "adaptive_semantic_threshold_multi_perspective", 0.95)
        )
        decision.reasons.append(f"multi_perspective→comparison threshold {decision.semantic_threshold}")

    risk = (risk_level or getattr(classification, "risk_level", "low") or "low").lower()
    if risk in ("medium", "high"):
        decision.answer_cache_write = False
        decision.reasons.append(f"risk={risk}→no answer-cache write")

    confidence = float(getattr(classification, "confidence", 1.0) or 1.0)
    if confidence < 0.5:
        decision.answer_cache_write = False
        decision.reasons.append("low_confidence→no answer-cache write")

    return decision


def apply_policy_record(decision: CachePolicyDecision, record: dict[str, Any] | None) -> CachePolicyDecision:
    """Fold a feedback policy record into a decision (mutates and returns it)."""
    if not record:
        return decision
    rating = record.get("rating")
    if rating == -1:
        decision.exact_lookup = False
        decision.semantic_lookup = False
        decision.answer_cache_write = False
        decision.reasons.append("feedback_penalty→answer layers bypassed")
        try:
            CACHE_POLICY_EVENTS.labels(action="bypass_serve").inc()
        except Exception:
            pass
    elif rating == 1:
        decision.answer_ttl_multiplier = max(
            decision.answer_ttl_multiplier,
            float(getattr(get_settings(), "feedback_boost_ttl_multiplier", 1.5)),
        )
        decision.reasons.append("feedback_boost→extended answer TTL")
    return decision


async def get_query_feedback_policy(query: str) -> dict[str, Any] | None:
    """Read the feedback policy record for a query (Redis-backed, safe no-op)."""
    if not query:
        return None
    try:
        return await get_cached_query_policy(query)
    except Exception as e:
        logger.debug("feedback_policy_read_failed error=%s", e)
        return None


async def record_feedback_policy(query: str, rating: int, reason: str | None = None) -> dict[str, Any]:
    """Persist the cache-policy consequence of a user feedback event.

    rating=-1 → penalty: answer caches refuse this query for
    ``feedback_penalty_ttl_seconds`` and the L1 answer entry is invalidated.
    rating=+1 → boost: next answer-cache write for this query uses an
    extended TTL.

    Returns the applied action dict (never raises).
    """
    settings = get_settings()
    applied: dict[str, Any] = {"recorded": False, "action": "none"}
    if not query or not getattr(settings, "enable_feedback_cache_policy", True):
        return applied

    action = "penalty" if rating == -1 else ("boost" if rating == 1 else "none")
    if action == "none":
        return applied

    try:
        if action == "penalty":
            ttl = int(getattr(settings, "feedback_penalty_ttl_seconds", 86400))
            record = {
                "rating": -1,
                "reason": (reason or "")[:200],
                "created_at": time.time(),
            }
            applied["recorded"] = await set_cached_query_policy(query, record, ttl)
            # Invalidate the L1 in-process entry immediately; Redis answer keys
            # are neutralized by the penalty record at read time.
            get_memory_cache().clear()
            applied["action"] = "penalty"
            applied["ttl_seconds"] = ttl
        else:
            ttl = int(getattr(settings, "feedback_penalty_ttl_seconds", 86400))
            record = {
                "rating": 1,
                "reason": (reason or "")[:200],
                "created_at": time.time(),
            }
            applied["recorded"] = await set_cached_query_policy(query, record, ttl)
            applied["action"] = "boost"
            applied["ttl_seconds"] = ttl
        try:
            CACHE_POLICY_EVENTS.labels(action=action).inc()
        except Exception:
            pass
        logger.info(
            "cache_policy.feedback_recorded",
            action=action,
            recorded=applied["recorded"],
            query_prefix=query[:60],
        )
    except Exception as e:
        logger.warning("cache_policy.feedback_record_failed error=%s", e)
        applied["recorded"] = False
    return applied


def skip_answer_cache_for_validation(
    report: dict[str, Any] | None, settings: Any = None
) -> bool:
    """True when an output-validation report forbids caching this answer."""
    if not report:
        return False
    settings = settings or get_settings()
    if not getattr(settings, "skip_answer_cache_on_validation_failure", True):
        return False
    grounding = (report.get("dimensions") or {}).get("grounding") or {}
    if grounding.get("passed") is False:
        try:
            CACHE_POLICY_EVENTS.labels(action="cache_skip_validation").inc()
        except Exception:
            pass
        return True
    return False


def answer_write_ttl(
    base_ttl_seconds: int,
    query: str,
    policy_record: dict[str, Any] | None = None,
    settings: Any = None,
) -> int:
    """Compute the answer-cache write TTL, applying any feedback boost."""
    settings = settings or get_settings()
    multiplier = 1.0
    if (policy_record or {}).get("rating") == 1:
        multiplier = float(getattr(settings, "feedback_boost_ttl_multiplier", 1.5))
    return int(max(1, base_ttl_seconds * multiplier))
