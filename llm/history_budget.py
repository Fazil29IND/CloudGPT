"""
Token-aware conversation history budgeting and rolling LLM compaction for CloudGPT.

Ensures chat history fits strictly within allocated token bounds (preserving whole messages,
newest-first) and folds older dropped turns into a persistent, rolling session summary.
"""

from __future__ import annotations

import logging
from typing import Any

from .context_metrics import estimate_tokens

logger = logging.getLogger(__name__)

COMPACTION_PROMPT = """You are a concise technical summarizer for CloudGPT, an AI cloud architect assistant.
Your task is to update or generate a concise running summary of older conversation turns that are being archived from the active context window.

INVARIANTS TO PRESERVE:
1. User decisions and stated cloud constraints (e.g. cloud provider, region, VPC setup, compliance requirements).
2. Key architectural choices, service names, and tool choices (e.g. Terraform, CDK, Kubernetes).
3. Exact resource names, IDs, numbers, CIDR blocks, and critical URLs.
4. Unresolved questions or pending tasks.

OUTPUT FORMAT:
- A brief bulleted summary under 400 words.
- Focus strictly on technical facts and decisions. Do not include conversational pleasantries.

{previous_summary_block}

NEW MESSAGES TO COMPACT:
{new_messages_text}

UPDATED RUNNING SUMMARY:"""


def trim_history_by_tokens(
    messages: list[dict[str, Any]],
    max_tokens: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Trim chat history messages to stay within max_tokens budget.

    Traverses messages newest-first and never splits a message mid-turn.

    Args:
        messages: Chronological list of message dicts ({'role': ..., 'content': ...}).
        max_tokens: Maximum tokens allowed for chat history.

    Returns:
        (kept_messages, dropped_messages) — both in chronological order.
    """
    if not messages or max_tokens <= 0:
        return [], list(messages or [])

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    accumulated_tokens = 0

    # Traverse backwards (newest to oldest)
    for msg in reversed(messages):
        content = msg.get("content", "") or ""
        # Base message envelope tokens (~4 tokens for role/format)
        msg_tokens = estimate_tokens(content) + 4
        if accumulated_tokens + msg_tokens <= max_tokens or not kept:
            kept.append(msg)
            accumulated_tokens += msg_tokens
        else:
            dropped.append(msg)

    # Restore chronological order
    kept.reverse()
    dropped.reverse()

    return kept, dropped


async def compact_history_turns(
    dropped_messages: list[dict[str, Any]],
    existing_summary: str | None = None,
) -> str:
    """
    Summarize dropped history messages into a rolling session summary.

    Args:
        dropped_messages: Older messages trimmed from the context window.
        existing_summary: The existing running summary, if any.

    Returns:
        An updated summary string (max ~500 tokens).
    """
    if not dropped_messages:
        return existing_summary or ""

    formatted_turns = []
    for m in dropped_messages:
        role = m.get("role", "unknown").upper()
        content = (m.get("content", "") or "").strip()
        if content:
            formatted_turns.append(f"{role}: {content}")

    new_messages_text = "\n\n".join(formatted_turns)
    prev_block = (
        f"PREVIOUS SESSION SUMMARY:\n{existing_summary}"
        if existing_summary
        else "PREVIOUS SESSION SUMMARY: None"
    )
    prompt = COMPACTION_PROMPT.format(
        previous_summary_block=prev_block,
        new_messages_text=new_messages_text[:10_000],  # Bound input size
    )

    try:
        from llm.provider import get_sub_model_provider

        summarizer = get_sub_model_provider()
        messages = [
            {"role": "system", "content": "You are a concise technical summarizer."},
            {"role": "user", "content": prompt},
        ]
        summary_text = await summarizer.generate(messages=messages, stream=False)
        summary = (summary_text or "").strip()
        if summary:
            logger.info("history_budget.compacted", extra={"turns_compacted": len(dropped_messages)})
            return summary
    except Exception as e:
        logger.warning("history_budget.compaction_failed", extra={"error": str(e)})

    # Fallback heuristic if LLM summarization is unavailable
    fallback_lines = []
    if existing_summary:
        fallback_lines.append(existing_summary)
    for m in dropped_messages:
        if m.get("role") == "user":
            snippet = (m.get("content", "") or "")[:120].strip()
            if snippet:
                fallback_lines.append(f"- User asked: {snippet}...")
    return "\n".join(fallback_lines[-8:])
