"""
Unit tests for Specialized RAG Context Engineering and Context Windows.

Verifies:
- LiteContextEngine: 4k/8k window, <verified_cloud_facts>, verified cost data, strict boundary, Lite prompt.
- AgenticContextEngine: 8k/32k window, <retrieval_plan>, <agentic_evidence_matrix>, tool executions, Core prompt.
- AdaptiveContextEngine: 16k/64k window, <well_architected_evidence_matrix>, [Ref: chunk_id] anchors, live verification, Apex prompt.
- ContextBuilder.build_rag_context dispatching.
- Backward compatibility with legacy ContextBuilder.build_context callers.
"""

from __future__ import annotations


from config import get_settings
from llm.context_builder import (
    AdaptiveContextEngine,
    AgenticContextEngine,
    ContextBuilder,
    LiteContextEngine,
)
from llm.system_prompts import (
    APEX_TIER_SYSTEM_PROMPT,
    CORE_TIER_SYSTEM_PROMPT,
    LITE_TIER_SYSTEM_PROMPT,
)


def test_lite_hybrid_context_engine_budget_and_formatting():
    settings = get_settings()
    engine = LiteContextEngine(settings)

    rag_results = [
        {
            "provider": "aws",
            "service": "ec2",
            "section": "Instance Types",
            "content": "Amazon EC2 m6i instances are powered by 3rd Generation Intel Xeon Scalable processors.",
        },
        {
            "provider": "gcp",
            "service": "compute engine",
            "section": "Machine Types",
            "content": "Google Compute Engine N2 series offers balanced compute and memory.",
        },
    ]
    pricing_data = [
        {
            "provider": "aws",
            "sku": "m6i.large",
            "hourly_cost": 0.096,
            "monthly_cost": 70.08,
            "currency": "USD",
        }
    ]
    calc_results = {"expression": "0.096 * 730", "result": "70.08 USD"}

    messages = engine.build(
        query="Compare EC2 m6i and Compute Engine N2",
        classification={"intent": "compare", "providers": ["aws", "gcp"]},
        rag_results=rag_results,
        pricing_data=pricing_data,
        calc_results=calc_results,
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == LITE_TIER_SYSTEM_PROMPT

    user_content = messages[1]["content"]
    assert "<verified_cloud_facts>" in user_content
    assert "</verified_cloud_facts>" in user_content
    assert "[Fact 1 |" in user_content
    assert "Amazon EC2 m6i" in user_content
    assert "<verified_cost_data>" in user_content
    assert "$0.0960/hr" in user_content
    assert "VERIFIED KNOWLEDGE BOUNDARY & CONSTRAINTS:" in user_content


def test_lite_hybrid_context_engine_attachment_expansion():
    settings = get_settings()
    engine = LiteContextEngine(settings)

    attachment = [
        {
            "filename": "infra_specs.txt",
            "text": "Production Kubernetes cluster require 3 worker nodes and 2 master nodes.",
        }
    ]

    messages = engine.build(
        query="Deploy cluster according to spec",
        classification={"intent": "deploy", "providers": ["aws"]},
        attachment_texts=attachment,
    )

    user_content = messages[1]["content"]
    assert "<attached_reference_data>" in user_content
    assert "infra_specs.txt" in user_content
    assert "Production Kubernetes cluster" in user_content


def test_agentic_context_engine_budget_and_plan_framing():
    settings = get_settings()
    engine = AgenticContextEngine(settings)

    plan = {
        "retrieval_strategy": "agentic_multi_hop",
        "intent": "architecture_design",
        "routes": ["RAG", "PRICING", "CALCULATOR"],
        "sub_queries": [
            "AWS Aurora Multi-AZ replication latency",
            "Azure Cosmos DB multi-region latency",
        ],
    }
    rag_results = [
        {
            "provider": "aws",
            "service": "aurora",
            "section": "Replication",
            "content": "Aurora uses a quorum-based storage system replicated across 3 Availability Zones.",
        },
        {
            "provider": "azure",
            "service": "cosmos_db",
            "section": "Consistency",
            "content": "Azure Cosmos DB offers 5 well-defined consistency levels with single-digit ms latencies.",
        },
    ]
    pricing_data = [
        {
            "provider": "aws",
            "sku": "aurora-db.r6g.xlarge",
            "hourly_cost": 0.52,
            "monthly_cost": 379.60,
            "currency": "USD",
        }
    ]
    calc_results = {"expression": "0.52 * 730 * 2", "result": "759.20 USD"}

    messages = engine.build(
        query="Design active-active multi-region database across AWS and Azure",
        classification={"intent": "architecture_design", "providers": ["aws", "azure"]},
        rag_results=rag_results,
        pricing_data=pricing_data,
        calc_results=calc_results,
        plan=plan,
        sub_queries=plan["sub_queries"],
        tier="Pro",
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == CORE_TIER_SYSTEM_PROMPT

    user_content = messages[1]["content"]
    assert "<retrieval_plan>" in user_content
    assert "AWS Aurora Multi-AZ replication latency" in user_content
    assert "Azure Cosmos DB multi-region latency" in user_content
    assert "<agentic_evidence_matrix>" in user_content
    assert "<cloud_tool_executions>" in user_content
    assert '<tool_result name="pricing">' in user_content
    assert '<tool_result name="calculator">' in user_content
    assert "AGENTIC ARCHITECTURE DIRECTIVES:" in user_content


def test_adaptive_context_engine_matrix_and_anchors():
    settings = get_settings()
    engine = AdaptiveContextEngine(settings)

    transformed = {
        "routing_path": "multi_perspective",
        "strategy": "multi_perspective",
        "perspective_queries": [
            {"dimension": "security", "query": "Cross-cloud zero trust IAM architecture"},
            {"dimension": "reliability", "query": "Cross-cloud automated failover RTO RPO"},
        ],
    }
    rag_results = [
        {
            "chunk_id": "chunk-sec-01",
            "provider": "aws",
            "service": "iam_roles_anywhere",
            "section": "Zero Trust",
            "content": "AWS IAM Roles Anywhere enables workloads outside AWS to use X.509 certificates to obtain temporary AWS credentials.",
        },
        {
            "chunk_id": "chunk-rel-02",
            "provider": "gcp",
            "service": "anthos",
            "section": "High Availability",
            "content": "Anthos provides unified multi-cluster management and automated ingress traffic routing.",
        },
    ]
    internet_results = [
        {
            "title": "AWS IAM Roles Anywhere Official Docs",
            "url": "https://docs.aws.amazon.com/roles-anywhere/",
            "content": "Verified live documentation: IAM Roles Anywhere supports PKI trust anchors.",
        }
    ]

    messages = engine.build(
        query="Design cross-cloud zero-trust hybrid architecture with live proofs",
        classification={"intent": "frontier_architecture", "providers": ["aws", "gcp"]},
        rag_results=rag_results,
        internet_results=internet_results,
        transformed=transformed,
        live_verified=True,
        tier="Max",
        policy_digest="Active Strategy: multi_perspective | Confidence: 0.95",
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == APEX_TIER_SYSTEM_PROMPT

    user_content = messages[1]["content"]
    assert "DYNAMIC PIPELINE POLICY" in user_content
    assert "<architectural_perspectives>" in user_content
    assert "Dimension: security" in user_content
    assert "<well_architected_evidence_matrix>" in user_content
    assert "[Ref: chunk-sec-01]" in user_content
    assert "[Ref: chunk-rel-02]" in user_content
    assert "<live_documentation_verification>" in user_content
    assert "AWS IAM Roles Anywhere Official Docs" in user_content
    assert "FRONTIER SYNTHESIS & CLAIM ATTRIBUTION DIRECTIVES:" in user_content


def test_context_builder_build_rag_context_dispatcher():
    builder = ContextBuilder()

    # Dispatch to Lite
    lite_msgs = builder.build_rag_context(
        rag_mode="lite",
        query="What is S3?",
        classification={"intent": "explain", "providers": ["aws"]},
    )
    assert lite_msgs[0]["content"] == LITE_TIER_SYSTEM_PROMPT

    # Dispatch to Agentic
    agentic_msgs = builder.build_rag_context(
        rag_mode="agentic",
        query="Plan VPC peering across AWS and Azure",
        classification={"intent": "design", "providers": ["aws", "azure"]},
    )
    assert agentic_msgs[0]["content"] == CORE_TIER_SYSTEM_PROMPT

    # Dispatch to Adaptive
    adaptive_msgs = builder.build_rag_context(
        rag_mode="adaptive",
        query="Synthesize enterprise cloud foundation blueprint",
        classification={"intent": "architecture", "providers": ["aws", "gcp", "azure"]},
    )
    assert adaptive_msgs[0]["content"] == APEX_TIER_SYSTEM_PROMPT


def test_context_builder_build_context_backward_compatibility():
    builder = ContextBuilder()
    # Test standard build_context call still works identically
    messages = builder.build_context(
        query="Explain AWS DynamoDB partition keys",
        classification={"intent": "explain", "providers": ["aws"]},
        max_context_tokens=4000,
    )
    assert len(messages) == 2
    assert "USER QUERY: <user_query>Explain AWS DynamoDB partition keys</user_query>" in messages[1]["content"]
