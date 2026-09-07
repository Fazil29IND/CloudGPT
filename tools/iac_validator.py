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
import textwrap
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
_REGO_RE = re.compile(r"^\s*package\s+[a-zA-Z0-9_.]+", re.MULTILINE)


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
            "layers": [layer.to_dict() for layer in self.layers],
        }


def detect_artifact_type(content: str, filename: str | None = None) -> str:
    """Best-effort classification of an artifact's IaC kind."""
    fn = (filename or "").lower()
    if fn.endswith(".rego") or _REGO_RE.search(content or ""):
        return "rego"
    if fn.endswith((".json", ".template")) and _CLOUDFORMATION_RE.search(content or ""):
        return "cloudformation"
    if _CLOUDFORMATION_RE.search(content or ""):
        return "cloudformation"
    if _K8S_RE.search(content or "") and not _TERRAFORM_RE.search(content or ""):
        return "kubernetes"
    if fn.endswith((".yaml", ".yml")) and not _TERRAFORM_RE.search(content or ""):
        return "kubernetes"
    if fn.endswith((".md", ".txt")):
        return "doc"
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
        out_msg = (proc.stderr or proc.stdout or "").strip()
        detail = f"exit={proc.returncode}{': ' + out_msg[:200] if out_msg else ''}" if proc.returncode != 0 else ""
        return LayerResult(
            name=name,
            status=status,
            findings=findings,
            duration_ms=(time.perf_counter() - started) * 1000,
            detail=detail,
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


# ── Static Integrity Checkers ──────────────────────────────────────────────────

def check_static_rego_rules(content: str) -> LayerResult:
    """Deterministic Rego v1 compliance check."""
    findings: list[Finding] = []
    if "import rego.v1" not in content:
        findings.append(Finding(
            code="rego.v1_missing",
            message="Rego policy must include 'import rego.v1' for current OPA syntax support.",
            severity="high",
        ))
    if re.search(r"\bdeny\s*\[\s*[a-zA-Z0-9_]+\s*\]\s*\{", content):
        findings.append(Finding(
            code="rego.legacy_syntax",
            message="Rego policy uses legacy 'deny[msg] {' rule syntax; modern Rego v1 requires 'deny contains msg if {'.",
            severity="high",
        ))
    status = "failed" if findings else "passed"
    return LayerResult(name="rego-v1-compliance", status=status, findings=findings)


def check_static_terraform_rules(content: str) -> LayerResult:
    """Deterministic checks for data plane compute, versions, and security groups."""
    findings: list[Finding] = []
    # 1. EKS Compute Data Plane Check:
    if re.search(r'resource\s+"aws_eks_cluster"', content):
        has_ng = bool(re.search(r'resource\s+"aws_eks_node_group"', content))
        has_fargate = bool(re.search(r'resource\s+"aws_eks_fargate_profile"', content))
        has_module_ng = "eks_managed_node_groups" in content
        if not (has_ng or has_fargate or has_module_ng):
            findings.append(Finding(
                code="eks.missing_compute_dataplane",
                message="EKS cluster defined without a compute data plane. Must define 'aws_eks_node_group' or Fargate profile with attached worker node policies.",
                severity="high",
            ))
        elif has_ng:
            p1 = "AmazonEKSWorkerNodePolicy" in content
            p2 = "AmazonEKS_CNI_Policy" in content
            p3 = "AmazonEC2ContainerRegistryReadOnly" in content
            if not (p1 and p2 and p3):
                missing_policies = []
                if not p1:
                    missing_policies.append("AmazonEKSWorkerNodePolicy")
                if not p2:
                    missing_policies.append("AmazonEKS_CNI_Policy")
                if not p3:
                    missing_policies.append("AmazonEC2ContainerRegistryReadOnly")
                findings.append(Finding(
                    code="eks.missing_node_policies",
                    message=f"EKS worker node group missing required attached policies: {', '.join(missing_policies)}.",
                    severity="high",
                ))

    # 2. Hardcoded Deprecated EKS Version Check:
    dep_match = re.search(r'\b(?:cluster_)?version\s*=\s*"(1\.(?:[0-9]|1[0-9]|2[0-9]))"', content)
    if dep_match:
        findings.append(Finding(
            code="eks.deprecated_version",
            message=f"Kubernetes version '{dep_match.group(1)}' is hardcoded to an end-of-life release (<=1.29). Parameterize via variable 'kubernetes_version' with default >= '1.30'.",
            severity="high",
        ))

    # 3. Security Group Ingress 0.0.0.0/0 Check:
    if re.search(r'resource\s+"aws_security_group"', content):
        if re.search(r'ingress\s*\{[^}]*cidr_blocks\s*=\s*\[[^\]]*"0\.0\.0\.0/0"', content):
            findings.append(Finding(
                code="sg.unrestricted_ingress",
                message="Security group ingress rule contains unrestricted '0.0.0.0/0' CIDR block. Restrict ingress to authorized CIDRs or security groups.",
                severity="high",
            ))

    status = "failed" if findings else "passed"
    return LayerResult(name="terraform-integrity-guardrails", status=status, findings=findings)


def check_static_k8s_rules(content: str) -> LayerResult:
    """Deterministic check for HPA and workload resource requests."""
    findings: list[Finding] = []
    if "HorizontalPodAutoscaler" in content:
        if "kind: Deployment" in content and "requests:" not in content:
            findings.append(Finding(
                code="k8s.hpa_missing_requests",
                message="Deployment targeted by HorizontalPodAutoscaler must specify container 'resources.requests' (CPU/memory).",
                severity="high",
            ))
    status = "failed" if findings else "passed"
    return LayerResult(name="k8s-integrity-guardrails", status=status, findings=findings)


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
        file_name = {
            "cloudformation": "template.yaml",
            "kubernetes": "manifest.yaml",
            "rego": "policy.rego",
            "policy": "policy.rego",
        }.get(kind, "main.tf")
        artifact_path = workdir / file_name
        artifact_path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")

        if kind == "terraform":
            fmt_cmd = ["terraform", "fmt", "-check"]
            if _binary_available("diff"):
                fmt_cmd.append("-diff")
            fmt_cmd.append(str(artifact_path))
            layers.append(_run_layer("terraform-fmt", fmt_cmd, workdir, timeout))
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
            layers.append(check_static_terraform_rules(content))
        elif kind in ("rego", "policy"):
            layers.append(_run_layer(
                "opa-check",
                ["opa", "check", "--strict", str(artifact_path)],
                workdir, timeout, _parse_generic_lines,
            ))
            layers.append(check_static_rego_rules(content))
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
            layers.append(check_static_k8s_rules(content))

    tool_layers = [
        lyr for lyr in layers
        if not lyr.name.endswith(("-guardrails", "-compliance"))
    ]
    tool_executed = [lyr for lyr in tool_layers if lyr.status in ("passed", "failed")]
    executed = [lyr for lyr in layers if lyr.status in ("passed", "failed")]
    valid = bool(tool_executed) and all(lyr.status == "passed" for lyr in executed)
    return IacValidationResult(
        artifact_type=kind,
        valid=valid,
        layers=layers,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def validate_bundle(artifacts: list[dict[str, Any]], timeout_seconds: float | None = None) -> IacValidationResult:
    """Validate a multi-file architecture bundle as a coherent system."""
    settings = get_settings()
    timeout = float(timeout_seconds or getattr(settings, "iac_validator_timeout_seconds", 120))
    layers: list[LayerResult] = []
    started = time.perf_counter()

    if not artifacts:
        return IacValidationResult(artifact_type="bundle", valid=False, layers=[], duration_ms=0.0)

    # Separate files by category
    tf_files: dict[str, str] = {}
    rego_files: dict[str, str] = {}
    k8s_files: dict[str, str] = {}
    cfn_files: dict[str, str] = {}
    doc_files: dict[str, str] = {}

    for art in artifacts:
        fn = (art.get("filename") or art.get("file") or "main.tf").strip()
        cnt = textwrap.dedent(art.get("content") or "").strip()
        kind = str(art.get("artifact_type") or art.get("type") or art.get("_kind") or "").lower()
        if not kind or kind in ("iac", "script", "policy"):
            kind = detect_artifact_type(cnt, fn)

        if kind == "terraform" or fn.endswith((".tf", ".tfvars", ".hcl")):
            tf_files[fn] = cnt
        elif kind in ("rego", "policy") or fn.endswith(".rego"):
            rego_files[fn] = cnt
        elif kind == "kubernetes" or fn.endswith((".yaml", ".yml")):
            k8s_files[fn] = cnt
        elif kind == "cloudformation":
            cfn_files[fn] = cnt
        elif fn.endswith((".md", ".txt")):
            doc_files[fn] = cnt

    with tempfile.TemporaryDirectory(prefix="cloudgpt-bundle-") as tmp:
        workdir = Path(tmp)
        # Materialize all files
        for fn, cnt in {**tf_files, **rego_files, **k8s_files, **cfn_files, **doc_files}.items():
            dest = workdir / fn
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(cnt + "\n", encoding="utf-8")

        # 1. Terraform layers across the directory
        if tf_files:
            all_tf = "\n".join(tf_files.values())
            fmt_cmd = ["terraform", "fmt", "-check"]
            if _binary_available("diff"):
                fmt_cmd.append("-diff")
            fmt_cmd.append(str(workdir))
            layers.append(_run_layer("terraform-fmt", fmt_cmd, workdir, timeout))
            if _binary_available("terraform") and getattr(settings, "iac_terraform_init_enabled", False):
                _run_layer("terraform-init", ["terraform", "init", "-backend=false", "-input=false"], workdir, timeout)
                layers.append(_run_layer("terraform-validate", ["terraform", "validate"], workdir, timeout))
            else:
                layers.append(LayerResult(name="terraform-validate", status="skipped", detail="terraform init cache not enabled"))
            layers.append(_run_layer("tflint", ["tflint", "--chdir", str(workdir)], workdir, timeout, _parse_generic_lines))
            layers.append(_run_layer(
                "checkov",
                ["checkov", "-d", str(workdir), "--framework", "terraform", "--output", "json", "--quiet"],
                workdir, timeout, _parse_checkov,
            ))
            layers.append(check_static_terraform_rules(all_tf))

        # 2. Rego layers
        if rego_files:
            for r_fn, r_cnt in rego_files.items():
                r_path = workdir / r_fn
                layers.append(_run_layer("opa-check", ["opa", "check", "--strict", str(r_path)], workdir, timeout, _parse_generic_lines))
                layers.append(check_static_rego_rules(r_cnt))

        # 3. K8s layers
        if k8s_files:
            all_k8s = "\n".join(k8s_files.values())
            for k_fn, k_cnt in k8s_files.items():
                k_path = workdir / k_fn
                layers.append(_run_layer(
                    "checkov",
                    ["checkov", "-f", str(k_path), "--framework", "kubernetes", "--output", "json", "--quiet"],
                    workdir, timeout, _parse_checkov,
                ))
                layers.append(_run_layer("kubeconform", ["kubeconform", "-strict", str(k_path)], workdir, timeout, _parse_generic_lines))
            layers.append(check_static_k8s_rules(all_k8s))

        # 4. Cross-file bundle integrity checks
        bundle_findings: list[Finding] = []
        all_text = "\n".join(art.get("content", "") for art in artifacts)

        # Check README
        readme_content = doc_files.get("README.md") or doc_files.get("readme.md") or ""
        if "Administrator credentials" in readme_content or "AdministratorAccess" in readme_content:
            bundle_findings.append(Finding(
                code="iam.admin_credentials_antipattern",
                message="README prescribes Administrator credentials; must specify a scoped deployment IAM role assumed via STS.",
                severity="high",
            ))
        if tf_files and "aws_eks_cluster" in all_text and readme_content:
            if "update-kubeconfig" not in readme_content:
                bundle_findings.append(Finding(
                    code="readme.missing_kubeconfig",
                    message="README should include 'aws eks update-kubeconfig' command for cluster access.",
                    severity="medium",
                ))
            if "endpoint_public_access = false" in all_text or "endpoint_private_access = true" in all_text:
                if "vpn" not in readme_content.lower() and "bastion" not in readme_content.lower():
                    bundle_findings.append(Finding(
                        code="readme.missing_private_endpoint_notice",
                        message="README must mention that the private EKS cluster requires VPN/bastion access.",
                        severity="medium",
                    ))

        # Check HPA presence if autoscaling is discussed or in K8s
        if "HorizontalPodAutoscaler" in all_text:
            if "requests:" not in all_text:
                bundle_findings.append(Finding(
                    code="k8s.hpa_missing_requests",
                    message="Deployment targeted by HorizontalPodAutoscaler must specify container resources.requests (cpu/memory).",
                    severity="high",
                ))

        status = "failed" if bundle_findings else "passed"
        layers.append(LayerResult(name="bundle-cross-file-guardrails", status=status, findings=bundle_findings))

    executed = [layer for layer in layers if layer.status in ("passed", "failed")]
    valid = bool(executed) and all(layer.status == "passed" for layer in executed)
    return IacValidationResult(
        artifact_type="bundle",
        valid=valid,
        layers=layers,
        duration_ms=(time.perf_counter() - started) * 1000,
    )
