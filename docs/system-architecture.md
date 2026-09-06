# CloudGPT — Enterprise System Architecture Specification

```
═══════════════════════════════════════════════════════════════════════════════════════════════════════
 DOCUMENT VERSION : 2026-09 (Enterprise Multi-Model Architecture & Complete File-by-File Blueprint)
 SYSTEM CLASS     : Enterprise Multi-Cloud Research, Troubleshooting, IaC & FinOps Copilot Platform
 TARGET CLOUDS    : Amazon Web Services (AWS) · Google Cloud Platform (GCP) · Microsoft Azure
 REPOSITORY ROOT  : C:\CloudGPT
 PAYMENT GATEWAY  : Stripe Hosted Checkout & Customer Portal (Exclusive)
 LLM BACKBONE     : Gemini Flash Cascade (gemini-3.8-flash → 3.7 → 3.6 → 3.5)
 SECURITY POSTURE : Zero-Trust Ingress · Dual-Token CSRF · Strict CSP · Magic-Byte Sniffing · Sandboxed
═══════════════════════════════════════════════════════════════════════════════════════════════════════
```

---

## 1. Executive Overview & Multi-Cloud Domain Scope

### 1.1 Purpose & Mission

**CloudGPT** is an enterprise-grade AI cloud research assistant and architectural copilot engineered specifically for multi-cloud environments across **Amazon Web Services (AWS)**, **Google Cloud Platform (GCP)**, and **Microsoft Azure**.

General-purpose Large Language Models (LLMs) suffer from severe hallucinations, stale pricing figures, regional service discrepancies, fabricated CLI flags, and shallow synthesis when addressing complex infrastructure queries. CloudGPT eliminates these failure modes through:

- **Deterministic Multi-Stage Hybrid RAG**: Merging 768-dimensional dense vector embeddings with sparse BM25s lexical indexing via Reciprocal Rank Fusion (RRF) and FlashRank cross-encoder reranking.
- **Unified Gemini Flash Cascade**: Powered exclusively by `gemini-3.8-flash` with automatic fallback to `3.7-flash`, `3.6-flash`, and `3.5-flash` across all tiers.
- **Dynamic Reasoning Thinking Engine**: Real-time chain-of-thought budgets ranging from **Low** (~1,024 tokens) to **Max** (~65,536 tokens) with collapsible thinking accordion streaming.
- **Enterprise Context Engine**: Strict context token budgets (Lite: 8K, Pro: 32K, Max: 64K, Developer: 128K), untrusted injection encapsulation (`<untrusted_content>`), and U-curve attention ordering optimizations.
- **Authoritative Multi-Cloud Pricing Engine**: Real-time pricing lookups against AWS Price List, Azure Retail Prices, and GCP Cloud Billing Catalog APIs.
- **Exclusive Stripe Billing Architecture**: Seamless hosted Stripe Checkout redirection, self-serve Customer Portal, and signature-verified webhook subscription lifecycle tracking.
- **Persistent Infrastructure Memory**: Durable cross-session user memory retaining VPC topologies, organizational standards, and cloud preferences.

### 1.2 Multi-Cloud Knowledge Corpus

CloudGPT's retrieval corpus indexes **848 cloud services** across 27 catalog categories and 15 cloud architecture lifecycle stages:

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                  848 CURATED CLOUD SERVICES                                     │
├───────────────────────────────┬─────────────────────────────────┬───────────────────────────────┤
│     AMAZON WEB SERVICES       │     GOOGLE CLOUD PLATFORM       │        MICROSOFT AZURE        │
├───────────────────────────────┼─────────────────────────────────┼───────────────────────────────┤
│ • Compute: EC2, Lambda, ECS,  │ • Compute: Compute Engine, GKE, │ • Compute: Virtual Machines,  │
│   EKS, Fargate, App Runner    │   Cloud Run, Cloud Functions    │   AKS, Container Apps, App Svc│
│ • Storage: S3, EBS, EFS, FSx  │ • Storage: Cloud Storage,       │ • Storage: Blob, Files, NetApp│
│ • Database: Aurora, DynamoDB, │   Filestore, Persistent Disk    │ • Database: Cosmos DB, Azure  │
│   RDS, Redshift, ElastiCache  │ • Database: Cloud Spanner,      │   SQL, Synapse, Redis Cache   │
│ • Network: VPC, Transit GW,   │   BigQuery, Cloud SQL, Bigtable │ • Network: VNet, Virtual WAN, │
│   Route 53, Direct Connect    │ • Network: VPC, Cloud Armor,    │   ExpressRoute, Private Link  │
│ • Security: IAM, KMS, Guard-  │   Cloud NAT, Interconnect       │ • Security: Entra ID, Key     │
│   Duty, Secrets Mgr, WAF      │ • Security: Cloud IAM, KMS,     │   Vault, Defender, Sentinel   │
│ • AI/ML: SageMaker, Bedrock   │   Secret Mgr, Security Command  │ • AI/ML: Azure OpenAI,        │
│ • FinOps: Cost Explorer, AWS  │ • AI/ML: Vertex AI, BigQuery ML │   Machine Learning Studio     │
│   Budgets, Compute Optimizer  │ • FinOps: Cloud Billing, Recom. │ • FinOps: Cost Management     │
└───────────────────────────────┴─────────────────────────────────┴───────────────────────────────┘
```

---

## 2. Global Architectural Topology & C4 Models

CloudGPT operates as an asynchronous, decoupled, multi-tier platform built on FastAPI, PostgreSQL, Redis, and Vanilla ES6+ Web Components.

### 2.1 C4 Level 1: System Context Diagram

```mermaid
graph TD
    User["Cloud Architect / DevOps / FinOps Engineer"]
    CloudGPT["CloudGPT Platform<br/><b>(Unified Multi-Cloud Copilot)</b>"]

    subgraph LLM_Chain ["LLM & Reasoning Engine"]
        Gemini38["Google Gemini 3.8 Flash<br/><i>(Primary Inference & Reasoning)</i>"]
        GeminiFallback["Gemini 3.7 / 3.6 / 3.5 Flash<br/><i>(Autonomous Degradation Cascade)</i>"]
    end

    subgraph Cloud_APIs ["Live Cloud & Vector APIs"]
        AWSPrice["AWS Price List API"]
        GCPBilling["GCP Billing Catalog API"]
        AzureRetail["Azure Retail Prices API"]
        Pinecone["Pinecone Serverless Vector DB<br/><i>(768-dim Dense Index)</i>"]
        SearXNG["SearXNG / DuckDuckGo<br/><i>(Live Web Cross-Verification)</i>"]
    end

    subgraph External_Gateways ["Security & Payment Gateways"]
        Stripe["Stripe Hosted Checkout & Portal<br/><i>(Signature-Verified Webhooks)</i>"]
        GoogleOAuth["Google OIDC Provider"]
        SMTP["SMTP Mail Relay"]
    end

    User -->|HTTPS / SSE Stream| CloudGPT
    CloudGPT -->|Thinking Tokens & Generation| LLM_Chain
    CloudGPT -->|Live Pricing & Dense Embeddings| Cloud_APIs
    CloudGPT -->|Auth, Billing & Alerts| External_Gateways
```

---

### 2.2 C4 Level 2: Container Topology

```mermaid
graph TB
    Client["Browser UI Client<br/><i>(Vanilla JS · Obsidian Theme · SSE Client)</i>"]

    subgraph Edge ["Edge & Reverse Proxy"]
        Caddy["Caddy Server<br/><i>(TLS 1.3 · HTTP/2 · Zstd Compression · Static Cache)</i>"]
    end

    subgraph App_Server ["FastAPI Application Container (app:app)"]
        Ingress["Ingress & Middleware Stack<br/><i>(CSRF · Security Headers · Rate Limiter)</i>"]
        ChatRouter["Chat & Pipeline Controller<br/><i>(/api/chat/stream)</i>"]
        BillingRouter["Billing Controller<br/><i>(/api/billing/*)</i>"]
        ArtifactsRouter["Artifacts CRUD Controller<br/><i>(/api/artifacts/*)</i>"]
        AdminRouter["Admin Control Plane<br/><i>(/admin · /api/admin/*)</i>"]
    end

    subgraph Memory_Tiers ["Multi-Tier Caching & Acceleration"]
        L1_Memory["L1: In-Memory LRU Cache<br/><i>(Bounded · Sub-microsecond)</i>"]
        L2_Redis["L2: Redis Key-Value Store<br/><i>(Sessions · Rate Limits · Tool Cache)</i>"]
        L3_Semantic["L3: Semantic Cosine Cache<br/><i>(Query Cosine Similarity ≥ 0.96)</i>"]
    end

    subgraph Persistence ["Relational Persistence"]
        Postgres["PostgreSQL 16 Database<br/><i>(ThreadedConnectionPool · Psycopg2)</i>"]
    end

    subgraph Workers ["Asynchronous Workers"]
        ARQ["ARQ Background Worker<br/><i>(Log Shipping · Digest Reports · Async Tasks)</i>"]
    end

    Client -->|HTTPS / SSE| Caddy
    Caddy -->|Reverse Proxy :5001| Ingress
    Ingress --> ChatRouter
    Ingress --> BillingRouter
    Ingress --> ArtifactsRouter
    Ingress --> AdminRouter

    ChatRouter --> L1_Memory
    ChatRouter --> L2_Redis
    ChatRouter --> L3_Semantic
    ChatRouter --> Postgres
    ChatRouter -.-> ARQ
    BillingRouter --> Postgres
    ArtifactsRouter --> Postgres
```

---

### 2.3 C4 Level 3: Component Architecture (Chat & Retrieval Engine)

```mermaid
graph LR
    subgraph Request_Intake ["1. Ingress & Router"]
        Input[User Prompt] --> Sanitize[Sanitization & Magic Sniffer]
        Sanitize --> Router{Query Router}
    end

    subgraph Retrieval_Subsystem ["2. Hybrid RAG Pipeline"]
        Router -->|Cloud Architecture| Dense[Pinecone Dense Retrieval]
        Router -->|Specific Service| BM25[BM25s Lexical Index]
        Dense & BM25 --> RRF[Reciprocal Rank Fusion]
        RRF --> Reranker[FlashRank Cross-Encoder]
        Router -->|Complex Spec| HyDE[Adaptive HyDE Expansion]
        HyDE --> Reranker
    end

    subgraph External_Tools ["3. Tool Subsystem"]
        Router -->|Live Pricing| PricingTool[AWS / GCP / Azure Pricing]
        Router -->|Breaking Update| WebTool[SearXNG / DuckDuckGo]
    end

    subgraph Synthesis_Subsystem ["4. Context & Inference"]
        Reranker & PricingTool & WebTool --> Context[Enterprise Context Engine]
        Context --> Thinking[Thinking Engine Budgeting]
        Thinking --> Gemini[Gemini 3.8 Flash Stream]
        Gemini --> Citation[Citation & Provenance Manager]
        Citation --> SSEOut[SSE Stream to Client]
    end
```

---

## 3. Unified 8-Stage Agent Execution Pipeline

Every user message sent to `/api/chat/stream` or `/api/chat` passes through a single, deterministic execution pipeline (`api/chat_routes.py:execute_agent_pipeline`).

```mermaid
sequenceDiagram
    autonumber
    actor User as Client Browser
    participant API as api/chat_routes.py
    participant DB as PostgreSQL (db.py)
    participant Cache as Multi-Tier Cache
    participant Router as router/query_router.py
    participant RAG as retrieval/hybrid.py
    participant Context as llm/context_builder.py
    participant LLM as llm/provider.py (Gemini 3.8 Flash)
    participant SSE as SSE Event Stream

    User->>API: POST /api/chat/stream (message, session_id, model_selector)
    API->>DB: Reserve token quota (reserve_usage)
    API->>Cache: Check Semantic Cache (cosine similarity ≥ 0.96)
    alt Semantic Cache Hit
        Cache-->>API: Cached answer & sources
        API->>SSE: Emit cached tokens + done event
    else Cache Miss
        API->>SSE: stage: "analyzing"
        API->>Router: Classify intent & detect tool requirements
        Router-->>API: Intent (architecture / pricing / smalltalk)
      
        opt RAG Required
            API->>SSE: stage: "retrieving"
            API->>RAG: Hybrid Search (Dense 768d + BM25s + FlashRank)
            RAG-->>API: Top-K curated evidence chunks
        end

        opt Pricing or Live Web Required
            API->>SSE: stage: "searching"
            API->>Tools: Fetch live cloud pricing or SearXNG web data
            Tools-->>API: Structured pricing or verification payload
        end

        API->>SSE: stage: "synthesizing"
        API->>Context: Build Declarative Context Profile (U-curve ordered)
        Context-->>API: Normalized System Prompt + Evidence + Untrusted Blocks

        API->>LLM: Stream Inference (thinking_budget allocated)
        loop Token Generation
            opt Thinking Phase
                LLM->>SSE: thinking_token: "..."
            end
            opt Answer Phase
                LLM->>SSE: token: "..."
            end
        end

        LLM->>SSE: thinking_done
        API->>DB: Settle token usage & persist message + artifacts
        API->>Cache: Cache completed answer (Redis + Semantic)
        API->>SSE: done: {usage, sources, thinking_time, session_title}
    end
```

---

## 4. SSE Streaming Protocol & State Machine

The `/api/chat/stream` endpoint implements an additive, backward-compatible SSE contract.

### 4.1 Client SSE State Machine

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Connecting: User submits prompt
    Connecting --> Analyzing: Receive event: stage (analyzing)
    Analyzing --> Retrieving: Receive event: stage (retrieving)
    Retrieving --> Searching: Receive event: stage (searching)
    Searching --> Synthesizing: Receive event: stage (synthesizing)
  
    Synthesizing --> Thinking: Receive event: thinking_token
    Thinking --> Thinking: Receive event: thinking_token
    Thinking --> Streaming: Receive event: thinking_done
  
    Synthesizing --> Streaming: Receive event: token
    Streaming --> Streaming: Receive event: token
  
    Streaming --> Finalizing: Receive event: memory_updated
    Streaming --> Completed: Receive event: done
    Finalizing --> Completed: Receive event: done
  
    Connecting --> ErrorState: Receive event: error / 4xx / 5xx
    Analyzing --> ErrorState: Receive event: error
    Retrieving --> ErrorState: Receive event: error
    Synthesizing --> ErrorState: Receive event: error
    Thinking --> ErrorState: Receive event: error
    Streaming --> ErrorState: Receive event: error
  
    ErrorState --> Idle: User dismisses or retries
    Completed --> Idle: Message finalized & action bar mounted
```

### 4.2 SSE Event Catalog

| Event Type            | Emitted By            | Payload Structure                                                        | Description                                     |
| --------------------- | --------------------- | ------------------------------------------------------------------------ | ----------------------------------------------- |
| `stage`             | Pipeline Orchestrator | `{"stage": "analyzing" \| "retrieving" \| "searching" \| "synthesizing"}` | Drives frontend step progression indicator      |
| `provider_detected` | Pipeline Orchestrator | `{"provider": "gemini"}`                                               | Identifies active inference engine              |
| `session_title`     | Auto-titler           | `{"title": "EKS Auto-Scaling VPC Design"}`                             | Dynamically titles new conversations            |
| `thinking_token`    | Gemini Provider       | `{"token": "Evaluating ALB vs NLB trade-offs..."}`                     | Streams into collapsible thinking accordion     |
| `thinking_done`     | Gemini Provider       | `{}`                                                                   | Closes the thinking stream; opens answer stream |
| `token`             | Gemini Provider       | `{"token": "Architectural recommendation: ..."}`                       | Streams formatted Markdown tokens into UI       |
| `memory_updated`    | Memory Manager        | `{"key": "cloud_provider", "value": "AWS"}`                            | Signals durable architectural memory update     |
| `error`             | Exception Handler     | `{"error": "Quota exceeded", "code": 429}`                             | Renders actionable user-tier error notification |
| `done`              | Pipeline Finalizer    | `{"usage": {...}, "sources": [...], "session_id": "..."}`              | Completes turn, settles quota, mounts actions   |

---

## 5. Multi-Tier Caching & Memory Architecture

To minimize token expenditure and latency, CloudGPT employs a 4-tier caching topology:

```mermaid
graph TD
    Query[User Query Ingress] --> L1{L1: In-Memory LRU Cache}
    L1 -->|Hit (<1ms)| ReturnL1[Instant Answer Return]
    L1 -->|Miss| L2{L2: Redis Distributed Cache}
    L2 -->|Hit (<5ms)| ReturnL2[Hydrate Session / Tool Cache]
    L2 -->|Miss| L3{L3: Semantic Cosine Cache}
    L3 -->|Hit (<50ms)| ReturnL3[Return Synthesized Answer]
    L3 -->|Miss| L4{L4: Vector Index & Hybrid RAG}
    L4 --> FreshInference[Execute Fresh Inference & Dual-Write Back]
    FreshInference -.->|Write Back| L1
    FreshInference -.->|Write Back| L2
    FreshInference -.->|Write Back| L3
```

| Cache Layer             | Mechanism                   | Key / Location                                | TTL                            | Eviction Policy           | Purpose                                         |
| ----------------------- | --------------------------- | --------------------------------------------- | ------------------------------ | ------------------------- | ----------------------------------------------- |
| **L1: Memory**    | Python`OrderedDict` LRU   | `core/memory_cache.py`                      | 5 minutes                      | Max 10,000 keys (LRU)     | Sub-microsecond local process cache             |
| **L2: Key-Value** | Redis 7.2 Standalone        | `cloudgpt:session:{id}cloudgpt:tool:{hash}` | 2 hours (Session)1 hour (Tool) | volatile-lru              | Distributed multi-instance state & tool results |
| **L3: Semantic**  | Cosine Similarity (≥ 0.96) | `core/semantic_cache.py`                    | 24 hours                       | Periodic TTL expiry       | Eliminates identical LLM re-computations        |
| **L4: Vector DB** | Pinecone Serverless         | Namespaces:`v1`, `v2`                     | Persistent                     | Immutable versioned index | 768-dim dense index over 848 cloud services     |

---

## 6. Exclusive Stripe Billing & Subscription Topology

CloudGPT operates an exclusive **Stripe** payment integration. All legacy payment systems have been completely eradicated.

```mermaid
sequenceDiagram
    autonumber
    actor User as Authenticated User
    participant Client as Web UI (billing.js / pricing.js)
    participant API as api/billing_routes.py
    participant Billing as services/billing.py
    participant Stripe as Stripe API & Hosted Checkout
    participant Webhook as POST /api/billing/webhook
    participant DB as PostgreSQL (db.py)

    User->>Client: Click "Upgrade to Pro ($29/mo)"
    Client->>API: POST /api/billing/checkout {plan_key: "pro", interval: "month"}
    API->>Billing: checkout(user, plan_key, interval)
    Billing->>DB: Get or Create Stripe Customer ID
    Billing->>Stripe: stripe.checkout.Session.create(...)
    Stripe-->>Billing: session.url (https://checkout.stripe.com/c/pay/cs_...)
    Billing-->>API: url
    API-->>Client: {"url": "https://checkout.stripe.com/..."}
    Client->>Stripe: Redirect user to Hosted Checkout Page

    User->>Stripe: Complete payment via Card / Apple Pay / Google Pay
    Stripe-->>User: Redirect to /billing?checkout=success

    Note over Stripe,Webhook: Asynchronous Webhook Notification
    Stripe->>Webhook: POST /api/billing/webhook (payload, stripe-signature)
    Webhook->>Billing: verify_webhook(payload, signature)
    Billing->>Stripe: stripe.Webhook.construct_event(...)
    alt Valid Signature
        Billing->>DB: record_billing_event(event_id, "stripe", event_type)
        Billing->>DB: upsert_subscription(user_id, "pro", "active", "stripe", end_date)
        Billing->>DB: update_user_tier(user_id, "Pro")
        Webhook-->>Stripe: HTTP 200 {"received": true}
    else Invalid Signature
        Webhook-->>Stripe: HTTP 400 Invalid Webhook Signature
    end
```

### 6.1 Plan Structure & Pricing Matrix (USD)

| Plan Tier           | Monthly Price       | Annual Price        | Model Access            | Thinking Range  | 5-Hour Token Budget | Weekly Token Budget |
| ------------------- | ------------------- | ------------------- | ----------------------- | --------------- | ------------------- | ------------------- |
| **Lite**      | **$0** (Free) | **$0**        | Lite (Gemini 3.8 Flash) | Low–High       | 50,000              | 300,000             |
| **Pro**       | **$29 / mo**  | **$290 / yr** | Core + Lite             | Low–High       | 250,000             | 2,000,000           |
| **Max**       | **$79 / mo**  | **$790 / yr** | Apex + Core + Lite      | Low–Max (~65k) | 500,000             | 15,000,000          |
| **Developer** | Internal Bypass     | Internal Bypass     | Full Access             | Low–Max (~65k) | 10,000,000          | Unlimited           |

---

## 7. Multimodal Security & Single Upload Funnel

To prevent remote code execution, decompression bombs, and prompt injection from untrusted files, all file input passes through a single fortified funnel (`file_processor.py`).

```mermaid
graph TD
    Upload[User Upload: POST /api/upload] --> AuthCheck{Authenticated?}
    AuthCheck -->|No| HTTP401[HTTP 401 Unauthorized]
    AuthCheck -->|Yes| ExtensionCheck{Extension Allow-List}
  
    ExtensionCheck -->|Disallowed| HTTP400[HTTP 400 Disallowed Extension]
    ExtensionCheck -->|Allowed| SizeCheck{Size <= Tier Max Bytes?}
  
    SizeCheck -->|Oversized| HTTP413[HTTP 413 Payload Too Large]
    SizeCheck -->|Pass| MagicSniff{Magic-Byte Content Sniffing}
  
    MagicSniff -->|Spoofed MIME| HTTP400B[HTTP 400 File Content Mismatch]
    MagicSniff -->|Valid Binary| FileTypeSwitch{File Category}
  
    FileTypeSwitch -->|Image: PNG/JPG/WEBP| PillowSanitize[Pillow EXIF Strip + Resize <= 2048px]
    FileTypeSwitch -->|PDF Document| PyPDFExtract[PyPDF2 Extract + <= 200k Chars Cap]
    FileTypeSwitch -->|DOCX / XLSX| XMLSafeExtract[DefusedXML Office Text Extraction]
    FileTypeSwitch -->|Code / TXT / MD| UTF8Decode[UTF-8 Decode + Null-Byte Check]
  
    PillowSanitize & PyPDFExtract & XMLSafeExtract & UTF8Decode --> RedisStage[Stage in Redis: cloudgpt:attachment:uuid<br/>TTL = 3600 seconds]
    RedisStage --> ReturnMeta[Return attachment_id & staged metadata]
```

---

## 8. Relational Database Schema Topology

PostgreSQL serves as the system of record. Concurrency is managed via synchronous `psycopg2` inside an isolated `ThreadedConnectionPool`, offloaded to background threads using `asyncio.to_thread()`.

```mermaid
erDiagram
    users ||--o{ chat_sessions : owns
    users ||--o{ subscriptions : has
    users ||--o{ billing_customers : mapped
    users ||--o{ user_memories : stores
    users ||--o{ usage_events : incurs
    chat_sessions ||--o{ messages : contains
    chat_sessions ||--o{ artifacts : produces
    messages ||--o{ feedback : receives

    users {
        bigserial id PK
        varchar email UK
        varchar password_hash
        varchar full_name
        varchar tier "Lite | Pro | Max | Developer"
        varchar role "user | admin"
        boolean is_active
        boolean is_verified
        timestamp created_at
    }

    chat_sessions {
        uuid id PK
        bigint user_id FK
        varchar title
        varchar cloud_provider "AWS | GCP | Azure | Multi"
        boolean is_archived
        timestamp updated_at
    }

    messages {
        uuid id PK
        uuid session_id FK
        varchar role "user | assistant | system"
        text content
        jsonb attachments
        jsonb artifacts
        text thinking_content
        integer prompt_tokens
        integer completion_tokens
        timestamp created_at
    }

    artifacts {
        uuid id PK
        uuid session_id FK
        bigint user_id FK
        varchar title
        varchar type "code | markdown | terraform | diagram"
        text content
        integer version
        timestamp created_at
    }

    subscriptions {
        bigserial id PK
        bigint user_id FK
        varchar plan_key "pro | max"
        varchar status "active | past_due | canceled"
        varchar provider "stripe"
        varchar provider_subscription_id UK
        timestamp current_period_end
    }

    billing_customers {
        bigserial id PK
        bigint user_id FK
        varchar provider "stripe"
        varchar provider_customer_id UK
    }

    user_memories {
        bigserial id PK
        bigint user_id FK
        varchar memory_key
        text memory_value
        varchar category
        float confidence
        timestamp updated_at
    }

    usage_events {
        bigserial id PK
        bigint user_id FK
        varchar request_id
        integer tokens_consumed
        varchar model
        timestamp created_at
    }
```

---

## 9. Observability & Telemetry Infrastructure

CloudGPT exposes production-grade Prometheus metrics and structured JSON logging for monitoring:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                OBSERVABILITY STACK                                     │
├────────────────────────────────┬───────────────────────────────────────────────────────┤
│ Endpoint: `/metrics`           │ Prometheus exposition format                          │
│ Time-to-First-Token (TTFT)     │ `cloudgpt_ttft_seconds` (Histogram, buckets 0.1s→10s) │
│ Stage Duration Metrics         │ `cloudgpt_rag_stage_duration_seconds` (By stage)       │
│ Request Counters               │ `cloudgpt_chat_requests_total` (By tier, model, status)│
│ Token Consumption Counters     │ `cloudgpt_token_usage_total` (Prompt & Completion)    │
│ Cache Performance Gauges       │ `cloudgpt_cache_hits_total` / `cloudgpt_cache_misses`  │
│ Database Pool Gauges           │ `cloudgpt_db_pool_connections_active` (Max: 30)      │
│ Structured Logging             │ Structlog JSON formatter with request_id & user_id    │
└────────────────────────────────┴───────────────────────────────────────────────────────┘
```

---

## 10. Security & Defense-in-Depth Posture

1. **Content Security Policy (CSP)**:
   - Scripts: `'self'`, `'unsafe-inline'`, `https://cdn.jsdelivr.net`, `https://cdnjs.cloudflare.com`, `https://js.stripe.com`.
   - Frames: `https://js.stripe.com`, `https://checkout.stripe.com`.
   - Connects: `'self'`, `https://api.stripe.com`.
   - Razorpay completely excluded from all directives.
2. **CSRF Protection**:
   - Double-submit cookie with cryptographic verification on all mutating requests (`POST`, `PUT`, `DELETE`).
   - Web UI extracts CSRF token from `<meta name="csrf-token">` and sends `X-CSRF-Token` header.
3. **Prompt Injection & Untrusted Boundary**:
   - All retrieved context, web pages, and uploaded attachments are wrapped in strict `<untrusted_content>` tags.
   - System prompts instruct the LLM to treat untrusted blocks purely as data references, ignoring embedded instructions.
4. **Secret Redaction**:
   - Regex-based scrubbing filters AWS Access Keys (`AKIA...`), GCP Service Account JSON keys, and Azure Connection Strings before logging or sending context to LLMs.
