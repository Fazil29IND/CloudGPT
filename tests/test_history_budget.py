"""Unit tests for history budgeting and compaction (llm/history_budget.py)."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from llm.history_budget import trim_history_by_tokens, compact_history_turns


def test_trim_history_by_tokens_under_budget():
    messages = [
        {"role": "user", "content": "What is AWS Lambda?"},
        {"role": "assistant", "content": "AWS Lambda is a serverless compute service."},
    ]
    # Generous budget
    kept, dropped = trim_history_by_tokens(messages, max_tokens=1000)
    assert kept == messages
    assert dropped == []


def test_trim_history_by_tokens_preserves_whole_messages_newest_first():
    messages = [
        {"role": "user", "content": "Turn 1: Very long question about cloud storage architecture " * 10},
        {"role": "assistant", "content": "Turn 1 answer: S3 is an object store " * 10},
        {"role": "user", "content": "Turn 2: Short question"},
        {"role": "assistant", "content": "Turn 2: Short answer"},
    ]
    # Tight budget that only fits Turn 2
    kept, dropped = trim_history_by_tokens(messages, max_tokens=60)
    assert len(kept) == 2
    assert kept[0]["content"] == "Turn 2: Short question"
    assert kept[1]["content"] == "Turn 2: Short answer"
    assert len(dropped) == 2
    assert "Turn 1" in dropped[0]["content"]
    assert "Turn 1" in dropped[1]["content"]


def test_trim_history_by_tokens_empty_input():
    kept, dropped = trim_history_by_tokens([], max_tokens=500)
    assert kept == []
    assert dropped == []


@pytest.mark.asyncio
async def test_compact_history_turns_success():
    dropped_turns = [
        {"role": "user", "content": "We use AWS with Terraform for our deployments."},
        {"role": "assistant", "content": "Understood. Terraform is great for AWS IaC."},
    ]

    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(
        return_value="User uses AWS and manages infrastructure via Terraform."
    )

    with patch("llm.provider.get_sub_model_provider", return_value=mock_provider):
        summary = await compact_history_turns(dropped_turns, existing_summary="Initial summary.")
        assert "AWS" in summary
        assert "Terraform" in summary


@pytest.mark.asyncio
async def test_compact_history_turns_fallback_on_error():
    dropped_turns = [
        {"role": "user", "content": "Failed compaction test."},
    ]

    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(side_effect=RuntimeError("LLM offline"))

    with patch("llm.provider.get_sub_model_provider", return_value=mock_provider):
        summary = await compact_history_turns(dropped_turns, existing_summary="Existing baseline.")
        # Graceful fallback appends user intent rather than crashing
        assert "Existing baseline." in summary
        assert "Failed compaction test" in summary

