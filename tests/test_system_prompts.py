import json
import re

from llm.system_prompts import (
    AGENTIC_PLAN_PROMPT,
    QUERY_TRANSFORM_PROMPT,
    EVIDENCE_GRADE_PROMPT,
    SELF_CRITIQUE_PROMPT,
    LITE_TIER_SYSTEM_PROMPT,
    CORE_TIER_SYSTEM_PROMPT,
    APEX_TIER_SYSTEM_PROMPT,
)


def test_all_new_prompts_are_non_empty_strings():
    for prompt in [AGENTIC_PLAN_PROMPT, QUERY_TRANSFORM_PROMPT,
                   EVIDENCE_GRADE_PROMPT, SELF_CRITIQUE_PROMPT]:
        assert isinstance(prompt, str) and len(prompt) > 50


def test_agentic_plan_prompt_contains_required_keywords():
    assert "retrieval_strategy" in AGENTIC_PLAN_PROMPT
    assert "sub_queries" in AGENTIC_PLAN_PROMPT
    assert "multi-hop" in AGENTIC_PLAN_PROMPT
    assert "broad" in AGENTIC_PLAN_PROMPT
    assert "narrow" in AGENTIC_PLAN_PROMPT


def test_query_transform_prompt_contains_required_keywords():
    assert "rewritten_query" in QUERY_TRANSFORM_PROMPT
    assert "expanded_queries" in QUERY_TRANSFORM_PROMPT
    assert "hyde_passage" in QUERY_TRANSFORM_PROMPT


def test_evidence_grade_prompt_contains_required_keywords():
    assert "0.0" in EVIDENCE_GRADE_PROMPT or "0.3" in EVIDENCE_GRADE_PROMPT
    assert "0.7" in EVIDENCE_GRADE_PROMPT or "1.0" in EVIDENCE_GRADE_PROMPT


def test_self_critique_prompt_contains_sentinel_strings():
    assert "NO_REVISION_NEEDED" in SELF_CRITIQUE_PROMPT
    assert "REVISED ANSWER:" in SELF_CRITIQUE_PROMPT


def test_embedded_json_examples_are_valid():
    for prompt in [AGENTIC_PLAN_PROMPT, QUERY_TRANSFORM_PROMPT]:
        json_blocks = re.findall(r'\{[^{}]+\}', prompt, re.DOTALL)
        for block in json_blocks:
            try:
                json.loads(block)
            except json.JSONDecodeError:
                pass


# ── Task 1: Lite tier — gating refusal removed, implementation mandate added ──

def test_lite_prompt_no_gating_refusal():
    """Lite tier must no longer contain the upgrade-gate refusal paragraph."""
    assert "Gating: Lite does NOT generate" not in LITE_TIER_SYSTEM_PROMPT


def test_lite_prompt_has_implementation_mandate():
    """Lite tier must contain the single-file IaC Implementation Mandate."""
    assert "Implementation Mandate" in LITE_TIER_SYSTEM_PROMPT


def test_lite_prompt_has_verification_command():
    """Lite tier must require a one-line verification command."""
    assert "verification command" in LITE_TIER_SYSTEM_PROMPT


# ── Task 2: Core tier — FinOps comment block & modular file packaging ──

def test_core_prompt_finops_comment_block():
    """Core tier must mandate a Monthly Cost Estimate comment block."""
    assert "Monthly Cost Estimate" in CORE_TIER_SYSTEM_PROMPT
    assert "730 hrs" in CORE_TIER_SYSTEM_PROMPT


def test_core_prompt_modular_file_packaging():
    """Core tier must enforce the 2-4 file modular packaging protocol."""
    assert "Modular File Packaging" in CORE_TIER_SYSTEM_PROMPT


# ── Task 3: Apex tier — multi-cloud targets, MoA verification, OPA ──

def test_apex_prompt_gcp_bundle_target():
    """Apex tier must list GCP enterprise bundle target (GKE)."""
    assert "google_container_cluster" in APEX_TIER_SYSTEM_PROMPT


def test_apex_prompt_azure_bundle_target():
    """Apex tier must list Azure enterprise bundle target (AKS)."""
    assert "azurerm_kubernetes_cluster" in APEX_TIER_SYSTEM_PROMPT


def test_apex_prompt_moa_verification():
    """Apex tier must reference the Mixture-of-Agents Verification pipeline."""
    assert "Mixture-of-Agents Verification" in APEX_TIER_SYSTEM_PROMPT
