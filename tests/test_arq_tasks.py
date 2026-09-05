"""Unit tests for ARQ background tasks, max_tries settings, and metric tracking."""

from unittest.mock import AsyncMock, patch

import pytest

from tasks import (
    WorkerSettings,
    cleanup_expired_reservations_task,
    ingest_services_task,
    send_email_task,
)


def test_worker_settings_configuration():
    assert WorkerSettings.max_tries == 3
    assert WorkerSettings.retry_jobs is True
    assert WorkerSettings.job_timeout == 120
    assert len(WorkerSettings.functions) == 3


@pytest.mark.asyncio
async def test_cleanup_expired_reservations_task_success():
    with patch("db.cleanup_expired_reservations", return_value=7):
        result = await cleanup_expired_reservations_task({}, ttl_minutes=15)
        assert result == 7


@pytest.mark.asyncio
async def test_cleanup_expired_reservations_task_failure():
    with patch("db.cleanup_expired_reservations", side_effect=RuntimeError("DB error")):
        with pytest.raises(RuntimeError, match="DB error"):
            await cleanup_expired_reservations_task({}, ttl_minutes=15)


@pytest.mark.asyncio
async def test_send_email_task_success():
    with patch("email_service.email_service.send_email", new_callable=AsyncMock, return_value=True):
        delivered = await send_email_task({}, to="dev@cloudgpt.local", subject="Test", html_body="<p>Hi</p>")
        assert delivered is True


@pytest.mark.asyncio
async def test_send_email_task_failure():
    with patch("email_service.email_service.send_email", new_callable=AsyncMock, side_effect=RuntimeError("SMTP failed")):
        with pytest.raises(RuntimeError, match="SMTP failed"):
            await send_email_task({}, to="dev@cloudgpt.local", subject="Test", html_body="<p>Hi</p>")


@pytest.mark.asyncio
async def test_ingest_services_task_success():
    with patch("ingest_services.ingest_services_rag", new_callable=AsyncMock, return_value=None):
        result = await ingest_services_task({})
        assert result == {"status": "ok"}
