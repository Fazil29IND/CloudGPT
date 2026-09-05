"""
Unit tests for Adaptive Agentic RAG (Apex) Query Orchestration:
- Adaptive Query Routing (direct_fast, semantic_hyde, multi_perspective)
- Selective Query Transformation (dynamically gating HyDE and expansion)
- Retrieval Feedback Evaluation and Re-planning Loop
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from retrieval import RetrievalResult
from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline


@pytest.fixture
def adaptive_pipeline():
    return AdaptiveAdvancedRAGPipeline()


@pytest.mark.asyncio
async def test_adaptive_query_routing_paths(adaptive_pipeline):
    mock_router = MagicMock()
    mock_router.classify = AsyncMock(return_value=json.dumps({
        "rewritten_query": "q",
        "expanded_queries": ["q"],
        "hyde_passage": "h",
    }))

    with patch("retrieval.adaptive_rag.get_sub_model_provider", return_value=mock_router):
        # 1. Exact CLI syntax / command
        cli_res = await adaptive_pipeline._transform_query("kubectl get pods -n kube-system", "Max")
        assert cli_res["routing_path"] == "direct_fast"

        # 2. Conceptual architecture query
        arch_res = await adaptive_pipeline._transform_query("multi-region active-active database replication architecture", "Max")
        assert arch_res["routing_path"] == "semantic_hyde"

        # 3. Comparative query
        comp_res = await adaptive_pipeline._transform_query("compare AWS ECS vs GCP Cloud Run", "Max")
        assert comp_res["routing_path"] == "multi_perspective"


@pytest.mark.asyncio
async def test_selective_query_transformation_bypasses_hyde_for_exact_syntax(adaptive_pipeline):
    mock_router = MagicMock()
    # When model generates generic response without explicit technical hyde
    mock_router.classify = AsyncMock(return_value=json.dumps({
        "rewritten_query": "aws ec2 describe-instances --output-json",
        "expanded_queries": ["aws ec2 describe-instances --output-json"],
        "hyde_passage": "",
    }))

    with patch("retrieval.adaptive_rag.get_sub_model_provider", return_value=mock_router):
        res = await adaptive_pipeline._transform_query(
            "aws ec2 describe-instances --output-json", "Max"
        )

    assert res["routing_path"] == "direct_fast"
    assert "direct_passthrough" in res["applied_transformations"]
    assert res["hyde_passage"] == "aws ec2 describe-instances --output-json"


def test_retrieval_feedback_evaluation_triggers_replan_on_low_score(adaptive_pipeline):
    # Poor retrieval output: low scores
    poor_candidates = [
        RetrievalResult(chunk_id="p1", text="irrelevant chunk 1", score=0.25),
        RetrievalResult(chunk_id="p2", text="irrelevant chunk 2", score=0.22),
    ]
    transformed = {"rewritten_query": "unknown tech question"}

    needs_replan, score, diag = adaptive_pipeline._evaluate_retrieval_feedback(
        poor_candidates, "unknown tech question", transformed
    )
    assert needs_replan is True
    assert score == 0.25
    assert "low_relevance_score" in diag


def test_retrieval_feedback_evaluation_sufficient_on_high_score(adaptive_pipeline):
    # Strong retrieval output
    good_candidates = [
        RetrievalResult(chunk_id="g1", text="Exact target architecture guide", score=0.88),
        RetrievalResult(chunk_id="g2", text="Secondary reference", score=0.55),
    ]
    transformed = {"rewritten_query": "s3 replication"}

    needs_replan, score, diag = adaptive_pipeline._evaluate_retrieval_feedback(
        good_candidates, "s3 replication", transformed
    )
    assert needs_replan is False
    assert score == 0.88
    assert diag == "sufficient_retrieval"


@pytest.mark.asyncio
async def test_replan_retrieval_executes_secondary_search(adaptive_pipeline):
    mock_router = MagicMock()
    replan_json = json.dumps({
        "replanned_query": "AWS S3 Cross-Region Replication CRR configuration steps IAM role KMS",
        "strategy_adjustment": "broaden_filter",
        "rationale": "Include KMS and IAM permissions",
    })
    mock_router.classify = AsyncMock(return_value=replan_json)

    mock_retriever = MagicMock()
    replanned_candidates = [
        RetrievalResult(chunk_id="r1", text="Re-planned KMS guide", score=0.82)
    ]
    mock_retriever.retrieve = AsyncMock(return_value=replanned_candidates)

    mock_reranker = MagicMock()
    mock_reranker.rerank = MagicMock(return_value=replanned_candidates)

    transformed = {"rewritten_query": "s3 replication fail"}

    with patch("retrieval.adaptive_rag.get_sub_model_provider", return_value=mock_router):
        new_cands, replan_pass = await adaptive_pipeline._replan_retrieval(
            query="s3 replication fail",
            transformed=transformed,
            feedback_diagnosis="low_relevance_score",
            tier="Max",
            retriever=mock_retriever,
            reranker=mock_reranker,
        )

    assert len(new_cands) == 1
    assert new_cands[0].chunk_id == "r1"
    assert "replanned" in replan_pass
