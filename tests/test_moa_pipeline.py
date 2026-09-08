"""Task 7: Test Apex Mixture-of-Agents (MoA) verification pipeline."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from config import get_settings
from generation.moa_pipeline import MoAResult, run_moa_verification


@pytest.mark.asyncio
async def test_moa_verification_remediates_high_severity_finding():
    """When the auditor detects a HIGH severity finding, synthesizer is triggered to fix it."""
    initial_bundle = """
    <cloudgpt_bundle id="eks-bundle">
      <cloudgpt_artifact filename="main.tf" type="terraform">
        resource "aws_security_group" "sg" {
          ingress {
            from_port   = 22
            to_port     = 22
            protocol    = "tcp"
            cidr_blocks = ["0.0.0.0/0"]
          }
        }
      </cloudgpt_artifact>
    </cloudgpt_bundle>
    """

    corrected_bundle = """
    <cloudgpt_bundle id="eks-bundle">
      <cloudgpt_artifact filename="main.tf" type="terraform">
        resource "aws_security_group" "sg" {
          ingress {
            from_port   = 22
            to_port     = 22
            protocol    = "tcp"
            cidr_blocks = ["10.0.0.0/16"]
          }
        }
      </cloudgpt_artifact>
    </cloudgpt_bundle>
    """

    audit_response = json.dumps({
        "findings": [
            {
                "code": "sg.unrestricted_ssh",
                "severity": "high",
                "file": "main.tf",
                "message": "SSH ingress open to 0.0.0.0/0 violates CIS AWS Benchmark 4.1",
            }
        ],
        "summary": "High risk SSH exposure detected.",
        "approved": False,
    })

    mock_auditor = MagicMock()
    mock_auditor.classify = AsyncMock(return_value=audit_response)

    mock_synthesizer = MagicMock()
    mock_synthesizer.generate = AsyncMock(return_value=corrected_bundle)

    events = []

    async def mock_emit(evt: dict):
        events.append(evt)

    with patch("generation.moa_pipeline.get_evaluator_provider", return_value=mock_auditor), \
         patch("generation.moa_pipeline.get_llm_provider", return_value=mock_synthesizer):

        res = await run_moa_verification(
            bundle_text=initial_bundle,
            query="Enterprise secure EKS VPC",
            tier="Apex",
            emit_event=mock_emit,
        )

    assert isinstance(res, MoAResult)
    assert res.repaired is True
    assert res.iterations == 1
    assert res.high_severity_count == 1
    assert "10.0.0.0/16" in res.final_bundle

    stages = [e.get("stage") for e in events if "stage" in e]
    assert "auditing_bundle" in stages
    assert "synthesizing_bundle" in stages


@pytest.mark.asyncio
async def test_moa_verification_clean_bundle_skips_synthesis():
    """When the auditor detects no high-severity findings, synthesis is skipped."""
    bundle = """
    <cloudgpt_bundle id="s3-secure">
      <cloudgpt_artifact filename="main.tf" type="terraform">
        resource "aws_s3_bucket" "b" {
          bucket = "secure-data"
        }
      </cloudgpt_artifact>
    </cloudgpt_bundle>
    """

    clean_audit_response = json.dumps({
        "findings": [],
        "summary": "Bundle passes all CIS benchmark checks.",
        "approved": True,
    })

    mock_auditor = MagicMock()
    mock_auditor.classify = AsyncMock(return_value=clean_audit_response)

    mock_synthesizer = MagicMock()
    mock_synthesizer.generate = AsyncMock()

    events = []

    async def mock_emit(evt: dict):
        events.append(evt)

    with patch("generation.moa_pipeline.get_evaluator_provider", return_value=mock_auditor), \
         patch("generation.moa_pipeline.get_llm_provider", return_value=mock_synthesizer):

        res = await run_moa_verification(
            bundle_text=bundle,
            query="Secure S3 bucket",
            tier="Apex",
            emit_event=mock_emit,
        )

    assert res.repaired is False
    assert res.iterations == 0
    assert res.high_severity_count == 0
    assert res.final_bundle == bundle
    mock_synthesizer.generate.assert_not_called()

    stages = [e.get("stage") for e in events if "stage" in e]
    assert "auditing_bundle" in stages
    assert "synthesizing_bundle" not in stages


@pytest.mark.asyncio
async def test_moa_verification_disabled_flag():
    """When enable_apex_moa is False, pipeline returns input bundle immediately."""
    settings = get_settings()
    with patch.object(settings, "enable_apex_moa", False):
        mock_auditor = MagicMock()
        with patch("generation.moa_pipeline.get_evaluator_provider", return_value=mock_auditor):
            res = await run_moa_verification(
                bundle_text="some bundle",
                query="create vpc",
                tier="Apex",
                settings=settings,
            )
            assert res.final_bundle == "some bundle"
            assert res.iterations == 0
            mock_auditor.classify.assert_not_called()


@pytest.mark.asyncio
async def test_moa_verification_skips_non_apex_tiers():
    """MoA verification only runs on Apex/Max/Developer/Admin tiers."""
    mock_auditor = MagicMock()
    with patch("generation.moa_pipeline.get_evaluator_provider", return_value=mock_auditor):
        for tier in ("Free", "Lite", "Pro", "Core"):
            res = await run_moa_verification(
                bundle_text="some bundle",
                query="create vpc",
                tier=tier,
            )
            assert res.final_bundle == "some bundle"
        mock_auditor.classify.assert_not_called()
