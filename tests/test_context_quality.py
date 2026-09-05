"""Unit tests for ContextQualityController."""

from core.context_quality import ContextQualityController


def test_deduplicate_by_canonical_url():
    cqc = ContextQualityController()
    chunks = [
        {"url": "https://docs.aws.amazon.com/s3/?ref=1", "provider": "aws", "service": "S3", "content": "chunk 1"},
        {"url": "https://docs.aws.amazon.com/s3/#section", "provider": "aws", "service": "S3", "content": "chunk 2"},
        {"url": "https://cloud.google.com/storage", "provider": "gcp", "service": "GCS", "content": "chunk 3"},
    ]
    deduped = cqc._deduplicate(chunks)
    assert len(deduped) == 2
    assert deduped[0]["service"] == "S3"
    assert deduped[1]["service"] == "GCS"


def test_diversity_rebalance():
    cqc = ContextQualityController()
    # 4 AWS chunks, 1 GCP chunk -> AWS accounts for 80% (> 70%)
    chunks = [
        {"url": f"https://aws.com/{i}", "provider": "aws", "service": "EC2", "content": f"aws {i}"}
        for i in range(4)
    ] + [
        {"url": "https://gcp.com/1", "provider": "gcp", "service": "GCE", "content": "gcp 1"}
    ]
    rebalanced, applied = cqc._diversity_rebalance(chunks)
    assert applied is True
    # The first 2 items should contain different providers
    providers = [c["provider"] for c in rebalanced[:2]]
    assert "aws" in providers and "gcp" in providers


def test_contradiction_detection_service_collision():
    cqc = ContextQualityController()
    chunks = [
        {"provider": "aws", "service": "RDS", "content": "AWS relational database"},
        {"provider": "gcp", "service": "RDS", "content": "Google claimed RDS"},  # collision
    ]
    score, contradictions = cqc._detect_contradictions(chunks, memory_facts=[], history_snippets=[])
    assert len(contradictions) == 1
    assert score == 0.75


def test_tier_gating_skips_contradictions_on_free():
    cqc = ContextQualityController()
    chunks = [
        {"url": "https://aws.com/1", "provider": "aws", "service": "RDS", "content": "db 1"},
        {"url": "https://gcp.com/1", "provider": "gcp", "service": "RDS", "content": "db 2"},
    ]
    # On Free: skips contradiction check -> coherence score remains 1.0
    res_free = cqc.run(chunks, tier="Free")
    assert res_free.context_coherence_score == 1.0

    # On Pro: runs contradiction check -> detects collision
    res_pro = cqc.run(chunks, tier="Pro")
    assert res_pro.context_coherence_score < 1.0
    assert len(res_pro.contradiction_pairs) > 0
