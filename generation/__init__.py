"""Generation & Validation Layer for CloudGPT's 3-tier RAG pipelines.

Layer 3 (evidence compression + context assembly) and Layer 4 (claim-level
grounded generation validation) shared across tiers with per-tier policies:

- Hybrid RAG (Lite/Free):  conditional compression, fixed evidence-aware
  assembly, deterministic claim support + multi-dimensional validation
  (zero added LLM latency).
- Agentic RAG (Core/Pro):  task-aware selective compression, plan-aware
  assembly, claim verification + bounded agent retry.
- Adaptive Agentic RAG (Apex/Max): strategy-adaptive compression policy,
  dynamic assembly, dynamic policy prompt, adaptive verification with
  retry/abstention policy.
"""

from generation.assembly import (
    assemble_dynamic_evidence,
    assemble_plan_aware_evidence,
    order_fixed_evidence,
)
from generation.claims import (
    ClaimAssessment,
    ClaimSupport,
    assess_claim_support,
    content_tokens,
    extract_claims,
    score_claim_support,
)
from generation.compression import compress_evidence
from generation.policy import build_policy_digest, pre_generation_decision
from generation.validator import (
    OutputValidationReport,
    ValidationPolicy,
    apply_lite_validation,
    build_abstention_answer,
    build_retry_messages,
    deterministic_cleanup,
    validate_output,
)

__all__ = [
    "assemble_dynamic_evidence",
    "assemble_plan_aware_evidence",
    "order_fixed_evidence",
    "ClaimAssessment",
    "ClaimSupport",
    "assess_claim_support",
    "content_tokens",
    "extract_claims",
    "score_claim_support",
    "compress_evidence",
    "build_policy_digest",
    "pre_generation_decision",
    "OutputValidationReport",
    "ValidationPolicy",
    "apply_lite_validation",
    "build_abstention_answer",
    "build_retry_messages",
    "deterministic_cleanup",
    "validate_output",
]
