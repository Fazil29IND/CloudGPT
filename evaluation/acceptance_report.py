"""
Acceptance Report Generator for CloudGPT RAG & Cache Architecture.

Runs acceptance verification and generates evaluation/ACCEPTANCE_REPORT.md.

Honesty contract of this report:
- Every gate status is computed from `evaluation/acceptance_check.run_acceptance_checks()`.
- No performance number is hardcoded. The alpha matrix lists *configured
  weights and the workloads they target*; measured recall only appears when
  this script is run with `--with-retrieval-eval` against a live index.
- Latency figures list *configured budgets* only; measured gates live in
  tests/load/test_latency_gates.py (p95 budget enforced in CI).
- The task list is an implementation record, not a verification claim.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import get_settings
from evaluation.acceptance_check import run_acceptance_checks


async def _run_retrieval_eval() -> list[dict] | None:
    """Optionally measure real recall@10 against the live index."""
    try:
        from evaluation.retrieval_eval import evaluate_alphas

        return await evaluate_alphas()
    except Exception as exc:
        print(f"Retrieval evaluation unavailable ({exc}); omitting measured recall.")
        return None


async def generate_report(with_retrieval_eval: bool = False) -> str:
    settings = get_settings()
    acceptance_results = await run_acceptance_checks()

    total_gates = len(acceptance_results)
    passed_gates = sum(1 for passed in acceptance_results.values() if passed)

    eval_metrics: list[dict] | None = None
    if with_retrieval_eval:
        eval_metrics = await _run_retrieval_eval()

    report_lines = [
        "# CloudGPT RAG & Cache Architecture — Acceptance Report",
        "",
        f"**Date:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%SZ')}  ",
        f"**Active Corpus Version:** `{getattr(settings, 'active_corpus_version', 'v1')}`  ",
        f"**Cache Schema:** `rag:v2:*` (3-Layer Versioned)  ",
        f"**Embedding Model:** `{settings.embedding_model}` (Dimension: {settings.embedding_dimension})  ",
        f"**BM25 Lexical Engine:** `BM25S (Robertson)`  ",
        "",
        "> **How to read this report:** gate statuses are computed from automated",
        "> checks at generation time. The checks are structural/unit-level — they",
        "> do not constitute production verification. Performance sections list",
        "> configured budgets; measured figures are only shown when explicitly",
        "> evaluated with live dependencies.",
        "",
        "---",
        "",
        f"## 1. Verification Gates — {passed_gates}/{total_gates} passed",
        "",
        "| Gate / Component | Status | Verification Criteria |",
        "| :--- | :--- | :--- |",
    ]

    gate_descriptions = {
        "config_versions": ("Configuration & Versioning", "Active corpus v1/v2, 3-layer version keys, latency budgets"),
        "redis_cache_and_locking": ("Redis Cache & Single-Flight Locks", "SET NX EX lock acquisition, Lua atomic release, TTL jitter"),
        "session_pipelining": ("Session Cache Pipelining", "Single round-trip pipelined warm-up and batch write"),
        "parent_child_chunker": ("Parent-Child Semantic Chunker", "1200-token parents, 450-token children, code/table boundary safety"),
        "ingestion_state_and_dlq": ("Idempotent State & Dead-Letter Queue", "Content hash skip checking, DLQ error logging & replay"),
        "bm25_sparse_vectors": ("BM25S Lexical Vector Sparse Retrieval", "Robertson BM25 fitted vocabulary, Pinecone sparse vectors"),
        "hybrid_retriever_fanout": ("Hybrid Multi-Namespace Retrieval", "Multi-namespace fan-out, adaptive alpha weights, URL dedup"),
        "golden_eval_set": ("30-Question Golden Evaluation Set", "Labeled questions across exact, conceptual, troubleshooting, and IaC"),
    }

    for key, passed in acceptance_results.items():
        name, desc = gate_descriptions.get(key, (key, "Automated check"))
        badge = "✅ **PASSED**" if passed else "❌ **FAILED**"
        report_lines.append(f"| {name} | {badge} | {desc} |")

    report_lines.extend([
        "",
        "---",
        "",
        "## 2. 3-Layer Versioned Redis Cache Hierarchy",
        "",
        "```",
        "rag:v2:",
        "  ├── plan:{router_version}:{hash(query)}                         [TTL: 86400s (24h) + jitter]",
        "  ├── retrieval:{hash(corpus_v:embedding_model:filter:query)}     [TTL: 21600s (6h)  + jitter]",
        "  ├── answer:{hash(prompt_v:model:mode:corpus_v:filter:query)}    [TTL: 3600s  (1h)  + jitter]",
        "  ├── negative:{hash(query:filter)}                              [TTL: 30s]",
        "  └── lock:{hash(answer_key)}                                    [TTL: 20s (single-flight)]",
        "```",
        "",
        "---",
        "",
        "## 3. Retrieval Alpha Configuration Matrix",
        "",
        "| Alpha (Dense Weight) | Sparse Weight (BM25S) | Target Workload | Measured Recall@10 |",
        "| :--- | :--- | :--- | :--- |",
    ])

    alpha_targets = [
        (0.30, 0.70, "CLI flags, Status codes, Errors, Exact identifiers"),
        (0.50, 0.50, "Balanced conceptual comparison"),
        (0.70, 0.30, "Broad architectural questions & explanations"),
    ]
    measured_by_alpha: dict[float, dict] = {}
    if eval_metrics:
        for m in eval_metrics:
            measured_by_alpha[float(m.get("alpha", -1))] = m

    for alpha, sparse, workload in alpha_targets:
        m = measured_by_alpha.get(alpha)
        measured = f"{m['recall_at_10']:.1f}%" if m and isinstance(m.get("recall_at_10"), (int, float)) else "not measured — run `--with-retrieval-eval`"
        report_lines.append(f"| **{alpha:.2f}** | {sparse:.2f} | {workload} | {measured} |")

    report_lines.extend([
        "",
        "---",
        "",
        "## 4. Latency Budgets (Configured Targets)",
        "",
        "Enforced in CI by `tests/load/test_latency_gates.py` (in-process p95 gate).",
        "Live per-stage latency is observable via Prometheus `rag_stage_duration_seconds`.",
        "",
        "| Stage | Configured Budget | Fallback Mechanism |",
        "| :--- | :--- | :--- |",
        "| Query Classification | 300 ms | Rule-based regex router |",
        "| Hybrid Vector Retrieval | 300 ms | Pass 2 / Pass 3 global search |",
        "| Reranking (BGE) | 400 ms | Top-k truncation |",
        "| Web Search (Freshness Gated) | 2000 ms | DuckDuckGo fallback / RAG only |",
        "| Context Assembly | 50 ms | Direct template injection |",
        "",
        "---",
        "",
        "## 5. Implementation Record (Phase 0 – Phase 5)",
        "",
        "Delivered engineering work items. This is a scope record, **not** a",
        "verification claim — verification status is defined exclusively by the",
        "gate table in section 1 and the test suite.",
        "",
        "- Per-Stage Latency Histograms & Telemetry",
        "- Corpus & Baseline Snapshot Tool",
        "- Freshness-Gated Internet Search",
        "- 3-Layer Versioned Redis Cache Schema",
        "- Single-Flight Distributed Lock with Jitter",
        "- Metadata Pre-Filter 3-Pass Fallback Sequence",
        "- Async HTTP Document Fetcher with Rate Limiting",
        "- Provider-Specific HTML to Markdown Normalizer",
        "- Token-Budgeted Parent-Child Chunker",
        "- Versioned Namespace Ingestion & Atomic Promotion CLI",
        "- BM25S Lexical Vector Sparse Retrieval Indexing",
        "- Multi-Namespace Fan-Out & Canonical URL Deduplication",
        "- Golden Evaluation Set & Alpha Tuning",
        "- Retrieval Result Caching Layer",
        "- Negative Result Short-Lived Caching (30s TTL)",
        "- Redis Telemetry Grafana Dashboard & Prometheus Alerts",
        "- End-to-End Latency Budget Enforcement",
        "- Ingestion Dead-Letter Queue Tracking & Replay CLI",
        "- Automated Latency Gate & Performance Test Suite",
        "- Acceptance Verification & Report Generation (this document)",
        "",
        "---",
        "",
        "## 6. Implementation Status Taxonomy",
        "",
        "Subsystem-level status lives in `docs/IMPLEMENTATION_STATUS.md` using the",
        "five-level scale: Architecture Only → Mock → Implemented → Tested →",
        "Production Verified.",
        "",
    ])

    report_content = "\n".join(report_lines)
    report_file = BASE_DIR / "evaluation" / "ACCEPTANCE_REPORT.md"
    report_file.write_text(report_content, encoding="utf-8")
    print(f"Acceptance report generated at: {report_file}")
    return report_content


def main() -> None:
    parser = argparse.ArgumentParser(description="CloudGPT acceptance report generator")
    parser.add_argument(
        "--with-retrieval-eval",
        action="store_true",
        help="Measure real recall@10 against the live index (requires credentials)",
    )
    args = parser.parse_args()
    asyncio.run(generate_report(with_retrieval_eval=args.with_retrieval_eval))


if __name__ == "__main__":
    main()
