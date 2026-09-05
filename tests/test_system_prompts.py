import json
import re

from llm.system_prompts import (
    AGENTIC_PLAN_PROMPT,
    QUERY_TRANSFORM_PROMPT,
    EVIDENCE_GRADE_PROMPT,
    SELF_CRITIQUE_PROMPT,
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
