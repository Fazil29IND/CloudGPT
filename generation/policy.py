"""Dynamic policy digest and pre-generation decisions for Adaptive Agentic RAG.

The Apex tier selects a retrieval strategy per query (direct_fast /
semantic_hyde / multi_perspective), collects live-verification results, and
tracks staleness. ``build_policy_digest`` condenses that runtime state into a
``<pipeline_policy>`` block appended to the system prompt so generation is
policy-aware rather than one-size-fits-all.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def build_policy_digest(
    *,
    strategy: str | None,
    complexity_score: float = 0.5,
    confidence: float = 0.5,
    evidence_stats: dict[str, Any] | None = None,
    live_verified: bool = False,
    tier: str = "Max",
) -> str:
    """Render the dynamic generation policy block for the Apex system prompt."""
    stats = evidence_stats or {}
    strategy = strategy or "semantic_hyde"

    focus = {
        "direct_fast": (
            "Response focus: exact syntax, flags, and defaults. Be maximally terse; "
            "verify every command token against the evidence verbatim."
        ),
        "semantic_hyde": (
            "Response focus: conceptual explanation grounded in retrieved documentation; "
            "expand with architectural context only where evidence supports it."
        ),
        "multi_perspective": (
            "Response focus: balanced multi-dimensional comparison (architecture, pricing, "
            "performance). Cover every dimension the planner retrieved evidence for; state "
            "asymmetries honestly instead of forcing parity."
        ),
    }.get(strategy, "Response focus: grounded technical answer.")

    evidence_count = int(stats.get("evidence_count", 0))
    stale_count = int(stats.get("stale_count", 0))
    avg_score = float(stats.get("avg_score", 0.0))
    compressed_count = int(stats.get("compressed_count", 0))

    grounding_line = (
        f"Evidence state: {evidence_count} chunks, mean relevance {avg_score:.2f}, "
        f"{compressed_count} compressed, {stale_count} stale."
    )
    if stale_count:
        grounding_line += " Stale chunks present: date-stamp any claim that depends on them."
    if live_verified:
        grounding_line += (
            " Live provider documentation was retrieved during verification — prefer it over "
            "static chunks where they conflict, and say so."
        )
    if evidence_count == 0 or avg_score < 0.35:
        grounding_line += (
            " Evidence is weak or absent: state explicitly that verified documentation could "
            "not be retrieved and mark architectural guidance as general best practice."
        )

    depth = (
        "full blueprint" if complexity_score >= 0.65
        else ("standard answer" if complexity_score >= 0.4 else "concise direct answer")
    )

    digest = "\n".join(
        [
            "<pipeline_policy>",
            f"routing_strategy: {strategy}",
            f"query_complexity: {complexity_score:.2f} (target response depth: {depth})",
            f"router_confidence: {confidence:.2f}",
            grounding_line,
            focus,
            (
                "Validation policy: answers are claim-checked against evidence after generation; "
                "unsupported specific values, flags, quotas, or prices will trigger revision."
            ),
            "</pipeline_policy>",
        ]
    )
    logger.debug("policy_digest.built", strategy=strategy, evidence_count=evidence_count)
    return digest


def pre_generation_decision(
    *,
    candidates: list[Any],
    live_verify_results: list[dict[str, Any]] | None,
    classification_confidence: float,
    settings: Any,
) -> dict[str, Any]:
    """Decide the generation mode before the main LLM call (Apex).

    Returns {"mode": "grounded" | "boundary", ...}. Full abstention is a
    post-generation, policy-based decision (see generation.validator); before
    generating we only decide whether the model must operate in an explicit
    knowledge-boundary mode.
    """
    has_evidence = bool(candidates)
    has_live = bool(live_verify_results)
    confidence = float(classification_confidence or 0.0)
    floor = float(getattr(settings, "adaptive_abstain_min_support", 0.30))

    if not has_evidence and not has_live and confidence < max(0.45, floor + 0.15):
        return {
            "mode": "boundary",
            "reason": "no_evidence_no_live_low_confidence",
        }
    return {"mode": "grounded", "reason": "evidence_or_live_available"}
