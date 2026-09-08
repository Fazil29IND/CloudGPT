"""Task 8: Test OPA sandbox execution during IaC validation."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from config import get_settings
from tools.iac_validator import (
    _run_opa_sandbox,
    validate_bundle,
)


def test_opa_sandbox_skipped_when_opa_not_installed():
    """When the opa binary is not on PATH, opa-sandbox layer reports skipped."""
    with patch("tools.iac_validator._binary_available", return_value=False):
        res = _run_opa_sandbox({"policy.rego": "package main\nimport rego.v1"}, Path("."), 10.0)
        assert res.name == "opa-sandbox"
        assert res.status == "skipped"
        assert "not installed" in res.detail


def test_opa_sandbox_passed_when_tests_pass(tmp_path: Path):
    """When opa test executes and passes, opa-sandbox layer reports passed."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "data.security.test_policy_compilation: PASS (0.5ms)\nPASS: 1/1"
    mock_proc.stderr = ""

    with patch("tools.iac_validator._binary_available", return_value=True), \
         patch("subprocess.run", return_value=mock_proc):

        rego_files = {
            "policies/security.rego": "package security\nimport rego.v1\ndefault allow = false",
        }
        res = _run_opa_sandbox(rego_files, tmp_path, 10.0)
        assert res.name == "opa-sandbox"
        assert res.status == "passed"
        assert len(res.findings) == 0


def test_opa_sandbox_failed_when_test_fails(tmp_path: Path):
    """When opa test reports failures, findings are extracted and status is failed."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = "data.security.test_deny_unrestricted_ingress: FAIL (0.8ms)\nFAIL: 1/1"
    mock_proc.stderr = ""

    with patch("tools.iac_validator._binary_available", return_value=True), \
         patch("subprocess.run", return_value=mock_proc):

        rego_files = {
            "policies/security.rego": "package security\nimport rego.v1",
        }
        res = _run_opa_sandbox(rego_files, tmp_path, 10.0)
        assert res.name == "opa-sandbox"
        assert res.status == "failed"
        assert len(res.findings) > 0
        assert res.findings[0].code == "opa.test_failure"


def test_validate_bundle_includes_opa_sandbox():
    """validate_bundle runs opa-sandbox layer for Rego policies."""
    artifacts = [
        {
            "identifier": "policies/security.rego",
            "filename": "policies/security.rego",
            "type": "rego",
            "content": "package security\nimport rego.v1\ndefault allow = false",
        }
    ]

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "PASS: 1/1"
    mock_proc.stderr = ""

    with patch("tools.iac_validator._binary_available", return_value=True), \
         patch("subprocess.run", return_value=mock_proc):

        res = validate_bundle(artifacts)
        sandbox_layer = next((layer for layer in res.layers if layer.name == "opa-sandbox"), None)
        assert sandbox_layer is not None
        assert sandbox_layer.status == "passed"


def test_validate_bundle_skips_sandbox_when_disabled():
    """When iac_opa_sandbox_enabled is False, opa-sandbox layer is not run."""
    artifacts = [
        {
            "identifier": "policies/security.rego",
            "filename": "policies/security.rego",
            "type": "rego",
            "content": "package security\nimport rego.v1",
        }
    ]

    settings = get_settings()
    with patch.object(settings, "iac_opa_sandbox_enabled", False):
        res = validate_bundle(artifacts)
        sandbox_layer = next((layer for layer in res.layers if layer.name == "opa-sandbox"), None)
        assert sandbox_layer is None
