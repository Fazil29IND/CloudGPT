"""Multi-dimensional output validation, claim verification, and retry/abstention policies.

Layer 4 of the tiered RAG stack. Every tier validates generated answers along
five dimensions — grounding, citations, completeness, format, safety — with
tier-specific policies:

- Lite: deterministic only (lexical claim entailment), plus minimal
  deterministic cleanup; never adds LLM latency.
- Core: deterministic validation + optional evaluator-model claim check on
  ambiguous grounding, followed by a bounded agent retry with feedback.
- Apex: same machinery with a wider retry budget, abstention policy, and
  adaptive re-checking after every retry.

All LLM access is lazy-imported, timeout-bounded, and exception-safe so tests
and keyless environments degrade to deterministic-only validation.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from generation.claims import assess_claim_support
from llm.system_prompts import CLAIM_VERIFICATION_PROMPT, GENERATION_RETRY_PROMPT
from metrics import (
    CLAIM_SUPPORT_SCORE,
    OUTPUT_VALIDATION_TOTAL,
)

logger = structlog.get_logger(__name__)

INJECTION_MARKERS: list[str] = [
    "ignore previous instructions",
    "disregard your system prompt",
    "you are now",
    "act as a",
    "jailbreak",
    "<|endoftext|>",
]

_LEADING_FILLER_RE = re.compile(
    r"^\s*(sure|certainly|of course|great question|good question|hmm[,!]?|absolutely|okay|ok)[,!.]?\s*",
    re.IGNORECASE,
)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


@dataclass
class ValidationPolicy:
    """Tier-calibrated validation policy."""

    tier: str = "Free"
    min_claim_support: float = 0.5
    max_retries: int = 0
    enable_llm_claim_check: bool = False
    enable_retry: bool = False
    enable_abstention: bool = False
    abstain_min_support: float = 0.3
    min_assessable_claims: int = 3
    format_profile: str = "lite"
    substantive_min_chars: int = 200


@dataclass
class DimensionResult:
    name: str
    passed: bool
    score: float
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "score": round(self.score, 3), "details": self.details}


@dataclass
class OutputValidationReport:
    is_valid: bool = True
    dimensions: dict[str, DimensionResult] = field(default_factory=dict)
    claim_support: float = 1.0
    assessable_claims: int = 0
    unsupported_claims: list[str] = field(default_factory=list)
    claim_sources: list[dict[str, Any]] = field(default_factory=list)
    retryable: bool = False
    abstain_recommended: bool = False
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "claim_support": self.claim_support,
            "assessable_claims": self.assessable_claims,
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "unsupported_claims": self.unsupported_claims,
            "claim_sources": self.claim_sources,
            "retryable": self.retryable,
            "abstain_recommended": self.abstain_recommended,
            "issues": self.issues,
        }


def policies_from_settings(tier: str, settings: Any) -> ValidationPolicy:
    """Build the tier-calibrated policy from config Settings."""
    t = (tier or "Free").strip().lower()
    if t in ("max", "apex", "developer", "admin"):
        return ValidationPolicy(
            tier="Max",
            min_claim_support=float(getattr(settings, "adaptive_min_claim_support", 0.6)),
            max_retries=int(getattr(settings, "adaptive_generation_max_retries", 2)),
            enable_llm_claim_check=True,
            enable_retry=True,
            enable_abstention=True,
            abstain_min_support=float(getattr(settings, "adaptive_abstain_min_support", 0.3)),
            min_assessable_claims=int(getattr(settings, "validation_min_assessable_claims", 3)),
            format_profile="apex",
        )
    if t in ("pro", "core"):
        return ValidationPolicy(
            tier="Pro",
            min_claim_support=float(getattr(settings, "core_min_claim_support", 0.55)),
            max_retries=int(getattr(settings, "core_generation_max_retries", 1)),
            enable_llm_claim_check=bool(getattr(settings, "enable_core_claim_verification", True)),
            enable_retry=True,
            enable_abstention=False,
            abstain_min_support=0.0,
            min_assessable_claims=int(getattr(settings, "validation_min_assessable_claims", 3)),
            format_profile="core",
        )
    return ValidationPolicy(
        tier="Free",
        min_claim_support=float(getattr(settings, "lite_min_claim_support", 0.5)),
        max_retries=0,
        enable_llm_claim_check=False,
        enable_retry=False,
        enable_abstention=False,
        abstain_min_support=0.0,
        min_assessable_claims=int(getattr(settings, "validation_min_assessable_claims", 3)),
        format_profile="lite",
    )


def deterministic_cleanup(answer: str) -> str:
    """Minimal deterministic cleanup: strip conversational fillers and excess blank lines."""
    if not answer:
        return answer
    cleaned = _LEADING_FILLER_RE.sub("", answer.strip(), count=1)
    cleaned = _MULTI_BLANK_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def _query_coverage(query: str, answer: str) -> float:
    from generation.claims import content_tokens

    q_toks = {t for t in content_tokens(query)}
    if len(q_toks) < 2:
        return 1.0
    a_toks = set(content_tokens(answer))
    return sum(1 for t in q_toks if t in a_toks) / len(q_toks)


def _format_check(answer: str, profile: str) -> DimensionResult:
    lowered = answer.lower()
    if len(answer) < 300:
        return DimensionResult("format", True, 1.0, "short answer — structure check skipped")
    missing: list[str] = []
    if "##" not in answer:
        missing.append("no markdown sections")
    if profile == "lite":
        if "direct solution" not in lowered and "verification" not in lowered:
            missing.append("Lite BLUF sections (Direct Solution / Verification)")
    elif profile == "core":
        if "executive summary" not in lowered and "remediation" not in lowered:
            missing.append("Core sections (Executive Summary / Remediation)")
    return DimensionResult(
        "format",
        not missing,
        1.0 if not missing else 0.5,
        "; ".join(missing) if missing else "tier structure present",
    )


def check_prose_code_alignment(answer: str) -> DimensionResult:
    """Validate that capabilities described in prose are either backed by code artifacts or explicitly marked as target-state roadmap."""
    answer_lower = (answer or "").lower()
    issues: list[str] = []

    # 1. Service mesh claims (Istio, Linkerd, SPIFFE/SPIRE)
    if any(mesh_kw in answer_lower for mesh_kw in ("service mesh", "istio", "spiffe", "spire")):
        has_mesh_code = bool(re.search(r'\b(?:apiVersion:\s*install\.istio\.io|kind:\s*EnvoyFilter|apiVersion:\s*networking\.istio\.io)\b', answer))
        has_roadmap = any(kw in answer_lower for kw in ("target-state", "roadmap", "phased delivery", "future phase", "not delivered"))
        if not has_mesh_code and not has_roadmap:
            issues.append("prose claims service mesh capability without delivering mesh manifests or qualifying under Target-State Roadmap")

    # 2. Multi-region active-active claims
    if any(mr_kw in answer_lower for mr_kw in ("active-active multi-region", "multi-region disaster recovery")):
        has_multi_region_code = bool(re.search(r'aws_route53_record|google_compute_global_forwarding_rule|azurerm_traffic_manager', answer))
        has_roadmap = any(kw in answer_lower for kw in ("target-state", "roadmap", "phased delivery", "future phase", "not delivered", "single-region"))
        if not has_multi_region_code and not has_roadmap:
            issues.append("prose claims active-active multi-region without secondary-region resources or Target-State Roadmap qualification")

    # 3. HorizontalPodAutoscaler claims
    if any(hpa_kw in answer_lower for hpa_kw in ("horizontalpodautoscaler", "horizontal pod autoscal")):
        has_hpa_manifest = "kind: HorizontalPodAutoscaler" in answer or "kind: horizontalpodautoscaler" in answer_lower
        has_roadmap = any(kw in answer_lower for kw in ("target-state", "roadmap", "phased", "not delivered"))
        if not has_hpa_manifest and not has_roadmap:
            issues.append("prose claims HPA autoscaling without delivering k8s/hpa.yaml manifest")

    # 4. PCI-DSS compliance tags without controls
    if 'compliance = "pci-dss-v4"' in answer_lower or 'compliance = "pci-dss"' in answer_lower:
        has_kms = "enable_key_rotation" in answer
        has_private = "endpoint_private_access" in answer or "private_endpoint" in answer_lower
        if not (has_kms and has_private):
            issues.append("contains 'Compliance = PCI-DSS-v4' tag without full audit baseline (KMS CMK rotation and private endpoints)")

    passed = len(issues) == 0
    score = 1.0 if passed else 0.5
    detail = "; ".join(issues) if issues else "prose-to-code alignment verified"
    return DimensionResult("alignment", passed, score, detail)


def validate_output(
    answer: str,
    query: str,
    chunks: list[Any],
    citation_mgr: Any | None,
    policy: ValidationPolicy,
    chunk_to_source: Any | None = None,
) -> OutputValidationReport:
    """Run the multi-dimensional validation over a generated answer."""
    report = OutputValidationReport()

    # 1. Grounding — deterministic claim-level entailment.
    assessment = assess_claim_support(answer, chunks, chunk_to_source=chunk_to_source)
    report.claim_support = assessment.support_ratio
    report.assessable_claims = assessment.assessable_count
    report.unsupported_claims = [c.text for c in assessment.unsupported[:5]]
    report.claim_sources = [
        c.to_dict() for c in assessment.claims if c.best_source_number is not None
    ][:20]

    judged = assessment.assessable_count >= policy.min_assessable_claims
    grounding_passed = (assessment.support_ratio >= policy.min_claim_support) or not judged
    grounding_detail = (
        f"support {assessment.support_ratio:.2f} vs floor {policy.min_claim_support:.2f} "
        f"({assessment.assessable_count} claims{'' if judged else ', below judgment floor'})"
    )
    report.dimensions["grounding"] = DimensionResult(
        "grounding", grounding_passed, assessment.support_ratio, grounding_detail
    )

    # 2. Citations — invalid reference numbers are hard failures.
    citation_details = "no citation manager"
    citations_passed = True
    if citation_mgr is not None:
        try:
            vr = citation_mgr.validate_answer_citations(answer)
            citations_passed = vr.is_valid
            citation_details = (
                f"invalid_refs={vr.invalid_references}" if vr.invalid_references
                else ("missing_citations" if vr.missing_citations else "citations ok")
            )
        except Exception as e:  # pragma: no cover - defensive
            citation_details = f"validator error: {e}"
    report.dimensions["citations"] = DimensionResult(
        "citations", citations_passed, 1.0 if citations_passed else 0.0, citation_details
    )

    # 3. Completeness — query-term coverage (informational, never retry-alone).
    coverage = _query_coverage(query, answer)
    report.dimensions["completeness"] = DimensionResult(
        "completeness",
        coverage >= 0.25,
        coverage,
        f"query-term coverage {coverage:.2f}",
    )

    # 4. Format — tier structure, lenient for short answers.
    report.dimensions["format"] = _format_check(answer, policy.format_profile)

    # 5. Safety — prompt-injection echoes and obvious leaks.
    answer_lower = (answer or "").lower()
    safety_hits = [m for m in INJECTION_MARKERS if m in answer_lower]
    report.dimensions["safety"] = DimensionResult(
        "safety", not safety_hits, 1.0 if not safety_hits else 0.0,
        "injection markers echoed" if safety_hits else "clean",
    )

    # 6. Alignment — Prose-to-Code Alignment Guardrail (Item 12)
    report.dimensions["alignment"] = check_prose_code_alignment(answer)

    report.is_valid = all(d.passed for d in report.dimensions.values())
    for name, dim in report.dimensions.items():
        if not dim.passed:
            report.issues.append(f"{name}: {dim.details}")
        try:
            OUTPUT_VALIDATION_TOTAL.labels(
                tier=policy.tier, dimension=name, outcome="pass" if dim.passed else "fail"
            ).inc()
        except Exception:  # pragma: no cover
            pass

    substantive = len(answer or "") >= policy.substantive_min_chars
    has_evidence = bool(chunks)
    blocking_failed = (
        not report.dimensions["grounding"].passed
        or not report.dimensions["citations"].passed
        or not report.dimensions["safety"].passed
    )
    report.retryable = bool(
        policy.enable_retry
        and has_evidence
        and substantive
        and judged
        and blocking_failed
        and policy.max_retries > 0
    )
    report.abstain_recommended = bool(
        policy.enable_abstention
        and has_evidence
        and substantive
        and judged
        and assessment.support_ratio < policy.abstain_min_support
    )

    try:
        CLAIM_SUPPORT_SCORE.labels(tier=policy.tier).observe(max(0.0, min(1.0, assessment.support_ratio)))
    except Exception:  # pragma: no cover
        pass

    logger.info(
        "output_validation.complete",
        tier=policy.tier,
        is_valid=report.is_valid,
        claim_support=assessment.support_ratio,
        assessable=assessment.assessable_count,
        retryable=report.retryable,
        abstain=report.abstain_recommended,
        issues=report.issues,
    )
    return report


def append_caveat(answer: str, report: OutputValidationReport) -> str:
    """Append a deterministic grounding caveat (Lite path — no retry budget)."""
    if report.dimensions.get("grounding", DimensionResult("grounding", True, 1.0)).passed:
        return answer
    unsupported = report.unsupported_claims[:2]
    if not unsupported:
        return answer
    lines = [answer.rstrip(), "", "> ⚠️ **Grounding notice:** the following statements could not be fully verified against the retrieved documentation and should be validated against official provider docs:"]
    for claim in unsupported:
        lines.append(f"> - {claim[:220]}")
    return "\n".join(lines)


def build_retry_messages(
    query: str,
    prior_answer: str,
    report: OutputValidationReport,
    evidence_chunks: list[Any],
) -> list[dict[str, str]]:
    """Build the feedback-grounded regeneration messages for a validation retry."""
    findings: list[str] = []
    if not report.dimensions["grounding"].passed:
        findings.append("UNSUPPORTED CLAIMS (must be grounded, caveated, or removed):")
        for claim in report.unsupported_claims[:5]:
            findings.append(f"  - {claim[:240]}")
    if not report.dimensions["citations"].passed:
        findings.append(f"CITATION DEFECTS: {report.dimensions['citations'].details}")
    if not report.dimensions["safety"].passed:
        findings.append(f"SAFETY DEFECTS: {report.dimensions['safety'].details}")
    if "alignment" in report.dimensions and not report.dimensions["alignment"].passed:
        findings.append(f"PROSE-TO-CODE ALIGNMENT DEFECTS: {report.dimensions['alignment'].details}")
    if not findings:
        findings.append("General grounding improvement required.")

    evidence_preview = []
    for i, chunk in enumerate(evidence_chunks[:6]):
        text = getattr(chunk, "text", None) or (chunk.get("content", "") if isinstance(chunk, dict) else "")
        evidence_preview.append(f"[{i}] {str(text)[:500]}")

    user_content = (
        f"ORIGINAL QUERY: {query}\n\n"
        f"PREVIOUS ANSWER:\n{prior_answer[:4000]}\n\n"
        f"VALIDATION FINDINGS:\n" + "\n".join(findings) + "\n\n"
        "SUPPORTING EVIDENCE (the only permitted factual source):\n" + "\n".join(evidence_preview)
    )
    return [
        {"role": "system", "content": GENERATION_RETRY_PROMPT},
        {"role": "user", "content": user_content},
    ]


def apply_lite_validation(
    result: Any,
    query: str,
    rag_results: list[dict[str, Any]],
    citation_mgr: Any | None,
    settings: Any,
    tier: str = "Free",
) -> None:
    """Lite-tier validated grounded generation (deterministic, zero added LLM).

    Applies, in order: minimal deterministic cleanup → claim-level evidence
    entailment/support check → claim-level source-to-chunk citation mapping →
    multi-dimensional output validation. Results ride the PipelineResult's
    ``context_validator_flags`` and ``validation`` fields; a deterministic
    grounding caveat is appended only when real evidence exists and support
    falls below the configured floor. Never raises.
    """
    try:
        answer = getattr(result, "answer", "") or ""
        if not answer:
            return

        result.answer = deterministic_cleanup(answer)
        answer = result.answer

        # Validation strictness follows the request's actual tier, not a
        # hardcoded Free policy — paying tiers must not get Free-tier checks.
        policy = policies_from_settings(tier, settings)
        if not bool(getattr(settings, "enable_lite_output_validation", True)):
            return

        chunk_to_source = None
        if citation_mgr is not None:
            chunk_to_source = citation_mgr.source_number_for_chunk

        report = validate_output(answer, query, rag_results, citation_mgr, policy, chunk_to_source)

        failed = [name for name, dim in report.dimensions.items() if not dim.passed]
        for name in failed:
            if name not in result.context_validator_flags:
                result.context_validator_flags.append(f"validation:{name}")

        result.validation = report.to_dict()

        # Deterministic mitigation instead of a retry budget: annotate only
        # when actual evidence existed and grounding genuinely failed.
        if rag_results and report.dimensions["grounding"].score < policy.min_claim_support:
            if len(answer) >= policy.substantive_min_chars:
                result.answer = append_caveat(result.answer, report)
    except Exception as e:  # pragma: no cover - validation must never break serving
        logger.warning("lite_output_validation.failed", error=str(e))


def build_abstention_answer(query: str, report: OutputValidationReport) -> str:
    """Honest knowledge-boundary answer when validated grounding cannot be achieved."""
    return "\n".join(
        [
            "## Verified Answer Unavailable",
            "",
            "I could not verify a sufficiently grounded answer to this question against the retrieved "
            "documentation. Rather than risk stating unverified specifics, here is what I can confirm:",
            "",
            "- The retrieved evidence did not support the specific claims required to answer this "
            "question with confidence (claim-evidence support score "
            f"{report.claim_support:.2f}, floor not met).",
            "- For authoritative details, consult the official provider documentation for the relevant "
            "service, or re-run this query with a provider filter enabled so retrieval can target the "
            "correct documentation set.",
            "",
            "## References",
            "- Retrieved via CloudGPT evidence loop — no sufficiently supported source for this specific question.",
        ]
    )


async def llm_claim_check(
    query: str,
    unsupported_claims: list[str],
    chunks: list[Any],
    tier: str,
    settings: Any,
) -> dict[int, float] | None:
    """Batched evaluator-model entailment check over the unsupported claims.

    Returns {claim_index: score} when the evaluator responds with a valid JSON
    array; None on any failure (deterministic scores remain authoritative).
    """
    if not unsupported_claims or not chunks:
        return None
    try:
        from llm.provider import get_evaluator_provider

        claims_block = "\n".join(f"[{i}]: {c[:300]}" for i, c in enumerate(unsupported_claims))
        evidence_block = "\n".join(
            f"[E{i}] {(getattr(c, 'text', None) or (c.get('content', '') if isinstance(c, dict) else ''))[:600]}"
            for i, c in enumerate(chunks[:6])
        )
        messages = [
            {"role": "system", "content": CLAIM_VERIFICATION_PROMPT},
            {
                "role": "user",
                "content": f"CONTEXT QUERY: {query}\n\nCLAIMS:\n{claims_block}\n\nEVIDENCE:\n{evidence_block}",
            },
        ]
        evaluator = get_evaluator_provider(tier)
        timeout = float(getattr(settings, "claim_verification_timeout_seconds", 8.0))
        raw = await asyncio.wait_for(
            evaluator.generate(messages=messages, stream=False, temperature=0.0, thinking_level="Low"),
            timeout=timeout,
        )
        parsed = json.loads(raw)
        if isinstance(parsed, list) and len(parsed) == len(unsupported_claims):
            scores = {}
            for idx, s in enumerate(parsed):
                try:
                    scores[idx] = max(0.0, min(1.0, float(s)))
                except (TypeError, ValueError):
                    scores[idx] = 0.0
            return scores
        logger.warning("claim_check.invalid_shape", tier=tier)
        return None
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning("claim_check.timeout", tier=tier)
        return None
    except Exception as e:
        logger.warning("claim_check.failed", tier=tier, error=str(e))
        return None


async def resolve_claim_verification(
    answer: str,
    query: str,
    chunks: list[Any],
    citation_mgr: Any | None,
    policy: ValidationPolicy,
    settings: Any,
    chunk_to_source: Any | None = None,
) -> OutputValidationReport:
    """Validate, and when grounding is ambiguous, re-check claims with the evaluator model.

    The LLM check can only *upgrade* verdicts for claims the deterministic pass
    marked unsupported; it never downgrades supported claims.
    """
    report = validate_output(answer, query, chunks, citation_mgr, policy, chunk_to_source)
    if (
        policy.enable_llm_claim_check
        and not report.dimensions["grounding"].passed
        and report.assessable_claims >= policy.min_assessable_claims
        and report.unsupported_claims
    ):
        scores = await llm_claim_check(query, report.unsupported_claims, chunks, policy.tier, settings)
        if scores:
            rescued = sum(1 for s in scores.values() if s >= 0.7)
            total = report.assessable_claims
            # Deterministic pass counted every claim; rescued claims move from
            # ~0 support to the evaluator-confirmed 0.7.
            adjusted = (report.claim_support * total + 0.7 * rescued) / total if total else 1.0
            report.claim_support = round(min(1.0, adjusted), 3)
            passed = report.claim_support >= policy.min_claim_support
            report.dimensions["grounding"] = DimensionResult(
                "grounding",
                passed,
                report.claim_support,
                f"after LLM claim check: {rescued}/{len(scores)} unsupported claims verified by evaluator",
            )
            report.is_valid = all(d.passed for d in report.dimensions.values())
            report.retryable = report.retryable and not passed
            report.abstain_recommended = report.abstain_recommended and not passed
            logger.info(
                "output_validation.llm_claim_check",
                tier=policy.tier,
                rescued=rescued,
                checked=len(scores),
                new_support=report.claim_support,
            )
    return report
