"""L1 in-process LRU memory cache with TTL eviction.

Sits in front of the Redis L2 cache in llm_cache.py.
Thread-safe via asyncio — do not use from sync code.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any


class MemoryCache:
    """In-memory LRU cache with per-item TTL expiration."""

    def __init__(
        self,
        max_entries: int = 200,
        default_ttl: float = 60.0,
        ttl_seconds: float | None = None,
    ) -> None:
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max = max_entries
        self._default_ttl = ttl_seconds if ttl_seconds is not None else default_ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        # Move to end (most-recently-used)
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        expires_at = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, expires_at)
        # Evict oldest if over capacity
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> int:
        """Evict all entries starting with prefix."""
        to_del = [k for k in self._store if k.startswith(prefix)]
        for k in to_del:
            self._store.pop(k, None)
        return len(to_del)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    @property
    def size(self) -> int:
        return len(self._store)


# Global singleton instance
_memory_cache = MemoryCache()


def get_memory_cache() -> MemoryCache:
    return _memory_cache
