# 3-Tier RAG Upgrade Plan — Query Representation, Retrieval, Context Assembly & Validated Generation

**Status:** Implemented (this document is both the design record and the acceptance map)
**Scope:** Upgrade all three tier pipelines so every tier covers the four RAG layers —
①Query Representation ②Retrieval ③Context Assembly ④Generation & Validation — with
tier-appropriate depth: **Hybrid RAG (Lite/Free)**, **Agentic RAG (Core/Pro)**,
**Adaptive Agentic RAG (Apex/Max/Developer)**.

---

## 0. Audit summary (what existed before this work)

| Layer | Lite (Free) | Core (Pro) | Apex (Max) |
|---|---|---|---|
| ① Query repr. | ✅ `retrieval/query_processor.py` — conditional rewrite + original preservation (`QueryContext`) | ✅ `retrieval/agentic_rag.py::_build_plan_guided_representation` | ✅ `retrieval/adaptive_rag.py::_transform_query` (direct_fast / semantic_hyde / multi_perspective) + feedback loop (`_evaluate_retrieval_feedback`, `_replan_retrieval`) |
| ② Retrieval | ✅ `retrieval/hybrid.py` — parallel BM25 + Dense ANN → RRF → `Reranker` (query-adaptive weights) | ✅ agent-planned iterative retrieval, conditional fusion (RRF only when modality=hybrid), rerank, sufficiency-driven refinement hop | ✅ adaptive retriever routing (HyDE via Quake), query-aware fusion weights, feedback-driven re-retrieval |
| ③ Context | ❌ no compression in Lite path; assembly via shared `ContextBuilder` | ⚠️ grading + hierarchical expansion only — no task-aware compression, no plan-aware assembly | ⚠️ `_compress_chunk` threshold policy only — no strategy-aware policy, no dynamic assembly |
| ④ Generation | ❌ generate → sanitize, nothing else | ⚠️ LLM self-critique only — no claim-level verification, no claim→source attribution, no validation-driven retry | ⚠️ Live Verify only — no policy-aware prompt, no abstention/retry, no claim-level validation loop |

**Conclusion:** Layers ①–② were already in place. This work delivers Layer ③ (evidence
selection + compression + assembly) and Layer ④ (claim-level verification, citation
attribution, multi-dimensional output validation, retry/abstention policies) as a shared
`generation/` package wired differently per tier.

---

## 1. Target architecture

```
                                    ┌──────────────────────────────────────────────┐
 ── ① QUERY ────────────────────── │ Lite: conditional rewrite + preserved original│
                                    │ Core: plan-guided dense/sparse representations│
                                    │ Apex: multi-strategy (fast/HyDE/perspectives) │
                                    └──────────────────────────────────────────────┘
 ── ② RETRIEVAL ────────────────── │ Lite: BM25 ∥ Dense ANN → RRF → strong reranker│
                                    │ Core: agent-planned iterative → conditional    │
                                    │       fusion → rerank → evidence loop         │
                                    │ Apex: adaptive routing → adaptive fusion →    │
                                    │       adaptive rerank → feedback re-retrieval │
                                    └──────────────────────────────────────────────┘
 ── ③ CONTEXT (NEW) ────────────── │ generation/compression.py                     │
                                    │   lite  = conditional extractive/contextual   │
                                    │   core  = task-aware selective (intent-aware) │
                                    │   apex  = strategy-adaptive policy            │
                                    │ generation/assembly.py                        │
                                    │   lite  = fixed evidence-aware ordering       │
                                    │   core  = plan-aware (sub-goal coverage)      │
                                    │   apex  = dynamic (strategy-ordered + budget) │
                                    └──────────────────────────────────────────────┘
 ── ④ GENERATION & VALIDATION (NEW)
                                    │ grounded generation (tier system prompt)      │
                                    │ generation/claims.py     — deterministic      │
                                    │   claim extraction + entailment/support +     │
                                    │   claim→chunk→source citation mapping         │
                                    │ generation/validator.py  — multi-dimensional  │
                                    │   validation (grounding, citations,           │
                                    │   completeness, format, safety) + retry/      │
                                    │   abstention policies + LLM claim check       │
                                    │ generation/policy.py     — Apex dynamic       │
                                    │   policy digest prompt                        │
                                    │   Lite: deterministic checks, zero added LLM  │
                                    │   Core: verify + bounded agent retry (1)      │
                                    │   Apex: adaptive verify + retry/abstain (2)   │
                                    └──────────────────────────────────────────────┘
```

### Tier contracts (requested stage → implementation)

**Hybrid RAG (Lite / Free tier)** — `api/chat_routes.py` (`_gather_pipeline_context` + free branch of `execute_agent_pipeline`)
1. Parallel BM25 + Dense ANN → RRF → strong reranker — *existing* (`HybridRetriever`, `Reranker` w/ Pinecone-rerank fallback + source-authority boost).
2. **Top-K reranked passages + conditional extractive/contextual compression** — NEW `generation/compression.py::compress_evidence(policy="lite")`; fires only when evidence tokens exceed `lite_compression_token_threshold`; tables/code/parent chunks preserved (contextual guard).
3. **Fixed evidence-aware assembly** — `generation/assembly.py::order_fixed_evidence` (stale demotion, score order) + existing token-budgeted `ContextBuilder` packing.
4. **Structured grounded prompt** — existing `LITE_TIER_SYSTEM_PROMPT` structure.
5. **Grounded generation + minimal deterministic cleanup** — `generation/validator.py::deterministic_cleanup` (strip conversational fillers, collapse whitespace) after `sanitize_model_output`.
6. **Claim-level evidence entailment/support check** — `generation/claims.py::assess_claim_support` (deterministic lexical entailment; zero added LLM latency on Free).
7. **Claim-level source-to-chunk citation mapping** — `CitationManager.register_source(chunk_id=…)` + `claims.map_claim_sources`.
8. **Multi-dimensional output validation + validated grounded response** — `generation/validator.py::validate_output` (grounding / citations / completeness / format / safety) → flags in `PipelineResult.validation`, deterministic caveat only when evidence exists and support < `lite_min_claim_support`.

**Agentic RAG (Core / Pro tier)** — `retrieval/agentic_rag.py`
1. Plan-guided dynamic query representation — *existing*.
2. Agent-planned iterative retrieval → conditional fusion → rerank → evidence loop — *existing* (multi-hop sub-queries, modality-routed fusion, grade→sufficiency→refine hop).
3. **Agent-guided evidence selection** — grading + sufficiency (existing) followed by NEW task-aware selection in compression.
4. **Task-aware selective compression** — `compress_evidence(policy="core", task_intent=plan["intent"])`: intent profiles keep commands/quotas/prices/spec sentences, preserve tables/parents/code.
5. **Plan-aware evidence assembly** — `generation/assembly.py::assemble_plan_aware_evidence` orders evidence by sub-goal coverage and emits a plan digest appended to the prompt (`policy_digest`).
6. **Plan/task-aware agent prompt + evidence-guided generation with iterative reasoning** — Core system prompt + plan digest; existing iterative reasoning via critique.
7. **Structured answer refinement** — existing `SELF_CRITIQUE_PROMPT` pass (confidence-gated).
8. **Claim-level verification + evidence sufficiency check** — deterministic support check; when grounding fails and evidence exists, optional batched LLM entailment (`CLAIM_VERIFICATION_PROMPT`) via evaluator model; answer-level sufficiency gate `core_min_answer_support`.
9. **Evidence-aware claim-level citation attribution** — claim→chunk→source-number mapping stored in `PipelineResult.validation["claim_sources"]`; sources registered with `chunk_id`.
10. **Multi-dimensional validation + agent retry** — `validate_output` + bounded regeneration (`core_generation_max_retries`) with feedback prompt; accept-with-caveat fallback.
11. **Validated response after evidence loop.**

**Adaptive Agentic RAG (Apex / Max / Developer tier)** — `retrieval/adaptive_rag.py`
1. Adaptive multi-strategy query representation + retrieval feedback loop — *existing*.
2. **Adaptive retriever routing → query-aware fusion → adaptive reranking** — NEW strategy-aware rerank (`enable_adaptive_reranking`): rerank query per strategy (sparse-preserving for `direct_fast`, rewritten for `semantic_hyde`, per-dimension round-robin for `multi_perspective`); **adaptive evidence selection** (`enable_adaptive_evidence_selection`) with per-strategy caps + dimension diversity.
3. **Adaptive compression policy** — `_compress_chunk(strategy=…)` thresholds per strategy.
4. **Dynamic context assembly** — `generation/assembly.py::assemble_dynamic_evidence`: strategy-ordered evidence + dynamic budget multiplier into the tier prompt budget.
5. **Dynamic policy-aware prompt** — `generation/policy.py::build_policy_digest` (`<pipeline_policy>` block: strategy, complexity, staleness, live-verify, validation policy).
6. **Adaptive grounded generation with abstention/retry decisions** — pre-generation abstention when evidence is empty/low-confidence and Live Verify produced nothing; post-generation adaptive loop.
7. **Adaptive claim-level verification + retry/abstain policy** — deterministic support + LLM check when ambiguous; retry up to `adaptive_generation_max_retries`, abstain (`build_abstention_answer`) when support floor `adaptive_abstain_min_support` still fails; engages only for substantive answers with ≥ `min_assessable_claims`.
8. **Verified claim-level citation attribution with adaptive re-checking** — same mapping machinery; re-checked after every retry.
9. **Adaptive multi-dimensional validation + policy-based retry/abstention → validated response.**

---

## 2. Files

| File | Change |
|---|---|
| `generation/__init__.py` | NEW — public exports |
| `generation/claims.py` | NEW — claim extraction, deterministic entailment/support scoring, claim→chunk→source mapping |
| `generation/compression.py` | NEW — lite/core extractive compression policies (tables/code/parents preserved) |
| `generation/assembly.py` | NEW — fixed / plan-aware / dynamic evidence assembly |
| `generation/validator.py` | NEW — multi-dimensional validation, deterministic cleanup, retry messages, abstention answer, LLM claim check |
| `generation/policy.py` | NEW — Apex dynamic policy digest + pre-generation abstention decision |
| `citations/citation_manager.py` | additive `chunk_id` on `register_source`, `source_number_for_chunk()` |
| `llm/system_prompts.py` | add `CLAIM_VERIFICATION_PROMPT`, `GENERATION_RETRY_PROMPT` |
| `llm/context_builder.py` | additive `policy_digest` kwarg → `<pipeline_policy>` block in system message |
| `api/chat_routes.py` | Lite wiring (compression in `_safe_rag`; validation in free branch); `_build_pipeline_messages` additive kwarg; `PipelineResult.validation` field |
| `retrieval/agentic_rag.py` | Core wiring (compression, plan-aware assembly, chunk_id sources, verification+retry loop) |
| `retrieval/adaptive_rag.py` | Apex wiring (adaptive rerank/select/compress, policy digest, dynamic assembly, retry/abstain loop) |
| `config.py` + `.env.example` | new Fields (below) |
| `metrics.py` | `OUTPUT_VALIDATION_TOTAL`, `CLAIM_SUPPORT_SCORE`, `GENERATION_RETRIES_TOTAL`, `GENERATION_ABSTENTIONS_TOTAL` |
| `tests/test_generation_layer.py` | NEW — unit + wiring tests |

## 3. New settings (all flag-gated, safe defaults)

`enable_lite_evidence_compression` (True) · `lite_compression_token_threshold` (3000) ·
`enable_lite_output_validation` (True) · `lite_min_claim_support` (0.5) ·
`enable_core_task_aware_compression` (True) · `enable_core_plan_aware_assembly` (True) ·
`enable_core_output_validation` (True) · `enable_core_claim_verification` (True) ·
`core_generation_max_retries` (1) · `core_min_claim_support` (0.55) ·
`enable_adaptive_reranking` (True) · `enable_adaptive_evidence_selection` (True) ·
`enable_adaptive_output_validation` (True) · `adaptive_generation_max_retries` (2) ·
`adaptive_min_claim_support` (0.6) · `adaptive_abstain_min_support` (0.3) ·
`enable_dynamic_policy_prompt` (True) · `validation_min_assessable_claims` (3)

## 4. Non-negotiables honored

- All knobs go through `config.py` Settings with `Field(...)` + `.env.example` entries.
- Zero LLM calls added on the Lite path (deterministic only); Core/Apex LLM checks are
  lazy-imported, timeout-bounded, and fully exception-safe (tests pass without keys).
- SSE contract untouched: validation rides the generic structured `stage` event
  (`stage="validate"`); no existing event shape changes.
- Everything is additive: new kwargs have defaults, `PipelineResult` gains an optional
  `validation` dict, `CitationManager` keeps its old signature working.
- Metrics via `metrics.py`; logs via structlog in the existing style.
