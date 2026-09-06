# 3-Tier Cache Architecture Plan — Exact/Semantic/Retrieval Cascades, Decision Caches, Adaptive Cache Router & Feedback-Driven Policy

**Status:** Implemented (this document is the design record + acceptance map)
**Scope:** Re-architect caching per tier exactly as specified —
**Hybrid RAG (Lite/Free):** Exact Cache → Semantic Cache → Hybrid Retrieval Cache ·
**Agentic RAG (Core/Pro):** Semantic Cache → Structured Decision Cache → Retrieval/Tool Cache ·
**Adaptive Agentic RAG (Apex/Max):** Adaptive Cache Router + Multi-Level Stage-Aware Caching + Feedback-Driven Cache Policy.

---

## 0. Audit — the existing cache stack

| Asset | Key / location | Used in production today? |
|---|---|---|
| L1 in-process LRU (60s) | `core/memory_cache.py` | ✅ inside `get/set_cached_answer` |
| Exact answer cache (L1+Redis) | `rag:v2:answer:{…}` (`core/llm_cache.py` Layer 3) | ⚠️ **not consulted by the pipeline itself** — in-pipeline serving only happens *after* a semantic-cache hit; the `/api/chat` endpoint checks it separately (non-stream only); the SSE path never does |
| Semantic answer cache | in-mem cosine + `semcache:{corpus}:{prompt}:{hash}` (`core/semantic_cache.py`) | ✅ checked pre-dispatch in `execute_agent_pipeline` (all tiers), gated on no-attachments + short history |
| Plan cache | `rag:v2:plan:{router_version}:{hash}` | ❌ **dead code** — only touched by `evaluation/acceptance_check.py` |
| Retrieval cache | `rag:v2:retrieval:{…}` | ✅ Lite `_safe_rag` only |
| Tier retrieval caches | `agentic:{hash}:{provider}:{tier}`, `adaptive:{hash}:{prov}:{tier}` | ✅ Core/Apex — **latent bug: keys omit `cache_corpus_version`, so stale chunks survive corpus re-ingestion** |
| Negative retrieval cache | `rag:v2:negative:{…}` (30s) | ✅ Lite `_safe_rag` only |
| Tool cache | `tool_cache:{tool}:{hash}` | ⚠️ only `web_search` internal; pricing/calculator uncached |
| Single-flight lock | `rag:v2:lock:{digest}` (Lua CAS) | ✅ `/api/chat` non-stream only |
| User feedback | `message_feedback` table | ⚠️ stored but **never influences caches** — a thumbs-down answer keeps serving until TTL |

## 1. Target architecture

```
                         ┌────────────────────────────────────────────────┐
 Lite (Free)             │ ① EXACT CACHE  (L1 LRU → rag:v2:answer,        │
                         │   history-hash keyed — no embedding needed)    │
                         │ ② SEMANTIC CACHE (embedding cosine, only on ①miss)
                         │ ③ HYBRID RETRIEVAL CACHE (per-representation    │
                         │   retrieval keys + negative cache — serves the │
                         │   retrieval stage even when generation reruns) │
                         └────────────────────────────────────────────────┘
 Core (Pro)              │ ① SEMANTIC CACHE (pre-dispatch, unchanged)     │
                         │ ② STRUCTURED DECISION CACHE (agentic plan JSON │
                         │   via rag:v2:decision:* — planning LLM skipped)│
                         │ ③ RETRIEVAL/TOOL CACHE (corpus-versioned agentic│
                         │   keys + tool_cache for pricing lookups)       │
                         └────────────────────────────────────────────────┘
 Apex (Max)              │ ADAPTIVE CACHE ROUTER (per-query policy from   │
                         │   strategy/risk/confidence → which levels to   │
                         │   consult, thresholds, write permissions)      │
                         │ MULTI-LEVEL STAGE-AWARE CACHING (transform     │
                         │   decision, retrieval, rerank+compress stage,  │
                         │   answer — each versioned + independently      │
                         │   flag-gated)                                  │
                         │ FEEDBACK-DRIVEN CACHE POLICY (thumbs down →    │
                         │   penalty marker: bypass answer caches;        │
                         │   thumbs up → boost marker: longer write TTL;  │
                         │   validation failures → skip answer caching)   │
                         └────────────────────────────────────────────────┘
```

### Requested stage → implementation map

**Hybrid RAG (Lite)** — `api/chat_routes.py::execute_agent_pipeline` (Free branch) + `_safe_rag`
1. **Exact Cache (NEW first hop):** `get_cached_answer(query, history_hash=…)` consulted *before* any embedding is computed. Hit → serve immediately (`pipeline_type="exact_cache"`, `CACHE_CASCADE_HITS{tier,layer="exact"}`). History hash computed with the same algorithm the endpoint uses, so multi-turn queries don't false-hit. Skipped for attachment requests (context-dependent).
2. **Semantic Cache (existing, repositioned):** runs only on exact miss → the per-request embedding call is now spent only when it can pay off. Hit → serve (`pipeline_type="semantic_cache"`).
3. **Hybrid Retrieval Cache:** `_safe_rag` now consults the versioned retrieval cache for **each query representation** actually used (original + contextual rewrite) and keeps the negative cache; on answer-cache misses the retrieval stage is still served from cache so only synthesis re-runs.

**Agentic RAG (Core)** — `retrieval/agentic_rag.py`
1. **Semantic Cache:** pre-dispatch (shared path, unchanged).
2. **Structured Decision Cache (NEW):** `_plan_and_route` consults `rag:v2:decision:{scope=agentic_plan}:{router_version}:{hash}`; hit → the planning LLM call is skipped entirely; miss → plan JSON cached (TTL `core_decision_cache_ttl_seconds`, jittered, version-invalidated via `CACHE_ROUTER_VERSION`).
3. **Retrieval/Tool Cache:** the `agentic:*` retrieval key becomes corpus-versioned (bug fix: `agentic:{corpus_version}:{hash}:{provider}:{tier}`); `fetch_cloud_pricing` results cached through `core/tool_cache` (`tool_cache:cloud_pricing:{providers:query}`), calculator stays uncached (deterministic, sub-ms).

**Adaptive Agentic RAG (Apex)** — `retrieval/adaptive_rag.py` + new `core/cache_policy.py`
1. **Adaptive Cache Router (NEW):** `core/cache_policy.py::route_cache_policy(query, strategy, classification, tier, settings)` returns a `CachePolicyDecision`: which levels to consult (`exact`/`semantic`/stage levels), a strategy-aware semantic threshold (`direct_fast` → `adaptive_semantic_threshold_direct` 0.97 so CLI queries differing by one flag never cross-match; `multi_perspective` → 0.95; `semantic_hyde` → intent defaults), and write permissions/TTL multipliers. The pre-dispatch semantic check is **bypassed for Apex** when the router is enabled (the pipeline consults caches itself, post-transform, when it actually knows the strategy).
2. **Multi-Level Stage-Aware Caching (NEW):** every Apex stage gets its own versioned, flag-gated cache — Stage 1 transform decision (`rag:v2:decision:{scope=adaptive_transform}`), Stage 2 retrieval (corpus-versioned `adaptive:` keys — bug fix), Stage 3 rerank+compress output (`rag:v2:stage:rerank:{corpus_version}:{digest(strategy+query+candidate ids)}`, short TTL), Stage 4 answer (existing, written only when the policy + validation allow).
3. **Feedback-Driven Cache Policy (NEW):** `core/cache_policy.py` — thumbs **down** writes a penalty record `rag:v2:policy:{digest}` (TTL `feedback_penalty_ttl_seconds`): the exact + semantic layers refuse to serve that query (retrieval cache stays — the evidence wasn't wrong, the synthesis was) and the L1 entry is invalidated; thumbs **up** writes a boost record that multiplies answer-cache write TTL by `feedback_boost_ttl_multiplier`. Additionally, when the Layer-4 output validation fails grounding, the answer is **not cached at all** (`skip_answer_cache_on_validation_failure`) — bad answers never enter the cache to begin with. The feedback endpoint now feeds this loop via a new `db.get_message_feedback_context(message_id, user_id)` lookup (db.py owns all SQL).

---

## 2. Files

| File | Change |
|---|---|
| `core/cache_policy.py` | NEW — CachePolicyDecision, `route_cache_policy`, policy records (`record_feedback_policy`, `get_cached_query_policy`, `clear_cached_query_policy`), validation-aware write gating |
| `core/llm_cache.py` | add decision cache (`get/set_cached_decision`, scope-keyed), policy-record helpers, extend `invalidate_corpus_cache` |
| `api/chat_routes.py` | Lite 3-stage cascade; Apex pre-dispatch bypass; feedback endpoint → policy wiring; validation-gated answer caching |
| `retrieval/agentic_rag.py` | decision cache in `_plan_and_route`; corpus-versioned retrieval key; tool-cache for pricing |
| `retrieval/adaptive_rag.py` | cache-router integration; transform decision cache; rerank stage cache; policy-aware answer caching |
| `db.py` | `get_message_feedback_context(message_id, user_id)` |
| `config.py` + `.env.example` | new Fields (below) |
| `metrics.py` | `CACHE_CASCADE_HITS`, `CACHE_POLICY_EVENTS` |
| `evaluation/CACHE_KEY_SCHEMA.md` | document decision / stage / policy key namespaces |
| `tests/test_cache_policy.py` | NEW |

## 3. New settings (all flag-gated, safe defaults)

`enable_lite_exact_cache` (True) · `enable_core_decision_cache` (True) ·
`core_decision_cache_ttl_seconds` (900) · `enable_core_tool_cache` (True) ·
`enable_adaptive_cache_router` (True) · `enable_apex_stage_caches` (True) ·
`apex_stage_cache_ttl_seconds` (1800) · `adaptive_semantic_threshold_direct` (0.97) ·
`adaptive_semantic_threshold_multi_perspective` (0.95) · `enable_feedback_cache_policy` (True) ·
`feedback_penalty_ttl_seconds` (86400) · `feedback_boost_ttl_multiplier` (1.5) ·
`skip_answer_cache_on_validation_failure` (True)

## 4. Non-negotiables honored

- All config through `config.py` Fields + `.env.example`; Redis optional everywhere (every
  new helper degrades to no-op/in-memory when `redis_client.is_available` is False).
- Cache keys follow the documented v2 schema; `evaluation/CACHE_KEY_SCHEMA.md` updated.
- structlog/logging + `metrics.py` counters for all policy decisions; no behavior change
  on the SSE event contract (cache hits ride the existing `pipeline_type`/timings fields).
- All existing tests pass unchanged; new tests are hermetic (no Redis required).
