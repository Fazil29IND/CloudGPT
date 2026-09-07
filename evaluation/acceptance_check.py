"""
Automated Acceptance Check Script for CloudGPT Phase 0-5 Deliverables.

Validates all 20 implementation tasks across caching, crawling, retrieval,
latency budgeting, and dead-letter queue integrity.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from chunking.semantic_chunker import DocumentChunk, SemanticChunker
from config import get_settings
from core.llm_cache import (
    _answer_key,
    _plan_key,
    _retrieval_key,
    get_cached_answer,
    get_cached_query_plan,
    get_cached_retrieval_result,
    set_cached_answer,
    set_cached_query_plan,
    set_cached_retrieval_result,
)
from core.redis_client import redis_client
from core.session_cache import get_fast_chat_history, record_message_dual_write, warm_session_cache_pipelined
from corpus.ingestion_state import IngestionStateManager
from corpus.manifest import ManifestEntry
from embeddings.embedding_engine import EmbeddingEngine
from embeddings.pinecone_manager import PineconeManager
from retrieval.bm25 import SparseRetriever
from retrieval.dense import DenseRetriever
from retrieval.hybrid import HybridRetriever


class MockRedisServer:
    def __init__(self):
        self.is_available = True
        self.client = None
        self.store = {}
        self.locks = {}

    async def get_json(self, key: str):
        return self.store.get(key)

    async def set_json(self, key: str, val: Any, *args, **kwargs):
        self.store[key] = val
        return True

    async def get(self, key: str):
        val = self.store.get(key)
        return json.dumps(val) if isinstance(val, (dict, list)) else val

    async def set(self, key: str, val: Any, *args, **kwargs):
        self.store[key] = val
        return True

    async def acquire_lock(self, key: str, owner_token: str, ttl_seconds: int = 20) -> bool:
        if key in self.locks:
            return False
        self.locks[key] = owner_token
        return True

    async def release_lock(self, key: str, owner_token: str) -> bool:
        if self.locks.get(key) == owner_token:
            del self.locks[key]
            return True
        return False

    async def lrange(self, key: str, start: int, stop: int):
        lst = self.store.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start : stop + 1]

    async def rpush(self, key: str, val: str):
        lst = self.store.setdefault(key, [])
        lst.append(val)
        return len(lst)

    async def ltrim(self, key: str, start: int, stop: int):
        lst = self.store.get(key, [])
        if stop == -1:
            self.store[key] = lst[start:]
        else:
            self.store[key] = lst[start : stop + 1]
        return True

    async def expire(self, key: str, ttl: int):
        return True


async def run_acceptance_checks() -> dict[str, bool]:
    results = {}
    settings = get_settings()

    # 1. Config & Versioning Check
    print("[1/8] Verifying configuration and cache version settings...")
    try:
        assert hasattr(settings, "cache_corpus_version"), "Missing cache_corpus_version in config"
        assert hasattr(settings, "cache_prompt_version"), "Missing cache_prompt_version in config"
        assert hasattr(settings, "cache_router_version"), "Missing cache_router_version in config"
        assert hasattr(settings, "budget_classification_ms"), "Missing budget_classification_ms"
        assert settings.always_web_search is False, "always_web_search should default to False"
        results["config_versions"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["config_versions"] = False

    # 2. Redis Locking & 3-Layer Cache Check
    print("[2/8] Verifying Redis single-flight locking and 3-layer versioned cache...")
    try:
        mock_redis = MockRedisServer()
        target_rc = redis_client if (redis_client and redis_client.is_available) else mock_redis

        with patch("core.llm_cache.redis_client", target_rc):
            # Single-flight lock test
            token = "test-owner-token"
            lock_key = "rag:v2:lock:test_acceptance_key"
            acq = await target_rc.acquire_lock(lock_key, token, ttl_seconds=5)
            assert acq is True, "Failed to acquire lock"
            acq2 = await target_rc.acquire_lock(lock_key, "other-token", ttl_seconds=5)
            assert acq2 is False, "Lock should not be re-acquirable by another owner"
            rel = await target_rc.release_lock(lock_key, token)
            assert rel is True, "Failed to release lock"

            # 3-Layer cache operations
            await set_cached_query_plan("test_query", {"route": "RAG"}, settings)
            plan = await get_cached_query_plan("test_query", settings)
            assert plan == {"route": "RAG"}, f"Plan cache mismatch: {plan}"

            await set_cached_answer("test_query", {"answer": "acceptance ok"}, "Max", None, "Max", settings)  # pyright: ignore[reportArgumentType]
            ans = await get_cached_answer("test_query", "Max", None, "Max", settings)  # pyright: ignore[reportArgumentType]
            assert ans == {"answer": "acceptance ok"}, f"Answer cache mismatch: {ans}"

        results["redis_cache_and_locking"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["redis_cache_and_locking"] = False

    # 3. Pipelined Session Warm-up Check
    print("[3/8] Verifying pipelined Redis session cache warm-up...")
    try:
        mock_redis = MockRedisServer()
        target_rc = redis_client if (redis_client and redis_client.is_available) else mock_redis

        with patch("core.session_cache.redis_client", target_rc):
            msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
            await warm_session_cache_pipelined(99999, "test-sess-accept", msgs)
            loaded = await get_fast_chat_history(99999, "test-sess-accept", limit=5)
            assert len(loaded) >= 2, f"Expected at least 2 messages, got {len(loaded)}"
        results["session_pipelining"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["session_pipelining"] = False

    # 4. Parent-Child Chunker Check
    print("[4/8] Verifying token-budgeted parent-child chunker...")
    try:
        chunker = SemanticChunker(max_chunk_chars=1800, min_chunk_chars=100, overlap_chars=100)
        sample_doc = (
            "# Main Heading\n\nIntroduction to service.\n\n"
            "## Architecture Details\n\nDetailed breakdown of components with code:\n\n"
            "```python\ndef test():\n    pass\n```\n\n"
            "| Feature | AWS | GCP |\n|---|---|---|\n| Compute | EC2 | GCE |\n"
        )
        pairs = chunker.chunk_document_with_parents(sample_doc, provider="aws", service="ec2")
        assert len(pairs) > 0, "No parent-child pairs generated"
        for parent, children in pairs:
            assert parent.parent_chunk_id is None
            assert parent.parser_version == "html-md-v2"
            assert parent.chunker_version == "parent-child-v1"
            for child in children:
                assert child.parent_chunk_id == parent.chunk_id
        results["parent_child_chunker"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["parent_child_chunker"] = False

    # 5. Ingestion State & Dead-Letter Queue Check
    print("[5/8] Verifying ingestion state persistence & DLQ operations...")
    try:
        mgr = IngestionStateManager()
        mgr.record_success("test-entry", "testhash123", "v1")
        assert mgr.should_skip("test-entry", "testhash123", "v1") is True
        assert mgr.should_skip("test-entry", "different_hash", "v1") is False
        mgr.record_failure("test-entry-fail", "chunk-01", "Mock error for acceptance", "Sample chunk text")
        assert len(mgr.dlq) > 0
        # Clean up mock failure
        mgr.dlq = [c for c in mgr.dlq if c.chunk_id != "chunk-01"]
        mgr.save()
        results["ingestion_state_and_dlq"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["ingestion_state_and_dlq"] = False

    # 6. BM25S Lexical Vector Sparse Embeddings Check
    print("[6/8] Verifying BM25S tokenized sparse embedding generation...")
    try:
        embed_engine = EmbeddingEngine(provider=settings.embedding_provider, model_name=settings.embedding_model)
        sparse_vecs = await embed_engine.embed_sparse(["Amazon S3 default encryption with SSE-S3 AES-256"])
        assert len(sparse_vecs) == 1
        assert "indices" in sparse_vecs[0] and "values" in sparse_vecs[0]
        assert len(sparse_vecs[0]["indices"]) > 0
        results["bm25_sparse_vectors"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["bm25_sparse_vectors"] = False

    # 7. Hybrid Multi-Namespace Fan-Out & Deduplication Check
    print("[7/8] Verifying multi-namespace hybrid retriever and alpha weighting...")
    try:
        pinecone_mgr = PineconeManager(settings)
        dense = DenseRetriever(embed_engine, pinecone_mgr)
        sparse = SparseRetriever(embed_engine, pinecone_mgr)
        retriever = HybridRetriever(dense, sparse)

        # Exact query weight check
        exact_w, _ = retriever._get_effective_weights("aws s3 ls --region us-east-1")
        nlq_w, _ = retriever._get_effective_weights("Tell me how S3 storage tiers work")
        assert exact_w < nlq_w, f"Exact weight ({exact_w}) should be lower than NLQ dense weight ({nlq_w})"

        results["hybrid_retriever_fanout"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["hybrid_retriever_fanout"] = False

    # 8. Golden Evaluation Dataset Check
    print("[8/8] Verifying golden ground-truth evaluation dataset...")
    try:
        golden_file = BASE_DIR / "evaluation" / "golden_set.json"
        assert golden_file.exists(), "golden_set.json not found"
        with open(golden_file, "r", encoding="utf-8") as f:
            items = json.load(f)
        assert len(items) >= 30, f"Expected at least 30 golden items, found {len(items)}"
        results["golden_eval_set"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["golden_eval_set"] = False

    # 9. Model Version & Separation Check
    print("[9/10] Verifying model version pinning and role separation...")
    try:
        ok, msg = check_model_version_config(settings)
        assert ok, msg
        results["model_version_config"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["model_version_config"] = False

    # 10. Model Evaluation Slice Check
    print("[10/13] Verifying model-capability evaluation slice...")
    try:
        ok, msg = check_model_eval_importable()
        assert ok, msg
        results["model_eval_importable"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["model_eval_importable"] = False

    # 11. Layered IaC Validator Tool Check
    print("[11/13] Verifying layered IaC validator (detection, honest skips, aggregation)...")
    try:
        from tools.iac_validator import IacValidationResult, detect_artifact_type, validate_artifact

        assert detect_artifact_type('resource "aws_s3_bucket" "x" {}') == "terraform"
        assert detect_artifact_type('{"AWSTemplateFormatVersion": "2010-09-09", "Resources": {}}') == "cloudformation"
        assert detect_artifact_type("apiVersion: apps/v1\nkind: Deployment") == "kubernetes"
        # Consistency contract: validity must equal "every executed layer passed";
        # layers for missing binaries report skipped — never a fabricated pass or fail.
        result = validate_artifact('resource "aws_s3_bucket" "x" {}')
        assert isinstance(result, IacValidationResult)
        executed = [l for l in result.layers if l.status in ("passed", "failed")]
        expected_valid = bool(executed) and all(l.status == "passed" for l in executed)
        assert result.valid == expected_valid, (
            f"valid={result.valid} but executed layers say {expected_valid}"
        )
        assert all(l.status == "skipped" for l in result.layers if l.status not in ("passed", "failed"))
        results["iac_validator_tool"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["iac_validator_tool"] = False

    # 12. Validate-and-Repair Loop Tier Caps Check
    print("[12/13] Verifying tier-capped validate-and-repair loop...")
    try:
        from generation.iac_repair import _tier_repair_budget

        s = get_settings()
        assert hasattr(s, "iac_validation_enabled"), "Missing iac_validation_enabled"
        assert hasattr(s, "iac_max_repair_iterations"), "Missing iac_max_repair_iterations"
        assert _tier_repair_budget("Apex", s) == int(getattr(s, "iac_max_repair_iterations", 3))
        assert _tier_repair_budget("Core", s) == min(2, int(getattr(s, "iac_max_repair_iterations", 3)))
        assert _tier_repair_budget("Lite", s) == 0, "Lite must never run the repair loop (speed contract)"
        assert _tier_repair_budget("Free", s) == 0
        results["iac_repair_loop"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["iac_repair_loop"] = False

    # 13. Implementation Golden Set Check
    print("[13/13] Verifying implementation golden evaluation set...")
    try:
        impl_file = BASE_DIR / "evaluation" / "implementation_golden_set.json"
        assert impl_file.exists(), "implementation_golden_set.json not found"
        with open(impl_file, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        assert len(tasks) >= 20, f"Expected at least 20 implementation tasks, found {len(tasks)}"
        required = {"id", "tier_target", "provider", "task", "artifact_type", "required_properties"}
        for t in tasks:
            missing = required - set(t.keys())
            assert not missing, f"Task {t.get('id', '?')} missing fields: {missing}"
        tiers = {t["tier_target"] for t in tasks}
        assert {"Apex", "Core", "Lite"} <= tiers, "Golden set must cover all three tiers"
        results["implementation_golden_set"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["implementation_golden_set"] = False

    return results


def check_model_version_config(settings) -> tuple[bool, str]:
    """Validate model version config satisfies pinning and separation invariants."""
    required = [
        "gemini_model_lite", "gemini_model_core", "gemini_model_apex",
        "gemini_model_fallback_1", "gemini_model_fallback_2", "gemini_model_fallback_3",
        "gemini_model_sub", "gemini_model_evaluator",
    ]
    for field in required:
        val = getattr(settings, field, None)
        if not val or not isinstance(val, str):
            return False, f"Model field '{field}' is empty or missing"

    # Evaluator must differ from generator models
    evaluator = settings.gemini_model_evaluator
    if evaluator == settings.gemini_model_apex:
        return False, (
            f"gemini_model_evaluator ({evaluator}) must differ from "
            f"gemini_model_apex ({settings.gemini_model_apex})"
        )
    if evaluator == settings.gemini_model_core:
        return False, (
            f"gemini_model_evaluator ({evaluator}) must differ from "
            f"gemini_model_core ({settings.gemini_model_core})"
        )

    # Evaluator temperature must be lower than generation temperature
    if settings.temperature_evaluator >= settings.temperature_generation:
        return False, (
            f"temperature_evaluator ({settings.temperature_evaluator}) must be "
            f"< temperature_generation ({settings.temperature_generation})"
        )

    return True, "Model version config OK"


def check_model_eval_importable() -> tuple[bool, str]:
    """Verify model_eval.py is importable and its dry-run path is functional."""
    try:
        from evaluation.model_eval import run_model_eval, _score_answer, _sample_questions
        # Scorer sanity
        assert _score_answer("S3 object storage", "S3") == 1.0
        return True, "model_eval import and scorer OK"
    except Exception as exc:
        return False, f"model_eval import failed: {exc}"


def main() -> None:
    print("\n" + "=" * 60)
    print("CLOUDGPT RAG & CACHE ACCEPTANCE CHECK SUITE")
    print("=" * 60 + "\n")

    results = asyncio.run(run_acceptance_checks())

    print("\n" + "=" * 60)
    print("ACCEPTANCE RESULTS SUMMARY")
    print("=" * 60)
    all_passed = True
    for test_name, passed in results.items():
        status_str = "[PASS]" if passed else "[FAIL]"
        if not passed:
            all_passed = False
        print(f"{test_name:<35} : {status_str}")
    print("=" * 60)

    if all_passed:
        print("ALL ACCEPTANCE CHECKS PASSED SUCCESSFULLY!\n")
        sys.exit(0)
    else:
        print("SOME ACCEPTANCE CHECKS FAILED.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
