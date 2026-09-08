"""Tests for the bounded validate-and-repair loop (generation/iac_repair.py)."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

import generation.iac_repair as iac_repair
from generation.iac_repair import run_iac_repair_loop
from tools.iac_validator import Finding, IacValidationResult, LayerResult

BAD_TERRAFORM = '<cloudgpt_artifact filename="main.tf" title="VPC">resource "aws_s3_bucket" "x" {\nbucket="y"\n}</cloudgpt_artifact>'
GOOD_TERRAFORM = '<cloudgpt_artifact filename="main.tf" title="VPC">resource "aws_s3_bucket" "x" {\n  bucket = "y"\n}</cloudgpt_artifact>'


def _result(valid: bool) -> IacValidationResult:
    layer = LayerResult(name="terraform-fmt", status="passed" if valid else "failed")
    if not valid:
        layer.findings = [Finding(code="fmt", message="misaligned indentation")]
    return IacValidationResult(artifact_type="terraform", valid=valid, layers=[layer])


def _settings(**overrides):
    base = {"iac_validation_enabled": True, "iac_max_repair_iterations": 3,
            "iac_validator_timeout_seconds": 120}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_no_artifacts_skips_loop(monkeypatch):
    calls = []

    async def gen(messages, thinking):
        calls.append(messages)
        return "plain answer"

    meta = await run_iac_repair_loop("q", "plain answer", [], gen, "Max", _settings())
    assert meta["repair_attempted"] is False
    assert meta["artifact_count"] == 0
    assert calls == []


@pytest.mark.asyncio
async def test_repair_converges_within_budget(monkeypatch):
    """First validation fails, regeneration returns valid IaC → loop repairs."""
    validations = iter([_result(False), _result(True)])

    def fake_validate(content, kind=None, timeout_seconds=None):
        return next(validations)

    monkeypatch.setattr(iac_repair, "validate_artifact", fake_validate)

    regen_calls = []

    async def gen(messages, thinking):
        regen_calls.append(messages)
        return GOOD_TERRAFORM

    meta = await run_iac_repair_loop("q", BAD_TERRAFORM, [{"role": "user", "content": "q"}],
                                     gen, "Max", _settings())

    assert meta["repair_attempted"] is True
    assert meta["iterations"] == 1
    assert meta["valid"] is True
    assert meta["final_answer"] == GOOD_TERRAFORM
    assert len(regen_calls) == 1
    # The regeneration prompt carries the validation findings
    assert "IAC VALIDATION FEEDBACK" in regen_calls[0][-1]["content"]


@pytest.mark.asyncio
async def test_budget_exhausted_keeps_original_answer_honestly(monkeypatch):
    """Every iteration fails → the ORIGINAL answer is kept and findings reported."""
    monkeypatch.setattr(iac_repair, "validate_artifact", lambda *a, **k: _result(False))

    async def gen(messages, thinking):
        return BAD_TERRAFORM

    meta = await run_iac_repair_loop("q", BAD_TERRAFORM, [], gen, "Max", _settings())

    assert meta["iterations"] == 3
    assert meta["valid"] is False
    assert meta["final_answer"] == BAD_TERRAFORM  # original kept, not the failed repair
    assert meta["unresolved_findings"]


@pytest.mark.asyncio
async def test_tier_caps_limit_iterations(monkeypatch):
    """Core caps at 2, Lite at 0 — regardless of the configured maximum."""
    seen_iterations = []

    def fake_validate(content, kind=None, timeout_seconds=None):
        seen_iterations.append(1)
        return _result(False)

    monkeypatch.setattr(iac_repair, "validate_artifact", fake_validate)

    async def gen(messages, thinking):
        return BAD_TERRAFORM

    core_meta = await run_iac_repair_loop("q", BAD_TERRAFORM, [], gen, "Core", _settings())
    assert core_meta["iterations"] == 2

    seen_iterations.clear()
    lite_meta = await run_iac_repair_loop("q", BAD_TERRAFORM, [], gen, "Lite", _settings())
    assert lite_meta["repair_attempted"] is False
    assert lite_meta["iterations"] == 0
    assert len(seen_iterations) == 1  # deterministic validation only, no repair


@pytest.mark.asyncio
async def test_emit_event_receives_additive_events(monkeypatch):
    """The loop emits only additive event payloads; contract shapes are new keys."""
    monkeypatch.setattr(iac_repair, "validate_artifact", lambda *a, **k: _result(True))
    events = []

    async def emit(evt):
        events.append(evt)

    async def gen(messages, thinking):
        return BAD_TERRAFORM

    await run_iac_repair_loop("q", BAD_TERRAFORM, [], gen, "Max", _settings(), emit_event=emit)
    assert any("iac_validation" in e for e in events)
    assert all(set(e.keys()) <= {"iac_validation", "iac_repair", "stage", "status", "label"} for e in events)
