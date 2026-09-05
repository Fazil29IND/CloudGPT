"""Unit tests for native HNSW (Hierarchical Navigable Small World) vector index."""

import tempfile
from pathlib import Path

import numpy as np
from retrieval.hnsw_index import HNSWIndex, _l2_normalize


def test_l2_normalize():
    vec = [3.0, 4.0]
    normed = _l2_normalize(vec)
    assert np.isclose(np.linalg.norm(normed), 1.0)

    zero_vec = [0.0, 0.0]
    normed_zero = _l2_normalize(zero_vec)
    assert np.allclose(normed_zero, 0.0)


def test_hnsw_add_and_search_exact_match():
    idx = HNSWIndex(dimension=4, m=4, ef_construction=16, ef_search=16, seed=42)

    # Insert 4 orthogonal vectors
    idx.add_item("item-x", [1.0, 0.0, 0.0, 0.0], text="Doc X", metadata={"provider": "aws"})
    idx.add_item("item-y", [0.0, 1.0, 0.0, 0.0], text="Doc Y", metadata={"provider": "gcp"})
    idx.add_item("item-z", [0.0, 0.0, 1.0, 0.0], text="Doc Z", metadata={"provider": "azure"})
    idx.add_item("item-w", [0.0, 0.0, 0.0, 1.0], text="Doc W", metadata={"provider": "aws"})

    assert len(idx.nodes) == 4
    assert idx.entry_point is not None

    # Query matching item-y
    results = idx.search([0.0, 1.0, 0.0, 0.0], top_k=2)
    assert len(results) == 2
    top_id, top_sim, top_text, top_meta = results[0]
    assert top_id == "item-y"
    assert np.isclose(top_sim, 1.0, atol=1e-4)
    assert top_text == "Doc Y"
    assert top_meta["provider"] == "gcp"


def test_hnsw_metadata_filtering():
    idx = HNSWIndex(dimension=4, m=4, ef_construction=16, ef_search=16, seed=42)

    idx.add_item("item-aws-1", [1.0, 0.0, 0.0, 0.0], metadata={"provider": "aws", "service": "s3"})
    idx.add_item("item-aws-2", [0.9, 0.1, 0.0, 0.0], metadata={"provider": "aws", "service": "ec2"})
    idx.add_item("item-gcp-1", [0.8, 0.2, 0.0, 0.0], metadata={"provider": "gcp", "service": "gcs"})

    # Query closest to item-aws-1, but filter for gcp
    results = idx.search([1.0, 0.0, 0.0, 0.0], top_k=5, filters={"provider": "gcp"})
    assert len(results) == 1
    assert results[0][0] == "item-gcp-1"

    # Multi-value provider filter
    results_multi = idx.search([1.0, 0.0, 0.0, 0.0], top_k=5, filters={"provider": ["aws"]})
    assert len(results_multi) == 2
    assert all("aws" in r[0] for r in results_multi)


def test_hnsw_save_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "test_hnsw.json"

        idx1 = HNSWIndex(dimension=3, m=4, ef_construction=16, ef_search=16, seed=7)
        idx1.add_item("node-1", [1.0, 0.0, 0.0], text="Alpha", metadata={"tag": "A"})
        idx1.add_item("node-2", [0.0, 1.0, 0.0], text="Beta", metadata={"tag": "B"})
        idx1.save(file_path)

        assert file_path.exists()

        idx2 = HNSWIndex.load(file_path)
        assert len(idx2.nodes) == 2
        assert idx2.dimension == 3
        assert "node-1" in idx2.nodes
        assert "node-2" in idx2.nodes

        res = idx2.search([1.0, 0.0, 0.0], top_k=1)
        assert len(res) == 1
        assert res[0][0] == "node-1"
        assert res[0][2] == "Alpha"


def test_hnsw_empty_index_returns_empty():
    idx = HNSWIndex(dimension=4)
    results = idx.search([1.0, 0.0, 0.0, 0.0], top_k=5)
    assert results == []
