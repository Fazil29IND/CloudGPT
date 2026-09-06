"""Deterministic claim-level evidence entailment and source-to-chunk citation mapping.

This module is pure Python (zero LLM, zero network) so the Lite tier gets
claim-level grounding checks without added latency, and Core/Apex tiers get a
deterministic first-pass before any evaluator-model verification.

1. Claim extraction: prose sentences plus command lines from fenced code blocks.
2. Support scoring: content-token overlap + bigram bonus + exact-technical boost.
   Claims carrying hard technical tokens (CLI flags, numbers, version strings)
   are capped at 0.5 unless every hard token is witnessed in the evidence.
3. Claim → chunk → citation-number mapping through the CitationManager's
   chunk_id registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import structlog

logger = structlog.get_logger(__name__)

# Minimal English stopword set — technical tokens (flags, service names, numbers)
# are intentionally never treated as stopwords.
_STOPWORDS = frozenset(
    "a an the is are was were be been being of in on at to for and or but if then than "
    "that this these those it its they them their there here as with without from by "
    "about into over under between within can could should would will shall may might "
    "do does did done have has had i you we he she my your our me us him her what which "
    "who whom when where why how not no yes so such very more most much many few also "
    "just only own same too s t d ll m o re ve y ain aren couldn didn doesn hadn hasn "
    "haven isn ma mightn mustn needn shan shouldn wasn weren won wouldn".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-.]*")
# Hard technical tokens: CLI flags, numeric values, versions, IPs/CIDRs, error codes.
_HARD_TOKEN_RE = re.compile(r"(--[\w-]+|\d+(?:\.\d+)+|\b\d{2,}\b|[A-Z][a-z]+(?:Exception|Error)\b)")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`*\-|])|\n+")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s")
_REFERENCE_LINE_RE = re.compile(r"^\s*\d+\.\s*\[")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_CODE_FENCE_RE = re.compile(r"```[\w]*\n(.*?)```", re.DOTALL)


def content_tokens(text: str) -> list[str]:
    """Lowercased content tokens, stopwords removed, technical tokens kept."""
    if not text:
        return []
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def _hard_tokens(claim: str) -> list[str]:
    return _HARD_TOKEN_RE.findall(claim or "")


def _bigrams(tokens: Sequence[str]) -> set[tuple[str, str]]:
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def extract_claims(answer: str) -> list[str]:
    """Extract assessable claims from a generated markdown answer.

    Prose sentences, table rows, and non-trivial code/command lines are claims.
    Headings, reference-list entries, and trivial fragments are skipped.
    """
    if not answer or not answer.strip():
        return []

    claims: list[str] = []

    # Command / code lines from fenced blocks are first-class technical claims.
    for block in _CODE_FENCE_RE.findall(answer):
        for line in block.splitlines():
            line = line.strip()
            if len(line) >= 8 and not line.startswith("#"):
                claims.append(line)
    prose = _CODE_FENCE_RE.sub("\n", answer)

    for raw_line in prose.splitlines():
        line = raw_line.strip()
        if not line or _HEADING_RE.match(line) or _REFERENCE_LINE_RE.match(line):
            continue
        if _TABLE_ROW_RE.match(line):
            # Table rows carry spec claims; strip the pipe framing.
            cells = [c.strip() for c in line.strip("|").split("|") if c.strip()]
            row_text = " ".join(cells)
            if len(content_tokens(row_text)) >= 3:
                claims.append(row_text)
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            sentence = sentence.strip().lstrip("-*• ").strip()
            if not sentence or len(sentence) < 15:
                continue
            if len(content_tokens(sentence)) >= 3 or _hard_tokens(sentence):
                claims.append(sentence)

    return claims


def score_claim_support(claim: str, evidence_text: str) -> float:
    """Lexical entailment score of a single claim against one evidence text.

    Combines unigram coverage with a bigram bonus. Claims containing hard
    technical tokens are capped at 0.5 unless every hard token appears in the
    evidence (exact technical grounding).
    """
    evidence_toks = content_tokens(evidence_text)
    return _score_claim(claim, evidence_toks, evidence_text.lower() if evidence_text else "")


def _score_claim(claim: str, evidence_toks: list[str], evidence_lower: str) -> float:
    claim_toks = content_tokens(claim)
    if not claim_toks:
        return 1.0  # nothing falsifiable
    if not evidence_toks:
        return 0.0

    evidence_set = set(evidence_toks)
    matched = sum(1 for t in claim_toks if t in evidence_set)
    base = matched / len(claim_toks)

    claim_bigrams = _bigrams(claim_toks)
    bigram_bonus = 0.0
    if claim_bigrams:
        evidence_bigrams = _bigrams(evidence_toks)
        hit = sum(1 for bg in claim_bigrams if bg in evidence_bigrams)
        bigram_bonus = 0.2 * (hit / len(claim_bigrams))

    score = min(1.0, base + bigram_bonus)

    hard = _hard_tokens(claim)
    if hard:
        all_present = all(h.lower() in evidence_lower for h in hard)
        if all_present:
            score = min(1.0, score + 0.1)
        else:
            score = min(score, 0.5)
    return round(score, 3)


@dataclass
class ClaimSupport:
    """Support verdict for one claim against the evidence pool."""

    text: str
    support_score: float
    best_chunk_id: str = ""
    best_source_number: int | None = None

    @property
    def level(self) -> str:
        if self.support_score >= 0.7:
            return "supported"
        if self.support_score >= 0.4:
            return "partial"
        return "unsupported"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.text,
            "support_score": self.support_score,
            "level": self.level,
            "chunk_id": self.best_chunk_id,
            "source_number": self.best_source_number,
        }


@dataclass
class ClaimAssessment:
    """Aggregate claim-level support assessment for a generated answer."""

    claims: list[ClaimSupport] = field(default_factory=list)
    support_ratio: float = 1.0
    assessable_count: int = 0

    @property
    def unsupported(self) -> list[ClaimSupport]:
        return [c for c in self.claims if c.level == "unsupported"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "support_ratio": self.support_ratio,
            "assessable_claims": self.assessable_count,
            "unsupported": [c.to_dict() for c in self.unsupported],
            "claims": [c.to_dict() for c in self.claims],
        }


def _normalize_chunks(chunks: Sequence[Any]) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    for chunk in chunks:
        if hasattr(chunk, "text"):
            cid = getattr(chunk, "chunk_id", "")
            normalized.append((str(cid or ""), chunk.text or ""))
        elif isinstance(chunk, dict):
            cid = chunk.get("chunk_id", "")
            text = chunk.get("text") or chunk.get("content") or ""
            normalized.append((str(cid or ""), text))
    return normalized


def assess_claim_support(
    answer: str,
    chunks: Sequence[Any],
    chunk_to_source: Callable[[str], int | None] | None = None,
    max_claims: int = 40,
) -> ClaimAssessment:
    """Score every extracted claim against the evidence pool (best chunk wins).

    ``chunk_to_source`` optionally resolves a chunk_id to its registered
    citation number for claim-level source-to-chunk attribution.
    """
    evidence = _normalize_chunks(chunks)
    claims = extract_claims(answer)[:max_claims]
    if not claims or not evidence:
        return ClaimAssessment(claims=[], support_ratio=1.0, assessable_count=0)

    # Pre-compute per-chunk token material once (claims × chunks scoring below).
    evidence_prepared = [
        (cid, content_tokens(text), text.lower()) for cid, text in evidence
    ]

    supported: list[ClaimSupport] = []
    for claim in claims:
        best_score = 0.0
        best_cid = ""
        for cid, ev_toks, ev_lower in evidence_prepared:
            s = _score_claim(claim, ev_toks, ev_lower)
            if s > best_score:
                best_score = s
                best_cid = cid
        src = None
        if best_score > 0 and chunk_to_source and best_cid:
            try:
                src = chunk_to_source(best_cid)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug("claim_source_lookup_failed", chunk_id=best_cid, error=str(e))
        supported.append(
            ClaimSupport(
                text=claim,
                support_score=best_score,
                best_chunk_id=best_cid,
                best_source_number=src,
            )
        )

    assessable = len(supported)
    ratio = round(sum(c.support_score for c in supported) / assessable, 3) if assessable else 1.0
    return ClaimAssessment(claims=supported, support_ratio=ratio, assessable_count=assessable)
