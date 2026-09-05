"""
Token estimation and Prometheus telemetry for the CloudGPT context pipeline.

Provides cl100k-based token estimation and records per-section token breakdowns
(RAG, web, tools, attachments, history, system prompt, instructions, memory)
into Prometheus metrics.
"""

from __future__ import annotations

import logging

from metrics import PROMPT_OVERFLOW_TOTAL, PROMPT_SECTION_TOKENS, PROMPT_TOTAL_TOKENS

logger = logging.getLogger(__name__)

try:
    import tiktoken
    _TIKTOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover
    _TIKTOKEN_ENCODER = None


def estimate_tokens(text: str | None) -> int:
    """Estimate token count for a text string using cl100k_base or len // 4 fallback."""
    if not text:
        return 0
    if _TIKTOKEN_ENCODER is not None:
        try:
            return len(_TIKTOKEN_ENCODER.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def record_prompt_breakdown(
    section_tokens: dict[str, int],
    tier: str = "Free",
    model: str = "unknown",
    budget_cap: int | None = None,
) -> int:
    """
    Record section-by-section token metrics and total prompt size in Prometheus.

    Args:
        section_tokens: Mapping of section name to estimated token count.
        tier: User plan tier (Free, Pro, Max).
        model: Target LLM model name.
        budget_cap: Optional total prompt budget cap to detect overflows.

    Returns:
        The total assembled prompt tokens.
    """
    canonical_tier = (tier or "Free").capitalize()
    canonical_model = model or "unknown"
    total_tokens = 0

    for section, count in section_tokens.items():
        if count > 0:
            total_tokens += count
            try:
                PROMPT_SECTION_TOKENS.labels(section=section, tier=canonical_tier).observe(count)
            except Exception as e:
                logger.debug("Failed to record PROMPT_SECTION_TOKENS: %s", e)

    try:
        PROMPT_TOTAL_TOKENS.labels(tier=canonical_tier, model=canonical_model).observe(total_tokens)
    except Exception as e:
        logger.debug("Failed to record PROMPT_TOTAL_TOKENS: %s", e)

    if budget_cap and total_tokens > budget_cap:
        try:
            PROMPT_OVERFLOW_TOTAL.labels(tier=canonical_tier, section="total").inc()
        except Exception as e:
            logger.debug("Failed to record PROMPT_OVERFLOW_TOTAL: %s", e)

    logger.debug(
        "context_metrics.recorded",
        extra={"tier": canonical_tier, "total_tokens": total_tokens, "sections": section_tokens},
    )
    return total_tokens
