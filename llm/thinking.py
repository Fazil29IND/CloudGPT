"""CloudGPT Thinking Engine — reasoning levels, budgets, and quota scaling.

Every chat request carries an optional thinking level (Low | Medium | High |
Max). The level maps to:

    - a reasoning token budget handed to thinking-capable providers
      (Claude extended thinking, Gemini thinking_config, OpenAI reasoning_effort)
    - a quota multiplier that scales worst-case token reservation
    - a list of models per tier that can serve the request

Providers that cannot reason natively simply ignore the level; the unified
pipeline still separates any ``<think>...</think>`` blocks the model emits so
the SSE stream and the UI stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


LOW: Final = "Low"
MEDIUM: Final = "Medium"
HIGH: Final = "High"
MAX: Final = "Max"

ALL_LEVELS: Final[tuple[str, ...]] = (LOW, MEDIUM, HIGH, MAX)
_ALIASES: Final[dict[str, str]] = {
    "low": LOW,
    "minimal": LOW,
    "off": LOW,
    "none": LOW,
    "medium": MEDIUM,
    "med": MEDIUM,
    "default": MEDIUM,
    "high": HIGH,
    "deep": HIGH,
    "max": MAX,
    "maximum": MAX,
    "ultra": MAX,
}

THINKING_REASONING_FRAMEWORK: Final[str] = """Cognitive Thinking Framework (Anti-Degradation Rubric):
1. Phase 1 - Multi-Part Query Decomposition:
   - Identify whether the user's prompt contains compound tasks, multiple questions, or sibling constraints.
   - Deconstruct into independent sub-problems.
   - Never collapse the entire response into a blanket refusal or 'UNKNOWN' if partial information is resolvable.
2. Phase 2 - Substantive Formulation (Dual-Axis Quality):
   - Prioritize technical accuracy, operational reasoning, and domain coherence before optimizing for superficial formatting metrics.
   - For structured sections (tables, matrices), ensure every row and column has a genuine, meaningful relationship to the core scenario.
3. Phase 3 - Structural Alignment:
   - Apply requested schemas, markdown headers, and formatting constraints without eroding the substantive clarity developed in Phase 2.
4. Phase 4 - Global Premise & Cross-Section Consistency Check:
   - Verify all generated steps, CLI commands, and bullet points against the overarching system architecture premise.
   - Ensure zero contradiction between sibling bullets or sequential phases.
5. Phase 5 - Adversarial Self-Audit (Anti-Self-Grading Bias):
   - Audit the draft against explicit constraints from first principles without confirmation bias.
   - Never claim 0 violations or 100% perfection without deterministic proof. Default to explicit caveats or partial status if any detail is unverified.
"""


@dataclass(frozen=True)
class ThinkingProfile:
    """Immutable configuration for one thinking level."""

    level: str
    budget_tokens: int
    quota_multiplier: float
    # OpenAI reasoning_effort vocabulary (o-series models).
    reasoning_effort: str

    @property
    def enabled(self) -> bool:
        return self.budget_tokens > 0


def default_thinking_for_tier(tier: str | None) -> str:
    """Return the default thinking level for a tier.

    Defaults are set so the tiers feel meaningfully different out of the box:
    Free/Lite → Low    (2 048 tokens  — Flash-class speed, lightweight)
    Pro/Core  → High   (24 576 tokens — deep structured reasoning)
    Max/Apex  → Max    (65 535 tokens — maximum reasoning budget)
    Users can still request lower levels; higher ones are clamped by
    entitlements (Lite/Core cannot select Max).
    """
    t = (tier or "free").strip().lower()
    if t in ("max", "apex", "developer", "admin"):
        return MAX
    if t in ("pro", "core"):
        return HIGH
    return LOW


def normalize_thinking_level(value: str | None, default: str = MEDIUM) -> str:
    """Normalize any user/config supplied level string to a canonical level."""
    if not value:
        return default
    return _ALIASES.get(value.strip().lower(), default)


def clamp_thinking_level(value: str | None, allowed: list[str] | tuple[str, ...]) -> str:
    """Clamp a requested level to the highest level permitted for the tier.

    The request is honoured when allowed; otherwise it degrades to the closest
    permitted level below it (or the lowest permitted level).
    """
    requested = normalize_thinking_level(value)
    allowed_canonical = [normalize_thinking_level(a) for a in allowed or []]
    if not allowed_canonical:
        return LOW
    if requested in allowed_canonical:
        return requested

    order = {level: idx for idx, level in enumerate(ALL_LEVELS)}
    requested_rank = order[requested]
    permitted = [lvl for lvl in allowed_canonical if order.get(lvl, 0) <= requested_rank]
    return permitted[-1] if permitted else allowed_canonical[0]


def profile_for(level: str, settings=None) -> ThinkingProfile:
    """Build the ThinkingProfile for a level from application settings."""
    if settings is None:
        from config import get_settings

        settings = get_settings()

    level = normalize_thinking_level(level)
    if level == LOW:
        return ThinkingProfile(LOW, int(settings.thinking_budget_low), float(settings.thinking_mult_low), "low")
    if level == HIGH:
        return ThinkingProfile(HIGH, int(settings.thinking_budget_high), float(settings.thinking_mult_high), "high")
    if level == MAX:
        return ThinkingProfile(MAX, int(settings.thinking_budget_max), float(settings.thinking_mult_max), "high")
    return ThinkingProfile(MEDIUM, int(settings.thinking_budget_medium), float(settings.thinking_mult_medium), "medium")


THINK_START: Final[str] = "<think>"
THINK_END: Final[str] = "</think>"


def split_thinking(text: str) -> tuple[str, str]:
    """Split a completed response into (thinking_text, answer_text).

    Handles zero or more ``<think>...</think>`` blocks anywhere in the text
    (models occasionally open with reasoning, then answer). Unterminated
    blocks (truncated streams) are treated entirely as thinking.
    """
    if THINK_START not in text:
        return "", text

    thinking_parts: list[str] = []
    answer_parts: list[str] = []
    cursor = 0
    while True:
        start = text.find(THINK_START, cursor)
        if start == -1:
            answer_parts.append(text[cursor:])
            break
        answer_parts.append(text[cursor:start])
        end = text.find(THINK_END, start + len(THINK_START))
        if end == -1:
            thinking_parts.append(text[start + len(THINK_START):])
            cursor = len(text)
            break
        thinking_parts.append(text[start + len(THINK_START):end])
        cursor = end + len(THINK_END)

    return "".join(thinking_parts).strip(), "".join(answer_parts).strip()


class ThinkingStreamSplitter:
    """Incremental splitter for token streams that may contain <think> blocks.

    Feed raw provider tokens via :meth:`feed`; it yields ``(kind, text)``
    tuples where kind is ``"thinking"`` or ``"answer"``. Handles start/end
    tags that arrive split across token boundaries.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._in_thinking = False
        self.thinking_text = ""
        self.answer_text = ""

    def feed(self, token: str) -> list[tuple[str, str]]:
        self._buffer += token
        events: list[tuple[str, str]] = []

        while self._buffer:
            if self._in_thinking:
                end_idx = self._buffer.find(THINK_END)
                if end_idx == -1:
                    # Keep a small tail in case THINK_END straddles tokens.
                    keep = len(THINK_END) - 1
                    emit, self._buffer = self._buffer[:-keep] if keep else self._buffer, self._buffer[-keep:] if keep else ""
                    if emit:
                        self.thinking_text += emit
                        events.append(("thinking", emit))
                    break
                emit = self._buffer[:end_idx]
                if emit:
                    self.thinking_text += emit
                    events.append(("thinking", emit))
                self._buffer = self._buffer[end_idx + len(THINK_END):]
                self._in_thinking = False
            else:
                start_idx = self._buffer.find(THINK_START)
                if start_idx == -1:
                    # Guard against a partial THINK_START at the buffer tail.
                    keep = 0
                    for size in range(min(len(THINK_START) - 1, len(self._buffer)), 0, -1):
                        if THINK_START.startswith(self._buffer[-size:]):
                            keep = size
                            break
                    emit = self._buffer[: len(self._buffer) - keep] if keep else self._buffer
                    if emit:
                        self.answer_text += emit
                        events.append(("answer", emit))
                    self._buffer = self._buffer[len(self._buffer) - keep:] if keep else ""
                    break
                emit = self._buffer[:start_idx]
                if emit:
                    self.answer_text += emit
                    events.append(("answer", emit))
                self._buffer = self._buffer[start_idx + len(THINK_START):]
                self._in_thinking = True

        return events

    def flush(self) -> tuple[str, str]:
        """Return the final (thinking, answer) texts once the stream ends."""
        if self._buffer:
            if self._in_thinking:
                self.thinking_text += self._buffer
            else:
                self.answer_text += self._buffer
            self._buffer = ""
        return self.thinking_text, self.answer_text
