"""
Global Pytest Configuration and Hermetic Test Fixtures for CloudGPT.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure workspace root is always on sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

# Safe defaults for hermetic test execution
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-hermetic-testing-only-123456789")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-google-client-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SEED_DEMO_ACCOUNTS", "false")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:Fazil@localhost:5000/pygpt")
# Cap BLAS thread pools: on this box OpenBLAS occasionally fails to reserve
# its per-thread buffer during collection ("Arena alloc failed"), which is
# pure flakiness for hermetic tests — single-threaded BLAS is enough here.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("ENABLE_TIER_MODEL_SPECIALIZATION", "true")


@pytest.fixture(scope="session")
def app_instance():
    """Load and return the main FastAPI app singleton."""
    from app import app
    return app


@pytest.fixture(autouse=True)
def isolate_rate_limiter():
    """Reset rate limiter state and in-memory caches before each test.

    All TestClient requests share the same 'testclient' host fingerprint, so
    without a reset the auth rate limit (10/min) trips across unrelated tests.
    Also clears process-local L1 answer and semantic caches to ensure test hermeticity.
    """
    from core.rate_limit import rate_limiter
    from core.memory_cache import get_memory_cache
    from core.semantic_cache import get_semantic_cache

    # LLM/embedding model cooldowns are module globals with 300s TTLs; a test
    # that trips one (e.g. quota tests) would otherwise leak into later tests
    # that assert on the full active candidate list.
    import llm.provider as _llm_provider
    import embeddings.embedding_engine as _embed_engine

    _llm_provider._model_cooldowns.clear()

    rate_limiter.reset()
    get_memory_cache().clear()
    get_semantic_cache().clear()
    yield
    _llm_provider._model_cooldowns.clear()
    _embed_engine._gemini_embed_cooldown_until = 0.0
    rate_limiter.reset()
    get_memory_cache().clear()
    get_semantic_cache().clear()


@pytest.fixture
def client(app_instance):
    """Standard FastAPI TestClient."""
    return TestClient(app_instance)


@pytest.fixture
def csrf_token_extractor():
    """Helper to extract a valid CSRF token from rendered HTML forms."""
    def _extract(client: TestClient) -> str:
        response = client.get("/")
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
        if match:
            return match.group(1)
        match_meta = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', response.text)
        if match_meta:
            return match_meta.group(1)
        return ""
    return _extract
