"""Best-effort cross-worker sync for model cooldowns.

Cooldown/trip state is kept in-process for the hot path, but the production
deployment runs multiple uvicorn workers: a cooldown tripped in worker A must
become visible in worker B. Redis is written through on every trip and read
back (throttled to at most one read per second) at the start of generation or
embedding calls. When Redis is unavailable every function degrades silently to
no-op, preserving the per-process behavior.

Values store epoch-second expiry timestamps — ``time.monotonic()`` readings are
meaningless across processes.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from core.redis_client import redis_client

# Hold references to fire-and-forget tasks so they are not garbage-collected
# before completing (CPython only keeps weak refs to running tasks).
_background_tasks: set[asyncio.Task] = set()


def schedule_background(coro_factory: Callable[[], object]) -> None:
    """Schedule a coroutine on the running loop if one exists; drop silently
    when called from a sync context without a loop (e.g. module import)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(coro_factory())  # type: ignore[arg-type]
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def persist_cooldown(namespace: str, key: str, ttl_seconds: float) -> None:
    """Write a cooldown expiry to Redis. No-ops when Redis is unavailable."""
    if not redis_client.is_available or redis_client.client is None:
        return
    try:
        await redis_client.client.setex(
            f"{namespace}:{key}",
            max(1, int(ttl_seconds)),
            str(time.time() + ttl_seconds),
        )
    except Exception:
        # Best-effort: a failed sync must never break the request path.
        pass


async def fetch_cooldowns(namespace: str) -> dict[str, float]:
    """Return ``{key: remaining_seconds}`` for persisted cooldowns, dropping
    expired entries. No-ops to an empty dict when Redis is unavailable."""
    if not redis_client.is_available or redis_client.client is None:
        return {}
    try:
        out: dict[str, float] = {}
        async for raw_key in redis_client.client.scan_iter(match=f"{namespace}:*", count=50):
            raw_val = await redis_client.client.get(raw_key)
            if raw_val is None:
                continue
            try:
                expiry = float(raw_val)
            except (TypeError, ValueError):
                continue
            remaining = expiry - time.time()
            if remaining <= 0:
                continue
            key_text = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
            out[key_text.split(":", 1)[1]] = remaining
        return out
    except Exception:
        return {}
