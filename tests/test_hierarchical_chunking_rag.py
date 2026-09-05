"""Comprehensive test suite for Hierarchical Parent-Child Semantic Chunking across all tiers:
1. Hybrid RAG (Lite) -> Semantic Chunking + Parent-Child Chunks
2. Agentic RAG -> Hierarchical Parent-Child Semantic Chunking
3. Adaptive Agentic RAG -> Adaptive Hierarchical Semantic Chunking
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chunking.hierarchical_store import HierarchicalChunkStore
from chunking.semantic_chunker import DocumentChunk, SemanticChunker
from retrieval.adaptive_rag import AdaptiveAdvancedRAGPipeline
from retrieval.agentic_rag import AgenticRAGPipeline
from retrieval.hybrid import HybridRetriever, RetrievalResult


# ── 1. Semantic Chunker & Parent-Child Chunks ─────────────────────────────────

SAMPLE_DOC = """# Cloud Compute Architecture

## 1. Virtual Machines Overview
Virtual machines provide elastic compute instances across availability zones.
Choose instance types based on CPU, memory, and network throughput requirements.

### Instance Comparison Table
| Type | vCPU | RAM (GiB) | Network |
| :--- | :--- | :--- | :--- |
| c6i.large | 2 | 4 | Up to 12.5 Gbps |
| m6i.large | 2 | 8 | Up to 12.5 Gbps |
| r6i.large | 2 | 16 | Up to 12.5 Gbps |

## 2. Serverless Containers
Serverless containers allow running containerized workloads without managing underlying EC2 hosts.
Task definitions specify container image, CPU, memory, and IAM execution roles.
"""


def test_chunk_document_with_parents_produces_hierarchy():
    chunker = SemanticChunker(
        max_chunk_chars=500,
        parent_token_budget=300,
        child_token_budget=100,
        child_token_overlap=20,
    )
    pairs = chunker.chunk_document_with_parents(
        SAMPLE_DOC,
        provider="aws",
        category="compute",
        service="ec2",
        url="https://docs.aws.amazon.com/ec2",
    )

    assert len(pairs) >= 2
    for parent, children in pairs:
        assert parent.is_parent is True
        assert parent.hierarchy_level in (1, 2)
        assert parent.child_chunk_ids
        assert len(children) >= 1
        for child in children:
            assert child.parent_chunk_id == parent.chunk_id
            assert child.hierarchy_level in (2, 3)
            assert child.provider == "aws"
            assert child.service == "ec2"


def test_table_preservation_in_parent_child_chunking():
    chunker = SemanticChunker(parent_token_budget=300, child_token_budget=150)
    pairs = chunker.chunk_document_with_parents(
        SAMPLE_DOC,
        provider="aws",
        service="ec2",
    )
    # Find child chunk or parent that contains the table
    all_chunks = []
    for parent, children in pairs:
        all_chunks.append(parent)
        all_chunks.extend(children)

    table_chunks = [c for c in all_chunks if c.is_table]
    assert len(table_chunks) >= 1
    assert "| c6i.large | 2 | 4 |" in table_chunks[0].text


# ── 2. HierarchicalChunkStore ──────────────────────────────────────────────────

def test_hierarchical_chunk_store_navigation_and_coalescing():
    store = HierarchicalChunkStore(chunks_path=None)

    # Register root doc
    store.register_chunk(DocumentChunk(
        chunk_id="root-1",
        text="# Compute Overview",
        service="ec2",
        provider="aws",
        hierarchy_level=1,
    ))

    # Register parent section
    store.register_chunk(DocumentChunk(
        chunk_id="parent-1",
        text="## EC2 Instance Types\nDetails about EC2 instance types and pricing.",
        service="ec2",
        provider="aws",
        parent_chunk_id="root-1",
        hierarchy_level=2,
    ))

    # Register two child chunks
    c1 = DocumentChunk(
        chunk_id="child-1",
        text="c6i instances are compute optimized.",
        service="ec2",
        provider="aws",
        parent_chunk_id="parent-1",
        hierarchy_level=3,
    )
    c2 = DocumentChunk(
        chunk_id="child-2",
        text="r6i instances are memory optimized.",
        service="ec2",
        provider="aws",
        parent_chunk_id="parent-1",
        hierarchy_level=3,
    )
    store.register_chunk(c1)
    store.register_chunk(c2)

    # Test parent resolution
    parent = store.get_parent_chunk("child-1")
    assert parent is not None
    assert parent.chunk_id == "parent-1"

    # Test children resolution
    children = store.get_child_chunks("parent-1")
    assert len(children) == 2
    assert {c.chunk_id for c in children} == {"child-1", "child-2"}

    # Test sibling resolution
    siblings = store.get_sibling_chunks("child-1")
    assert len(siblings) == 1
    assert siblings[0].chunk_id == "child-2"

    # Test parent coalescing
    coalesced = store.coalesce_parent_context("child-1")
    assert coalesced is not None
    assert coalesced.chunk_id == "parent-1"
    assert "c6i instances are compute optimized." in coalesced.content
    assert "r6i instances are memory optimized." in coalesced.content


# ── 3. Hybrid RAG (Lite): expand_to_parents Retrieval ─────────────────────────

@pytest.mark.asyncio
async def test_hybrid_retriever_expand_to_parents():
    store = HierarchicalChunkStore.get_instance()
    parent_chunk = DocumentChunk(
        chunk_id="parent-vm-1",
        text="## Virtual Machines Complete Guide\nEverything about VM sizing and limits.",
        service="ec2",
        provider="aws",
        hierarchy_level=2,
    )
    child_chunk = DocumentChunk(
        chunk_id="child-vm-1",
        text="VM sizing limits: max 128 vCPUs.",
        service="ec2",
        provider="aws",
        parent_chunk_id="parent-vm-1",
        hierarchy_level=3,
    )
    store.register_chunk(parent_chunk)
    store.register_chunk(child_chunk)

    dense_mock = MagicMock()
    sparse_mock = MagicMock()
    dense_mock.pinecone_manager.active_namespace.return_value = "services-v1"
    dense_mock.retrieve = AsyncMock(return_value=[
        RetrievalResult(
            chunk_id="child-vm-1",
            text=child_chunk.content,
            score=0.92,
            metadata={"provider": "aws", "service": "ec2", "parent_chunk_id": "parent-vm-1"},
        )
    ])
    sparse_mock.retrieve = AsyncMock(return_value=[])

    retriever = HybridRetriever(dense_mock, sparse_mock)
    results = await retriever.retrieve("VM limits", top_k=5, expand_to_parents=True)

    assert len(results) >= 1
    res = results[0]
    assert res.metadata.get("is_coalesced_parent") is True
    assert res.metadata.get("hierarchy_level") == 2
    assert "Virtual Machines Complete Guide" in res.text


# ── 4. Agentic RAG: Hierarchical Context Navigation ───────────────────────────

@pytest.mark.asyncio
async def test_agentic_rag_expands_hierarchical_context_for_top_evidence():
    pipeline = AgenticRAGPipeline()
    store = HierarchicalChunkStore.get_instance()

    # Create a parent and two sibling child chunks
    store.register_chunk(DocumentChunk(
        chunk_id="p-eks-sec",
        text="## EKS Security Architecture\nNetwork policies and IAM roles for service accounts.",
        service="eks",
        provider="aws",
        hierarchy_level=2,
    ))
    c1 = DocumentChunk(
        chunk_id="c-eks-irsa",
        text="IRSA allows assigning IAM roles to Kubernetes pods.",
        service="eks",
        provider="aws",
        parent_chunk_id="p-eks-sec",
        hierarchy_level=3,
    )
    c2 = DocumentChunk(
        chunk_id="c-eks-netpol",
        text="Calico and AWS VPC CNI support Kubernetes network policies.",
        service="eks",
        provider="aws",
        parent_chunk_id="p-eks-sec",
        hierarchy_level=3,
    )
    store.register_chunk(c1)
    store.register_chunk(c2)

    # Initial evidence only retrieved c-eks-irsa with high score
    initial_chunks = [
        RetrievalResult(
            chunk_id="c-eks-irsa",
            text=c1.content,
            score=0.88,
            metadata={"service": "eks", "provider": "aws", "grade_score": 0.88},
        )
    ]

    expanded = pipeline._expand_hierarchical_context(initial_chunks, max_expansions=3)
    chunk_ids = [c.chunk_id for c in expanded]

    assert "c-eks-irsa" in chunk_ids
    assert "c-eks-netpol" in chunk_ids
    sibling_res = next(c for c in expanded if c.chunk_id == "c-eks-netpol")
    assert sibling_res.metadata.get("hierarchical_expanded") is True


# ── 5. Adaptive Agentic RAG: Table and Parent Preservation in Compression ──────

def test_adaptive_rag_preserves_tables_during_hierarchical_compression():
    pipeline = AdaptiveAdvancedRAGPipeline()

    table_text = (
        "Here is the comparison table:\n"
        "| Service | Cost | SLA |\n"
        "| :--- | :--- | :--- |\n"
        "| S3 Standard | $0.023/GB | 99.9% |\n"
        "| S3 Glacier | $0.004/GB | 99.99% |\n"
    )
    chunk = RetrievalResult(
        chunk_id="t-1",
        text=table_text,
        score=0.90,
        metadata={"is_table": True},
    )

    compressed = pipeline._compress_chunk("s3 pricing table comparison", chunk)
    assert compressed.metadata.get("table_preserved") is True
    assert compressed.metadata.get("compressed") is False
    assert "| S3 Standard | $0.023/GB | 99.9% |" in compressed.text


def test_adaptive_rag_preserves_coalesced_parents_during_hierarchical_compression():
    pipeline = AdaptiveAdvancedRAGPipeline()

    parent_text = (
        "## Cloud Storage Architecture & Lifecycle\n"
        "Organizations manage multi-region replication, encryption at rest with KMS, "
        "and lifecycle transition rules across storage classes."
    )
    chunk = RetrievalResult(
        chunk_id="p-1",
        text=parent_text,
        score=0.85,
        metadata={"is_coalesced_parent": True, "hierarchy_level": 2},
    )

    compressed = pipeline._compress_chunk("cloud storage architecture", chunk)
    assert compressed.metadata.get("parent_preserved") is True
    assert compressed.metadata.get("compressed") is False
    assert "Cloud Storage Architecture & Lifecycle" in compressed.text
