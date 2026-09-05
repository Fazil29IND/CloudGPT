# CloudGPT — Operations Runbook

## Overview
CloudGPT is an enterprise multi-cloud research assistant for AWS, Google Cloud, and Azure architecture, pricing, and troubleshooting.

## Quickstart

### Prerequisites
- Python 3.11+
- PostgreSQL 15+
- Redis (Optional, gracefully degrades to in-memory)
- Pinecone API Key (Optional, mocks available for testing)
- Gemini API Key

### Local Development Setup
```bash
# 1. Create and activate virtual environment
python -m venv .venv
source .venv/Scripts/activate  # On Windows Git Bash

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your credentials

# 4. Initialize Database and Migrations
python init_db.py
python migrate.py

# 5. Run Development Server
python app.py
# Application running at http://localhost:5001
```

### Running Tests
```bash
# Run full suite
.venv/Scripts/python -m pytest -v

# Run context engineering tests
.venv/Scripts/python -m pytest tests/test_context_builder.py tests/test_history_budget.py tests/test_user_memory.py tests/test_semantic_cache.py -v

# Run linting
.venv/Scripts/python -m ruff check .

# Run documentation integrity checks
python tools/check_docs.py
```

### Probes & Monitoring
- Liveness Probe: `GET /healthz`
- Readiness Probe: `GET /readyz`
- API Health: `GET /api/health`
- Prometheus Metrics: `GET /metrics`
- Grafana Dashboards: `monitoring/dashboards/`
