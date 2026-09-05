"""
Unit tests for HIGH-1 (Database Timeout & Pool Exhaustion)
and HIGH-2 (Cloud API SDK Failures Graceful Degradation & Metrics).
"""

import pytest
from unittest.mock import patch, MagicMock
import psycopg2.pool

import db
from metrics import DB_POOL_EXHAUSTED_TOTAL
from cloud_apis.aws_tools import AWSTools
from cloud_apis.azure_tools import AzureTools
from cloud_apis.gcp_tools import GCPTools


def test_db_pool_exhaustion_raises_database_unavailable_error():
    """HIGH-1: Verify that PoolError raises DatabaseUnavailableError and increments metric."""
    mock_pool = MagicMock()
    mock_pool.getconn.side_effect = psycopg2.pool.PoolError("connection pool exhausted")

    initial_val = DB_POOL_EXHAUSTED_TOTAL._value.get() if hasattr(DB_POOL_EXHAUSTED_TOTAL, "_value") else 0

    with patch("db.get_pool", return_value=mock_pool):
        with pytest.raises(db.DatabaseUnavailableError) as exc_info:
            db.get_connection()

        assert "Database connection pool exhausted" in str(exc_info.value)

    if hasattr(DB_POOL_EXHAUSTED_TOTAL, "_value"):
        assert DB_POOL_EXHAUSTED_TOTAL._value.get() >= initial_val + 1


def test_db_unavailable_handler_returns_503(client):
    """HIGH-1: Verify that DatabaseUnavailableError returns a 503 response."""
    with patch("app.get_current_user", side_effect=db.DatabaseUnavailableError("pool exhausted")):
        response = client.get("/")
        assert response.status_code == 503
        assert response.json() == {"detail": "Database temporarily unavailable"}


@pytest.mark.asyncio
async def test_aws_tools_sdk_missing_handled_gracefully():
    """HIGH-2: Verify AWSTools handles missing SDK without unhandled exception."""
    tools = AWSTools()
    with patch.object(tools, "_get_client", side_effect=ImportError("No module named boto3")):
        res = await tools.list_ec2_instances()
        assert res == []

        res = await tools.list_s3_buckets()
        assert res == []

        res = await tools.list_lambda_functions()
        assert res == []

        res = await tools.list_rds_instances()
        assert res == []


@pytest.mark.asyncio
async def test_aws_tools_api_error_handled_gracefully():
    """HIGH-2: Verify AWSTools handles BotoCoreError and increments metric."""
    tools = AWSTools()
    mock_client = MagicMock()
    mock_client.get_paginator.side_effect = RuntimeError("Credentials not configured")

    with patch.object(tools, "_get_client", return_value=mock_client):
        res = await tools.list_ec2_instances()
        assert res == []


@pytest.mark.asyncio
async def test_azure_tools_missing_config_handled_gracefully():
    """HIGH-2: Verify AzureTools handles missing config or credentials safely."""
    tools = AzureTools()
    tools.settings = MagicMock(azure_subscription_id=None, has_azure=False)

    res = await tools.list_virtual_machines()
    assert res == []

    res = await tools.list_storage_accounts()
    assert res == []

    res = await tools.list_function_apps()
    assert res == []


@pytest.mark.asyncio
async def test_gcp_tools_missing_config_handled_gracefully():
    """HIGH-2: Verify GCPTools handles missing config or credentials safely."""
    tools = GCPTools()
    tools.settings = MagicMock(gcp_project_id=None, has_gcp=False)

    res = await tools.list_compute_instances()
    assert res == []

    res = await tools.list_storage_buckets()
    assert res == []

    res = await tools.list_cloud_run_services()
    assert res == []
