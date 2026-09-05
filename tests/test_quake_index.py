"""Unit tests for Quake adaptive vector indexing system."""

import tempfile
from pathlib import Path

import numpy as np
from retrieval.quake_index import QuakeIndex


def test_quake_add_and_search():
    idx = QuakeIndex(dimension=4, num_partitions=4, min_probe=1, max_probe=3)

    idx.add_item("q1", [1.0, 0.0, 0.0, 0.0], text="AWS Lambda", metadata={"provider": "aws"})
    idx.add_item("q2", [0.0, 1.0, 0.0, 0.0], text="Google Cloud Functions", metadata={"provider": "gcp"})
    idx.add_item("q3", [0.0, 0.0, 1.0, 0.0], text="Azure Functions", metadata={"provider": "azure"})
    idx.add_item("q4", [0.0, 0.0, 0.0, 1.0], text="Cloud Run", metadata={"provider": "gcp"})

    assert len(idx) == 4
    assert len(idx.partitions) == 4

    # Search for AWS Lambda vector
    results = idx.search([1.0, 0.0, 0.0, 0.0], top_k=2)
    assert len(results) >= 1
    assert results[0][0] == "q1"
    assert np.isclose(results[0][1], 1.0, atol=1e-3)
    assert results[0][2] == "AWS Lambda"


def test_quake_cost_based_probe_tuning():
    idx = QuakeIndex(dimension=4, num_partitions=4, min_probe=1, max_probe=4)

    # 4 distinct orthogonal cluster centers
    idx.add_item("c1", [1.0, 0.0, 0.0, 0.0])
    idx.add_item("c2", [0.0, 1.0, 0.0, 0.0])
    idx.add_item("c3", [0.0, 0.0, 1.0, 0.0])
    idx.add_item("c4", [0.0, 0.0, 0.0, 1.0])

    # 1. Clear winner query (aligned exactly with c1)
    q_focused = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    probes_focused = idx._estimate_cost_and_probes(q_focused)
    assert len(probes_focused) == idx.min_probe

    # 2. Ambiguous query (equidistant between c1, c2, c3, c4)
    q_diffuse = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)
    probes_diffuse = idx._estimate_cost_and_probes(q_diffuse)
    assert len(probes_diffuse) > idx.min_probe


def test_quake_centroid_adaptation():
    idx = QuakeIndex(dimension=4, num_partitions=2, min_probe=1, adaptation_rate=0.1)

    idx.add_item("item-1", [1.0, 0.0, 0.0, 0.0])
    idx.add_item("item-2", [0.0, 1.0, 0.0, 0.0])

    p0_initial_centroid = idx.partitions[0].centroid.copy()

    # Query with a vector slightly offset from p0
    query = np.array([0.9, 0.1, 0.0, 0.0], dtype=np.float32)
    idx.search(query, top_k=1, adapt=True)

    # After search with adapt=True, p0 centroid should have shifted toward query
    p0_adapted_centroid = idx.partitions[0].centroid
    assert not np.allclose(p0_initial_centroid, p0_adapted_centroid)


def test_quake_dynamic_partition_split():
    idx = QuakeIndex(dimension=2, num_partitions=2, split_threshold=5)

    # Seed 2 partitions
    idx.add_item("seed-0", [1.0, 0.0])
    idx.add_item("seed-1", [0.0, 1.0])

    # Add 25 items to partition 0 with 2 distinct clusters: (1, 0) and (1, 0.5)
    for i in range(25):
        vec = [1.0, 0.05 * (i % 5)]
        idx.add_item(f"p0-item-{i}", vec)

    initial_partition_count = len(idx.partitions)

    # Query repeatedly to exceed split_threshold (5 hits)
    for _ in range(6):
        idx.search([1.0, 0.0], top_k=5, adapt=True)

    # Partition should have split!
    assert len(idx.partitions) > initial_partition_count


def test_quake_save_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "test_quake.json"

        idx1 = QuakeIndex(dimension=3, num_partitions=2)
        idx1.add_item("i1", [1.0, 0.0, 0.0], text="Doc 1", metadata={"cloud": "aws"})
        idx1.add_item("i2", [0.0, 1.0, 0.0], text="Doc 2", metadata={"cloud": "gcp"})
        idx1.save(file_path)

        assert file_path.exists()

        idx2 = QuakeIndex.load(file_path)
        assert len(idx2) == 2
        assert len(idx2.partitions) == 2

        results = idx2.search([1.0, 0.0, 0.0], top_k=1)
        assert len(results) == 1
        assert results[0][0] == "i1"
        assert results[0][2] == "Doc 1"
