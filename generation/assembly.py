"""Evidence assembly strategies (Layer 3 — Context Assembly).

- order_fixed_evidence:        Lite — deterministic fixed assembly (stale demotion,
                               score order) feeding the token-budgeted ContextBuilder.
- assemble_plan_aware_evidence: Core — order evidence by plan sub-goal coverage and
                               emit a plan digest that rides into the prompt.
- assemble_dynamic_evidence:   Apex — strategy-ordered evidence plus a dynamic
                               budget multiplier for the tier context budget.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _text_of(chunk: Any) -> str:
    return getattr(chunk, "text", None) or (chunk.get("content", "") if isinstance(chunk, dict) else "")


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9_\-.]*", (text or "").lower()) if len(t) > 1}


def order_fixed_evidence(chunks: list[Any]) -> list[Any]:
    """Lite fixed evidence-aware assembly: demote stale chunks, keep score order."""
    def _key(c: Any) -> tuple[int, float]:
        meta = getattr(c, "metadata", None) or (c.get("metadata", {}) if isinstance(c, dict) else {})
        try:
            stale = 1 if meta.get("stale") else 0
        except Exception:
            stale = 0
        try:
            score = float(getattr(c, "score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        return (stale, -score)

    return sorted(chunks, key=_key)


def assemble_plan_aware_evidence(
    chunks: list[Any],
    plan: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], str]:
    """Core plan-aware assembly.

    Returns (rag_dicts, plan_digest). Chunks are annotated with the plan
    sub-goals they cover and ordered by sub-goal coverage then grade score, so
    the generator reads evidence grouped by the agent's retrieval plan.
    """
    plan = plan or {}
    sub_goals = [str(s).strip() for s in (plan.get("sub_queries") or []) if str(s).strip()]
    sub_goal_tokens = [_tokens(g) for g in sub_goals]

    rag_dicts: list[dict[str, Any]] = []
    for chunk in chunks:
        meta = getattr(chunk, "metadata", {}) or {}
        text = _text_of(chunk)
        covered: list[int] = []
        if text:
            text_toks = _tokens(text)
            for idx, gt in enumerate(sub_goal_tokens):
                # Require a meaningful share of the sub-goal's terms, not a
                # single incidental token, to count a chunk as covering it.
                if gt and len(gt & text_toks) >= max(2, len(gt) // 3):
                    covered.append(idx)
        rag_dicts.append(
            {
                "chunk_id": chunk.chunk_id,
                "provider": meta.get("provider", "cloud"),
                "service": meta.get("service", ""),
                "section": meta.get("section", ""),
                "url": meta.get("url", ""),
                "content": text,
                "title": meta.get("title", ""),
                "parent_chunk_id": meta.get("parent_chunk_id"),
                "hierarchy_level": meta.get("hierarchy_level", 1),
                "is_coalesced_parent": meta.get("is_coalesced_parent", False),
                "grade_score": float(meta.get("grade_score", chunk.score)),
                "plan_goals": covered,
                "plan_role": (
                    "multi_goal" if len(covered) >= 2
                    else (f"goal_{covered[0]}" if covered else "general")
                ),
            }
        )

    rag_dicts.sort(
        key=lambda d: (-len(d["plan_goals"]), -d["grade_score"]) if sub_goals else (-d["grade_score"],)
    )

    digest_lines = [
        "<retrieval_plan>",
        f"intent: {plan.get('intent', 'explain')}",
        f"strategy: {plan.get('retrieval_strategy', 'broad')} (modality: {plan.get('retrieval_modality', 'hybrid')})",
        f"complexity: {plan.get('complexity_score', 0.35)}",
    ]
    for i, goal in enumerate(sub_goals):
        n = sum(1 for d in rag_dicts if i in d["plan_goals"])
        digest_lines.append(f"sub_goal_{i}: {goal} — evidence chunks covering: {n}")
    if not sub_goals:
        digest_lines.append("sub_goals: none (focused single-pass retrieval)")
    digest_lines.append("</retrieval_plan>")
    digest = "\n".join(digest_lines)

    logger.debug(
        "assembly.plan_aware",
        chunks=len(rag_dicts),
        sub_goals=len(sub_goals),
        multi_goal=sum(1 for d in rag_dicts if d["plan_role"] == "multi_goal"),
    )
    return rag_dicts, digest


# Dynamic budget multipliers by Apex routing strategy — multi-perspective
# comparisons need more room; direct syntax lookups need less.
_STRATEGY_BUDGET_MULTIPLIER = {
    "direct_fast": 0.85,
    "semantic_hyde": 1.0,
    "multi_perspective": 1.15,
}


def assemble_dynamic_evidence(
    chunks: list[Any],
    strategy: str | None,
    complexity_score: float = 0.5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apex dynamic assembly.

    Returns (rag_dicts, assembly_stats). Evidence ordering adapts to the
    routing strategy and the stats drive the dynamic policy digest and budget.
    """
    ordered = order_fixed_evidence(chunks)
    rag_dicts: list[dict[str, Any]] = []
    for chunk in ordered:
        meta = getattr(chunk, "metadata", {}) or {}
        rag_dicts.append(
            {
                "chunk_id": chunk.chunk_id,
                "provider": meta.get("provider", "cloud"),
                "service": meta.get("service", ""),
                "section": meta.get("section", ""),
                "url": meta.get("url", ""),
                "content": _text_of(chunk),
                "title": meta.get("title", ""),
                "parent_chunk_id": meta.get("parent_chunk_id"),
                "hierarchy_level": meta.get("hierarchy_level", 1),
                "is_coalesced_parent": meta.get("is_coalesced_parent", False),
                "grade_score": float(meta.get("grade_score", chunk.score)),
                "compressed": bool(meta.get("compressed", False)),
                "stale": bool(meta.get("stale", False)),
            }
        )

    scores = [float(getattr(c, "score", 0.0) or 0.0) for c in ordered]
    stats = {
        "strategy": strategy or "semantic_hyde",
        "complexity_score": complexity_score,
        "evidence_count": len(rag_dicts),
        "avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "max_score": max(scores) if scores else 0.0,
        "stale_count": sum(1 for d in rag_dicts if d["stale"]),
        "compressed_count": sum(1 for d in rag_dicts if d["compressed"]),
        "budget_multiplier": _STRATEGY_BUDGET_MULTIPLIER.get(strategy or "semantic_hyde", 1.0),
    }
    return rag_dicts, stats
