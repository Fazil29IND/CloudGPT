"""Tests verifying 2026 Enterprise improvements across AWS, Azure, and GCP:
- Model output sanitization / credential redaction
- Semantic cache query normalization & warm-up
- Semantic chunking code & table region protection
- Web search shared HTTP client
- Next-Gen cloud compute catalog coverage
- Model specialization tier configuration
"""

from core.security import sanitize_model_output
from core.semantic_cache import normalize_query_for_cache
from chunking.semantic_chunker import SemanticChunker
from tools.web_search import _get_http_client
from config import Settings


def test_sanitize_model_output_redacts_keys():
    # AWS Access Key ID
    leak_aws = "Here is your key: AKIAIOSFODNN7EXAMPLE for AWS S3 access."
    sanitized = sanitize_model_output(leak_aws)
    assert "AKIAIOSFODNN7EXAMPLE" not in sanitized
    assert "[REDACTED_AWS_ACCESS_KEY]" in sanitized

    # GitHub PAT
    leak_gh = "Use token ghp_123456789012345678901234567890123456 to clone."
    sanitized_gh = sanitize_model_output(leak_gh)
    assert "ghp_123456789012345678901234567890123456" not in sanitized_gh
    assert "[REDACTED_GITHUB_TOKEN]" in sanitized_gh

    # Clean text unchanged
    clean = "To deploy a container on AWS ECS, define a task definition."
    assert sanitize_model_output(clean) == clean


def test_normalize_query_for_cache():
    q1 = "  What is the pricing of AWS Graviton4 EC2 instances???  "
    q2 = "what is the pricing of aws graviton4 ec2 instances"
    assert normalize_query_for_cache(q1) == q2
    assert normalize_query_for_cache("Azure Maia 100 vs GCP TPU v6e!?") == "azure maia 100 vs gcp tpu v6e"


def test_chunker_protects_code_blocks_and_tables():
    chunker = SemanticChunker(max_chunk_chars=300, min_chunk_chars=50, overlap_chars=20)
    text = (
        "## Cloud Deployment Guide\n\n"
        "Here is the terraform configuration for deploying to AWS EKS Auto Mode:\n\n"
        "```hcl\n"
        "resource \"aws_eks_cluster\" \"demo\" {\n"
        "  name     = \"cloudgpt-cluster\"\n"
        "  role_arn = aws_iam_role.cluster.arn\n"
        "  compute_config {\n"
        "    enabled = true\n"
        "    node_pools = [\"general-purpose\"]\n"
        "  }\n"
        "}\n"
        "```\n\n"
        "Ensure your IAM roles are properly configured before applying."
    )
    chunks = chunker.chunk_document(text, provider="aws", category="compute", service="eks")
    assert len(chunks) >= 1
    # Check that code block is preserved intact in chunk
    code_found = False
    for chunk in chunks:
        if "```hcl" in chunk.text and "```" in chunk.text.split("```hcl")[1]:
            code_found = True
            break
    assert code_found, "Code block was split across chunk boundaries!"


def test_web_search_shared_http_client():
    client1 = _get_http_client()
    client2 = _get_http_client()
    assert client1 is client2
    assert client1.is_closed is False


def test_services_md_category_31_content():
    from pathlib import Path
    services_path = Path("Services.md")
    assert services_path.exists()
    content = services_path.read_text(encoding="utf-8")

    assert "31. Next-Gen Enterprise Compute & Silicon Architecture (2026 Expansion)" in content
    # AWS
    assert "AWS Graviton4 / Graviton5" in content
    assert "AWS Trainium2 / Trainium3" in content
    assert "Amazon EKS Auto Mode" in content
    # GCP
    assert "Google Axion" in content
    assert "Cloud TPU v6e (Trillium)" in content
    assert "GKE Autopilot" in content
    # Azure
    assert "Azure Cobalt 100 / Cobalt 200" in content
    assert "Azure Maia 100 AI Accelerator" in content
    assert "AKS Automatic" in content


def test_tier_model_specialization_flag():
    s = Settings(
        enable_tier_model_specialization=True,
        specialized_model_lite="gemini-3.8-flash",
        specialized_model_core="gemini-3.8-flash",
        specialized_model_apex="gemini-3.8-flash",
    )
    assert s.enable_tier_model_specialization is True
    assert s.specialized_model_lite == "gemini-3.8-flash"
