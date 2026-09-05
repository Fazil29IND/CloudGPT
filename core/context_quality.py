"""Context Quality Controller (CQC) — Pre-generation Evidence Curation.

Performs deduplication, provider diversity rebalancing, and cross-source
contradiction scoring (context_coherence_score) to protect generation accuracy.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
from typing import Any

import structlog

from config import Settings, get_settings
from llm.context_builder import _canonical_url

logger = structlog.get_logger(__name__)


@dataclass
class QualityControlResult:
    curated_chunks: list[dict[str, Any]]
    context_coherence_score: float = 1.0
    contradiction_pairs: list[tuple[str, str]] = field(default_factory=list)
    stale_chunk_ids: list[str] = field(default_factory=list)
    diversity_rebalanced: bool = False


class ContextQualityController:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _deduplicate(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        deduped: list[dict[str, Any]] = []

        for chunk in chunks:
            url = chunk.get("url", "")
            canon = _canonical_url(url)
            if canon:
                if canon in seen_urls:
                    continue
                seen_urls.add(canon)
            else:
                content = chunk.get("content", "")
                h = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
            deduped.append(chunk)

        return deduped

    def _diversity_rebalance(self, chunks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        if len(chunks) <= 2:
            return chunks, False

        provider_counts = Counter(
            (c.get("provider") or "unknown").lower() for c in chunks
        )
        total = len(chunks)
        max_dup_threshold = getattr(self.settings, "cqc_max_provider_duplication", 0.7)

        # Check if any provider dominates beyond the threshold
        dominant_provider = None
        for prov, count in provider_counts.items():
            if count / total > max_dup_threshold and len(provider_counts) > 1:
                dominant_provider = prov
                break

        if not dominant_provider:
            return chunks, False

        # Group chunks by provider preserving relative order
        by_provider: dict[str, list[dict[str, Any]]] = {}
        for c in chunks:
            p = (c.get("provider") or "unknown").lower()
            by_provider.setdefault(p, []).append(c)

        rebalanced: list[dict[str, Any]] = []
        # First round: take top 1 from each provider
        for p, prov_chunks in by_provider.items():
            if prov_chunks:
                rebalanced.append(prov_chunks.pop(0))

        # Fill remaining slots alternating providers
        while any(by_provider.values()):
            for p in sorted(by_provider.keys(), key=lambda k: len(by_provider[k])):
                if by_provider[p]:
                    rebalanced.append(by_provider[p].pop(0))

        return rebalanced, True

    def _detect_contradictions(
        self,
        chunks: list[dict[str, Any]],
        memory_facts: list[dict[str, Any]],
        history_snippets: list[str],
    ) -> tuple[float, list[tuple[str, str]]]:
        contradictions: list[tuple[str, str]] = []

        # 1. Service name collision across conflicting providers
        service_to_provider: dict[str, str] = {}
        for c in chunks:
            svc = (c.get("service") or "").strip().lower()
            prov = (c.get("provider") or "").strip().lower()
            if svc and prov and prov not in ("cloud", "general", "unknown"):
                if svc in service_to_provider and service_to_provider[svc] != prov:
                    contradictions.append(
                        (f"{service_to_provider[svc]}:{svc}", f"{prov}:{svc}")
                    )
                else:
                    service_to_provider[svc] = prov

        # 2. Check user memory vs retrieved evidence
        if memory_facts and chunks:
            evidence_providers = [
                (c.get("provider") or "").lower() for c in chunks if c.get("provider")
            ]
            majority_prov = (
                Counter(evidence_providers).most_common(1)[0][0]
                if evidence_providers
                else None
            )

            if majority_prov:
                for fact in memory_facts:
                    fact_str = str(fact.get("fact") or fact.get("value") or "").lower()
                    for p in ["aws", "gcp", "azure"]:
                        if p != majority_prov and f"prefer {p}" in fact_str:
                            contradictions.append(("user_memory", f"evidence_majority_{majority_prov}"))
                            break

        # 3. Check recent chat history turns
        if history_snippets and chunks:
            recent_text = " ".join(history_snippets).lower()
            evidence_providers = [
                (c.get("provider") or "").lower() for c in chunks if c.get("provider")
            ]
            if evidence_providers:
                majority_prov = Counter(evidence_providers).most_common(1)[0][0]
                for p in ["aws", "gcp", "azure"]:
                    if p != majority_prov and f"only use {p}" in recent_text:
                        contradictions.append(("chat_history", f"evidence_{majority_prov}"))
                        break

        score = max(0.0, 1.0 - min(1.0, len(contradictions) * 0.25))
        return score, contradictions

    def run(
        self,
        chunks: list[dict[str, Any]],
        memory_facts: list[dict[str, Any]] | None = None,
        history_snippets: list[str] | None = None,
        tier: str = "Free",
    ) -> QualityControlResult:
        if not chunks:
            return QualityControlResult(
                curated_chunks=[],
                context_coherence_score=1.0,
                contradiction_pairs=[],
                stale_chunk_ids=[],
                diversity_rebalanced=False,
            )

        memory_facts = memory_facts or []
        history_snippets = history_snippets or []
        tier_norm = (tier or "Free").capitalize()

        # Extract stale chunk labels
        stale_chunk_ids: list[str] = [
            str(c.get("chunk_id") or c.get("url") or c.get("service") or "chunk")
            for c in chunks
            if c.get("stale") is True
            or (isinstance(c.get("metadata"), dict) and c["metadata"].get("stale") is True)
        ]

        # 1. Deduplicate
        deduped = self._deduplicate(chunks)

        # 2. Diversity rebalance (skip on Free/Lite)
        is_lite = tier_norm in ("Free", "Lite")
        if not is_lite:
            curated, rebalanced = self._diversity_rebalance(deduped)
        else:
            curated, rebalanced = deduped, False

        # 3. Contradiction check (Core / Apex / Pro / Max only)
        allowed_tiers = [
            t.capitalize() for t in getattr(self.settings, "cqc_contradiction_check_tiers", ["Pro", "Max"])
        ]
        if tier_norm in ("Core", "Pro"):
            tier_matches = any(t in ("Pro", "Core") for t in allowed_tiers)
        elif tier_norm in ("Apex", "Max"):
            tier_matches = any(t in ("Max", "Apex") for t in allowed_tiers)
        else:
            tier_matches = tier_norm in allowed_tiers

        if tier_matches:
            coherence_score, contradictions = self._detect_contradictions(
                curated, memory_facts, history_snippets
            )
        else:
            coherence_score, contradictions = 1.0, []

        logger.info(
            "context_quality.run_complete",
            tier=tier,
            input_count=len(chunks),
            output_count=len(curated),
            coherence_score=coherence_score,
            contradiction_count=len(contradictions),
            diversity_rebalanced=rebalanced,
            stale_count=len(stale_chunk_ids),
        )

        return QualityControlResult(
            curated_chunks=curated,
            context_coherence_score=coherence_score,
            contradiction_pairs=contradictions,
            stale_chunk_ids=stale_chunk_ids,
            diversity_rebalanced=rebalanced,
        )
