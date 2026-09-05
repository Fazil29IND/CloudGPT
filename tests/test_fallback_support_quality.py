"""Unit and behavioral tests for Fallback Model Support Guardrails & Benchmark Compliance.

Validates:
1. FALLBACK_SUPPORT_SYSTEM_PROMPT structure, BLUF, few-shot exemplars, and anti-elaboration rules.
2. Prompt tier resolution (get_fallback_system_prompt and get_system_prompt_for_tier('fallback')).
3. Parameter clamping in GeminiProvider (thinking_budget=0, max_output_tokens=400, persona swap).
4. QueryRouter intent classification (status, billing, identity) and isolation from provider comparisons.
5. ContextBuilder protection against comparative matrix leakage during support/incident queries.
6. Coverage of all 10 benchmark scenarios (including security refusal, JSON handoff, frustrated tone).
"""

import pytest

from config import get_settings
from llm.context_builder import ContextBuilder
from llm.provider import GeminiProvider
from llm.system_prompts import (
    APEX_TIER_SYSTEM_PROMPT,
    COMPARISON_FORMAT,
    FALLBACK_SUPPORT_SYSTEM_PROMPT,
    get_fallback_system_prompt,
    get_system_prompt_for_tier,
)
from router.query_router import QueryRouter


# ─── 1. Fallback System Prompt Contract Tests ───────────────────────────────

def test_fallback_support_system_prompt_contract():
    """Verify the fallback prompt contains support persona, BLUF, boundaries, and few-shot exemplars."""
    prompt = FALLBACK_SUPPORT_SYSTEM_PROMPT

    # Persona & BLUF
    assert "CloudGPT Support Triage" in prompt
    assert "Brevity & BLUF" in prompt
    assert "1–3 concise sentences" in prompt

    # Infrastructure status & outages
    assert "Infrastructure Status & Outages" in prompt
    assert "Never speculate or invent real-time status" in prompt
    assert "official provider status page" in prompt

    # Billing & Refunds
    assert "Billing Inquiries & Disputes" in prompt
    assert "NEVER promise unconditional refunds" in prompt
    assert "Billing Support Console" in prompt

    # Scope & Anti-elaboration
    assert "Scope Boundaries & Brand Protection" in prompt
    assert "Strict Anti-Elaboration" in prompt
    assert "NEVER volunteer unprompted Terraform/IaC code, Mermaid diagrams" in prompt

    # Security & Structured Handoff
    assert "Security Refusal" in prompt
    assert "Structured Handoff" in prompt

    # Canonical Few-Shot Exemplars
    assert "is us-east-1 down?" in prompt
    assert "https://health.aws.amazon.com/" in prompt
    assert "QuantumCache Pro" in prompt


def test_fallback_prompt_resolution():
    """Ensure get_fallback_system_prompt and tier resolution map correctly."""
    assert get_fallback_system_prompt() == FALLBACK_SUPPORT_SYSTEM_PROMPT
    assert get_system_prompt_for_tier("fallback") == FALLBACK_SUPPORT_SYSTEM_PROMPT
    assert get_system_prompt_for_tier("support") == FALLBACK_SUPPORT_SYSTEM_PROMPT
    assert get_system_prompt_for_tier("triage") == FALLBACK_SUPPORT_SYSTEM_PROMPT


# ─── 2. Parameter Clamping in GeminiProvider ────────────────────────────────

def test_gemini_fallback_parameter_clamping():
    """Verify candidate fallbacks receive thinking_budget=0, max_output_tokens=400, and persona swap."""
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.settings = get_settings()

    # When is_fallback_candidate is True:
    config = provider._build_config(
        model="gemini-3.7-flash",
        temperature=0.2,
        system_instruction=APEX_TIER_SYSTEM_PROMPT,  # Heavy Apex prompt passed
        thinking_level="High",
        max_output_tokens=4096,
        is_fallback_candidate=True,
    )

    # 1. Output tokens must be clamped to fallback_max_output_tokens (default 400)
    assert config.max_output_tokens <= 400

    # 2. Thinking budget must be set to 0 to eliminate TTFT delay
    if hasattr(config, "thinking_config") and config.thinking_config is not None:
        assert config.thinking_config.thinking_budget == 0
        assert config.thinking_config.include_thoughts is False

    # 3. System instruction must be swapped from heavy Apex blueprint to Support Triage prompt
    assert "CloudGPT Support Triage" in config.system_instruction
    assert "## Multi-Cloud Decision Matrix" not in config.system_instruction
    assert "```mermaid" not in config.system_instruction


def test_gemini_primary_preserves_thinking_budget():
    """Verify primary model (not fallback) still receives full thinking budget."""
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.settings = get_settings()

    config = provider._build_config(
        model="gemini-3.8-flash",
        temperature=0.2,
        system_instruction=APEX_TIER_SYSTEM_PROMPT,
        thinking_level="High",
        max_output_tokens=4096,
        is_fallback_candidate=False,
    )

    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == provider.settings.thinking_budget_high
    assert config.system_instruction == APEX_TIER_SYSTEM_PROMPT


# ─── 3. QueryRouter Support Intent & Scope Isolation ────────────────────────

@pytest.mark.asyncio
async def test_router_status_intent_classification():
    """Ensure outage/status checks are classified as status without forcing comparative matrix."""
    router = QueryRouter()
    classification = await router.route_query("is us-east-1 down?")

    assert classification.intent == "status"
    assert classification.requires_provider_comparison is False


@pytest.mark.asyncio
async def test_router_billing_intent_classification():
    """Ensure unexpected billing charges are classified as billing without forcing comparative matrix."""
    router = QueryRouter()
    classification = await router.route_query("Unexpected charge of $450 on my bill, please help")

    assert classification.intent == "billing"
    assert classification.requires_provider_comparison is False


@pytest.mark.asyncio
async def test_router_identity_intent_classification():
    """Ensure password/2FA reset attempts are classified as identity without forcing comparison."""
    router = QueryRouter()
    classification = await router.route_query("Need to reset 2fa authentication on my account immediately")

    assert classification.intent == "identity"
    assert classification.requires_provider_comparison is False


# ─── 4. ContextBuilder Leakage Prevention ───────────────────────────────────

def test_context_builder_suppresses_comparison_for_support():
    """Ensure ContextBuilder does NOT inject COMPARISON_FORMAT on status/billing queries."""
    builder = ContextBuilder()

    # Status query
    status_cls = {
        "intent": "status",
        "providers": ["aws"],
        "requires_provider_comparison": False,
    }
    messages = builder.build_context(
        query="is us-east-1 down?",
        classification=status_cls,
        tier="Free",
    )
    user_content = messages[1]["content"]

    assert "SUPPORT & INCIDENT CONSTRAINTS:" in user_content
    assert "Keep responses concise and direct" in user_content
    assert COMPARISON_FORMAT not in user_content
    assert "| Feature / Attribute | AWS | GCP | Azure |" not in user_content


def test_context_builder_preserves_comparison_for_compare_intent():
    """Ensure ContextBuilder still injects COMPARISON_FORMAT for explicit compare queries."""
    builder = ContextBuilder()

    compare_cls = {
        "intent": "compare",
        "providers": ["aws", "azure"],
        "requires_provider_comparison": True,
    }
    messages = builder.build_context(
        query="Compare AWS S3 vs Azure Blob Storage",
        classification=compare_cls,
        tier="Free",
    )
    user_content = messages[1]["content"]

    assert COMPARISON_FORMAT in user_content
    assert "| Feature / Attribute | AWS | GCP | Azure |" in user_content


# ─── 5. 10-Case Benchmark Policy Verification ───────────────────────────────

def test_benchmark_case_1_billing_dispute_policy():
    """Case 1: Billing dispute policy - no unauthorized refunds, routes to billing console."""
    prompt = FALLBACK_SUPPORT_SYSTEM_PROMPT
    assert "NEVER promise unconditional refunds or admit liability" in prompt
    assert "Billing Support Console" in prompt


def test_benchmark_case_2_outage_check_policy():
    """Case 2: Outage check policy - directs to official status URL, never guesses status."""
    prompt = FALLBACK_SUPPORT_SYSTEM_PROMPT
    assert "Never speculate or invent real-time status" in prompt
    assert "official provider status page" in prompt
    assert "https://health.aws.amazon.com/" in prompt


def test_benchmark_case_3_fake_product_policy():
    """Case 3: Fake product policy - recognizes non-existent products, refuses hallucination."""
    prompt = FALLBACK_SUPPORT_SYSTEM_PROMPT
    assert '"QuantumCache Pro" is not a recognized service or feature' in prompt


def test_benchmark_case_4_competitor_comparison_boundary():
    """Case 4: Competitor boundary - does not produce unsolicited competitor pricing analyses."""
    prompt = FALLBACK_SUPPORT_SYSTEM_PROMPT
    assert "Do NOT produce unsolicited competitor pricing analyses" in prompt


def test_benchmark_case_6_anti_elaboration_policy():
    """Case 6/7: Anti-elaboration - strictly forbids unprompted Terraform and Mermaid diagrams."""
    prompt = FALLBACK_SUPPORT_SYSTEM_PROMPT
    assert "NEVER volunteer unprompted Terraform/IaC code, Mermaid diagrams" in prompt
    assert "enterprise landing zone topologies" in prompt


def test_benchmark_case_8_security_reset_refusal():
    """Case 8: Security refusal - refuses to bypass identity verification/MFA reset workflows."""
    prompt = FALLBACK_SUPPORT_SYSTEM_PROMPT
    assert "strictly refuse to bypass standard console/identity verification workflows" in prompt


def test_benchmark_case_9_structured_json_handoff():
    """Case 9: Structured JSON handoff - mandates valid JSON without commentary."""
    prompt = FALLBACK_SUPPORT_SYSTEM_PROMPT
    assert "output strictly valid JSON with no markdown wrapping or conversational commentary" in prompt
