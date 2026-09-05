# CloudGPT

CloudGPT is a FastAPI and Jinja web application for researching AWS, Google
Cloud, and Azure questions with an AI chat agent.

## Features

- Google OAuth and email/password sign-in with tiered billing (Lite / Pro / Max)
- Streaming (SSE) and non-streaming chat APIs with a unified agent pipeline:
  query routing → gated web search + hybrid RAG (Pinecone dense + BM25 sparse,
  reranked) → context assembly → Gemini generation with thinking levels
  (Low / Medium / High / Max)
- Small-talk gate: greetings answer directly without paying for retrieval
- User-facing model selector — **Apex**, **Core**, **Lite** — backed by the
  same Gemini chain, differentiated by thinking depth and quota
- Multimodal uploads (documents, images, audio) and downloadable artifacts
- Per-user conversation history, session summaries, user memory, and token
  quotas across rolling 5-hour / daily / weekly / monthly windows
- Observability: Prometheus metrics (`/metrics`), structured logging, Grafana
  dashboards (see `docker-compose.yml`)

## Setup

1. Create a Python virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in the credentials you need.
3. Create the PostgreSQL schema:

   ```bash
   python init_db.py
   ```

4. (Optional) Ingest the cloud service catalog. This requires
   `PINECONE_API_KEY` and the related Pinecone settings:

   ```bash
   python ingest_services.py
   ```

5. Start the application:

   ```bash
   python app.py
   ```

   Open <http://localhost:5001>.

## Tiers

New users receive the **Lite** tier by default. All tiers are powered by the
Gemini Flash chain (`gemini-3.8-flash` with 3.7/3.6/3.5 fallbacks — see
`config.py`), so `GEMINI_API_KEY` is the only LLM key required. The user-facing
model selector offers three modes — **Apex** (Max tier), **Core** (Pro tier),
and **Lite** (Free mode) — differentiated by thinking depth and token quota,
not by different model providers. The whitelisted Developer account receives
access to the full Apex/Core/Lite selector with elevated quotas.

## API keys by route

- `/api/chat` and `/api/chat/stream`: `GEMINI_API_KEY` (all tiers share the
  Gemini Flash chain).
- Chat web search: `SEARXNG_URL` is optional when DuckDuckGo fallback is
  enabled.
- RAG and `ingest_services.py`: `PINECONE_API_KEY`.
- Cloud pricing and cloud API tools: the relevant AWS, GCP, or Azure
  credentials.
- Billing (`/pricing`, `/api/billing/*`): Razorpay or Stripe credentials when
  `BILLING_ENABLED=true`.
- `/api/health` and session/history endpoints do not require additional
  provider keys, but session/history endpoints require sign-in.
