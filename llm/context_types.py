"""
Enterprise Context Engineering Types and Primitives (llm/context_types.py).

Defines core context primitives:
- ProvenanceRecord: Tracks citation lineage, chunk identity, token weight, and trust tier.
- WorkingMemoryState: Structured working state kept outside the prompt.
- ContextProfile: Declarative policy governing context assembly (budgets, ceilings, weights, strategies).
- ContextBuildResult: Structured context result object with backward-compatible sequence emulation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .system_prompts import (
    APEX_TIER_SYSTEM_PROMPT,
    CORE_TIER_SYSTEM_PROMPT,
    LITE_TIER_SYSTEM_PROMPT,
)


class OrderingStrategy(str, Enum):
    """Supported evidence ordering strategies for context assembly."""
    U_CURVE = "u_curve"
    SCORE_DESCENDING = "score_descending"
    COHERENCE_PRESERVING = "coherence_preserving"
    ADAPTIVE = "adaptive"


@dataclass
class ProvenanceRecord:
    """
    First-class context primitive representing an evidence item included in the prompt.
    Tracks lineage, chunk ID, URL, token weight, and trustworthiness.
    """
    source_id: str
    source_type: str  # "rag", "internet", "web", "pricing", "calc", "api", "attachment"
    provider: str | None = None
    service: str | None = None
    section: str | None = None
    url: str | None = None
    canonical_url: str | None = None
    token_count: int = 0
    tokens: int = 0  # alias
    is_truncated: bool = False
    relevance_score: float | None = None
    position_index: int = 0
    chunk_id: str | None = None
    title: str | None = None
    content_preview: str = ""
    content_hash: str = ""
    trust_tier: str = "verified_rag"

    def __post_init__(self) -> None:
        if self.tokens and not self.token_count:
            self.token_count = self.tokens
        elif self.token_count and not self.tokens:
            self.tokens = self.token_count

    @classmethod
    def create(
        cls,
        source_id: str,
        source_type: str,
        content: str,
        token_count: int,
        provider: str | None = None,
        service: str | None = None,
        section: str | None = None,
        url: str | None = None,
        canonical_url: str | None = None,
        is_truncated: bool = False,
        relevance_score: float | None = None,
        position_index: int = 0,
        chunk_id: str | None = None,
        trust_tier: str = "verified_rag",
    ) -> ProvenanceRecord:
        content_preview = (content[:120] + "…") if len(content) > 120 else content
        content_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return cls(
            source_id=source_id,
            source_type=source_type,
            provider=provider,
            service=service,
            section=section,
            url=url,
            canonical_url=canonical_url,
            token_count=token_count,
            tokens=token_count,
            is_truncated=is_truncated,
            relevance_score=relevance_score,
            position_index=position_index,
            chunk_id=chunk_id,
            content_preview=content_preview,
            content_hash=content_hash,
            trust_tier=trust_tier,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "provider": self.provider,
            "service": self.service,
            "section": self.section,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "token_count": self.token_count,
            "is_truncated": self.is_truncated,
            "relevance_score": self.relevance_score,
            "position_index": self.position_index,
            "chunk_id": self.chunk_id,
            "title": self.title,
            "content_preview": self.content_preview,
            "content_hash": self.content_hash,
            "trust_tier": self.trust_tier,
        }


@dataclass
class WorkingMemoryState:
    """
    Structured working memory maintained outside the prompt.
    Encapsulates durable facts, active entities, conversation scratchpad, and preferences.
    """
    session_id: str = ""
    preferences: dict[str, Any] = field(default_factory=dict)
    facts: list[dict[str, Any]] = field(default_factory=list)
    active_entities: dict[str, Any] = field(default_factory=dict)
    scratchpad: dict[str, Any] = field(default_factory=dict)

    def set_fact(self, key: str, value: Any, category: str = "general") -> None:
        self.facts.append({"memory_key": key, "memory_value": value, "category": category})

    def set_preference(self, key: str, value: Any) -> None:
        self.preferences[key] = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "preferences": self.preferences,
            "facts": self.facts,
            "active_entities": self.active_entities,
            "scratchpad": self.scratchpad,
        }

    def format_prompt_block(
        self,
        tag_name: str = "user_preferences",
        max_tokens: int = 400,
        estimate_fn: Callable[[str], int] | None = None,
    ) -> str:
        """Deterministic formatter to selectively project active working memory into the prompt."""
        if not self.facts and not self.preferences:
            return ""

        from .context_metrics import estimate_tokens
        calc_tokens = estimate_fn or estimate_tokens

        lines = [f"\n<{tag_name}>"]
        cur_tokens = calc_tokens(lines[0])

        for k, v in self.preferences.items():
            line = f"- {k}: {v}"
            t = calc_tokens(line)
            if cur_tokens + t <= max_tokens:
                lines.append(line)
                cur_tokens += t
            else:
                break

        for fact in self.facts:
            k = fact.get("memory_key") or fact.get("key", "")
            v = fact.get("memory_value") or fact.get("value", "")
            if not k and not v:
                continue
            line = f"- {k}: {v}" if k else f"- {v}"
            t = calc_tokens(line)
            if cur_tokens + t <= max_tokens:
                lines.append(line)
                cur_tokens += t
            else:
                break

        lines.append(f"</{tag_name}>")
        if len(lines) <= 2:
            return ""
        return "\n".join(lines)


@dataclass
class ContextProfile:
    """
    Declarative policy profile governing how context is assembled for a tier / workload.
    Replaces monolithic duplicated builder code with reusable policy parameters.
    """
    name: str  # "standard", "lite", "agentic", "adaptive", "developer"
    baseline_budget: int
    ceiling_budget: int
    system_prompt_tier: str  # "Free", "Pro", "Max", "Developer"
    evidence_tag: str
    section_weights: dict[str, float] = field(default_factory=lambda: {
        "rag": 0.45,
        "internet": 0.15,
        "tools": 0.15,
        "attachments": 0.20,
    })
    ordering_strategy: OrderingStrategy = OrderingStrategy.ADAPTIVE
    directives: list[str] = field(default_factory=list)
    system_prompt: str | None = None
    allow_user_memory: bool = True
    allow_plan_scaffolding: bool = False
    allow_live_verification: bool = False
    allow_dimension_matrix: bool = False
    allow_verification_anchors: bool = False
    is_unlimited: bool = False

    @property
    def weights(self) -> dict[str, float]:
        return self.section_weights

    @classmethod
    def standard(cls) -> ContextProfile:
        return cls(
            name="standard",
            baseline_budget=4000,
            ceiling_budget=12000,
            system_prompt_tier="Dynamic",
            evidence_tag="--- RAG SOURCES ---",
            section_weights={
                "rag": 0.45,
                "internet": 0.15,
                "web": 0.10,
                "tools": 0.10,
                "attachments": 0.20,
            },
            ordering_strategy=OrderingStrategy.ADAPTIVE,
            system_prompt=None,
        )

    @classmethod
    def lite(cls) -> ContextProfile:
        return cls(
            name="lite",
            baseline_budget=4000,
            ceiling_budget=8000,
            system_prompt_tier="Free",
            evidence_tag="verified_cloud_facts",
            section_weights={
                "rag": 0.55,
                "internet": 0.10,
                "tools": 0.15,
                "attachments": 0.20,
            },
            ordering_strategy=OrderingStrategy.U_CURVE,
            system_prompt=LITE_TIER_SYSTEM_PROMPT,
        )

    @classmethod
    def agentic(cls) -> ContextProfile:
        return cls(
            name="agentic",
            baseline_budget=8000,
            ceiling_budget=32000,
            system_prompt_tier="Pro",
            evidence_tag="agentic_evidence_matrix",
            section_weights={
                "rag": 0.45,
                "internet": 0.15,
                "tools": 0.20,
                "attachments": 0.20,
            },
            ordering_strategy=OrderingStrategy.ADAPTIVE,
            system_prompt=CORE_TIER_SYSTEM_PROMPT,
            allow_plan_scaffolding=True,
        )

    @classmethod
    def adaptive(cls) -> ContextProfile:
        return cls(
            name="adaptive",
            baseline_budget=16000,
            ceiling_budget=64000,
            system_prompt_tier="Max",
            evidence_tag="well_architected_evidence_matrix",
            section_weights={
                "rag": 0.40,
                "internet": 0.25,
                "tools": 0.15,
                "attachments": 0.20,
            },
            ordering_strategy=OrderingStrategy.ADAPTIVE,
            system_prompt=APEX_TIER_SYSTEM_PROMPT,
            allow_live_verification=True,
            allow_dimension_matrix=True,
            allow_verification_anchors=True,
        )

    @classmethod
    def developer(cls) -> ContextProfile:
        return cls(
            name="developer",
            baseline_budget=32000,
            ceiling_budget=128000,
            system_prompt_tier="Developer",
            evidence_tag="well_architected_evidence_matrix",
            section_weights={
                "rag": 0.40,
                "internet": 0.20,
                "tools": 0.20,
                "attachments": 0.20,
            },
            ordering_strategy=OrderingStrategy.ADAPTIVE,
            system_prompt=APEX_TIER_SYSTEM_PROMPT,
            is_unlimited=True,
        )


@dataclass
class ContextBuildResult:
    """
    Rich structured result object produced by ContextPipelineEngine.
    Provides deep observability into token allocations, provenance, memory state, and safety.
    """
    messages: list[dict[str, str]]
    provenance: list[ProvenanceRecord] = field(default_factory=list)
    working_memory: WorkingMemoryState = field(default_factory=WorkingMemoryState)
    system_prompt: str = ""
    user_content: str = ""
    token_breakdown: dict[str, int] = field(default_factory=dict)
    token_budget_breakdown: dict[str, int] = field(default_factory=dict)
    total_tokens: int = 0
    tokens_used: int = 0
    budget_ceiling: int = 0
    remaining_budget: int = 0
    truncated_sources: list[str] = field(default_factory=list)
    truncated_sections: list[str] = field(default_factory=list)
    dropped_sources_count: dict[str, int] = field(default_factory=dict)
    ordering_strategy: str = "adaptive"
    safety_status: dict[str, Any] = field(default_factory=dict)
    safety_passed: bool = True
    profile_name: str = "standard"

    def __post_init__(self) -> None:
        if self.messages:
            if not self.system_prompt and len(self.messages) > 0:
                self.system_prompt = self.messages[0].get("content", "")
            if not self.user_content and len(self.messages) > 1:
                self.user_content = self.messages[1].get("content", "")
        if self.tokens_used and not self.total_tokens:
            self.total_tokens = self.tokens_used
        elif self.total_tokens and not self.tokens_used:
            self.tokens_used = self.total_tokens
        if self.token_budget_breakdown and not self.token_breakdown:
            self.token_breakdown = self.token_budget_breakdown
        elif self.token_breakdown and not self.token_budget_breakdown:
            self.token_budget_breakdown = self.token_breakdown
        if self.truncated_sections and not self.truncated_sources:
            self.truncated_sources = self.truncated_sections
        elif self.truncated_sources and not self.truncated_sections:
            self.truncated_sections = self.truncated_sources

    # Sequence emulation for 100% backward compatibility with code expecting list[dict]
    def __iter__(self):
        return iter(self.messages)

    def __getitem__(self, index: int | slice):
        return self.messages[index]

    def __len__(self) -> int:
        return len(self.messages)

    def to_messages(self) -> list[dict[str, str]]:
        return self.messages

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "budget_ceiling": self.budget_ceiling,
            "remaining_budget": self.remaining_budget,
            "profile_name": self.profile_name,
            "ordering_strategy": self.ordering_strategy,
            "token_breakdown": self.token_breakdown,
            "provenance_count": len(self.provenance),
            "provenance": [p.to_dict() for p in self.provenance],
            "working_memory": self.working_memory.to_dict(),
            "truncated_sources": self.truncated_sources,
            "dropped_sources_count": self.dropped_sources_count,
            "safety_status": self.safety_status,
            "safety_passed": self.safety_passed,
        }
