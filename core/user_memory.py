"""
Persistent user preference memory and durable context extraction for CloudGPT.

Manages durable facts (primary cloud provider, region, preferred IaC tool, tech stack)
that persist across sessions and re-enter prompts within a bounded token budget.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from config import get_settings
from core.redis_client import redis_client
import db

logger = logging.getLogger(__name__)


def _memory_cache_key(user_id: int) -> str:
    return f"user_memory:{user_id}"


async def get_user_memories_cached(user_id: int | None) -> list[dict[str, Any]]:
    """Retrieve user memories with Redis caching and PostgreSQL fallback."""
    if not user_id:
        return []

    key = _memory_cache_key(user_id)
    if redis_client.is_available:
        try:
            cached_json = await redis_client.get(key)
            if cached_json:
                return json.loads(cached_json)
        except Exception as e:
            logger.debug("Redis user_memory read error: %s", e)

    # Fallback to PostgreSQL
    memories = await asyncio.to_thread(db.get_user_memories, user_id)
    if redis_client.is_available and memories:
        try:
            settings = get_settings()
            await redis_client.set(
                key, json.dumps(memories, default=str), ex=settings.redis_session_ttl_seconds
            )
        except Exception as e:
            logger.debug("Redis user_memory set error: %s", e)

    return memories


async def upsert_user_memory_cached(
    user_id: int,
    key: str,
    value: str,
    category: str = "general",
    confidence: float = 1.0,
) -> dict[str, Any] | None:
    """Store or update user memory fact, syncing PostgreSQL and invalidating Redis cache."""
    if not user_id or not key or not value:
        return None

    result = await asyncio.to_thread(db.upsert_user_memory, user_id, key, value, category, confidence)

    if redis_client.is_available:
        try:
            await redis_client.delete(_memory_cache_key(user_id))
        except Exception as e:
            logger.debug("Redis user_memory invalidate error: %s", e)

    return result


async def delete_user_memory_cached(user_id: int, memory_id: int) -> bool:
    """Delete user memory fact by ID, syncing PostgreSQL and invalidating Redis cache."""
    if not user_id or not memory_id:
        return False

    deleted = await asyncio.to_thread(db.delete_user_memory, user_id, memory_id)

    if redis_client.is_available:
        try:
            await redis_client.delete(_memory_cache_key(user_id))
        except Exception as e:
            logger.debug("Redis user_memory delete invalidate error: %s", e)

    return deleted


async def delete_all_user_memory_cached(user_id: int) -> int:
    """Delete all user memory facts for a user, syncing PostgreSQL and invalidating Redis cache."""
    if not user_id:
        return 0

    deleted = await asyncio.to_thread(db.delete_all_user_memory, user_id)

    if redis_client.is_available:
        try:
            await redis_client.delete(_memory_cache_key(user_id))
        except Exception as e:
            logger.debug("Redis user_memory delete all invalidate error: %s", e)

    return deleted


# ── Fast Heuristic Durable Fact Extractor ─────────────────────────────────────
FACT_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"\b(?:we use|our (?:primary )?cloud is|org is on)\s+(aws|amazon web services|gcp|google cloud|azure)\b", re.I),
        "primary_cloud",
        "cloud_infrastructure",
    ),
    (
        re.compile(r"\b(?:our (?:default|primary|main) region is|we deploy in(?:to)?)\s+([a-z]{2}-[a-z]+-\d)\b", re.I),
        "primary_region",
        "cloud_infrastructure",
    ),
    (
        re.compile(r"\b(?:we use|our iac tool is|our infrastructure is in)\s+(terraform|opentofu|pulumi|cdk|cloudformation)\b", re.I),
        "iac_tool",
        "devops_tools",
    ),
    (
        re.compile(r"\b(?:we deploy (?:on|with)|our cluster is)\s+(kubernetes|k8s|eks|gke|aks|ecs|cloud run)\b", re.I),
        "container_platform",
        "compute_architecture",
    ),
    (
        re.compile(r"\b(?:our stack is|we code in|our backend is(?:\s+written in)?)\s+(python|typescript|golang|java|rust|c#|\.net)\b", re.I),
        "backend_language",
        "tech_stack",
    ),

]


def extract_durable_user_facts(text: str) -> list[dict[str, str]]:
    """Extract durable architectural facts and preferences from a user message."""
    if not text:
        return []

    facts = []
    for pattern, key, category in FACT_PATTERNS:
        match = pattern.search(text)
        if match:
            val = match.group(1).strip()
            # Clean up normalized values
            if val.lower() in ("aws", "amazon web services"):
                val = "AWS (Amazon Web Services)"
            elif val.lower() in ("gcp", "google cloud"):
                val = "GCP (Google Cloud Platform)"
            elif val.lower() == "azure":
                val = "Microsoft Azure"
            elif val.lower() in ("k8s", "kubernetes"):
                val = "Kubernetes"
            facts.append({"key": key, "value": val, "category": category})
    return facts
