# How to Run CloudGPT

This guide walks you through setting up and running **CloudGPT** locally on Windows, macOS/Linux, or with Docker Compose.

---

## 📋 Prerequisites

Before running the application, ensure you have:

- **Python 3.10+** (Python 3.11 – 3.13 strongly recommended)
- **PostgreSQL 16+** (running locally or in Docker; the docker-compose stack uses PostgreSQL 16; default database name: `pygpt`)
- **Redis 7+** (optional for local dev; recommended for rate limiting, session cache, and staged multimodal uploads)
- **Pillow / Image Libraries** (installed automatically via `requirements.txt` for image sanitization and EXIF stripping)
- **Git**

---

## ⚡ Option 1: Quick Start on Windows (1-Click)

If you are on Windows and have PostgreSQL running:

- In **PowerShell**: run `.\start_app.ps1`
- In **Command Prompt** or by double-clicking in Explorer: run `.\start_app.bat`

The script will automatically:

- Detect and activate the `.venv` virtual environment.
- Launch the FastAPI server at `http://localhost:5001`.
- Open your default browser to `http://localhost:5001`.

---

## 🛠️ Option 2: Step-by-Step Manual Setup (Local Development)

### 1. Set Up Python Virtual Environment

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\activate
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables (`.env`)

Copy `.env.example` to `.env`:

```powershell
# Windows (PowerShell)
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

Open `.env` and configure your settings:

```env
# ── Core Server & Database ──────────────────────────────────────────────────
SECRET_KEY=your-random-32-character-secret-key-here
ENVIRONMENT=development
DATABASE_URL=postgresql://postgres:password@localhost:5432/pygpt

# ── Redis (Optional for local dev; in-memory fallbacks exist) ───────────────
REDIS_ENABLED=true
REDIS_URL=redis://localhost:6379/0

# ── Admin & Developer Accounts ──────────────────────────────────────────────
ADMIN_EMAIL=shahulrahumath2007.s@gmail.com
DEVELOPER_EMAILS=amanullahfazil2007.s@gmail.com
SEED_DEMO_ACCOUNTS=false

# ── AI Model Provider Keys ──────────────────────────────────────────────────
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.8-flash

# ── Multimodal Feature Flags & Limits ───────────────────────────────────────
ENABLE_VISION=true
ENABLE_AUDIO_INPUT=true
ENABLE_TTS=true
ENABLE_ARTIFACTS=true
MAX_IMAGE_DIMENSION=2048

# ── Optional: Search & Live Cloud Tools ─────────────────────────────────────
SEARXNG_URL=http://localhost:8080
ENABLE_CLOUD_API_TOOLS=false

# ── Optional: Google OAuth 2.0 ──────────────────────────────────────────────
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret

# ── Optional: Transactional Email ───────────────────────────────────────────
EMAIL_ENABLED=false
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=user@example.com
SMTP_PASSWORD=secret
```

### 4. Initialize Database & Migrations

Run database initialization and versioned schema migrations (`001`–`012`; the latest add message metadata, session summaries, and user memory):

```bash
# 1. Initialize base schema
python init_db.py

# 2. Apply all versioned migrations (001 through 012)
python migrate.py
```

### 5. (Optional) Ingest Cloud Catalog for Hybrid RAG

To populate the Pinecone vector index and BM25 sparse index with 1,100+ cloud services and senior engineer knowledge playbooks:

```bash
python ingest_services.py
```

### 6. Start the CloudGPT Server

```bash
python app.py
```

*Or using Uvicorn directly with hot-reload:*

```bash
uvicorn app:app --host 0.0.0.0 --port 5001 --reload
```

Open your browser and navigate to: **[http://localhost:5001](http://localhost:5001)**

### 7. (Optional) Start Background Worker

To run the ARQ background worker for expired reservation cleanups and async tasks:

```bash
\python worker.py
```

---

## 🔑 Developer & Testing Accounts

For rapid manual testing without setting up new accounts, CloudGPT provides pre-configured testing accounts with password complexity exemptions (see [`Developer Accounts.txt`](<file:///c:/CloudGPT/Developer%20Accounts.txt>)):

| Account Role                                                                                                                                                             | Email | Password | Tier | Thinking Levels Allowed | Multimodal Limits |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----- | -------- | ---- | ----------------------- | ----------------- |
| Passwords are real credentials and are intentionally NOT documented here — they live only in`Developer Accounts.txt` (never paste them into docs, tickets, or chats). |       |          |      |                         |                   |

| Account Role               | Email                              | Password                     | Tier      | Thinking Levels Allowed | Multimodal Limits                |
| -------------------------- | ---------------------------------- | ---------------------------- | --------- | ----------------------- | -------------------------------- |
| **Lite User (Free)** | `fazilprojects@gmail.com`        | (see Developer Accounts.txt) | Lite      | Low, Medium, High       | 5MB Image, 10MB Audio, 10k TTS   |
| **Pro User (Core)**  | `fazilprojects9@gmail.com`       | (see Developer Accounts.txt) | Pro       | Low, Medium, High       | 10MB Image, 25MB Audio, 50k TTS  |
| **Max User (Apex)**  | `shahulrahumath2007.s@gmail.com` | (see Developer Accounts.txt) | Max       | Low, Medium, High, Max  | 20MB Image, 50MB Audio, 200k TTS |
| **Developer (Root)** | `amanullahfazil2007.s@gmail.com` | (see Developer Accounts.txt) | Developer | All Levels (Unlimited)  | 50MB Image, 100MB Audio, 1M TTS  |

---

## 🐳 Option 3: Run with Docker Compose (Full Stack)

To run the complete production-grade stack (App, PostgreSQL, Redis, Worker, Caddy Proxy, Prometheus, Grafana, Loki, Promtail):

```bash
# Start all containers in the background
docker compose --env-file .env up -d --build

# View container logs
docker compose logs -f app

# Run migrations inside running app container
docker compose exec app python migrate.py

# Stop all containers
docker compose down
```

---

## 🧪 Running Automated Tests

CloudGPT includes a comprehensive suite of automated tests covering authentication, multi-model routing, thinking engine, RAG retrieval, billing, multimodal uploads, message actions, and artifacts:

```powershell
# Run the complete test suite
.venv\Scripts\pytest -v

# Run the multimodal, message actions, artifacts, and upload test suites:
.venv\Scripts\pytest tests/test_chat_ui_rendering.py tests/test_file_upload.py tests/test_message_actions.py tests/test_artifacts.py tests/test_multimodal_attachments.py -v
```

---

## 🌐 Application Routes & Endpoints Catalog

| Service / Page                 | URL / Endpoint                                | Method       | Description                                                  |
| :----------------------------- | :-------------------------------------------- | :----------- | :----------------------------------------------------------- |
| **Sign In / Home**       | `http://localhost:5001/`                    | `GET`      | User sign-in page                                            |
| **Sign Up**              | `http://localhost:5001/signup`              | `GET`      | Account registration                                         |
| **Chat Dashboard**       | `http://localhost:5001/chat`                | `GET`      | Main AI multi-cloud research workspace                       |
| **Post-Login Dashboard** | `http://localhost:5001/dashboard`           | `GET`      | Landing page after sign-in (plan overview, quick links)      |
| **Billing Dashboard**    | `http://localhost:5001/billing`             | `GET`      | Current plan, token usage, and upgrade entry point           |
| **Pricing Plans**        | `http://localhost:5001/pricing`             | `GET`      | Lite, Pro, and Max subscription tiers                        |
| **Terms / Privacy**      | `http://localhost:5001/terms`, `/privacy` | `GET`      | Legal pages (linked from the pricing footer)                 |
| **Admin Dashboard**      | `http://localhost:5001/admin/users`         | `GET`      | User & usage administration (RBAC gated)                     |
| **Chat SSE Stream**      | `/api/chat/stream`                          | `POST`     | Real-time reasoning + answer token stream                    |
| **Billing Checkout API** | `/api/billing/*`                            | `POST/GET` | Checkout, plans catalog, webhook endpoints (Stripe) |
| **File Upload**          | `/api/upload`                               | `POST`     | Multimodal upload (images, audio, code, docs)                |
| **Attachment Download**  | `/api/attachments/{id}/download`            | `GET`      | Download staged user attachments                             |
| **Turn Truncation**      | `/api/sessions/{id}/truncate`               | `POST`     | History rollback for Undo and Inline Edit                    |
| **Message Feedback**     | `/api/feedback`                             | `POST`     | Thumbs up/down rating (+1/-1) and reason                     |
| **User Data Export**     | `/api/user/export`                          | `GET`      | GDPR data portability export                                 |
| **Delete Account**       | `/api/user/delete-account`                  | `POST`     | GDPR account self-deletion                                   |
| **Password Reset**       | `/forgot-password`, `/reset-password`     | `GET/POST` | Email-based password reset flow (SMTP)                       |
| **Artifacts API**        | `/api/artifacts`                            | `POST/GET` | Create, list, and download generated artifacts               |
| **Health Check API**     | `/api/health`                               | `GET`      | System health probe (DB, Redis, models)                      |
| **Prometheus Metrics**   | `/metrics`                                  | `GET`      | Prometheus scrape endpoint                                   |

---

## ❓ Troubleshooting

### 1. Database Connection Error (`psycopg2.OperationalError`)

- Ensure your PostgreSQL service is running.
- Verify `DATABASE_URL` in `.env` matches your PostgreSQL port, username, password, and database name (e.g. `postgresql://postgres:password@localhost:5432/pygpt`).

### 2. CSRF Forbidden Error (403) on API Calls

- All state-mutating requests (`POST`, `PUT`, `DELETE`) to authenticated endpoints require an `X-CSRF-Token` header.
- The web UI handles this automatically via `<meta name="csrf-token">`.
- For automated testing or curl scripts, extract the token from `GET /chat` and supply `headers={"X-CSRF-Token": token}`.

### 3. Redis Offline / In-Memory Fallback

- If Redis is disabled (`REDIS_ENABLED=false`) or unreachable, CloudGPT automatically falls back to in-memory sliding windows, in-memory attachment/artifact staging, and direct PostgreSQL queries without crashing.

### 4. Port Already in Use (5001)

- If port `5001` is in use, start the app on another port:
  ```bash
  uvicorn app:app --port 5002 --reload
  ```
