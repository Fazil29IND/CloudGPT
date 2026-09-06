# CloudGPT RAG & Cache Architecture — Acceptance Report

**Date:** 2026-09-06 22:13:41Z  
**Active Corpus Version:** `v2`  
**Cache Schema:** `rag:v2:*` (3-Layer Versioned)  
**Embedding Model:** `text-embedding-004` (Dimension: 768)  
**BM25 Lexical Engine:** `BM25S (Robertson)`  

> **How to read this report:** gate statuses are computed from automated
> checks at generation time. The checks are structural/unit-level — they
> do not constitute production verification. Performance sections list
> configured budgets; measured figures are only shown when explicitly
> evaluated with live dependencies.

---

## 1. Verification Gates — 10/10 passed

| Gate / Component | Status | Verification Criteria |
| :--- | :--- | :--- |
| Configuration & Versioning | ✅ **PASSED** | Active corpus v1/v2, 3-layer version keys, latency budgets |
| Redis Cache & Single-Flight Locks | ✅ **PASSED** | SET NX EX lock acquisition, Lua atomic release, TTL jitter |
| Session Cache Pipelining | ✅ **PASSED** | Single round-trip pipelined warm-up and batch write |
| Parent-Child Semantic Chunker | ✅ **PASSED** | 1200-token parents, 450-token children, code/table boundary safety |
| Idempotent State & Dead-Letter Queue | ✅ **PASSED** | Content hash skip checking, DLQ error logging & replay |
| BM25S Lexical Vector Sparse Retrieval | ✅ **PASSED** | Robertson BM25 fitted vocabulary, Pinecone sparse vectors |
| Hybrid Multi-Namespace Retrieval | ✅ **PASSED** | Multi-namespace fan-out, adaptive alpha weights, URL dedup |
| 30-Question Golden Evaluation Set | ✅ **PASSED** | Labeled questions across exact, conceptual, troubleshooting, and IaC |
| model_version_config | ✅ **PASSED** | Automated check |
| model_eval_importable | ✅ **PASSED** | Automated check |

---

## 2. 3-Layer Versioned Redis Cache Hierarchy

```
rag:v2:
  ├── plan:{router_version}:{hash(query)}                         [TTL: 86400s (24h) + jitter]
  ├── retrieval:{hash(corpus_v:embedding_model:filter:query)}     [TTL: 21600s (6h)  + jitter]
  ├── answer:{hash(prompt_v:model:mode:corpus_v:filter:query)}    [TTL: 3600s  (1h)  + jitter]
  ├── negative:{hash(query:filter)}                              [TTL: 30s]
  └── lock:{hash(answer_key)}                                    [TTL: 20s (single-flight)]
```

---

## 3. Retrieval Alpha Configuration Matrix

| Alpha (Dense Weight) | Sparse Weight (BM25S) | Target Workload | Measured Recall@10 |
| :--- | :--- | :--- | :--- |
| **0.30** | 0.70 | CLI flags, Status codes, Errors, Exact identifiers | not measured — run `--with-retrieval-eval` |
| **0.50** | 0.50 | Balanced conceptual comparison | not measured — run `--with-retrieval-eval` |
| **0.70** | 0.30 | Broad architectural questions & explanations | not measured — run `--with-retrieval-eval` |

---

## 4. Latency Budgets (Configured Targets)

Enforced in CI by `tests/load/test_latency_gates.py` (in-process p95 gate).
Live per-stage latency is observable via Prometheus `rag_stage_duration_seconds`.

| Stage | Configured Budget | Fallback Mechanism |
| :--- | :--- | :--- |
| Query Classification | 300 ms | Rule-based regex router |
| Hybrid Vector Retrieval | 300 ms | Pass 2 / Pass 3 global search |
| Reranking (BGE) | 400 ms | Top-k truncation |
| Web Search (Freshness Gated) | 2000 ms | DuckDuckGo fallback / RAG only |
| Context Assembly | 50 ms | Direct template injection |

---

## 5. Implementation Record (Phase 0 – Phase 5)

Delivered engineering work items. This is a scope record, **not** a
verification claim — verification status is defined exclusively by the
gate table in section 1 and the test suite.

- Per-Stage Latency Histograms & Telemetry
- Corpus & Baseline Snapshot Tool
- Freshness-Gated Internet Search
- 3-Layer Versioned Redis Cache Schema
- Single-Flight Distributed Lock with Jitter
- Metadata Pre-Filter 3-Pass Fallback Sequence
- Async HTTP Document Fetcher with Rate Limiting
- Provider-Specific HTML to Markdown Normalizer
- Token-Budgeted Parent-Child Chunker
- Versioned Namespace Ingestion & Atomic Promotion CLI
- BM25S Lexical Vector Sparse Retrieval Indexing
- Multi-Namespace Fan-Out & Canonical URL Deduplication
- Golden Evaluation Set & Alpha Tuning
- Retrieval Result Caching Layer
- Negative Result Short-Lived Caching (30s TTL)
- Redis Telemetry Grafana Dashboard & Prometheus Alerts
- End-to-End Latency Budget Enforcement
- Ingestion Dead-Letter Queue Tracking & Replay CLI
- Automated Latency Gate & Performance Test Suite
- Acceptance Verification & Report Generation (this document)

---

## 6. Implementation Status Taxonomy

Subsystem-level status lives in `docs/IMPLEMENTATION_STATUS.md` using the
five-level scale: Architecture Only → Mock → Implemented → Tested →
Production Verified.
