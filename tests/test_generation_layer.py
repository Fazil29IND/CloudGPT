"""Tests for the 3-tier Generation & Validation Layer (docs/rag-tier-upgrade-plan.md).

Covers:
- generation.claims: claim extraction, deterministic entailment, claim→source mapping
- generation.compression: Lite conditional gate, Core task-aware profiles, guards
- generation.assembly: fixed / plan-aware / dynamic evidence assembly
- generation.validator: multi-dimensional validation, retry messages, abstention,
  deterministic cleanup, Lite wiring helper
- generation.policy: Apex dynamic policy digest + pre-generation decision
- Tier wiring: Lite path validation via execute_agent_pipeline, Core task-aware
  compression, Apex adaptive rerank/compress strategy awareness.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.chat_routes import PipelineResult
from citations.citation_manager import CitationManager
from generation.assembly import (
    assemble_dynamic_evidence,
    assemble_plan_aware_evidence,
    order_fixed_evidence,
)
from generation.claims import assess_claim_support, extract_claims, score_claim_support
from generation.compression import compress_evidence
from generation.policy import build_policy_digest, pre_generation_decision
from generation.validator import (
    ValidationPolicy,
    apply_lite_validation,
    build_abstention_answer,
    build_retry_messages,
    deterministic_cleanup,
    validate_output,
)
from retrieval import RetrievalResult
from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline
from retrieval.agentic_rag import AgenticRAGPipeline


def _chunk(cid: str, text: str, score: float = 0.9, **meta) -> RetrievalResult:
    return RetrievalResult(chunk_id=cid, text=text, score=score, metadata=meta)


# ── generation.claims ────────────────────────────────────────────────────────

def test_extract_claims_skips_headers_and_references():
    answer = (
        "## Direct Solution\n"
        "AWS S3 bucket policies are attached directly to buckets.\n"
        "1. [AWS S3 Docs](https://docs.aws.amazon.com/s3)\n"
        "```bash\naws s3api put-bucket-policy --bucket my-bucket\n```\n"
    )
    claims = extract_claims(answer)
    assert any("bucket policies" in c for c in claims)
    assert any("put-bucket-policy" in c for c in claims)  # command line is a claim
    assert not any(c.startswith("#") for c in claims)
    assert not any("[AWS S3 Docs]" in c for c in claims)


def test_score_claim_support_exact_technical_requires_hard_token_witness():
    claim = "aws s3api put-object --bucket my-bucket --key file.txt requires 403"
    evidence_without = "S3 stores objects in buckets with durable replication."
    evidence_with = "aws s3api put-object --bucket my-bucket --key file.txt can return 403 when denied."

    low = score_claim_support(claim, evidence_without)
    high = score_claim_support(claim, evidence_with)
    assert high > low
    assert high >= 0.7


def test_assess_claim_support_maps_claims_to_sources():
    chunks = [_chunk("c1", "AWS Lambda provisioned concurrency keeps functions initialized.", 0.9)]
    mgr = CitationManager()
    mgr.register_source(
        source_type="rag", url="https://aws/lambda", provider="aws",
        service="lambda", title="Lambda docs", chunk_id="c1",
    )
    answer = "AWS Lambda provisioned concurrency keeps functions initialized and ready."
    assessment = assess_claim_support(answer, chunks, chunk_to_source=mgr.source_number_for_chunk)

    assert assessment.assessable_count >= 1
    assert assessment.support_ratio >= 0.5
    supported = [c for c in assessment.claims if c.best_source_number is not None]
    assert supported, "claim backed by chunk c1 should map to citation 1"
    assert supported[0].best_chunk_id == "c1"
    assert supported[0].best_source_number == 1


# ── generation.compression ───────────────────────────────────────────────────

def _long_evidence(cid: str, filler: int = 40) -> RetrievalResult:
    body = " ".join(
        f"Generic documentation filler sentence number {i} with ordinary English words." for i in range(filler)
    )
    key = "Provisioned concurrency keeps Lambda functions initialized and reduces cold starts."
    return _chunk(cid, f"{key} {body}", score=0.9)


def test_lite_compression_conditional_gate_skips_small_pools():
    chunks = [_chunk("c1", "Short relevant Lambda documentation content.", 0.9)]
    settings = MagicMock(lite_compression_token_threshold=3000)
    out = compress_evidence("lambda concurrency", chunks, policy="lite", settings=settings)
    assert out is chunks  # gate: pool below threshold stays untouched
    assert "compressed" not in out[0].metadata


def test_lite_compression_fires_on_large_pools_and_preserves_tables():
    big = _long_evidence("big1")
    original_len = len(big.text)  # capture before compression mutates the chunk
    table = _chunk(
        "tbl1",
        "| Instance | vCPU |\n|---|---|\n| m5.large | 2 |\n| m5.xlarge | 4 |",
        score=0.95,
    )
    settings = MagicMock(lite_compression_token_threshold=10)
    out = compress_evidence("lambda provisioned concurrency", [big, table], policy="lite", settings=settings)

    by_id = {c.chunk_id: c for c in out}
    assert by_id["big1"].metadata.get("compressed") is True
    assert len(by_id["big1"].text) < original_len
    assert "Provisioned concurrency" in by_id["big1"].text
    assert by_id["tbl1"].metadata.get("compressed") is False
    assert by_id["tbl1"].metadata.get("table_preserved") is True
    assert "m5.xlarge" in by_id["tbl1"].text  # table rows intact


def test_core_task_aware_compression_keeps_commands_for_troubleshooting():
    body = (
        "The instance profile might be missing from the cluster configuration. "
        "Random architecture discussion about general platform scaling across regions. "
        "Decorative paragraph with no operational signal whatsoever in this entire sentence. "
        "Additional narrative content that provides historical background and context. "
        "aws ec2 describe-instances --instance-ids i-123 to verify the IAM role is attached."
    )
    chunk = _chunk("c1", body, score=0.9)
    settings = MagicMock(lite_compression_token_threshold=10)
    out = compress_evidence(
        "missing IAM role on EC2", [chunk], policy="core", task_intent="troubleshooting", settings=settings
    )
    compressed_text = out[0].text
    assert "describe-instances" in compressed_text  # command survives task-aware extraction


# ── generation.assembly ──────────────────────────────────────────────────────

def test_order_fixed_evidence_demotes_stale():
    fresh = _chunk("fresh", "a", score=0.5)
    stale = _chunk("stale", "b", score=0.99, stale=True)
    out = order_fixed_evidence([stale, fresh])
    assert [c.chunk_id for c in out] == ["fresh", "stale"]


def test_plan_aware_assembly_orders_by_sub_goal_coverage_and_emits_digest():
    plan = {
        "intent": "compare",
        "retrieval_strategy": "multi-hop",
        "retrieval_modality": "hybrid",
        "complexity_score": 0.8,
        "sub_queries": ["AWS EKS node pool configuration", "GKE node pool configuration"],
    }
    both = _chunk("both", "EKS and GKE node pool configuration options compared", score=0.8)
    aws_only = _chunk("aws", "AWS EKS node pools use managed node groups", score=0.95)
    rag_dicts, digest = assemble_plan_aware_evidence([aws_only, both], plan)

    assert rag_dicts[0]["chunk_id"] == "both"  # covers 2 sub-goals, ranked first
    assert rag_dicts[0]["plan_role"] == "multi_goal"
    assert "sub_goal_0: AWS EKS node pool configuration" in digest
    assert digest.startswith("<retrieval_plan>")


def test_dynamic_assembly_strategy_budget_multiplier():
    chunks = [_chunk("c1", "Multi-cloud architecture comparison", score=0.9, provider="aws")]
    rag_dicts, stats = assemble_dynamic_evidence(chunks, "multi_perspective", 0.65)
    assert stats["budget_multiplier"] == 1.15
    assert stats["evidence_count"] == 1
    assert rag_dicts[0]["chunk_id"] == "c1"

    _, stats_fast = assemble_dynamic_evidence(chunks, "direct_fast", 0.35)
    assert stats_fast["budget_multiplier"] == 0.85


# ── generation.validator ─────────────────────────────────────────────────────

def test_deterministic_cleanup_strips_fillers_only():
    assert deterministic_cleanup("Sure, AWS S3 is object storage.") == "AWS S3 is object storage."
    assert deterministic_cleanup("Free answer") == "Free answer"
    assert deterministic_cleanup("Line one.\n\n\n\nLine two.") == "Line one.\n\nLine two."


def test_validate_output_multi_dimensional_pass_and_fail():
    policy = ValidationPolicy(tier="Pro", min_claim_support=0.55, max_retries=1, enable_retry=True,
                              min_assessable_claims=3, substantive_min_chars=50)
    chunks = [_chunk("c1", "AWS Lambda provisioned concurrency keeps functions initialized. "
                             "It costs extra per provisioned GB-second and scales instantly.", 0.9)]

    grounded_answer = (
        "AWS Lambda provisioned concurrency keeps functions initialized and reduces cold starts. "
        "Provisioned concurrency costs extra per provisioned GB-second in AWS Lambda. "
        "It scales instantly when traffic spikes occur in AWS Lambda functions today."
    )
    mgr = CitationManager()
    mgr.register_source(source_type="rag", url="https://aws/lambda", provider="aws",
                        service="lambda", title="Lambda docs", chunk_id="c1")
    report = validate_output(grounded_answer, "How does Lambda provisioned concurrency work?", chunks, mgr, policy,
                             chunk_to_source=mgr.source_number_for_chunk)
    assert report.dimensions["grounding"].passed
    assert report.dimensions["citations"].passed
    assert report.is_valid
    assert not report.retryable

    hallucinated_answer = (
        "Lambda provisioned concurrency in AWS is completely free of charge always. "
        "Azure Functions warms containers automatically with the same exact mechanism named Xyz. "
        "Google Cloud Run guarantees forty two seconds of startup latency for every single request."
    )
    report_bad = validate_output(hallucinated_answer, "How does Lambda provisioned concurrency work?", chunks, mgr,
                                 policy, chunk_to_source=mgr.source_number_for_chunk)
    assert not report_bad.dimensions["grounding"].passed
    assert report_bad.unsupported_claims
    assert report_bad.retryable


def test_validate_output_retry_skipped_for_tiny_answers():
    policy = ValidationPolicy(tier="Pro", min_claim_support=0.55, max_retries=1, enable_retry=True,
                              min_assessable_claims=3, substantive_min_chars=200)
    chunks = [_chunk("c1", "Lambda keeps functions initialized.", 0.9)]
    report = validate_output("Short wrong answer about Azure.", "Lambda concurrency?", chunks, None, policy)
    assert not report.retryable  # below substantive floor


def test_build_retry_messages_lists_unsupported_claims():
    policy = ValidationPolicy(tier="Pro", min_claim_support=0.9, enable_retry=True, min_assessable_claims=1,
                              substantive_min_chars=1)
    chunks = [_chunk("c1", "AWS Lambda scales automatically with invocation volume.", 0.9)]
    report = validate_output(
        "AWS Lambda charges exactly five dollars per million invocations, which is a very specific and detailed claim.",
        "Lambda pricing", chunks, None, policy,
    )
    messages = build_retry_messages("Lambda pricing", "draft answer", report, chunks)
    assert messages[0]["role"] == "system"
    assert "UNSUPPORTED CLAIMS" in messages[1]["content"] or "grounding improvement" in messages[1]["content"]


def test_build_abstention_answer_is_honest_boundary():
    report = validate_output(
        "Completely ungrounded long answer about Azure Quantum pricing that is not supported by any evidence at all.",
        "Azure Quantum pricing", [_chunk("c1", "AWS S3 storage documentation.", 0.9)], None,
        ValidationPolicy(tier="Max", min_assessable_claims=1, substantive_min_chars=1, enable_abstention=True,
                         abstain_min_support=0.3),
    )
    assert report.abstain_recommended
    abstention = build_abstention_answer("Azure Quantum pricing", report)
    assert "Verified Answer Unavailable" in abstention


def test_apply_lite_validation_annotates_result_without_retry():
    result = PipelineResult(answer="Sure! " + ("AWS Lambda is serverless compute. " * 20))
    chunks = [{"chunk_id": "c1", "content": "AWS Lambda is a serverless compute service that runs code in response to events."}]
    mgr = CitationManager()
    mgr.register_source(source_type="rag", url="https://aws/lambda", provider="aws", service="lambda",
                        title="Lambda", chunk_id="c1")
    settings = MagicMock(lite_min_claim_support=0.5, validation_min_assessable_claims=3,
                         enable_lite_output_validation=True, substantive_min_chars=200)
    # Force a grounding failure: claims about DynamoDB not present in evidence
    result.answer = "Sure! " + ("Azure Cosmos DB offers five nines availability. " * 12)

    apply_lite_validation(result, "Tell me about AWS Lambda", chunks, mgr, settings)
    assert result.answer.startswith("Azure Cosmos DB")  # filler stripped, caveat appended
    assert "Grounding notice" in result.answer
    assert result.validation.get("claim_support", 1.0) < 0.5
    assert any(f.startswith("validation:grounding") for f in result.context_validator_flags)


# ── generation.policy ────────────────────────────────────────────────────────

def test_policy_digest_reflects_strategy_and_staleness():
    digest = build_policy_digest(
        strategy="multi_perspective", complexity_score=0.7, confidence=0.9,
        evidence_stats={"evidence_count": 4, "avg_score": 0.8, "stale_count": 1, "compressed_count": 2},
        live_verified=True, tier="Max",
    )
    assert digest.startswith("<pipeline_policy>")
    assert "routing_strategy: multi_perspective" in digest
    assert "Stale chunks present" in digest
    assert "Live provider documentation" in digest

    weak = build_policy_digest(strategy="semantic_hyde", evidence_stats={"evidence_count": 0, "avg_score": 0.0},
                               live_verified=False)
    assert "Evidence is weak or absent" in weak


def test_pre_generation_decision_boundary_mode():
    settings = MagicMock(adaptive_abstain_min_support=0.3)
    grounded = pre_generation_decision(
        candidates=[_chunk("c1", "evidence", 0.9)], live_verify_results=[],
        classification_confidence=0.5, settings=settings,
    )
    assert grounded["mode"] == "grounded"

    boundary = pre_generation_decision(
        candidates=[], live_verify_results=[], classification_confidence=0.2, settings=settings,
    )
    assert boundary["mode"] == "boundary"


# ── Tier wiring ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_free_tier_pipeline_attaches_validation_report():
    from api.chat_routes import execute_agent_pipeline

    fake_classification = MagicMock(
        routes=["RAG"], providers=["aws"], needs_internet=False, intent="explain",
        confidence=0.9, model_dump=lambda: {"intent": "explain"},
    )
    fake_router = MagicMock(route_query=AsyncMock(return_value=fake_classification))
    rag_result = _chunk("c1", "AWS Lambda is a serverless compute service that runs code on demand.",
                        score=0.9, provider="aws", service="lambda", url="https://aws/lambda")

    long_answer = (
        "AWS Lambda is a serverless compute service that runs code in response to events. "
        "It automatically scales with request volume and charges only for execution time used. "
        "Lambda functions can be triggered by S3 uploads, API Gateway requests, and queue events."
    )

    with patch("api.chat_routes.pipeline.get_router", return_value=fake_router), \
         patch("api.chat_routes.pipeline.web_search.search", AsyncMock(return_value=[])), \
         patch("api.chat_routes.pipeline.get_embeddings", side_effect=RuntimeError("no embeddings in test")), \
         patch("api.chat_routes.pipeline.get_retrieval", return_value=(MagicMock(), MagicMock())), \
         patch("api.chat_routes._retrieve_with_fallback", AsyncMock(return_value=([rag_result], "pass3_global"))), \
         patch("api.chat_routes.generate_with_fallback", AsyncMock(return_value=(long_answer, "gemini"))), \
         patch("api.chat_routes.get_cached_retrieval_result", AsyncMock(return_value=None)), \
         patch("api.chat_routes.set_cached_retrieval_result", AsyncMock(return_value=None)), \
         patch("api.chat_routes.redis_client", None):
        result = await execute_agent_pipeline("What is AWS Lambda?", tier="Free")

    assert result.pipeline_type == "current_rag"
    assert "validation" in result.pipeline_timings or result.validation
    assert result.validation.get("assessable_claims", 0) >= 1
    assert result.validation.get("claim_support", 0) >= 0.3


@pytest.mark.asyncio
async def test_core_pipeline_applies_task_compression_and_validation():
    pipeline = AgenticRAGPipeline()
    plan = {"intent": "troubleshooting", "routes": ["RAG"], "retrieval_strategy": "broad",
            "sub_queries": [], "providers": ["aws"], "needs_internet": False,
            "confidence": 0.9, "_timing_ms": 1.0}
    chunks = [_chunk("c1", "AWS Lambda throttling occurs when ReservedConcurrentExecutions "
                            "is below the invocation rate. Increase the reserved concurrency limit "
                            "in the Lambda console and verify with CloudWatch Throttles metric.", 0.9,
                     provider="aws", url="https://aws/lambda")]
    answer = (
        "AWS Lambda throttling happens when ReservedConcurrentExecutions is below the invocation rate. "
        "Increase the reserved concurrency limit in the Lambda console. "
        "Verify with the CloudWatch Throttles metric after applying the change."
    )

    with patch.object(pipeline, "_plan_and_route", AsyncMock(return_value=plan)), \
         patch.object(pipeline, "_hybrid_retrieve", AsyncMock(return_value=(chunks, "agentic_hybrid_rrf"))), \
         patch.object(pipeline, "_grade_evidence", AsyncMock(return_value=chunks)), \
         patch.object(pipeline, "_expand_hierarchical_context", return_value=chunks), \
         patch("llm.provider.get_evaluator_provider", return_value=MagicMock(
             generate=AsyncMock(return_value="NO_REVISION_NEEDED"))), \
         patch("api.chat_routes.generate_with_fallback", AsyncMock(return_value=(answer, "claude"))), \
         patch("api.chat_routes._build_pipeline_messages", return_value=[{"role": "user", "content": "q"}]), \
         patch("api.chat_routes.pipeline.get_retrieval", return_value=(MagicMock(), MagicMock())):
        result = await pipeline.run(query="Lambda throttling", tier="Pro")

    assert result.pipeline_type == "agentic_rag"
    assert result.answer == answer
    assert result.validation, "Core must attach a validation report"
    assert "validate" in result.pipeline_timings
    # chunk_id-linked citation source enables claim→source attribution
    assert result.sources and result.sources[0]["source_type"] == "rag"


@pytest.mark.asyncio
async def test_apex_adaptive_rerank_strategy_aware():
    pipeline = AdaptiveAdvancedRAGPipeline()
    transformed = {
        "routing_path": "multi_perspective",
        "rewritten_query": "Aurora vs Spanner",
        "sparse_query": "Aurora vs Spanner",
        "perspective_queries": [
            {"dimension": "architecture", "query": "Aurora vs Spanner architecture"},
            {"dimension": "pricing", "query": "Aurora vs Spanner pricing"},
        ],
    }
    candidates = [
        _chunk("d1", "Aurora architecture deep dive", 0.9, provider="aws"),
        _chunk("d2", "Spanner pricing model details", 0.85, provider="google-cloud"),
    ]
    reranker = MagicMock()
    # Each dimension rerank returns its own ordering.
    reranker.rerank = MagicMock(
        side_effect=lambda q, cands, top_k: ([c for c in cands if "architecture" in c.text.lower()]
                                             or cands)[:top_k] if "architecture" in q else cands[:top_k]
    )
    out = await pipeline._rerank_and_compress("Aurora vs Spanner", candidates, "Max", reranker, transformed)
    assert {c.chunk_id for c in out} == {"d1", "d2"}
    # multi_perspective trigger threshold (0.65) keeps both chunks uncompressed
    assert all(not c.metadata.get("compressed") for c in out)


@pytest.mark.asyncio
async def test_apex_validation_loop_retries_then_accepts():
    pipeline = AdaptiveAdvancedRAGPipeline()
    evidence = [_chunk("c1", "DynamoDB on-demand capacity mode charges per request unit. "
                              "On-demand mode scales instantly with no capacity planning required. "
                              "DynamoDB on-demand pricing applies per million read request units.", 0.95,
                       provider="aws")]
    good_answer = (
        "DynamoDB on-demand capacity mode charges per request unit with no capacity planning. "
        "On-demand mode scales instantly when traffic spikes occur in DynamoDB tables. "
        "Pricing applies per million read request units in DynamoDB on-demand mode."
    )
    timings: dict[str, float] = {}

    final, report = await pipeline._validate_and_repair(
        query="DynamoDB on-demand mode", final_answer=good_answer, evidence_chunks=evidence,
        citation_mgr=None, tier="Max", thinking_level="Medium", timings=timings, emit_event=None,
    )
    assert final == good_answer
    assert report.get("is_valid") is True
    assert timings.get("validate") is not None
