import pytest
from router.query_router import QueryRouter


@pytest.fixture
def router():
    return QueryRouter()


@pytest.mark.asyncio
async def test_documentation_query(router):
    classification = await router.route_query("What are the storage classes available in S3?")
    assert "RAG" in classification.routes
    assert "aws" in classification.providers
    assert "s3" in classification.services


@pytest.mark.asyncio
async def test_comparison_query(router):
    classification = await router.route_query("Compare AWS EC2 vs GCP Compute Engine")
    assert classification.intent == "compare"
    assert "RAG" in classification.routes
    assert "aws" in classification.providers
    assert "gcp" in classification.providers


@pytest.mark.asyncio
async def test_pricing_query(router):
    classification = await router.route_query("What is the cost of running an Azure VM per hour?")
    assert classification.intent == "price"
    assert "PRICING" in classification.routes
    assert "azure" in classification.providers


@pytest.mark.asyncio
async def test_live_resource_query(router):
    classification = await router.route_query("List my running EC2 instances")
    assert classification.intent == "live_resource"
    assert "CLOUD_API" in classification.routes


@pytest.mark.asyncio
async def test_calculation_query(router):
    classification = await router.route_query("Calculate total cost for 5 instances at $0.05 per hour")
    assert classification.intent == "calculate"
    assert "CALCULATOR" in classification.routes


@pytest.mark.asyncio
async def test_troubleshooting_query(router):
    classification = await router.route_query("My AWS Lambda function is getting timeout error and crash")
    assert classification.intent == "troubleshooting"
    assert "INTERNET" in classification.routes
    assert classification.needs_internet is True


@pytest.mark.asyncio
async def test_problem_solving_query(router):
    classification = await router.route_query("How to fix S3 403 Forbidden AccessDenied error")
    assert classification.intent == "problem_solving"
    assert "INTERNET" in classification.routes
    assert classification.needs_internet is True


@pytest.mark.asyncio
async def test_freshness_query(router):
    classification = await router.route_query("What changed in S3 in 2026?")
    assert "INTERNET" in classification.routes
    assert classification.needs_internet is True


@pytest.mark.asyncio
async def test_multicloud_parity_general_query(router):
    classification = await router.route_query("How do I configure object storage lifecycle policies?")
    assert "RAG" in classification.routes
    assert "aws" in classification.providers
    assert "gcp" in classification.providers
    assert "azure" in classification.providers
    assert classification.requires_provider_comparison is True


@pytest.mark.asyncio
async def test_gcp_specific_query(router):
    classification = await router.route_query("How does Google Kubernetes Engine autopilot work?")
    assert "RAG" in classification.routes
    assert any(p in classification.providers for p in ("gcp", "google-cloud"))
    assert "aws" not in classification.providers

