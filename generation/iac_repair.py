"""Bounded validate-and-repair loop for generated IaC artifacts.

Implements the current best-practice pattern from the IaC-generation research
(IaCGen, arXiv:2506.05623; feedback-loop generation, arXiv:2411.19043):

    generate → validate (full stack) → feed findings back → regenerate
    → re-validate (FULL stack re-run each iteration — fixes can regress
    previously-passing checks) → bounded iterations (tier-capped)

The loop never fabricates success: if the repair budget is exhausted with
findings outstanding, the last answer is kept and the unresolved findings are
reported honestly to the caller.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog

from api.artifacts import extract_artifacts_from_text
from tools.iac_validator import detect_artifact_type, validate_artifact

logger = structlog.get_logger(__name__)

_IAC_TYPES = {"terraform", "cloudformation", "kubernetes"}


def _tier_repair_budget(tier: str, settings: Any) -> int:
    """Tier caps: Apex gets the configured maximum, Core two iterations,
    Lite none (speed contract). The global config is the ceiling for all."""
    ceiling = int(getattr(settings, "iac_max_repair_iterations", 3))
    t = (tier or "Free").strip().lower()
    if t in ("max", "apex", "developer", "admin"):
        return ceiling
    if t in ("pro", "core"):
        return min(2, ceiling)
    return 0


def _extract_iac_artifacts(answer: str) -> list[dict[str, Any]]:
    artifacts = []
    for art in extract_artifacts_from_text(answer or ""):
        content = art.get("content") or ""
        declared_type = str(art.get("artifact_type") or art.get("type") or "").lower()
        kind = declared_type if declared_type in _IAC_TYPES else detect_artifact_type(content)
        if kind in _IAC_TYPES and content.strip():
            artifacts.append({**art, "_kind": kind})
    return artifacts


def _repair_instruction(findings_lines: list[str], iteration: int, budget: int) -> str:
    lines = [
        "IAC VALIDATION FEEDBACK (iteration "
        f"{iteration}/{budget}) — your previous answer contained infrastructure-as-code "
        "artifacts that failed automated validation. Regenerate the COMPLETE corrected "
        "answer, fixing every finding below. Re-run nothing; just return the corrected "
        "answer in the same format:",
        "",
    ]
    lines.extend(f"- {line}" for line in findings_lines[:30])
    lines.extend([
        "",
        "Rules: keep the same <cloudgpt_artifact> tags and filenames; produce "
        "complete, deployable code with no TODOs; address every finding, not just the first.",
    ])
    return "\n".join(lines)


async def run_iac_repair_loop(
    query: str,
    prior_answer: str,
    base_messages: list[dict[str, Any]],
    generate_fn: Callable[[list[dict[str, Any]], str], Awaitable[str]],
    tier: str,
    settings: Any,
    emit_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Validate generated IaC artifacts and repair them within the tier budget.

    ``generate_fn(messages, thinking_level)`` must return the full corrected
    answer text. Returns a metadata dict; the caller replaces the stored answer
    when ``final_answer`` differs from ``prior_answer``.
    """
    meta: dict[str, Any] = {
        "repair_attempted": False,
        "iterations": 0,
        "valid": None,
        "final_answer": prior_answer,
        "artifact_count": 0,
    }
    if not bool(getattr(settings, "iac_validation_enabled", True)):
        return meta

    artifacts = _extract_iac_artifacts(prior_answer)
    meta["artifact_count"] = len(artifacts)
    if not artifacts:
        return meta

    budget = _tier_repair_budget(tier, settings)
    if budget <= 0:
        # Lite: deterministic validation only, no repair loop.
        first = artifacts[0]
        result = validate_artifact(
            first["content"], first["_kind"],
            timeout_seconds=float(getattr(settings, "iac_validator_timeout_seconds", 120)),
        )
        meta["valid"] = result.valid
        return meta

    if emit_event is not None:
        try:
            await emit_event({"iac_validation": {"status": "checking", "artifacts": len(artifacts)}})
        except Exception:
            pass

    current_answer = prior_answer
    last_findings: list[str] = []

    for iteration in range(1, budget + 1):
        # Full stack re-runs every iteration (regression guard)
        results = [
            validate_artifact(art["content"], art["_kind"],
                              timeout_seconds=float(getattr(settings, "iac_validator_timeout_seconds", 120)))
            for art in artifacts
        ]
        findings_lines: list[str] = []
        for art, res in zip(artifacts, results):
            for line in res.summary_lines():
                findings_lines.append(f"{art.get('filename', 'artifact')}: {line}")

        if all(r.valid for r in results):
            meta.update({
                "repair_attempted": iteration > 1,
                "iterations": iteration - 1,
                "valid": True,
                "final_answer": current_answer,
            })
            if emit_event is not None:
                try:
                    await emit_event({"iac_validation": {
                        "status": "passed", "iterations": iteration - 1, "artifacts": len(artifacts)}})
                except Exception:
                    pass
            return meta

        last_findings = findings_lines
        meta["repair_attempted"] = True
        if emit_event is not None:
            try:
                await emit_event({"iac_repair": {
                    "iteration": iteration, "budget": budget, "findings": len(findings_lines)}})
            except Exception:
                pass
        logger.info(
            "iac_repair.iteration", tier=tier, iteration=iteration,
            budget=budget, findings=len(findings_lines),
        )

        repair_messages = list(base_messages) + [
            {"role": "assistant", "content": current_answer},
            {"role": "user", "content": _repair_instruction(findings_lines, iteration, budget)},
        ]
        thinking_level = getattr(settings, "iac_repair_thinking_level", None)
        current_answer = await generate_fn(repair_messages, thinking_level)
        artifacts = _extract_iac_artifacts(current_answer)
        if not artifacts:
            # Repair dropped the artifacts entirely — treat as failure of the loop.
            break

    # Budget exhausted: final validation pass for honest reporting.
    results = [
        validate_artifact(art["content"], art["_kind"],
                          timeout_seconds=float(getattr(settings, "iac_validator_timeout_seconds", 120)))
        for art in artifacts
    ]
    valid = bool(results) and all(r.valid for r in results)
    meta.update({
        "iterations": budget,
        "valid": valid,
        "final_answer": current_answer if valid else prior_answer,
    })
    if not valid:
        meta["unresolved_findings"] = last_findings[:15]
    if emit_event is not None:
        try:
            await emit_event({"iac_validation": {
                "status": "passed" if valid else "failed",
                "iterations": budget, "artifacts": len(artifacts),
                "repaired": bool(valid and current_answer != prior_answer),
            }})
        except Exception:
            pass
    return meta
