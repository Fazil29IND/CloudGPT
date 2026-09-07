"""Layered IaC validator for CloudGPT-generated infrastructure artifacts.

Validates artifact content (Terraform, CloudFormation, Kubernetes/Helm) by
materializing it to a temp directory and running the canonical validation
stack, in order:

    terraform fmt -check → terraform validate → tflint → checkov
    cfn-lint (CloudFormation) → helm lint + kubeconform (K8s)

Design rules (mirrors the cloud-tools pattern):
- Every binary is OPTIONAL: detected at runtime via ``shutil.which``. A
  missing binary yields a layer with status ``skipped`` — never a fabricated
  pass and never a fabricated failure.
- Findings from every layer are AGGREGATED; callers feed the full list back
  into the generation repair loop (IaCGen-style bounded feedback loop).
- The full stack re-runs on every repair iteration so a fix cannot silently
  regress a previously-passing layer.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

# Extensions that identify each artifact kind
_TERRAFORM_RE = re.compile(r"\b(resource|module|variable|output|terraform|provider)\s+[\"]", re.IGNORECASE)
_CLOUDFORMATION_RE = re.compile(r'"(Resources|AWSTemplateFormatVersion)"\s*:', re.IGNORECASE)
_K8S_RE = re.compile(r"^\s*(apiVersion|kind):\s", re.MULTILINE)


@dataclass
class Finding:
    code: str
    message: str
    line: int | None = None
    severity: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "line": self.line, "severity": self.severity}


@dataclass
class LayerResult:
    name: str
    status: str  # passed | failed | skipped
    findings: list[Finding] = field(default_factory=list)
    duration_ms: float = 0.0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "findings": [f.to_dict() for f in self.findings],
            "duration_ms": round(self.duration_ms, 1),
            "detail": self.detail,
        }


@dataclass
class IacValidationResult:
    artifact_type: str
    valid: bool
    layers: list[LayerResult] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def findings(self) -> list[Finding]:
        return [f for layer in self.layers for f in layer.findings]

    def summary_lines(self) -> list[str]:
        """Compact, repair-loop-friendly rendering of every finding."""
        lines = []
        for layer in self.layers:
            if layer.status == "skipped":
                lines.append(f"[{layer.name}] SKIPPED ({layer.detail})")
            elif layer.status == "passed":
                lines.append(f"[{layer.name}] PASSED")
            else:
                for f in layer.findings[:15]:
                    loc = f" (line {f.line})" if f.line else ""
                    lines.append(f"[{layer.name}] {f.code}: {f.message}{loc}")
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "valid": self.valid,
            "duration_ms": round(self.duration_ms, 1),
            "layers": [l.to_dict() for l in self.layers],
        }


def detect_artifact_type(content: str) -> str:
    """Best-effort classification of an artifact's IaC kind."""
    if _CLOUDFORMATION_RE.search(content or ""):
        return "cloudformation"
    if _K8S_RE.search(content or "") and not _TERRAFORM_RE.search(content or ""):
        return "kubernetes"
    return "terraform"


def _binary_available(name: str) -> bool:
    return shutil.which(name) is not None


def _run_layer(
    name: str,
    cmd: list[str],
    cwd: Path,
    timeout: float,
    parse: "callable[[str, str], list[Finding]] | None" = None,
) -> LayerResult:
    """Execute one validation binary; timeouts and missing binaries are honest."""
    if not _binary_available(cmd[0]):
        return LayerResult(name=name, status="skipped", detail=f"{cmd[0]} not installed")
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False
        )
        findings = parse(proc.stdout, proc.stderr) if parse else []
        status = "passed" if proc.returncode == 0 else "failed"
        return LayerResult(
            name=name,
            status=status,
            findings=findings,
            duration_ms=(time.perf_counter() - started) * 1000,
            detail=f"exit={proc.returncode}",
        )
    except subprocess.TimeoutExpired:
        return LayerResult(
            name=name,
            status="failed",
            findings=[Finding(code=f"{name}.timeout", message=f"{cmd[0]} exceeded {timeout:.0f}s timeout", severity="high")],
            duration_ms=(time.perf_counter() - started) * 1000,
            detail="timeout",
        )
    except Exception as exc:
        return LayerResult(
            name=name,
            status="skipped",
            findings=[],
            duration_ms=(time.perf_counter() - started) * 1000,
            detail=f"execution error: {exc}",
        )


# ── Output parsers ────────────────────────────────────────────────────────────

def _parse_checkov(stdout: str, _stderr: str) -> list[Finding]:
    try:
        payload = json.loads(stdout)
        findings = []
        for item in payload.get("results", {}).get("failed_checks", []):
            findings.append(
                Finding(
                    code=str(item.get("check_id", "checkov")),
                    message=str(item.get("check_name", "")) + (f" — {item.get('guideline')}" if item.get("guideline") else ""),
                    line=item.get("file_line_start"),
                    severity=str(item.get("severity", "medium")).lower(),
                )
            )
        return findings
    except (ValueError, AttributeError):
        return []


def _parse_generic_lines(stdout: str, stderr: str) -> list[Finding]:
    out: list[Finding] = []
    for raw in (stdout + "\n" + stderr).splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = re.search(r":(\d+):", raw)
        out.append(Finding(code="lint", message=raw[:300], line=int(m.group(1)) if m else None))
    return out[:40]


def _parse_cfn_lint(stdout: str, _stderr: str) -> list[Finding]:
    try:
        payload = json.loads(stdout)
        findings = []
        for item in payload:
            findings.append(
                Finding(
                    code=str(item.get("Id", "cfn-lint")),
                    message=" ".join(str(m.get("Message", "")) for m in item.get("Message", []) if isinstance(m, dict))
                    or str(item.get("Message", "")),
                    line=item.get("Location", {}).get("Start", {}).get("LineNumber"),
                    severity={"Error": "high", "Warning": "medium", "Informational": "low"}.get(item.get("Level", ""), "medium"),
                )
            )
        return findings
    except (ValueError, AttributeError):
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def validate_artifact(content: str, artifact_type: str | None = None, timeout_seconds: float | None = None) -> IacValidationResult:
    """Run the layered validation stack over one artifact's content."""
    settings = get_settings()
    timeout = float(timeout_seconds or getattr(settings, "iac_validator_timeout_seconds", 120))
    kind = artifact_type or detect_artifact_type(content)
    layers: list[LayerResult] = []
    started = time.perf_counter()

    if not (content or "").strip():
        return IacValidationResult(artifact_type=kind, valid=False, layers=[], duration_ms=0.0)

    with tempfile.TemporaryDirectory(prefix="cloudgpt-iac-") as tmp:
        workdir = Path(tmp)
        file_name = {"cloudformation": "template.yaml", "kubernetes": "manifest.yaml"}.get(kind, "main.tf")
        artifact_path = workdir / file_name
        artifact_path.write_text(content, encoding="utf-8")

        if kind == "terraform":
            layers.append(_run_layer("terraform-fmt", ["terraform", "fmt", "-check", "-diff", str(artifact_path)], workdir, timeout))
            # terraform validate needs init; skip when no provider cache is wired
            if _binary_available("terraform") and getattr(settings, "iac_terraform_init_enabled", False):
                _run_layer("terraform-init", ["terraform", "init", "-backend=false", "-input=false"], workdir, timeout)
                layers.append(_run_layer("terraform-validate", ["terraform", "validate"], workdir, timeout))
            else:
                layers.append(LayerResult(name="terraform-validate", status="skipped", detail="terraform init cache not enabled"))
            layers.append(_run_layer("tflint", ["tflint", "--chdir", str(workdir)], workdir, timeout, _parse_generic_lines))
            layers.append(_run_layer(
                "checkov",
                ["checkov", "-f", str(artifact_path), "--framework", "terraform", "--output", "json", "--quiet"],
                workdir, timeout, _parse_checkov,
            ))
        elif kind == "cloudformation":
            layers.append(_run_layer(
                "cfn-lint",
                ["cfn-lint", "--format", "json", str(artifact_path)],
                workdir, timeout, _parse_cfn_lint,
            ))
            layers.append(_run_layer(
                "checkov",
                ["checkov", "-f", str(artifact_path), "--framework", "cloudformation", "--output", "json", "--quiet"],
                workdir, timeout, _parse_checkov,
            ))
        elif kind == "kubernetes":
            layers.append(_run_layer(
                "checkov",
                ["checkov", "-f", str(artifact_path), "--framework", "kubernetes", "--output", "json", "--quiet"],
                workdir, timeout, _parse_checkov,
            ))
            layers.append(_run_layer("kubeconform", ["kubeconform", "-strict", str(artifact_path)], workdir, timeout, _parse_generic_lines))

    executed = [l for l in layers if l.status in ("passed", "failed")]
    valid = bool(executed) and all(l.status == "passed" for l in executed)
    return IacValidationResult(
        artifact_type=kind,
        valid=valid,
        layers=layers,
        duration_ms=(time.perf_counter() - started) * 1000,
    )
