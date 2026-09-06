"""Tests for CloudGPT Artifact Extraction, Multi-File Bundling, and Session ZIP Downloads.

Verifies:
1. Extraction of <cloudgpt_artifact> and <cloudgpt_bundle> tags from model text.
2. Fallback extraction of annotated code blocks (```terraform filename=main.tf).
3. Hot-caching and database storage of extracted artifacts.
4. ZIP archive generation via GET /api/artifacts/session/{session_id}/zip.
5. User isolation and authorization on ZIP downloads.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.artifacts import extract_artifacts_from_text, store_artifact_record_and_cache


SAMPLE_LLM_OUTPUT_APEX = """
## Architecture Blueprint & Executive Summary
Here is the production-grade multi-account architecture for a Global MNC.

<cloudgpt_bundle id="fortune500-landing-zone" title="Fortune 500 Enterprise Landing Zone">
<cloudgpt_artifact filename="terragrunt.hcl" title="Root Terragrunt Config" language="hcl">
locals {
  org_name = "global-corp"
  aws_region = "us-east-1"
}
</cloudgpt_artifact>

<cloudgpt_artifact filename="modules/vpc/main.tf" title="Enterprise VPC Module" language="hcl">
resource "aws_vpc" "inspection" {
  cidr_block = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
}
</cloudgpt_artifact>

<cloudgpt_artifact filename="k8s/deployment.yaml" title="Production App Deployment" language="yaml">
apiVersion: apps/v1
kind: Deployment
metadata:
  name: banking-core
spec:
  replicas: 5
</cloudgpt_artifact>
</cloudgpt_bundle>

## Operational Risk & Governance
Complete runbooks and guardrails.
"""

SAMPLE_LLM_OUTPUT_CORE = """
## Executive Summary
Turnkey startup stack for ECS Fargate with Aurora PostgreSQL.

<cloudgpt_artifact filename="terraform/main.tf" title="ECS Fargate Stack" language="hcl">
resource "aws_ecs_cluster" "startup_prod" {
  name = "startup-prod"
}
</cloudgpt_artifact>

<cloudgpt_artifact filename="Dockerfile" title="Multi-Stage Dockerfile" language="dockerfile">
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
</cloudgpt_artifact>

<cloudgpt_artifact filename="docker-compose.yml" title="Local Development Compose" language="yaml">
version: "3.8"
services:
  app:
    build: .
    ports:
      - "8000:8000"
</cloudgpt_artifact>
"""

SAMPLE_LLM_FALLBACK_BLOCKS = """
Here is your configuration:

```terraform filename=main.tf
provider "aws" {
  region = "us-west-2"
}
```

```yaml filename=.github/workflows/deploy.yml
name: Deploy
on: [push]
```
"""


def test_extract_artifacts_bundle_apex():
    """Verify multi-file bundle extraction from Apex enterprise output."""
    arts = extract_artifacts_from_text(SAMPLE_LLM_OUTPUT_APEX)
    assert len(arts) == 3
    filenames = [a["filename"] for a in arts]
    assert "terragrunt.hcl" in filenames
    assert "modules/vpc/main.tf" in filenames
    assert "k8s/deployment.yaml" in filenames

    # Verify bundle tagging
    for a in arts:
        assert a["bundle_id"] == "fortune500-landing-zone"
        assert a["bundle_title"] == "Fortune 500 Enterprise Landing Zone"

    # Verify content
    vpc_art = next(a for a in arts if a["filename"] == "modules/vpc/main.tf")
    assert 'resource "aws_vpc" "inspection"' in vpc_art["content"]


def test_extract_artifacts_standalone_core():
    """Verify standalone artifact extraction from Core startup output."""
    arts = extract_artifacts_from_text(SAMPLE_LLM_OUTPUT_CORE)
    assert len(arts) == 3
    filenames = [a["filename"] for a in arts]
    assert "terraform/main.tf" in filenames
    assert "Dockerfile" in filenames
    assert "docker-compose.yml" in filenames

    docker_art = next(a for a in arts if a["filename"] == "Dockerfile")
    assert "FROM python:3.12-slim" in docker_art["content"]


def test_extract_artifacts_code_fences_fallback():
    """Verify fallback extraction for fenced code blocks with filename attributes."""
    arts = extract_artifacts_from_text(SAMPLE_LLM_FALLBACK_BLOCKS)
    assert len(arts) == 2
    assert arts[0]["filename"] == "main.tf"
    assert arts[1]["filename"] == ".github/workflows/deploy.yml"


@pytest.mark.asyncio
async def test_store_and_download_session_zip(app_instance):
    """Verify storing artifacts and downloading the bundled session ZIP archive."""
    client = TestClient(app_instance)

    # Create User A
    import db
    email_a = f"zip_user_a_{uuid.uuid4().hex[:6]}@test.cloudgpt.local"
    user_a = db.create_user(email=email_a, password="HashPassword123!", name="User A")
    user_id_a = user_a["id"]

    session_id = f"test-sess-{uuid.uuid4().hex[:8]}"

    # Store 2 artifacts
    art1 = await store_artifact_record_and_cache(
        user_id=user_id_a,
        session_id=session_id,
        filename="terraform/main.tf",
        content='resource "aws_s3_bucket" "b" { bucket = "my-test-bucket" }',
        mime="text/plain",
    )
    art2 = await store_artifact_record_and_cache(
        user_id=user_id_a,
        session_id=session_id,
        filename="Dockerfile",
        content="FROM alpine:3.18\nCMD ['echo', 'ready']",
        mime="text/plain",
    )
    assert art1["id"]
    assert art2["id"]

    # Download ZIP with mock session or logged-in client
    with patch("starlette.requests.HTTPConnection.session", new_callable=lambda: property(lambda self: {"user_id": user_id_a})):
        zip_res = client.get(f"/api/artifacts/session/{session_id}/zip")
        assert zip_res.status_code == 200
        assert zip_res.headers["content-type"] == "application/zip"
        assert "attachment" in zip_res.headers["content-disposition"]
        assert f"cloudgpt-architecture-{session_id[:8]}.zip" in zip_res.headers["content-disposition"]

        # Validate that the ZIP is valid and contains both files
        zip_data = io.BytesIO(zip_res.content)
        with zipfile.ZipFile(zip_data, "r") as zf:
            namelist = zf.namelist()
            assert "terraform/main.tf" in namelist
            assert "Dockerfile" in namelist

            tf_content = zf.read("terraform/main.tf").decode("utf-8")
            assert 'resource "aws_s3_bucket"' in tf_content

            docker_content = zf.read("Dockerfile").decode("utf-8")
            assert "FROM alpine:3.18" in docker_content


@pytest.mark.asyncio
async def test_session_zip_user_isolation(app_instance):
    """Verify that User B cannot download User A's session ZIP archive."""
    client = TestClient(app_instance)

    user_a_id = 99901
    user_b_id = 99902
    session_id = f"test-iso-{uuid.uuid4().hex[:8]}"

    await store_artifact_record_and_cache(
        user_id=user_a_id,
        session_id=session_id,
        filename="secret.tf",
        content="secret_token = 123",
    )

    # User B requests User A's session ZIP -> should return 404 (no artifacts found for User B)
    with patch("starlette.requests.HTTPConnection.session", new_callable=lambda: property(lambda self: {"user_id": user_b_id})):
        res = client.get(f"/api/artifacts/session/{session_id}/zip")
        assert res.status_code == 404
        assert "No artifacts found" in res.json()["detail"]
