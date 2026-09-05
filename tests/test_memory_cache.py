"""Unit tests for L1 In-Process Memory Cache (core/memory_cache.py)."""

import time
from core.memory_cache import MemoryCache, get_memory_cache


def test_memory_cache_basic_set_get():
    cache = MemoryCache(max_entries=5, ttl_seconds=10.0)
    cache.set("key1", "val1")
    assert cache.get("key1") == "val1"
    assert cache.get("nonexistent") is None


def test_memory_cache_lru_eviction():
    cache = MemoryCache(max_entries=3, ttl_seconds=60.0)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    cache.set("k3", "v3")

    # Access k1 to make k2 the least recently used
    assert cache.get("k1") == "v1"

    # Add k4 -> should evict k2
    cache.set("k4", "v4")
    assert cache.get("k1") == "v1"
    assert cache.get("k2") is None
    assert cache.get("k3") == "v3"
    assert cache.get("k4") == "v4"


def test_memory_cache_ttl_expiration():
    cache = MemoryCache(max_entries=5, ttl_seconds=0.05)
    cache.set("k1", "v1")
    assert cache.get("k1") == "v1"

    time.sleep(0.06)
    # Expired
    assert cache.get("k1") is None
    assert "k1" not in cache


def test_memory_cache_clear_and_stats():
    cache = MemoryCache(max_entries=5, ttl_seconds=60.0)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    assert len(cache) == 2

    cache.clear()
    assert len(cache) == 0
    assert cache.get("k1") is None


def test_get_memory_cache_singleton():
    c1 = get_memory_cache()
    c2 = get_memory_cache()
    assert c1 is c2
