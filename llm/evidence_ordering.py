"""
Enterprise Evidence Ordering Engine (llm/evidence_ordering.py).

Provides multi-strategy evidence reordering:
1. U_CURVE: Classic primacy/recency distribution to mitigate 'Lost in the Middle'.
2. SCORE_DESCENDING: Strict relevance rank-ordering.
3. COHERENCE_PRESERVING: Groups related cloud providers and services together.
4. ADAPTIVE: Selects the optimal ordering strategy dynamically based on target
   model architecture, context length, and chunk distribution.
"""

from __future__ import annotations

from typing import Any
from .context_types import OrderingStrategy


class EvidenceOrderingManager:
    """Manages evidence ordering algorithms for LLM context assembly."""

    @classmethod
    def reorder_u_curve(cls, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Reorders chunks using U-curve attention optimization:
        - Primacy boundary: Rank 2, Rank 4, ...
        - Recency boundary: Rank 1, Rank 3, ...
        """
        if len(chunks) <= 2:
            return list(chunks)

        primacy: list[dict[str, Any]] = []
        recency: list[dict[str, Any]] = []

        for idx, chunk in enumerate(chunks):
            if idx % 2 == 0:
                recency.append(chunk)
            else:
                primacy.append(chunk)

        return primacy + list(reversed(recency))

    @classmethod
    def reorder_score_descending(cls, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sorts chunks strictly by relevance score descending."""
        def _get_score(c: dict[str, Any]) -> float:
            s = c.get("score") or c.get("relevance_score") or c.get("similarity") or 0.0
            try:
                return float(s)
            except (ValueError, TypeError):
                return 0.0

        return sorted(chunks, key=_get_score, reverse=True)

    @classmethod
    def reorder_coherence_preserving(cls, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Groups chunks by provider and service hierarchy to preserve semantic continuity
        and architectural reasoning flow across multi-cloud documents.
        """
        if len(chunks) <= 2:
            return list(chunks)

        # Preserve top chunk at primacy
        top_chunk = chunks[0]
        remaining = chunks[1:]

        # Group by provider/service
        groups: dict[str, list[dict[str, Any]]] = {}
        for c in remaining:
            prov = str(c.get("provider", "other")).lower()
            svc = str(c.get("service", "other")).lower()
            key = f"{prov}:{svc}"
            groups.setdefault(key, []).append(c)

        ordered = [top_chunk]
        for key in sorted(groups.keys()):
            ordered.extend(groups[key])

        return ordered

    @classmethod
    def reorder_adaptive(
        cls,
        chunks: list[dict[str, Any]],
        model_name: str | None = None,
        context_tokens: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Dynamically chooses between U-Curve, Coherence, or Score-descending based
        on model family, context scale, and provider diversity.
        """
        if len(chunks) <= 2:
            return list(chunks)

        norm_model = (model_name or "").lower()

        # Frontier long-context models (Gemini 2.5 Pro/Flash with 1M context, Claude 3.7 with 200k)
        # perform best with coherent, sequential grouping.
        if "gemini" in norm_model or "claude" in norm_model or context_tokens > 16000:
            # Check if multi-cloud
            providers = {str(c.get("provider", "")).lower() for c in chunks if c.get("provider")}
            if len(providers) > 1:
                return cls.reorder_coherence_preserving(chunks)
            return cls.reorder_score_descending(chunks)

        # Default for compact/constrained context windows: U-curve attention mitigation
        return cls.reorder_u_curve(chunks)

    @classmethod
    def reorder(
        cls,
        chunks: list[dict[str, Any]],
        strategy: OrderingStrategy | str = OrderingStrategy.ADAPTIVE,
        model_name: str | None = None,
        context_tokens: int = 0,
    ) -> list[dict[str, Any]]:
        """Dispatcher for ordering strategy."""
        strat = str(getattr(strategy, "value", strategy)).lower()

        if strat == OrderingStrategy.U_CURVE.value or strat == "u_curve":
            return cls.reorder_u_curve(chunks)
        elif strat == OrderingStrategy.SCORE_DESCENDING.value or strat == "score_descending":
            return cls.reorder_score_descending(chunks)
        elif strat == OrderingStrategy.COHERENCE_PRESERVING.value or strat == "coherence_preserving":
            return cls.reorder_coherence_preserving(chunks)
        elif strat == OrderingStrategy.ADAPTIVE.value or strat == "adaptive":
            return cls.reorder_adaptive(chunks, model_name=model_name, context_tokens=context_tokens)
        else:
            return cls.reorder_u_curve(chunks)

    order_evidence = reorder


EvidenceOrderingEngine = EvidenceOrderingManager


class EvidenceOrderingBenchmark:
    """Benchmark helper for evidence ordering strategies."""

    @classmethod
    def run_benchmark(
        cls,
        chunks: list[dict[str, Any]],
        query: str = "",
        model_name: str | None = None,
    ) -> dict[str, Any]:
        from evaluation.ordering_benchmark import evaluate_ordering_strategy

        strategies = [
            OrderingStrategy.SCORE_DESCENDING,
            OrderingStrategy.U_CURVE,
            OrderingStrategy.COHERENCE_PRESERVING,
            OrderingStrategy.ADAPTIVE,
        ]
        results = {}
        for strat in strategies:
            results[strat.value] = evaluate_ordering_strategy(chunks, strat, model_name=model_name)
        return {"query": query, "strategies": results}
