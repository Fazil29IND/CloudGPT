"""
Evidence Ordering Benchmark Suite (evaluation/ordering_benchmark.py).

Benchmarks evidence ordering strategies (U-Curve, Score-Descending, Coherence-Preserving, Adaptive)
across multiple criteria:
1. Extremity Concentration (Primacy/Recency placement of top evidence)
2. Semantic Coherence Index (Cluster continuity of provider/service topics)
3. Latency & Execution Speed
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

# Ensure project root is in sys.path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.context_types import OrderingStrategy
from llm.evidence_ordering import EvidenceOrderingManager


def evaluate_ordering_strategy(
    chunks: list[dict[str, Any]],
    strategy: OrderingStrategy | str,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Evaluates a single ordering strategy on a set of candidate chunks."""
    start_t = time.perf_counter()
    reordered = EvidenceOrderingManager.reorder(
        chunks=chunks,
        strategy=strategy,
        model_name=model_name,
    )
    elapsed_ms = (time.perf_counter() - start_t) * 1000

    n = len(reordered)
    if n == 0:
        return {"strategy": str(strategy), "chunks_count": 0, "elapsed_ms": elapsed_ms}

    # Find position of highest-scored chunk
    max_score = -1.0
    best_orig_id = None
    for c in chunks:
        score = float(c.get("score") or c.get("relevance_score") or 0.0)
        if score > max_score:
            max_score = score
            best_orig_id = c.get("id") or c.get("chunk_id")

    best_final_pos = -1
    for idx, c in enumerate(reordered):
        cid = c.get("id") or c.get("chunk_id")
        if cid == best_orig_id:
            best_final_pos = idx
            break

    # Extremity score: distance from center towards either boundary (0 = dead center, 1 = absolute edge)
    center = (n - 1) / 2.0
    extremity_score = abs(best_final_pos - center) / center if center > 0 else 1.0

    # Topic coherence: count provider/service transitions
    transitions = 0
    for i in range(1, n):
        prev_prov = reordered[i - 1].get("provider")
        curr_prov = reordered[i].get("provider")
        if prev_prov != curr_prov:
            transitions += 1

    coherence_index = 1.0 - (transitions / (n - 1)) if n > 1 else 1.0

    return {
        "strategy": str(getattr(strategy, "value", strategy)),
        "chunks_count": n,
        "elapsed_ms": round(elapsed_ms, 3),
        "best_chunk_position": best_final_pos,
        "extremity_score": round(extremity_score, 3),
        "topic_coherence_index": round(coherence_index, 3),
    }


def run_benchmark_suite() -> dict[str, Any]:
    """Runs a full benchmark suite comparing all ordering strategies."""
    # Synthetic realistic cloud evidence set
    sample_chunks = [
        {"id": "c1", "provider": "aws", "service": "ec2", "score": 0.96, "content": "AWS EC2 m6i specs"},
        {"id": "c2", "provider": "aws", "service": "s3", "score": 0.88, "content": "AWS S3 standard tier"},
        {"id": "c3", "provider": "gcp", "service": "compute", "score": 0.94, "content": "GCP N2 machine types"},
        {"id": "c4", "provider": "gcp", "service": "gcs", "score": 0.82, "content": "GCP Cloud Storage"},
        {"id": "c5", "provider": "azure", "service": "vm", "score": 0.91, "content": "Azure D-series VMs"},
        {"id": "c6", "provider": "azure", "service": "blob", "score": 0.85, "content": "Azure Blob Hot Tier"},
        {"id": "c7", "provider": "aws", "service": "vpc", "score": 0.79, "content": "AWS VPC Peering"},
        {"id": "c8", "provider": "gcp", "service": "vpc", "score": 0.75, "content": "GCP VPC Network Peering"},
    ]

    strategies = [
        OrderingStrategy.SCORE_DESCENDING,
        OrderingStrategy.U_CURVE,
        OrderingStrategy.COHERENCE_PRESERVING,
        OrderingStrategy.ADAPTIVE,
    ]

    results = {}
    for strat in strategies:
        results[strat.value] = evaluate_ordering_strategy(sample_chunks, strat, model_name="gemini-2.5-pro")

    return results


if __name__ == "__main__":
    import json
    suite_results = run_benchmark_suite()
    print(json.dumps(suite_results, indent=2))
