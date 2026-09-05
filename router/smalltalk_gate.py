"""Small-talk gate for the chat agent pipeline.

Stops greetings and social messages ("hi", "thanks", "bye") from running the
full retrieval pipeline. Two layers, both fail-open:

- Layer 1: deterministic full-match regex over greetings / thanks / closers /
  identity pings. Zero cost, zero latency, trivially testable. A compound
  message like "hi, also what is S3 lifecycle?" never full-matches.
- Layer 2: cosine similarity of the query embedding against a canonical
  small-talk utterance list, reusing the semantic-cache embedding so the gate
  costs no extra API call. The threshold is calibrated against the local
  bge-small embedder (paraphrase greetings >= 0.93, unrelated short queries
  <= 0.91); scores in the fail-open band fall through to the normal pipeline.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from config import Settings, get_settings

logger = logging.getLogger(__name__)

# Full-match patterns applied to the trimmed query. Trailing punctuation and
# whitespace are tolerated; anything longer or more specific falls through.
_SMALLTALK_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Greetings
    re.compile(r"^(h+i+|hey+|hello+|hiya|heya|howdy|yo|sup)\b[\s!.?]*$", re.IGNORECASE),
    re.compile(r"^(hi|hey|hello)\s+(there|everyone|folks|team|all|guys)\b[\s!.?]*$", re.IGNORECASE),
    re.compile(r"^good\s+(morning|afternoon|evening|day)\b[\s!.?]*$", re.IGNORECASE),
    # Thanks
    re.compile(r"^(thanks?|thank\s+you|thx|ty|many\s+thanks|thanks\s+a\s+lot|thank\s+you\s+so\s+much)\b[\s!.?]*$", re.IGNORECASE),
    # Closers / acknowledgements
    re.compile(r"^(bye+|goodbye|good\s+night|goodnight|gn|see\s+ya|see\s+you|later)\b[\s!.?]*$", re.IGNORECASE),
    re.compile(r"^(ok|okay|kk|k|cool|great|nice|awesome|wow|perfect|sounds\s+good)\b[\s!.?]*$", re.IGNORECASE),
    # Identity pings
    re.compile(r"^(who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|who\s+made\s+you|help|help\s+me)\?*[\s!.]*$", re.IGNORECASE),
    # Social pleasantries
    re.compile(r"^(how\s+are\s+you|how('?s| is)\s+it\s+going|how\s+do\s+you\s+do|what('?s| is)\s+up|wassup)\?*[\s!.]*$", re.IGNORECASE),
)

# Canonical utterances for Layer 2 cosine matching (shipped list; a deployment
# can override via SMALLTALK_UTTERANCES_OVERRIDE).
CANONICAL_SMALLTALK_UTTERANCES: tuple[str, ...] = (
    "hi",
    "hello",
    "hey",
    "hey there",
    "hi there",
    "yo",
    "sup",
    "good morning",
    "good afternoon",
    "good evening",
    "thanks",
    "thank you",
    "thanks a lot",
    "thank you so much",
    "thx",
    "bye",
    "goodbye",
    "see you later",
    "good night",
    "ok",
    "okay",
    "cool",
    "great",
    "nice",
    "awesome",
    "who are you",
    "what are you",
    "what can you do",
    "help",
    "how are you",
    "how's it going",
    "what's up",
)


def _load_utterances(settings: Settings) -> tuple[str, ...]:
    """Return the canonical utterance list, honouring an optional override file."""
    override_path = getattr(settings, "smalltalk_utterances_override", None)
    if not override_path:
        return CANONICAL_SMALLTALK_UTTERANCES
    try:
        raw = json.loads(Path(override_path).read_text(encoding="utf-8"))
        utterances = tuple(str(u).strip() for u in raw if str(u).strip())
        if utterances:
            return utterances
        logger.warning("smalltalk.override_file_empty: %s — using shipped list", override_path)
    except Exception as e:
        logger.warning("smalltalk.override_file_unreadable: %s (%s) — using shipped list", override_path, e)
    return CANONICAL_SMALLTALK_UTTERANCES


def is_smalltalk_regex_query(query: str, settings: Settings | None = None) -> bool:
    """Layer 1: deterministic full-match small-talk check (0 cost).

    Fails open for empty input and queries longer than smalltalk_max_query_chars.
    """
    settings = settings or get_settings()
    text = (query or "").strip()
    if not text:
        return False
    max_chars = getattr(settings, "smalltalk_max_query_chars", 120)
    if len(text) > max_chars:
        return False
    return any(pattern.match(text) for pattern in _SMALLTALK_PATTERNS)


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors (0.0 when degenerate)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SmallTalkGate:
    """Layer 1: full-match regex (0 cost). Layer 2: cosine vs canonical
    utterances reusing the semantic-cache embedding. Fail open on doubt."""

    def __init__(self, embedder: Any | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._embedder = embedder
        self._canonical_vectors: list[list[float]] | None = None

    async def is_smalltalk(self, query: str, query_embedding: list[float] | None) -> bool:
        """Return True when the query is confidently small talk.

        Layer 1 hit is decisive. Layer 2 requires a query embedding and a
        working embedder; any error or missing input fails open.
        """
        if not getattr(self.settings, "smalltalk_gate_enabled", True):
            return False

        if is_smalltalk_regex_query(query, self.settings):
            return True

        if query_embedding is None or self._embedder is None:
            return False

        threshold = float(getattr(self.settings, "smalltalk_similarity_threshold", 0.92))
        fail_open_band = float(getattr(self.settings, "smalltalk_failopen_band", 0.80))

        try:
            best = 0.0
            for vector in await self._canonical_embeddings():
                best = max(best, _cosine(query_embedding, vector))
        except Exception as e:
            logger.warning("smalltalk.gate_layer2_error: %s — failing open", e)
            return False

        if best >= threshold:
            return True
        if best >= fail_open_band:
            # Ambiguous band: v1 fails open to the normal pipeline.
            logger.info("smalltalk.ambiguous_band: similarity=%.3f", round(best, 3))
        return False

    async def _canonical_embeddings(self) -> list[list[float]]:
        """Embed the canonical utterance list once per gate instance."""
        if self._canonical_vectors is not None:
            return self._canonical_vectors
        utterances = _load_utterances(self.settings)
        vectors: list[list[float]] = []
        for utterance in utterances:
            vectors.append(await self._embedder.embed_query(utterance))
        self._canonical_vectors = vectors
        return vectors
