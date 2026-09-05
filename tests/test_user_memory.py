"""Unit tests for user memory (core/user_memory.py and core/entitlements.py)."""

import pytest
from unittest.mock import patch

from core.user_memory import extract_durable_user_facts, get_user_memories_cached
from core.entitlements import resolve_entitlements


def test_extract_durable_user_facts_patterns():
    # Test AWS cloud detection
    facts_aws = extract_durable_user_facts("We use AWS for all of our production services.")
    assert any(f["key"] == "primary_cloud" and "AWS" in f["value"] for f in facts_aws)

    # Test Region detection
    facts_region = extract_durable_user_facts("Our primary region is us-east-1.")
    assert any(f["key"] == "primary_region" and f["value"] == "us-east-1" for f in facts_region)

    # Test Terraform IaC detection
    facts_iac = extract_durable_user_facts("We use terraform for infrastructure as code.")
    assert any(f["key"] == "iac_tool" and "terraform" in f["value"].lower() for f in facts_iac)

    # Test Kubernetes detection
    facts_k8s = extract_durable_user_facts("We deploy on kubernetes in production.")
    assert any(f["key"] == "container_platform" and "Kubernetes" in f["value"] for f in facts_k8s)

    # Test Backend language
    facts_lang = extract_durable_user_facts("Our backend is written in python.")
    assert any(f["key"] == "backend_language" and "python" in f["value"].lower() for f in facts_lang)


def test_extract_durable_user_facts_empty():
    assert extract_durable_user_facts("") == []
    assert extract_durable_user_facts("What is AWS Lambda pricing?") == []


def test_user_memory_entitlements():
    ent_lite = resolve_entitlements("Lite")
    assert ent_lite.has_user_memory is False

    ent_pro = resolve_entitlements("Pro")
    assert ent_pro.has_user_memory is True

    ent_max = resolve_entitlements("Max")
    assert ent_max.has_user_memory is True

    ent_dev = resolve_entitlements("Developer")
    assert ent_dev.has_user_memory is True


@pytest.mark.asyncio
async def test_get_user_memories_cached_none_user():
    res = await get_user_memories_cached(None)
    assert res == []


@pytest.mark.asyncio
async def test_get_user_memories_cached_db_fallback():
    from unittest.mock import PropertyMock
    from core.redis_client import RedisManager

    mock_db_memories = [
        {"id": 1, "memory_key": "primary_cloud", "memory_value": "AWS", "category": "cloud_infrastructure"}
    ]
    with patch.object(RedisManager, "is_available", new_callable=PropertyMock, return_value=False), \
         patch("db.get_user_memories", return_value=mock_db_memories):
        res = await get_user_memories_cached(123)
        assert len(res) == 1
        assert res[0]["memory_key"] == "primary_cloud"

