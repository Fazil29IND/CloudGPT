# CloudGPT Redis Cache Key Schema (v1 vs v2)

This document formalizes the caching layer architecture and key schema before and after the optimization upgrade.

---

## Version 1 (Legacy Single-Layer)

Previously, CloudGPT used a single unversioned cache key for all synthesized LLM answers:

```
llm_cache:{SHA256(model:mode:provider_filter:normalized_query:history_hash)}
```

### Limitations of Version 1
1. **No layer separation**: If the corpus was updated, all prompt caches had to be flushed manually.
2. **No query plan caching**: Query router LLM calls were re-evaluated on every miss.
3. **No intermediate retrieval cache**: Candidate vector retrieval had to be re-queried even if only model prompts changed.
4. **Stampede risk**: When hot keys expired, concurrent requests caused thundering herds to Pinecone and LLM providers.

---

## Version 2 (Multi-Tier Versioned Hierarchy)

CloudGPT v2 implements distinct caching tiers with independent TTLs, versioning pointers, jitter, and in-process L1 LRU fallbacks:

### Layer 1 — Query Plan & Classification
Caches intent routing, detected services, and provider extraction from router models.
- **Key Pattern**: `rag:v2:plan:{router_version}:{SHA256(normalized_query)[:24]}`
- **Default TTL**: 900s (15 min) + 15% random jitter
- **Invalidation**: Increment `CACHE_ROUTER_VERSION` in settings.

### Layer 2 — Retrieval Results (Candidates)
Caches the candidate chunks before prompt synthesis.
- **Lite/Free Tier Pattern**: `rag:v2:retrieval:{SHA256(corpus_version:embedding_model:provider_filter:normalized_query)[:24]}`
- **Agentic RAG (Core/Pro) Pattern**: `agentic:{corpus_version}:{SHA256(query)[:16]}:{provider_filter}:{tier}`
- **Adaptive RAG (Apex/Max) Pattern**: `adaptive:{corpus_version}:{SHA256(query)[:16]}:{provider_filter}:{tier}`
- **Default TTL**: 21600s (6 hours) + 15% random jitter
- **Invalidation**: Increment `CACHE_CORPUS_VERSION` or `ACTIVE_CORPUS_VERSION` when documents are re-ingested.

### Layer 3 — Structured Stage Decisions
Caches intermediate stage decisions emitted by router models or planner agents (e.g. Agentic RAG planning JSON or Adaptive RAG query transformations).
- **Key Pattern**: `rag:v2:decision:{scope}:{producer_version}:{SHA256(scope:normalized_query)[:24]}`
- **Supported Scopes**: `agentic_plan`, `adaptive_transform`, `router_classification`
- **Default TTL**: 900s (15 min) + 15% random jitter
- **Invalidation**: Increment `CACHE_ROUTER_VERSION` in settings.

### Layer 4 — Multi-Level Stage Caches (Apex)
Caches intermediate multi-candidate transformations such as the cross-encoder reranked and sentence-compressed context in Adaptive RAG.
- **Key Pattern**: `rag:v2:stage:rerank:{corpus_version}:{SHA256(strategy:query)[:16]}:{SHA256(sorted_candidate_ids)[:16]}`
- **Default TTL**: 1800s (30 min) + 15% random jitter
- **Invalidation**: Increment `CACHE_CORPUS_VERSION` or when candidate set changes.

### Layer 5 — Final Synthesized Answer
Caches the final model response, thinking metadata, and source citations.
- **Key Pattern**: `rag:v2:answer:{SHA256(prompt_version:model:mode:corpus_version:provider_filter:normalized_query:history_hash)[:24]}`
- **Default TTL**: 86400s (24 hours) or configured `redis_cache_ttl_seconds` + 15% random jitter
- **Validation Gate**: Skipped when Layer-4 validation detects grounding failures (`skip_answer_cache_on_validation_failure`).
- **Feedback Boost**: Multiplies TTL by `feedback_boost_ttl_multiplier` (1.5x) when positive feedback exists.
- **Invalidation**: Increment `CACHE_PROMPT_VERSION` or `CACHE_CORPUS_VERSION`.

---

## Feedback-Driven Policy Records
Stores per-query user feedback markers that drive cache routing and serve-time decisions across all tiers.
- **Key Pattern**: `rag:v2:policy:{SHA256(normalized_query)[:24]}`
- **Default TTL**: 86400s (24 hours)
- **Rating -1 (Thumbs Down Penalty)**:
  - Bypasses exact and semantic answer-cache lookups.
  - Forbids answer-cache writes.
  - Clears L1 in-process answer cache immediately.
  - Leaves retrieval caches active (preserves evidence chunks).
- **Rating +1 (Thumbs Up Boost)**:
  - Multiplies answer-cache write TTL for future runs.

---

## Resilience & Stampede Control
- **Negative Caching**: `rag:v2:negative:{SHA256(query:provider_filter)[:24]}` with 30s TTL to prevent repeating dead queries.
- **Single-Flight Lock**: `rag:v2:lock:{answer_key_digest}` with a 20s atomic compare-and-delete lock token via Lua scripts.
- **Attachment Isolation**: Any query with multimodal or text attachments automatically disables exact/semantic lookup and answer caching to avoid cross-tenant context leaks.

---

## Vector Semantic Cache (Redis 8 & In-Memory Fallback)
Caches query embeddings with intent-aware similarity thresholds for near-duplicate question resolution before RAG pipeline execution.
- **Key Pattern**: `semcache:{corpus_version}:{prompt_version}:{SHA256(normalized_query)[:24]}`
- **Default TTL**: 3600s (1 hour)
- **Strategy & Intent-Aware Thresholds**:
  - Direct Fast (Apex CLI/Syntax): `0.97` (`adaptive_semantic_threshold_direct`)
  - Multi-Perspective (Apex Comparisons): `0.95` (`adaptive_semantic_threshold_multi_perspective`)
  - Pricing & Cost: `0.95` (reject near-matches that could output stale pricing)
  - Troubleshooting & Errors: `0.95` (strictly match exact error scenarios)
  - Conceptual & Comparisons: `0.90` (broader match tolerance for general overviews)
  - Default: `0.92`
- **Invalidation**: Increment `CACHE_CORPUS_VERSION` or `CACHE_PROMPT_VERSION`.

---

## Memory & Eviction Configuration
Configured in `docker-compose.yml`:
- **Engine**: Redis 8 (`redis:8-alpine`)
- **Memory Limit**: `maxmemory 512mb`
- **Eviction Policy**: `allkeys-lru` (least-recently-used eviction across cache-dominant keys)
- **Persistence**: Append-Only File (`--appendonly yes`)
- **Degradation**: All operations degrade safely to in-process memory caches (`core/memory_cache.py`, `core/semantic_cache.py`) if Redis is disabled or unreachable.
