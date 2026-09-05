"""Unit tests for Cognitive Reasoning & Quality Safeguards (Anti-Degradation Framework).

Verifies the 5 benchmark failure mode resolutions:
1. Multi-Part Decomposition & Isolated Fallback (Task 2: avoids blanket UNKNOWN).
2. Global Premise & Cross-Section Consistency (Task 7: prevents local vs global contradiction).
3. Anti-Self-Grading Bias & Calibrated Honesty (Task 10: prevents false 10/10 or unearned 0-violation claims).
4. Dual-Axis Quality (Task 1: prevents countable metrics from crowding out semantic coherence).
5. Relational Substance in Structured Outputs (Task 8: prevents tables with valid schemas but meaningless rows).
"""


from llm.context_builder import ContextBuilder
from llm.system_prompts import (
    APEX_TIER_SYSTEM_PROMPT,
    CLOUD_AGENT_SYSTEM_PROMPT,
    COGNITIVE_REASONING_SAFEGUARDS,
    CORE_TIER_SYSTEM_PROMPT,
    LITE_TIER_SYSTEM_PROMPT,
    SELF_CRITIQUE_PROMPT,
)
from llm.thinking import THINKING_REASONING_FRAMEWORK


def test_cognitive_reasoning_safeguards_constant_defined():
    """Verify COGNITIVE_REASONING_SAFEGUARDS covers the four core principles."""
    assert isinstance(COGNITIVE_REASONING_SAFEGUARDS, str)
    assert len(COGNITIVE_REASONING_SAFEGUARDS) > 200
    assert "Multi-Part Decomposition & Isolated Fallback" in COGNITIVE_REASONING_SAFEGUARDS
    assert "Global Premise & Cross-Section Consistency" in COGNITIVE_REASONING_SAFEGUARDS
    assert "Calibrated Honesty & Anti-Self-Grading Bias" in COGNITIVE_REASONING_SAFEGUARDS
    assert "Dual-Axis Quality" in COGNITIVE_REASONING_SAFEGUARDS


def test_system_prompts_contain_cognitive_safeguards():
    """Ensure all tier prompts incorporate the cognitive safeguards."""
    # Base fallback agent prompt
    assert "Multi-Part Decomposition & Isolated Fallback" in CLOUD_AGENT_SYSTEM_PROMPT
    assert "Global Premise Consistency" in CLOUD_AGENT_SYSTEM_PROMPT
    assert "Anti-Self-Grading Bias & Calibration" in CLOUD_AGENT_SYSTEM_PROMPT
    assert "Dual-Axis Quality" in CLOUD_AGENT_SYSTEM_PROMPT

    # Lite Tier
    assert "Multi-Part Decomposition & Global Consistency" in LITE_TIER_SYSTEM_PROMPT
    assert 'never emit a blanket "UNKNOWN"' in LITE_TIER_SYSTEM_PROMPT

    # Core Tier
    assert "Cognitive Consistency & Anti-Self-Grading Bias" in CORE_TIER_SYSTEM_PROMPT
    assert "Cross-validate every remediation step against global architecture premises" in CORE_TIER_SYSTEM_PROMPT

    # Apex Tier
    assert "Dual-Axis Relational Quality & Holistic Consistency" in APEX_TIER_SYSTEM_PROMPT
    assert "zero premise contradictions" in APEX_TIER_SYSTEM_PROMPT


def test_self_critique_prompt_evaluation_checklist():
    """Verify SELF_CRITIQUE_PROMPT includes the anti-degradation audit items."""
    assert "NO_REVISION_NEEDED" in SELF_CRITIQUE_PROMPT
    assert "REVISED ANSWER:" in SELF_CRITIQUE_PROMPT
    assert "Multi-part completeness" in SELF_CRITIQUE_PROMPT
    assert "Global & cross-bullet consistency" in SELF_CRITIQUE_PROMPT
    assert "Relational substance vs. empty metrics" in SELF_CRITIQUE_PROMPT
    assert "Calibrated honesty" in SELF_CRITIQUE_PROMPT


def test_context_builder_injects_cognitive_guardrails():
    """Verify ContextBuilder injects all cognitive load reduction and anti-degradation rules."""
    builder = ContextBuilder()
    query = "Explain AWS S3 vs Azure Blob and calculate Glacier costs"
    classification = {
        "intent": "compare",
        "providers": ["aws", "azure"],
        "requires_provider_comparison": True,
    }

    for tier in ("Free", "Pro", "Max"):
        messages = builder.build_context(
            query=query,
            classification=classification,
            tier=tier,
        )
        user_content = messages[1]["content"]

        # Cognitive guardrail checks
        assert "- Multi-Part Decomposition:" in user_content
        assert "- Global Consistency:" in user_content
        assert "- Substance & Relational Quality:" in user_content
        assert "- Calibrated Honesty:" in user_content

        # Recency reinforcement preserved within the last 100 characters
        assert f"CURRENT USER REQUEST: {query}" in user_content[-100:]


def test_thinking_reasoning_framework_phases():
    """Verify the 5-phase thinking engine rubric is documented and exported."""
    assert isinstance(THINKING_REASONING_FRAMEWORK, str)
    assert "Phase 1 - Multi-Part Query Decomposition" in THINKING_REASONING_FRAMEWORK
    assert "Phase 2 - Substantive Formulation (Dual-Axis Quality)" in THINKING_REASONING_FRAMEWORK
    assert "Phase 3 - Structural Alignment" in THINKING_REASONING_FRAMEWORK
    assert "Phase 4 - Global Premise & Cross-Section Consistency Check" in THINKING_REASONING_FRAMEWORK
    assert "Phase 5 - Adversarial Self-Audit (Anti-Self-Grading Bias)" in THINKING_REASONING_FRAMEWORK
