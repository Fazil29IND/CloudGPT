"""Tests for token economics fixes (Part 4 of the implementation plan).

Covers:
- settle_usage single-writer accounting: no more 5h/week double-count
  (regression guard with a scripted fake connection — no PostgreSQL needed)
- estimated_cost from the configured price table
- true-input billing helpers (provider-reported → estimate → query-only)
- Max thinking restricted to the Apex plan
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import db as db_module
from api.chat_routes import _true_input_tokens
from config import Settings, get_settings
from core.entitlements import Entitlements, THINKING_CORE, resolve_entitlements


# ─── settle_usage: single-writer accounting ──────────────────────────────────


class _FakeCursor:
    def __init__(self, fetch_results):
        self._fetch_results = list(fetch_results)
        self.executed: list[str] = []

    def execute(self, sql, params=None):
        self.executed.append(" ".join(sql.split()))

    def fetchone(self):
        if self._fetch_results:
            return self._fetch_results.pop(0)
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run_settle(status="settled"):
    """Call settle_usage against a scripted fake connection; return the cursor."""
    cursor = _FakeCursor(
        fetch_results=[
            {"id": 1, "status": "reserved", "reserved_tokens": 500},  # reservation row
            {"tokens_used_5h": 1000, "tokens_used_week": 2000},       # users row
        ]
    )
    ent = Entitlements(
        plan_key="pro", model_tier="Pro", tokens_day=1000, tokens_month=1000,
        tokens_5h=1000, tokens_week=1000, web_search=True, rag=True,
        cloud_api=False, max_file_bytes=1, max_files=1,
    )
    with patch.object(db_module, "get_connection", return_value=_FakeConnection(cursor)), \
         patch.object(db_module, "put_connection", lambda conn: None):
        usage = db_module.settle_usage(
            1, "00000000-0000-0000-0000-000000000001", ent,
            model="gemini-3.8-flash", input_tokens=100, output_tokens=200,
            status=status, thinking_tokens=50, estimated_cost=0.0012,
        )
    assert usage == {"tokens_used_5h": 1000, "tokens_used_week": 2000}
    return cursor


def test_settle_usage_never_writes_token_windows():
    """increment_token_usage is the single writer for the four token windows.

    Historically settle_usage ALSO added the total to tokens_used_5h/week,
    double-counting every settled request against those windows.
    """
    cursor = _run_settle(status="settled")
    update_users = [s for s in cursor.executed if s.startswith("UPDATE users")]
    assert update_users == [], f"settle_usage must not write token windows, ran: {update_users}"


def test_settle_usage_writes_estimated_cost_to_ledger():
    cursor = _run_settle(status="settled")
    insert = [s for s in cursor.executed if "INSERT INTO usage_events" in s]
    assert insert, "usage ledger insert missing"
    assert "estimated_cost" in insert[0]


def test_settle_usage_cancelled_still_never_writes_windows():
    cursor = _run_settle(status="cancelled")
    update_users = [s for s in cursor.executed if s.startswith("UPDATE users")]
    assert update_users == []


# ─── Price table / estimated cost ────────────────────────────────────────────


def test_estimate_llm_cost_known_model():
    settings = get_settings()
    cost = settings.estimate_llm_cost_usd(
        "gemini-3.8-flash",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        thinking_tokens=1_000_000,
    )
    rates = settings.llm_price_input_per_m["gemini-3.8-flash"]
    rateo = settings.llm_price_output_per_m["gemini-3.8-flash"]
    ratet = settings.llm_price_thinking_per_m["gemini-3.8-flash"]
    assert cost == pytest.approx(rates + rateo + ratet)


def test_estimate_llm_cost_unknown_model_uses_conservative_max():
    settings = get_settings()
    cost = settings.estimate_llm_cost_usd(
        "mystery-model", input_tokens=1_000_000, output_tokens=1_000_000
    )
    assert cost == pytest.approx(
        max(settings.llm_price_input_per_m.values())
        + max(settings.llm_price_output_per_m.values())
    )


def test_estimate_llm_cost_partial_tokens():
    settings = get_settings()
    rate = settings.llm_price_input_per_m["gemini-3.8-flash"]
    cost = settings.estimate_llm_cost_usd("gemini-3.8-flash", input_tokens=500_000, output_tokens=0)
    assert cost == pytest.approx(0.5 * rate)


def test_estimate_llm_cost_empty_table_returns_none():
    settings = Settings(llm_price_input_per_m={}, llm_price_output_per_m={})
    assert settings.estimate_llm_cost_usd("gemini-3.8-flash", 100, 100) is None


# ─── True-input billing ──────────────────────────────────────────────────────


def test_true_input_tokens_prefers_provider_reported():
    result = type("R", (), {"usage": {"prompt_tokens": 4321}})()
    assert _true_input_tokens(result, fallback=10) == 4321


def test_true_input_tokens_falls_back_to_estimate():
    from api.chat_routes import _last_gen_usage

    token = _last_gen_usage.set({"prompt_tokens": 2500})
    try:
        result = type("R", (), {"usage": {}})()
        assert _true_input_tokens(result, fallback=10) == 2500
    finally:
        _last_gen_usage.reset(token)


def test_true_input_tokens_final_fallback_is_query_count():
    from api.chat_routes import _last_gen_usage

    token = _last_gen_usage.set(None)
    try:
        result = type("R", (), {"usage": {}})()
        assert _true_input_tokens(result, fallback=77) == 77
    finally:
        _last_gen_usage.reset(token)


# ─── Max thinking restricted to Apex ────────────────────────────────────────


def test_max_thinking_is_apex_exclusive():
    assert "Max" not in THINKING_CORE
    core = resolve_entitlements("Pro")
    assert "Max" not in core.allowed_thinking_levels
    apex = resolve_entitlements("Max")
    assert "Max" in apex.allowed_thinking_levels

    from core.entitlements import get_allowed_thinking_for_model

    # The Pro x Lite matrix cell no longer leaks Max to Core plans.
    assert "Max" not in get_allowed_thinking_for_model("Pro", "Lite")
