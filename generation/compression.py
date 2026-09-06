"""Tiered evidence compression policies (Layer 3 — Context Assembly).

Lite (Hybrid RAG):  conditional extractive compression — fires only when the
                    tokenized evidence pool exceeds the configured threshold,
                    so low-latency lookups pay zero compression cost.
Core (Agentic RAG): task-aware selective compression — intent-driven profiles
                    keep the sentences that matter per task (commands for
                    troubleshooting, specs for comparison, numbers for cost).
Apex (Adaptive):    strategy-adaptive — delegated profiles per routing strategy.

All policies are deterministic and evidence-aware: markdown tables, fenced
code blocks, and coalesced parent chunks are never compressed (contextual
guard), and every compressed chunk records compression metadata.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_STOPWORDS = frozenset(
    "a an the is are was were be been being of in on at to for and or but if then than "
    "that this these those it its they them their there here as with without from by "
    "about into over under between within can could should would will shall may might "
    "do does did done have has had i you we he she my your our me us what which who "
    "when where why how not no yes so such very more most much many also just only".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-.]*")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_TABLE_HINT_RE = re.compile(r"\|.+\|\s*\n?\s*\|?-{3,}")
_CODE_FENCE_RE = re.compile(r"```[\w]*\n(.*?)```", re.DOTALL)

# Task-aware profiles (Core): keywords whose presence marks a sentence as
# load-bearing for the given intent.
TASK_PROFILES: dict[str, tuple[str, ...]] = {
    "troubleshooting": (
        "error", "fix", "resolve", "remediat", "command", "cli", "step", "cause",
        "permission", "iam", "policy", "timeout", "throttl", "quota", "retry", "log",
        "diagnos", "verify", "check", "failed", "exception", "mitigat",
    ),
    "error_fix": (
        "error", "fix", "resolve", "exception", "failed", "throttl", "permission",
        "denied", "quota", "timeout", "command", "verify", "remediat",
    ),
    "compare": (
        "versus", "vs", "difference", "compared", "feature", "sla", "pricing",
        "performance", "throughput", "latency", "limit", "quota", "supports",
        "option", "tier", "model", "availability",
    ),
    "service_selection": (
        "recommend", "best", "choose", "option", "feature", "limit", "pricing",
        "performance", "sla", "supports", "tier", "use case",
    ),
    "cost_estimate": (
        "price", "pricing", "cost", "hourly", "monthly", "per gb", "tier",
        "reserved", "savings plan", "on-demand", "billing", "free tier", "budget",
    ),
    "pricing": ("price", "pricing", "cost", "hourly", "monthly", "billing", "tier"),
    "architecture": (
        "architecture", "pattern", "design", "component", "availability zone",
        "region", "scal", "resilien", "failover", "redundan", "vpc", "subnet",
        "high availability", "replica", "shard", "best practice",
    ),
    "how_to": (
        "step", "configure", "create", "install", "setup", "set up", "enable",
        "command", "cli", "console", "parameter", "flag", "api", "example", "run",
    ),
    "explain": (
        "overview", "definition", "means", "works", "provides", "feature",
        "service", "function", "purpose", "component", "enables",
    ),
}
TASK_PROFILES["problem_solving"] = TASK_PROFILES["troubleshooting"]

# Strategy profiles (Apex delegation).
STRATEGY_PROFILES: dict[str, tuple[str, ...]] = {
    "direct_fast": (
        "command", "cli", "flag", "parameter", "syntax", "option", "--", "api",
        "endpoint", "default", "value", "example", "required",
    ),
    "semantic_hyde": TASK_PROFILES["explain"],
    "multi_perspective": (
        "architecture", "pattern", "pricing", "cost", "performance", "latency",
        "sla", "scal", "availability", "limit", "quota", "feature", "trade-off",
        "difference", "supports",
    ),
}

_DEFAULT_PROFILE = TASK_PROFILES["explain"]


def _content_tokens(text: str) -> set[str]:
    return {
        t for t in _TOKEN_RE.findall((text or "").lower())
        if t not in _STOPWORDS and len(t) > 1
    }


def _profile_hits(sentence: str, profile: tuple[str, ...]) -> int:
    s = sentence.lower()
    return sum(1 for kw in profile if kw in s)


def _is_table_chunk(text: str) -> bool:
    return bool(_TABLE_HINT_RE.search(text or ""))


def _code_block_ratio(text: str) -> float:
    if not text:
        return 0.0
    blocks = "".join(_CODE_FENCE_RE.findall(text))
    return len(blocks) / max(1, len(text))


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text or "") if s.strip()]


def compress_chunk(
    query: str,
    chunk: Any,
    *,
    profile: tuple[str, ...],
    keep_score_threshold: float = 0.55,
    max_keep_ratio: float = 0.65,
    min_keep_sentences: int = 2,
) -> Any:
    """Extractively compress one RetrievalResult chunk (or dict) toward a profile.

    Tables, code-heavy chunks, and coalesced parents are preserved intact
    (contextual guard). Returns the chunk unchanged when compression would
    destroy structure or the yield is trivial.
    """
    is_dict = isinstance(chunk, dict)
    text = (chunk.get("text", chunk.get("content", "")) if is_dict else getattr(chunk, "text", None)) or ""
    original_len = len(text)

    score = float(chunk.get("score", 1.0) if is_dict else getattr(chunk, "score", 1.0))
    metadata = chunk.setdefault("metadata", {}) if is_dict else getattr(chunk, "metadata", {})

    if score < keep_score_threshold:
        metadata["compressed"] = False
        return chunk
    if metadata.get("is_table") or _is_table_chunk(text):
        metadata["compressed"] = False
        metadata["table_preserved"] = True
        return chunk
    if metadata.get("is_coalesced_parent") and score >= 0.8:
        metadata["compressed"] = False
        metadata["parent_preserved"] = True
        return chunk
    if _code_block_ratio(text) >= 0.4:
        metadata["compressed"] = False
        metadata["code_preserved"] = True
        return chunk

    query_tokens = _content_tokens(query)
    sentences = _split_sentences(text)
    if len(sentences) <= min_keep_sentences:
        metadata["compressed"] = False
        return chunk

    # Fenced code blocks inside prose chunks survive as atomic units.
    code_spans: list[str] = _CODE_FENCE_RE.findall(text)

    def sentence_score(s: str) -> int:
        toks = _content_tokens(s)
        overlap = len(toks & query_tokens)
        return overlap + 2 * _profile_hits(s, profile)

    kept: list[str] = []
    for i, sentence in enumerate(sentences):
        if i < min_keep_sentences or sentence_score(sentence) > 0:
            kept.append(sentence)

    rebuilt = " ".join(kept)
    # Re-attach code fences that sentence splitting may have stranded.
    if code_spans and not _CODE_FENCE_RE.search(rebuilt):
        rebuilt = rebuilt + "\n" + "\n".join(f"```\n{c}```" for c in code_spans[:1])

    target_len = int(original_len * max_keep_ratio)
    if len(rebuilt) < 60 or len(rebuilt) > target_len:
        # Compression not worthwhile — keep the full chunk.
        metadata["compressed"] = False
        return chunk

    metadata["compressed"] = True
    metadata["original_length"] = original_len
    metadata["compressed_length"] = len(rebuilt)
    if is_dict:
        chunk["text"] = rebuilt
    else:
        chunk.text = rebuilt
    return chunk


def _evidence_token_total(chunks: list[Any]) -> int:
    def _chunk_text(c: Any) -> str:
        if isinstance(c, dict):
            return str(c.get("text", c.get("content", "")) or "")
        return str(getattr(c, "text", "") or "")

    try:
        from llm.context_metrics import estimate_tokens
    except ImportError:  # pragma: no cover
        return sum(len(_chunk_text(c)) // 4 for c in chunks)
    return sum(estimate_tokens(_chunk_text(c)) for c in chunks)


def compress_evidence(
    query: str,
    chunks: list[Any],
    *,
    policy: str,
    task_intent: str | None = None,
    strategy: str | None = None,
    settings: Any = None,
) -> list[Any]:
    """Compress a reranked evidence list under the tier's policy.

    policy="lite":  conditional — skips entirely while the pool fits within
                    ``lite_compression_token_threshold``.
    policy="core":  task-aware selective compression via ``task_intent``.
    policy="apex":  strategy-adaptive via ``strategy`` (kept for completeness;
                    the Apex pipeline currently tunes its own `_compress_chunk`).
    """
    if not chunks:
        return chunks

    if policy == "lite":
        threshold = int(getattr(settings, "lite_compression_token_threshold", 3000))
        if _evidence_token_total(chunks) <= threshold:
            return chunks
        profile = _DEFAULT_PROFILE
        max_ratio, threshold_score = 0.6, 0.55
    elif policy == "core":
        profile = TASK_PROFILES.get((task_intent or "explain").lower(), _DEFAULT_PROFILE)
        max_ratio, threshold_score = 0.7, 0.6
    elif policy == "apex":
        profile = STRATEGY_PROFILES.get(strategy or "semantic_hyde", _DEFAULT_PROFILE)
        max_ratio, threshold_score = 0.7, 0.6
    else:
        return chunks

    compressed: list[Any] = []
    for chunk in chunks:
        try:
            compressed.append(
                compress_chunk(
                    query,
                    chunk,
                    profile=profile,
                    keep_score_threshold=threshold_score,
                    max_keep_ratio=max_ratio,
                )
            )
        except Exception as e:  # pragma: no cover - defensive per-chunk isolation
            logger.warning("evidence_compression.chunk_failed", error=str(e))
            compressed.append(chunk)

    logger.info(
        "evidence_compression.applied",
        policy=policy,
        task_intent=task_intent,
        strategy=strategy,
        total=len(compressed),
        compressed_count=sum(1 for c in compressed if c.metadata.get("compressed")),
    )
    return compressed
