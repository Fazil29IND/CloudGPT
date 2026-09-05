"""
Unit tests for Hybrid RAG (Lite) Query Processing:
- Query Normalization (noise stripping, technical preservation, canonical mapping)
- Contextual Query Rewriting (multi-turn anaphora and pronoun resolution)
- Original Query Preservation (dual-stream BM25 + HNSW execution)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from retrieval import RetrievalResult
from retrieval.hybrid import HybridRetriever
from retrieval.query_processor import (
    QueryContext,
    normalize_query,
    rewrite_contextual_query,
)


def test_query_normalization_preserves_cli_flags_and_errors():
    # CLI flag preservation
    q_flag = "   aws ec2 describe-instances --output-json --region us-east-1 ?  "
    norm_flag = normalize_query(q_flag)
    assert "--output-json" in norm_flag
    assert "--region" in norm_flag
    assert not norm_flag.endswith("?")

    # Exception class preservation
    q_err = "How to fix ResourceNotFoundException in DynamoDB??!"
    norm_err = normalize_query(q_err)
    assert "ResourceNotFoundException" in norm_err
    assert not norm_err.endswith("?")
    assert not norm_err.endswith("!")

    # HTTP error code preservation
    q_http = "Getting HTTP 403 Forbidden on S3 upload"
    norm_http = normalize_query(q_http)
    assert "403 Forbidden" in norm_http or "403" in norm_http


def test_query_normalization_canonical_acronym_mapping():
    q = "how to setup k8s cluster with gcs and iam role"
    norm = normalize_query(q)
    assert "kubernetes" in norm
    assert "google cloud storage" in norm
    assert "identity and access management" in norm


def test_contextual_query_rewriting_with_history():
    history = [
        {"role": "user", "content": "Tell me about AWS Lambda provisioned concurrency."},
        {"role": "assistant", "content": "AWS Lambda provisioned concurrency keeps functions initialized."},
    ]

    # Ambiguous follow-up turn with pronoun
    follow_up = "What are its maximum limits?"
    ctx = rewrite_contextual_query(follow_up, chat_history=history)

    assert isinstance(ctx, QueryContext)
    assert ctx.original_query == "What are its maximum limits?"
    assert ctx.is_rewritten is True
    assert "lambda" in ctx.rewritten_query.lower() or "aws" in ctx.rewritten_query.lower()
    assert ctx.effective_search_query == ctx.rewritten_query


def test_contextual_query_rewriting_standalone_no_history():
    # Self-contained query without history
    q = "How to configure VPC peering in AWS?"
    ctx = rewrite_contextual_query(q, chat_history=None)

    assert ctx.original_query == q
    assert ctx.is_rewritten is False
    assert ctx.effective_search_query == ctx.normalized_query


@pytest.mark.asyncio
async def test_original_query_preservation_in_hybrid_retriever():
    mock_dense = MagicMock()
    mock_sparse = MagicMock()

    dense_res = [RetrievalResult(chunk_id="d1", text="Lambda limits overview", score=0.85)]
    sparse_res = [RetrievalResult(chunk_id="s1", text="Exact CLI --concurrency flag", score=0.90)]

    mock_dense.retrieve = AsyncMock(return_value=dense_res)
    mock_sparse.retrieve = AsyncMock(return_value=sparse_res)

    retriever = HybridRetriever(mock_dense, mock_sparse)

    history = [
        {"role": "user", "content": "AWS Lambda concurrency"},
    ]
    raw_query = "What are its limits --concurrency-limit?"

    results = await retriever.retrieve(
        query=raw_query,
        top_k=5,
        chat_history=history,
        namespaces=["services-v1"],
    )

    # Verify dense retriever was called with rewritten/normalized query containing entity
    dense_call_args = mock_dense.retrieve.call_args[0]
    assert "lambda" in dense_call_args[0].lower() or "aws" in dense_call_args[0].lower()

    # Verify sparse retriever was called with original query strictly preserving the exact flag
    sparse_call_args = mock_sparse.retrieve.call_args[0]
    assert "--concurrency-limit" in sparse_call_args[0]

    # Verify results fused both streams
    cids = [r.chunk_id for r in results]
    assert "d1" in cids
    assert "s1" in cids


def test_self_contained_query_not_hijacked_by_prior_history():
    """Verify that a self-contained query with internal pronoun 'it' is NOT corrupted by prior turns."""
    history = [
        {"role": "user", "content": "How do I configure Azure Cosmos DB?"},
        {"role": "assistant", "content": "Azure Cosmos DB offers multi-region replication."},
    ]
    # Self-contained query mentioning AWS S3 explicitly
    query = "What is AWS S3 and how does it handle lifecycle expiration policies?"
    ctx = rewrite_contextual_query(query, chat_history=history)

    assert ctx.is_rewritten is False
    assert "cosmos" not in ctx.effective_dense_query.lower()
    assert "s3" in ctx.effective_dense_query.lower()


def test_cli_invocation_not_rewritten_by_history():
    """Verify direct CLI command invocations remain untransformed."""
    history = [
        {"role": "user", "content": "Tell me about Amazon DynamoDB tables."},
    ]
    cli_cmd = "kubectl get pods -n kube-system -l app=ingress"
    ctx = rewrite_contextual_query(cli_cmd, chat_history=history)

    assert ctx.is_rewritten is False
    assert ctx.original_query == cli_cmd
    assert ctx.effective_sparse_query == cli_cmd


def test_dual_stream_effective_query_properties():
    """Verify effective_dense_query expands context while effective_sparse_query preserves syntax."""
    history = [
        {"role": "user", "content": "Tell me about Amazon S3 glacier flexible retrieval."},
    ]
    query = "What are its retrieval tiers --tier Expedited?"
    ctx = rewrite_contextual_query(query, chat_history=history)

    assert ctx.is_rewritten is True
    # Dense gets entity context
    assert "s3" in ctx.effective_dense_query.lower()
    # Sparse preserves exact CLI flag
    assert "--tier Expedited" in ctx.effective_sparse_query
