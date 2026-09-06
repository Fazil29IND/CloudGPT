"""Semantic similarity cache for near-duplicate query detection.

Stores (embedding_vector, cache_key, expires_at) tuples and returns
the cached answer for the nearest match above a configurable threshold.
Supports intent-aware similarity thresholds (higher precision for pricing/troubleshooting)
and optional Redis persistence.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from typing import Any

from config import get_settings
from core.redis_client import redis_client

logger = logging.getLogger(__name__)


try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False


def _cosine(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vector lists."""
    if not a or not b or len(a) != len(b):
        return 0.0
    if _NUMPY_AVAILABLE:
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        norm_a = float(np.linalg.norm(va))
        norm_b = float(np.linalg.norm(vb))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return float(np.dot(va, vb) / (norm_a * norm_b))
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def get_threshold_for_intent(intent: str | None) -> float:
    """Return tuned similarity threshold based on query intent."""
    settings = get_settings()
    if not intent:
        return getattr(settings, "semantic_cache_threshold", 0.92)
    norm = str(intent).strip().lower()
    if norm in ("pricing", "cost", "calculator"):
        return getattr(settings, "semantic_cache_threshold_pricing", 0.95)
    if norm in ("troubleshooting", "error", "debug", "incident"):
        return getattr(settings, "semantic_cache_threshold_troubleshooting", 0.95)
    if norm in ("conceptual", "overview", "comparison", "general"):
        return getattr(settings, "semantic_cache_threshold_conceptual", 0.90)
    return getattr(settings, "semantic_cache_threshold", 0.92)


def normalize_query_for_cache(query: str) -> str:
    """Canonicalize query for deterministic semantic and exact cache keys."""
    if not query:
        return ""
    q = query.strip().lower()
    q = re.sub(r"[?!.,;:]+$", "", q).strip()
    return re.sub(r"\s+", " ", q)


def generate_semcache_key(cache_key: str, corpus_version: str = "v1", prompt_version: str = "v1") -> str:
    """Generate versioned Redis key for semantic cache entry."""
    import hashlib
    canonical = normalize_query_for_cache(cache_key)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"semcache:{corpus_version}:{prompt_version}:{digest}"


class SemanticCache:
    """Semantic cache storing embedding vectors to match near-duplicate queries."""

    def __init__(
        self,
        max_entries: int = 500,
        threshold: float = 0.92,
        default_threshold: float | None = None,
        ttl: int = 3600,
    ) -> None:
        self._entries: list[tuple[list[float], str, float]] = []
        self._max = max_entries
        self._threshold = default_threshold if default_threshold is not None else threshold
        self._ttl = ttl

    def get(
        self,
        embedding: list[float],
        threshold: float | None = None,
        intent: str | None = None,
    ) -> tuple[str | None, float]:
        """Return (best_cache_key, similarity_score) if above threshold, else (None, 0.0)."""
        if not embedding or not self._entries:
            return None, 0.0
        now = time.monotonic()
        best_score = 0.0
        best_key = None
        live = []
        for vec, key, exp in self._entries:
            if exp < now:
                continue  # expired
            live.append((vec, key, exp))

        if len(live) < len(self._entries) * 0.8:
            self._entries = live

        if not live:
            return None, 0.0

        if _NUMPY_AVAILABLE and len(embedding) > 0:
            q = np.asarray(embedding, dtype=np.float32)
            norm_q = float(np.linalg.norm(q))
            if norm_q > 0.0:
                mat = np.asarray([e[0] for e in live], dtype=np.float32)
                norms = np.linalg.norm(mat, axis=1)
                valid_mask = norms > 0.0
                if np.any(valid_mask):
                    safe_norms = np.where(valid_mask, norms, 1.0)
                    sims = np.dot(mat, q) / (safe_norms * norm_q)
                    sims[~valid_mask] = 0.0
                    best_idx = int(np.argmax(sims))
                    best_score = float(sims[best_idx])
                    best_key = live[best_idx][1]
        else:
            for vec, key, _ in live:
                score = _cosine(embedding, vec)
                if score > best_score:
                    best_score = score
                    best_key = key

        if threshold is not None:
            target_threshold = threshold
        elif intent is not None:
            target_threshold = get_threshold_for_intent(intent)
        else:
            target_threshold = self._threshold

        # Prometheus metrics recording if available
        try:
            from metrics import SEMANTIC_CACHE_SIMILARITY
            SEMANTIC_CACHE_SIMILARITY.observe(best_score)
        except Exception:
            pass

        if best_score >= target_threshold and best_key is not None:
            return best_key, best_score
        return None, best_score

    def find(self, embedding: list[float], intent: str | None = None) -> str | None:
        """Return the cache key of the most similar entry above threshold."""
        target_thresh = get_threshold_for_intent(intent) if intent else self._threshold
        key, _ = self.get(embedding, threshold=target_thresh, intent=intent)
        return key

    def set(
        self,
        embedding: list[float],
        cache_key: str,
        ttl: int | None = None,
        intent: str | None = None,
    ) -> None:
        """Register an embedding vector and associated cache key."""
        self.add(embedding, cache_key, ttl=ttl, intent=intent)

    def add(
        self,
        embedding: list[float],
        cache_key: str,
        ttl: int | None = None,
        intent: str | None = None,
    ) -> None:
        """Register an embedding vector and associated cache key with in-memory + redis sync."""
        if not embedding or not cache_key:
            return
        effective_ttl = ttl if ttl is not None else self._ttl
        expires_at = time.monotonic() + effective_ttl
        self._entries.append((embedding, cache_key, expires_at))
        if len(self._entries) > self._max:
            self._entries.pop(0)  # FIFO eviction

        # Sync to Redis if available using versioned semcache schema
        if redis_client.is_available:
            try:
                import asyncio
                settings = get_settings()
                c_ver = getattr(settings, "cache_corpus_version", "v1")
                p_ver = getattr(settings, "cache_prompt_version", "v1")
                r_key = generate_semcache_key(cache_key, c_ver, p_ver)
                data = json.dumps({
                    "embedding": embedding,
                    "target_key": cache_key,
                    "intent": intent or "general",
                    "corpus_version": c_ver,
                    "prompt_version": p_ver,
                })
                # Non-blocking async dispatch if loop running
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(redis_client.set(r_key, data, ex=effective_ttl))
                except RuntimeError:
                    pass
            except Exception as e:
                logger.debug("Redis semantic cache sync skipped: %s", e)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return self.size

    @property
    def size(self) -> int:
        now = time.monotonic()
        return sum(1 for _, _, exp in self._entries if exp >= now)

    async def warm_cache_from_redis(self) -> int:
        """Populate in-memory index from persistent Redis semcache keys on startup."""
        if not redis_client.is_available:
            return 0
        loaded = 0
        try:
            client = redis_client.client
            if client is None:
                return 0
            settings = get_settings()
            c_ver = getattr(settings, "cache_corpus_version", "v1")
            p_ver = getattr(settings, "cache_prompt_version", "v1")
            pattern = f"semcache:{c_ver}:{p_ver}:*"
            cursor = 0
            keys: list[Any] = []
            while True:
                cursor, batch = await client.scan(cursor=cursor, match=pattern, count=100)
                keys.extend(batch)
                if cursor == 0 or len(keys) >= self._max:
                    break

            # Fallback scan for legacy keys if versioned semcache was empty
            if not keys:
                cursor = 0
                while True:
                    cursor, batch = await client.scan(cursor=cursor, match="semantic_cache:*", count=100)
                    keys.extend(batch)
                    if cursor == 0 or len(keys) >= self._max:
                        break

            target_keys = keys[:self._max]
            if target_keys:
                batch_size = 100
                for i in range(0, len(target_keys), batch_size):
                    chunk = target_keys[i : i + batch_size]
                    try:
                        vals = await client.mget(chunk)
                    except Exception:
                        vals = [await client.get(k) for k in chunk]

                    for k, val in zip(chunk, vals):
                        if val:
                            try:
                                obj = json.loads(val)
                                emb = obj.get("embedding")
                                cache_key = obj.get("target_key")
                                if not cache_key:
                                    raw_k = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                                    if raw_k.startswith("semantic_cache:"):
                                        cache_key = raw_k[len("semantic_cache:"):]
                                    else:
                                        cache_key = raw_k
                                if emb and cache_key:
                                    expires_at = time.monotonic() + self._ttl
                                    self._entries.append((emb, cache_key, expires_at))
                                    loaded += 1
                            except Exception:
                                continue
            logger.info("semantic_cache.warmed_from_redis count=%s", loaded)
        except Exception as e:
            logger.debug("Failed to warm semantic cache from Redis: %s", e)
        return loaded


_semantic_cache = SemanticCache()


def get_semantic_cache() -> SemanticCache:
    return _semantic_cache


async def warm_semantic_cache() -> int:
    """Warm up the semantic similarity cache from Redis."""
    return await _semantic_cache.warm_cache_from_redis()

