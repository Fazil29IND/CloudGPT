# CloudGPT — Agent Context

CloudGPT is an enterprise FastAPI + Jinja2 research assistant for AWS / Google Cloud / Azure
questions. A single agent pipeline (query router → gated web search + hybrid RAG + pricing/cloud-API
tools → context builder → multi-model LLM → citation manager) is executed exactly once in
`api/chat_routes.py:execute_agent_pipeline` and shared by the non-streaming and SSE endpoints.
Auth (OAuth + email/password), tiered billing (Lite/Pro/Max/Developer), multimodal uploads,
artifacts, and a full observability stack ride on top.

## Commands (Windows dev box, Git Bash)

```bash
source .venv/Scripts/activate
python app.py                      # server at http://localhost:5001 (uvicorn app:app --reload also works)
.venv/Scripts/python -m pytest -v  # full suite (pytest.ini: testpaths=tests, asyncio_mode=auto)
.venv/Scripts/python -m pytest tests/test_auth.py -v              # one file
.venv/Scripts/python -m ruff check .                              # lint
python init_db.py && python migrate.py                            # schema + versioned migrations
python ingest_services.py                        # build Pinecone + BM25 indexes (needs keys)
python worker.py                                 # ARQ background worker
docker compose --env-file .env up -d --build     # full stack incl. monitoring
```

Health probes: `/healthz`, `/readyz`, `/api/health`; metrics at `/metrics`.

## Architecture map

| Path | Responsibility |
|---|---|
| `app.py` | FastAPI app factory, lifespan, page routes (login/signup/chat/admin), OAuth, CSRF middleware |
| `api/` | Routers: `chat_routes.py` (agent pipeline + SSE), `billing_routes.py`, `artifacts.py`; `admin_routes.py` at root |
| `config.py` | Single pydantic-settings `Settings` — every env var is declared here with `Field(...)` + `.env.example` updated |
| `db.py` | ALL PostgreSQL access. Synchronous psycopg2 + `SimpleConnectionPool`. Never open connections elsewhere |
| `core/` | `entitlements.py` (tier/thinking gating), `rate_limit.py`, `security.py`, `redis_client.py`, per-domain caches (`session_cache`, `semantic_cache`, `tool_cache`, `memory_cache`) |
| `llm/` | `provider.py` (ABC + GeminiProvider + fallback/cooldown), `thinking.py` (thinking levels Low→Max), `context_builder.py`, `system_prompts.py` |
| `retrieval/` | Hybrid RAG: `dense.py`, `bm25.py`, `hybrid.py`, `reranker.py`, `adaptive_rag.py`, `agentic_rag.py` |
| `embeddings/`, `chunking/`, `citations/` | `embedding_engine.py` + `pinecone_manager.py`; `semantic_chunker.py` + `metadata_extractor.py`; `citation_manager.py` |
| `router/query_router.py` | Query classification that gates web search / tools |
| `tools/`, `cloud_apis/` | `web_search.py` (SearXNG + DDG fallback), `calculator.py`, `pricing/`; AWS/GCP/Azure SDK wrappers |
| `file_processor.py`, `email_service.py`, `tasks.py`, `worker.py` | Upload validation/extraction, SMTP, ARQ task definitions + worker |
| `migrations/` | Numbered SQL files (`001_…012_…`), applied only by `python migrate.py` |
| `templates/` + `static/js/` | Jinja pages; vanilla JS (`chat.js` is the SSE client). Vendor libs vendored in `static/js/vendor` |
| `evaluation/` | Golden-set retrieval eval, acceptance checks, load tests, `CACHE_KEY_SCHEMA.md` |
| `data/`, `corpus/`, `sources/` | RAG corpus (848 cloud services across 27 categories, senior-engineer playbooks) and manifests |

## Non-negotiables

- **Config** only through `config.py` Settings — never read `os.environ` ad hoc; new settings need a
  `Field` with description AND an `.env.example` entry.
- **Secrets**: `.env`, `Developer Accounts.txt`, and seeded account passwords are real credentials.
  Never print, echo, commit, or copy them into docs/tests.
- **Auth & CSRF**: every mutating request (`POST/PUT/DELETE`) on authenticated endpoints requires the
  `X-CSRF-Token` header (web UI supplies it via `<meta name="csrf-token">`). Route auth goes through
  the `get_current_user` dependency pattern; tier gating through `core/entitlements.py`.
- **DB is synchronous** (psycopg2). In `async def` routes, call `db.py` functions via
  `run_in_executor` / `enqueue_task` patterns already used in `app.py` — don't block the event loop.
- **Redis is optional**: `REDIS_ENABLED=false` or an unreachable Redis must degrade to in-memory
  fallbacks (`core/memory_cache.py` pattern), never crash. Tests rely on this.
- **One upload funnel**: all file input goes through `POST /api/upload` → `file_processor.validate_upload`
  (extension allow-list in config + magic-byte sniffing → staged with TTL → `attachment_id`). Never add
  a second upload path.
- **Additive-only frontend**: new DOM goes inside existing containers; new outputs ride the existing SSE
  stream as new event types (unknown events are ignored by old clients). See the Implementation Plan §1.
- **Migrations**: append a new numbered SQL file; never edit an applied migration. `004`/`005` are
  intentional placeholders.
- **Comments/log language**: match existing style — structlog for logging, `metrics.py` counters for
  anything observability-relevant.

## SSE event contract (`/api/chat/stream`)

`status`, `stage`, `provider_detected`, `session_title`, `thinking_token`, `thinking_done`,
`token`, `memory_updated`, `error`, `done` (usage, sources, thinking). New features add event types; never change
existing event shapes. The client lives in `static/js/chat.js`.

## Documentation map (read just-in-time)

| Doc | When to read |
|---|---|
| `docs/adr/0001-context-budgets.md` | Architecture Decision Record for token budgeting, compaction, and memory |
| `docs/runbook.md` / `How to Run CloudGPT.md` | Runbook: setup, env vars, test/dev accounts, endpoint catalog, troubleshooting |
| `docs/system-architecture.md` | Full technical specification (models, thinking engine, pipeline stages) |
| `docs/system-design.md` | Approved system design blueprint and SSE contracts |
| `docs/services-corpus.md` / `Services.md` | The RAG corpus itself — 848 AWS/GCP/Azure services across 27 categories |
| `Implementation Plan - Smart Routing, Quota UX, Thinking UI, Token Economics & Pricing.md` | Active workstream: small-talk gate, quota error UX, thinking UI, token economics, pricing content |
| `evaluation/ACCEPTANCE_REPORT.md`, `evaluation/CACHE_KEY_SCHEMA.md` | Acceptance status and cache-key conventions |

## Testing notes

- `tests/conftest.py` sets hermetic env defaults and resets rate-limiter state per test; use its
  fixtures (`app_instance`, TestClient) rather than constructing your own app.
- Tests must pass without real API keys, Redis, or Pinecone — mock providers at the `llm/provider.py`
  boundary or rely on in-memory fallbacks.
- `asyncio_mode = auto` — write plain `async def` tests without decorators.
