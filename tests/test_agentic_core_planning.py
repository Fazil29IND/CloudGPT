"""
Unit tests for Agentic RAG (Core) Query Orchestration:
- Agent-Driven Query Planning (complexity calculation & intent routing)
- Conditional Query Decomposition (gating simple vs comparative queries)
- Iterative Query Refinement (evidence sufficiency check & refinement retrieval)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from retrieval import RetrievalResult
from retrieval.agentic_rag import AgenticRAGPipeline


@pytest.fixture
def agentic_pipeline():
    return AgenticRAGPipeline()


@pytest.mark.asyncio
async def test_agent_driven_query_planning_calculates_complexity(agentic_pipeline):
    mock_router = MagicMock()
    valid_json = json.dumps({
        "intent": "compare",
        "routes": ["RAG"],
        "retrieval_strategy": "multi-hop",
        "retrieval_modality": "hybrid",
        "sub_queries": ["AWS S3", "GCS"],
        "providers": ["aws", "gcp"],
        "needs_internet": False,
        "confidence": 0.92,
        "complexity_score": 0.85,
    })
    mock_router.classify = AsyncMock(return_value=valid_json)

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        plan = await agentic_pipeline._plan_and_route("Compare AWS S3 vs GCS storage classes", "Pro")

    assert "complexity_score" in plan
    assert plan["complexity_score"] >= 0.65
    assert plan["decomposition_applied"] is True
    assert len(plan["sub_queries"]) == 2


@pytest.mark.asyncio
async def test_conditional_decomposition_skips_simple_atomic_query(agentic_pipeline):
    mock_router = MagicMock()
    # Model mistakenly tried to output sub-queries on a simple atomic query
    valid_json = json.dumps({
        "intent": "explain",
        "routes": ["RAG"],
        "retrieval_strategy": "broad",
        "retrieval_modality": "dense",
        "sub_queries": [],
        "providers": ["aws"],
        "needs_internet": False,
        "confidence": 0.88,
        "complexity_score": 0.30,
    })
    mock_router.classify = AsyncMock(return_value=valid_json)

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        plan = await agentic_pipeline._plan_and_route("How to create an S3 bucket", "Pro")

    # Verify decomposition was not applied for low-complexity atomic query
    assert plan["decomposition_applied"] is False
    assert plan["sub_queries"] == []
    assert plan["retrieval_strategy"] in ("broad", "narrow")


def test_sufficiency_evaluation_low_evidence(agentic_pipeline):
    # Evidence with all low grade scores
    chunks = [
        RetrievalResult(chunk_id="c1", text="Unrelated text", score=0.3, metadata={"grade_score": 0.35}),
        RetrievalResult(chunk_id="c2", text="Vague text", score=0.2, metadata={"grade_score": 0.40}),
    ]
    plan = {"intent": "explain", "retrieval_strategy": "broad"}

    is_sufficient, max_score, reason = agentic_pipeline._evaluate_sufficiency(chunks, plan)
    assert is_sufficient is False
    assert max_score == 0.40
    assert "low_relevance" in reason


def test_sufficiency_evaluation_high_evidence(agentic_pipeline):
    # Evidence with strong grade score
    chunks = [
        RetrievalResult(chunk_id="c1", text="Target documentation with exact config", score=0.9, metadata={"grade_score": 0.85}),
        RetrievalResult(chunk_id="c2", text="Secondary context", score=0.5, metadata={"grade_score": 0.60}),
    ]
    plan = {"intent": "explain", "retrieval_strategy": "broad"}

    is_sufficient, max_score, reason = agentic_pipeline._evaluate_sufficiency(chunks, plan)
    assert is_sufficient is True
    assert max_score == 0.85
    assert reason == "sufficient"


@pytest.mark.asyncio
async def test_refine_query_formulates_targeted_query(agentic_pipeline):
    mock_router = MagicMock()
    refine_json = json.dumps({
        "refined_query": "AWS Aurora Serverless v2 ACU scaling limits and min max configuration",
        "missing_aspect": "ACU configuration limits",
        "suggested_modality": "sparse",
    })
    mock_router.classify = AsyncMock(return_value=refine_json)

    chunks = [
        RetrievalResult(chunk_id="c1", text="General Aurora info", score=0.4, metadata={"grade_score": 0.4}),
    ]
    plan = {"intent": "how_to"}

    with patch("retrieval.agentic_rag.get_sub_model_provider", return_value=mock_router):
        refined = await agentic_pipeline._refine_query(
            "Aurora serverless limits", plan, chunks, "Pro"
        )

    assert "ACU" in refined or "Aurora" in refined
