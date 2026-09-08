"""Task 6: Test SSE stage event emission during IaC repair loop."""
from __future__ import annotations

import pytest
from config import get_settings
from generation.iac_repair import run_iac_repair_loop


@pytest.mark.asyncio
async def test_iac_repair_sse_events_on_repair_pass():
    """Verify validating_syntax and applying_fixes events are emitted when an artifact is repaired."""
    settings = get_settings()

    failing_tf = """
    <cloudgpt_artifact identifier="main.tf" type="terraform">
    resource "aws_eks_cluster" "main" {
      name    = "demo"
      version = "1.28"
    }
    </cloudgpt_artifact>
    """

    passing_tf = """
    <cloudgpt_artifact identifier="main.tf" type="terraform">
    resource "aws_eks_cluster" "main" {
      name    = "demo"
      version = "1.30"
    }
    resource "aws_eks_node_group" "workers" {
      cluster_name = aws_eks_cluster.main.name
      # AmazonEKSWorkerNodePolicy AmazonEKS_CNI_Policy AmazonEC2ContainerRegistryReadOnly
    }
    </cloudgpt_artifact>
    """

    events = []

    async def mock_emit(evt: dict):
        events.append(evt)

    async def mock_generate(msgs, thinking):
        return passing_tf

    res = await run_iac_repair_loop(
        query="deploy eks",
        prior_answer=failing_tf,
        base_messages=[{"role": "user", "content": "deploy eks"}],
        generate_fn=mock_generate,
        tier="Pro",
        settings=settings,
        emit_event=mock_emit,
    )

    assert res["valid"] is True
    stages = [e["stage"] for e in events if "stage" in e]
    assert "validating_syntax" in stages
    assert "applying_fixes" in stages

    active_syntax = any(e.get("stage") == "validating_syntax" and e.get("status") == "active" for e in events)
    complete_syntax = any(e.get("stage") == "validating_syntax" and e.get("status") == "complete" for e in events)
    active_fixes = any(e.get("stage") == "applying_fixes" and e.get("status") == "active" for e in events)

    assert active_syntax is True
    assert complete_syntax is True
    assert active_fixes is True


@pytest.mark.asyncio
async def test_iac_repair_sse_events_on_clean_pass():
    """When an artifact passes validation immediately, applying_fixes should not be emitted."""
    settings = get_settings()

    passing_tf = """
    <cloudgpt_artifact identifier="main.tf" type="terraform">
    resource "aws_s3_bucket" "b" {
      bucket = "my-test-bucket"
    }
    </cloudgpt_artifact>
    """

    events = []

    async def mock_emit(evt: dict):
        events.append(evt)

    async def mock_generate(msgs, thinking):
        return passing_tf

    res = await run_iac_repair_loop(
        query="create s3 bucket",
        prior_answer=passing_tf,
        base_messages=[{"role": "user", "content": "create s3 bucket"}],
        generate_fn=mock_generate,
        tier="Pro",
        settings=settings,
        emit_event=mock_emit,
    )

    assert res["valid"] is True
    stages = [e["stage"] for e in events if "stage" in e]
    assert "validating_syntax" in stages
    assert "applying_fixes" not in stages
