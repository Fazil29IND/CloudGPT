# CloudGPT — Implementation Status

**Scale used (five levels, no shortcuts):**

| Level | Meaning |
|---|---|
| **Architecture Only** | Described or scaffolded; no executable path reaches it |
| **Mock** | Returns fabricated/simulated data, or self-certifies without running |
| **Implemented** | Real logic, wired into the pipeline; not meaningfully tested or mock-tested only |
| **Tested** | Real logic + meaningful automated tests (hermetic unit/integration) |
| **Production Verified** | Demonstrated against the real external service with stored evidence |

**Rule:** no README/docs claim may outrank this table. Update this file with
every PR that changes a subsystem's level. "Tested" requires the test to
exercise the real logic, not a mock of the thing being claimed.

| Subsystem | Status | Notes |
|---|---|---|
| Gemini embeddings (API + local fallback) | **Production Verified** | Live call verified; failure path returns empty vectors (degrades to sparse), never fake vectors |
| Pinecone index | **Production Verified** | `evaluation/baseline_snapshot.json` is a real captured artifact; live roundtrip test |
| Web search (SearXNG → Tavily → DDG) | **Implemented** | Real chain; tested via mocks only |
| Azure pricing | **Implemented** | Real public Retail Prices API; no stored live-run artifact |
| AWS pricing | **Implemented** | Real boto3 Price List API; requires credentials, not exercised live |
| GCP pricing | **Tested** (with honest fallback) | Live Cloud Billing Catalog API path (ADC); static catalog fallback flagged `estimated=True`; unknown SKUs fail honestly |
| AWS/GCP/Azure resource tools | **Implemented** | Wired into the gated `live_resource` tools stage; requires provider credentials; return [] without them |
| Calculator tool | **Implemented** | Wired via `_calculator_context` on calculate-intent queries |
| A2A / agent-to-agent | **Architecture Only** | Corpus content and marketing table only — no feature exists |
| Claude/OpenAI providers | **Architecture Only** | Dead code; `get_llm_provider` returns Gemini only |
| LLM failover chain + quota fail-fast | **Tested** | Real cascade logic; cooldowns synced across workers via Redis (`core/cooldown_sync.py`) |
| Model cooldowns ("circuit breaker") | **Tested** | Named honestly: a cooldown, not a circuit breaker — no half-open probing |
| Embedding cooldown | **Tested** | Same honest cooldown model with cross-worker sync |
| Hybrid RAG (dense+sparse+RRF, 3-pass filters) | **Tested** | Real fallback metrics; structure intentionally untouched |
| Semantic cache | **Tested** | Cosine match real; Redis warm-up fixed (`client` attr); per-worker L1 is by design |
| Rate limiting | **Tested** | Atomic Redis Lua sliding window; per-process deque fallback |
| Token economics / quota ledger | **Tested** | Real reserve/settle ledger; provider-reported thinking tokens preferred |
| Stripe billing | **Implemented** | Real integration; Stripe mocked in tests; Razorpay removed |
| Auth / CSRF / sessions | **Tested** | Hermetic suite; no production evidence yet |
| DB + migrations | **Tested** | Hardened pool: pre-ping, stale-connection replacement, idle-in-transaction & lock timeouts; CI applies migrations to real Postgres |
| Health endpoints | **Tested** | `/readyz` reports per-dependency status honestly (degraded ≠ down); no fabricated strings |
| Prometheus metrics | **Tested** | Real instruments in hot paths; no alert rules in code |
| Acceptance report / benchmarks | **Tested** | `acceptance_report.py` computes from gate results; measured recall only with `--with-retrieval-eval`; no hardcoded conclusions |
| RAG corpus (service cards) | **Implemented (templated)** | Catalog entries expanded to templated cards; run `sources/validate_urls.py` to check citation links; senior-engineer playbooks are genuinely authored |
| Qdrant | **Implemented** | Real client + embedded fallback; `search_sparse` stub returns [] |
| Email / ARQ / uploads / admin | **Tested** | Real logic, hermetic tests |
| generation/ two-tier validation | **Tested** | Tier-aware policy (no more Free hardcode); Apex retry/abstention reachable via Agentic/Adaptive routes |
| Thinking engine (Low→Max) | **Tested** | Defaults: Apex=Max, Core=High, Lite=Low; entitlements clamp escalation; provider-reported thinking tokens captured |
| Layered IaC validator (`tools/iac_validator.py`) | **Tested** | terraform fmt/validate → tflint → checkov → cfn-lint → kubeconform; binaries optional — missing = `skipped`, never fabricated pass; real-binary path opt-in |
| Validate-and-repair loop (`generation/iac_repair.py`) | **Tested** | IaCGen-pattern bounded loop, tier-capped (Apex=3, Core=2, Lite=0); full stack re-runs each iteration; budget exhaustion keeps original answer + reports unresolved findings honestly |
| Implementation knowledge packs (module catalog, runbooks, postmortems, hardening, migration, CI/CD) | **Tested (chunked)** | 6 packs ≈ 446 chunks confirmed chunked into senior-engineer/troubleshooting/iac-templates namespaces; embedding upsert DLQ'd pending Gemini free-tier quota reset — replay with `python ingest_services.py` (idempotent) or `python corpus/replay_dead_letter.py` |
| Official architecture manifest | **Tested (link-verified)** | 157 entries (90 original + 67 verified additions: WAF pillars, Architecture Centers, CAF, AVM, module READMEs); `sources/expand_manifest.py` link-checks and prunes dead URLs |
| Implementation golden set + eval | **Tested (gates)** | 31 tier-tagged tasks; gate 13 validates structure/coverage; `evaluation/implementation_eval.py` measures real deployability (LIVE_API_TESTS gate) |

## Gaps deliberately NOT fixed (documented, not hidden)

1. **Adaptive/Agentic pipelines do not consume `api_data`** — cloud resource
   results feed the base pipeline's context only. Core/Apex tier pipelines
   assemble context separately (RAG workflow structure intentionally
   untouched per constraint).
2. **Multi-worker in-memory L1s** (semantic cache L1, memory cache) remain
   per-process by design; the Redis sync layer covers cooldowns only.
3. **Corpus citation URLs** are slug-derived; a validator exists
   (`sources/validate_urls.py`) but links are not re-verified automatically at
   serve time.

*Last full audit: 2026-09-06 (deep sweep: integrations, fabrication inventory, resilience-vs-claims).*
