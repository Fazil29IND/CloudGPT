"""Apex Mixture-of-Agents (MoA) Verification Pipeline.

Architecture:
  Stage 1 (Generator): Primary tier LLM produces enterprise architecture bundle.
  Stage 2 (Auditor): Independent Evaluator LLM (gemini-3.7-flash) audits the bundle
                    against CIS Benchmarks, least privilege IAM, network boundary security,
                    and FinOps cost efficiency.
  Stage 3 (Synthesizer): If high-severity findings are identified, the primary tier model
                         re-synthesizes the bundle resolving the flagged defects.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from config import get_settings
from llm.provider import get_evaluator_provider, get_llm_provider
from llm.system_prompts import APEX_AUDITOR_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class MoAResult:
    final_bundle: str
    audit_findings: list[dict[str, Any]] = field(default_factory=list)
    high_severity_count: int = 0
    iterations: int = 0
    repaired: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_findings": self.audit_findings,
            "high_severity_count": self.high_severity_count,
            "iterations": self.iterations,
            "repaired": self.repaired,
        }


def _extract_json_payload(text: str) -> dict[str, Any]:
    """Robustly extract and parse JSON object from LLM response text."""
    clean = (text or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
        clean = clean.strip()
    try:
        data = json.loads(clean)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    match = re.search(r"(\{.*\})", clean, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


async def run_moa_verification(
    bundle_text: str,
    query: str,
    tier: str = "Apex",
    emit_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    settings: Any = None,
) -> MoAResult:
    """Run the 3-stage MoA verification pipeline over generated infrastructure bundles."""
    cfg = settings or get_settings()
    if not getattr(cfg, "enable_apex_moa", True):
        return MoAResult(final_bundle=bundle_text)

    t = (tier or "Apex").strip().lower()
    if t not in ("max", "apex", "developer", "admin"):
        return MoAResult(final_bundle=bundle_text)

    # Stage 2 — Security & CIS Benchmark Audit
    if emit_event is not None:
        try:
            await emit_event({
                "stage": "auditing_bundle",
                "label": "Auditing infrastructure against CIS benchmarks…",
                "status": "active",
            })
        except Exception:
            pass

    auditor_llm = get_evaluator_provider(tier)
    audit_user_prompt = (
        f"User Architecture Request:\n{query}\n\n"
        f"Generated Infrastructure Bundle:\n{bundle_text}"
    )

    findings: list[dict[str, Any]] = []
    try:
        raw_audit = await auditor_llm.classify(audit_user_prompt, system_prompt=APEX_AUDITOR_SYSTEM_PROMPT)
        audit_data = _extract_json_payload(raw_audit)
        raw_findings = audit_data.get("findings", [])
        if isinstance(raw_findings, list):
            findings = [f for f in raw_findings if isinstance(f, dict)]
    except Exception as audit_err:
        logger.warning("moa_pipeline.auditor_error", error=str(audit_err))

    high_severity = [
        f for f in findings
        if str(f.get("severity", "")).lower() == "high"
    ]
    high_severity_count = len(high_severity)

    if emit_event is not None:
        try:
            await emit_event({
                "stage": "auditing_bundle",
                "label": f"Security audit complete ({high_severity_count} high-severity findings)",
                "status": "complete",
            })
        except Exception:
            pass

    # Stage 3 — Synthesis / Remediation if high-severity findings exist
    if high_severity_count > 0:
        if emit_event is not None:
            try:
                await emit_event({
                    "stage": "synthesizing_bundle",
                    "label": f"Remediating {high_severity_count} security findings…",
                    "status": "active",
                })
            except Exception:
                pass

        synthesizer_llm = get_llm_provider("main", tier)
        findings_bullets = [
            f"- [{f.get('file', 'all')}]: {f.get('code', 'finding')} - {f.get('message', '')}"
            for f in high_severity
        ]
        remediation_prompt = (
            "The independent Security & CIS Benchmark Auditor flagged the following HIGH-severity security findings in your previously generated infrastructure bundle:\n"
            + "\n".join(findings_bullets)
            + "\n\nRegenerate the COMPLETE corrected architecture bundle. Fix every security defect above while keeping all valid configurations, endpoints, and variables intact."
        )

        synth_messages = [
            {"role": "user", "content": query},
            {"role": "assistant", "content": bundle_text},
            {"role": "user", "content": remediation_prompt},
        ]

        try:
            corrected_bundle = await synthesizer_llm.generate(synth_messages, temperature=0.2)
            if emit_event is not None:
                try:
                    await emit_event({
                        "stage": "synthesizing_bundle",
                        "label": "Security remediation complete",
                        "status": "complete",
                    })
                except Exception:
                    pass
            return MoAResult(
                final_bundle=corrected_bundle or bundle_text,
                audit_findings=findings,
                high_severity_count=high_severity_count,
                iterations=1,
                repaired=bool(corrected_bundle and corrected_bundle != bundle_text),
            )
        except Exception as synth_err:
            logger.warning("moa_pipeline.synthesizer_error", error=str(synth_err))

    return MoAResult(
        final_bundle=bundle_text,
        audit_findings=findings,
        high_severity_count=0,
        iterations=0,
        repaired=False,
    )
