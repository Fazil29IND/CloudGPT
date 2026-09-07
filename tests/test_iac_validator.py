"""Unit tests for the layered IaC validator.

Binaries are mocked at the subprocess boundary so the hermetic suite never
requires terraform/tflint/checkov installed. A separate opt-in class runs the
real binaries when present (skipped silently otherwise).
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import tools.iac_validator as iac_validator
from tools.iac_validator import (
    IacValidationResult,
    _parse_checkov,
    detect_artifact_type,
    validate_artifact,
)

TERRAFORM_SAMPLE = """
resource "aws_s3_bucket" "data" {
  bucket = "prod-data-bucket"
}
"""

CLOUDFORMATION_SAMPLE = """
{
  "AWSTemplateFormatVersion": "2010-09-09",
  "Resources": {
    "Queue": { "Type": "AWS::SQS::Queue" }
  }
}
"""

K8S_SAMPLE = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: web
          image: nginx:1.27
"""


# ── Artifact type detection ───────────────────────────────────────────────────

def test_detect_artifact_types():
    assert detect_artifact_type(TERRAFORM_SAMPLE) == "terraform"
    assert detect_artifact_type(CLOUDFORMATION_SAMPLE) == "cloudformation"
    assert detect_artifact_type(K8S_SAMPLE) == "kubernetes"
    assert detect_artifact_type("random text without structure") == "terraform"  # default


# ── Missing binaries → honest skips ──────────────────────────────────────────

def test_missing_binaries_produce_skipped_layers_and_invalid(monkeypatch):
    monkeypatch.setattr(iac_validator.shutil, "which", lambda name: None)
    result = validate_artifact(TERRAFORM_SAMPLE)

    assert isinstance(result, IacValidationResult)
    assert result.artifact_type == "terraform"
    # No layer executed → nothing may claim a pass
    assert result.valid is False
    statuses = {layer.name: layer.status for layer in result.layers}
    assert statuses["terraform-fmt"] == "skipped"
    assert statuses["terraform-validate"] == "skipped"
    assert statuses["checkov"] == "skipped"


def test_empty_artifact_is_invalid_without_layers():
    result = validate_artifact("   ")
    assert result.valid is False
    assert result.layers == []


# ── Execution paths with mocked binaries ─────────────────────────────────────

def _fake_which(monkeypatch, available: dict[str, bool]):
    def which(name):
        return "/usr/bin/" + name if available.get(name) else None
    monkeypatch.setattr(iac_validator.shutil, "which", which)


def test_all_layers_pass(monkeypatch):
    _fake_which(monkeypatch, {"terraform": True, "tflint": True, "checkov": True})

    def fake_run(cmd, **kwargs):
        name = cmd[0]
        if name == "terraform":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if name == "checkov":
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout='{"results": {"failed_checks": [], "passed_checks": []}}',
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(iac_validator.subprocess, "run", fake_run)
    result = validate_artifact(TERRAFORM_SAMPLE)

    assert result.valid is True
    # Skipped layers (e.g. terraform-validate without init cache) don't block validity,
    # but every layer that EXECUTED must have passed.
    executed = [layer for layer in result.layers if layer.status != "skipped"]
    assert len(executed) >= 3
    assert all(layer.status == "passed" for layer in executed)


def test_failing_layer_aggregates_findings(monkeypatch):
    _fake_which(monkeypatch, {"terraform": True, "tflint": True, "checkov": True})

    def fake_run(cmd, **kwargs):
        if cmd[0] == "terraform" and "fmt" in cmd:
            return subprocess.CompletedProcess(
                cmd, 3, stdout="--- a/main.tf\n+++ b/main.tf", stderr=""
            )
        if cmd[0] == "checkov":
            return subprocess.CompletedProcess(
                cmd, 1,
                stdout=(
                    '{"results": {"failed_checks": ['
                    '{"check_id": "CKV_AWS_18", "check_name": "S3 access logging",'
                    ' "file_line_start": 2, "severity": "MEDIUM"}]}}'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(iac_validator.subprocess, "run", fake_run)
    result = validate_artifact(TERRAFORM_SAMPLE)

    assert result.valid is False
    by_name = {layer.name: layer for layer in result.layers}
    assert by_name["terraform-fmt"].status == "failed"
    checkov = by_name["checkov"]
    assert checkov.status == "failed"
    assert checkov.findings[0].code == "CKV_AWS_18"
    assert checkov.findings[0].line == 2

    # Summary lines must render findings for the repair loop
    summary = result.summary_lines()
    assert any("CKV_AWS_18" in line for line in summary)


def test_timeout_is_a_failure_not_a_crash(monkeypatch):
    _fake_which(monkeypatch, {"checkov": True})

    def fake_run(cmd, **kwargs):
        raise iac_validator.subprocess.TimeoutExpired(cmd, 120)

    monkeypatch.setattr(iac_validator.subprocess, "run", fake_run)
    result = validate_artifact(CLOUDFORMATION_SAMPLE)
    by_name = {layer.name: layer for layer in result.layers}
    assert by_name["checkov"].status == "failed"
    assert "timeout" in by_name["checkov"].detail


def test_cloudformation_uses_cfn_lint_and_checkov(monkeypatch):
    _fake_which(monkeypatch, {"cfn-lint": True, "checkov": True})
    seen_cmds = []

    def fake_run(cmd, **kwargs):
        seen_cmds.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, stdout="[]" if cmd[0] == "cfn-lint" else '{"results": {"failed_checks": []}}', stderr="")

    monkeypatch.setattr(iac_validator.subprocess, "run", fake_run)
    result = validate_artifact(CLOUDFORMATION_SAMPLE)

    assert result.artifact_type == "cloudformation"
    assert "cfn-lint" in seen_cmds and "checkov" in seen_cmds
    assert result.valid is True


# ── Output parsers ────────────────────────────────────────────────────────────

def test_checkov_parser_extracts_findings():
    stdout = (
        '{"results": {"failed_checks": ['
        '{"check_id": "CKV_K8S_14", "check_name": "Image tag not fixed",'
        ' "file_line_start": 7, "severity": "HIGH"}]}}'
    )
    findings = _parse_checkov(stdout, "")
    assert len(findings) == 1
    assert findings[0].code == "CKV_K8S_14"
    assert findings[0].severity == "high"
    assert findings[0].line == 7


def test_checkov_parser_survives_malformed_json():
    assert _parse_checkov("not json at all", "") == []


# ── Opt-in real-binary verification (never blocks hermetic CI) ────────────────

class TestRealBinaries:
    pytestmark = pytest.mark.skipif(
        not all(iac_validator._binary_available(b) for b in ("terraform",)),
        reason="terraform binary not installed",
    )

    def test_real_terraform_fmt_detects_bad_format(self):
        result = validate_artifact("resource \"aws_s3_bucket\" \"x\" {\n  bucket=\"bad-spacing\"\n}")
        fmt = {layer.name: layer for layer in result.layers}["terraform-fmt"]
        assert fmt.status in ("failed", "skipped")
