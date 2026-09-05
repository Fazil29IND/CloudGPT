"""ARQ background task definitions for CloudGPT.

Long-running or latency-insensitive work runs here instead of blocking the
request path: quota-reservation cleanup, transactional email delivery, and
RAG re-ingestion. The worker process is started via `worker.py` (see
docker-compose `worker` service).
"""

from __future__ import annotations

import asyncio

from arq import cron
from arq.connections import RedisSettings
import structlog

from config import get_settings
from metrics import TASK_FAILURES_TOTAL, TASK_SUCCESS_TOTAL

logger = structlog.get_logger(__name__)


async def cleanup_expired_reservations_task(ctx: dict, ttl_minutes: int = 10) -> int:
    """Reap quota reservations stuck in 'reserved' beyond the TTL."""
    task_name = "cleanup_expired_reservations_task"
    try:
        import db

        count = await asyncio.to_thread(db.cleanup_expired_reservations, ttl_minutes)
        logger.info("cleanup_reservations_completed", count=count, ttl_minutes=ttl_minutes)
        TASK_SUCCESS_TOTAL.labels(task=task_name).inc()
        return count
    except Exception as exc:
        TASK_FAILURES_TOTAL.labels(task=task_name).inc()
        logger.exception("cleanup_reservations_failed", error=str(exc))
        raise


async def send_email_task(
    ctx: dict, to: str, subject: str, html_body: str, text_body: str | None = None
) -> bool:
    """Deliver a transactional email off the request path."""
    task_name = "send_email_task"
    try:
        from email_service import email_service

        delivered = await email_service.send_email(to=to, subject=subject, html_body=html_body, text_body=text_body)
        if delivered:
            TASK_SUCCESS_TOTAL.labels(task=task_name).inc()
            logger.info("email_delivered_successfully", to=to, subject=subject)
        else:
            TASK_FAILURES_TOTAL.labels(task=task_name).inc()
            logger.warning("email_delivery_unsuccessful", to=to, subject=subject)
        return delivered
    except Exception as exc:
        TASK_FAILURES_TOTAL.labels(task=task_name).inc()
        logger.exception("send_email_task_failed", to=to, subject=subject, error=str(exc))
        raise


async def ingest_services_task(ctx: dict) -> dict:
    """Re-run the Services.md RAG ingestion pipeline (admin-triggered)."""
    task_name = "ingest_services_task"
    try:
        from ingest_services import ingest_services_rag

        await ingest_services_rag()
        TASK_SUCCESS_TOTAL.labels(task=task_name).inc()
        logger.info("ingest_services_task_completed")
        return {"status": "ok"}
    except Exception as exc:
        TASK_FAILURES_TOTAL.labels(task=task_name).inc()
        logger.exception("ingest_services_task_failed", error=str(exc))
        raise


async def startup_task(ctx: dict) -> None:
    logger.info("arq_worker_started", functions=["cleanup", "email", "ingest"])


async def shutdown_task(ctx: dict) -> None:
    logger.info("arq_worker_shutting_down")


class WorkerSettings:
    """Settings consumed by `arq worker.Worker` (see worker.py)."""

    functions = [
        cleanup_expired_reservations_task,
        send_email_task,
        ingest_services_task,
    ]
    on_startup = startup_task
    on_shutdown = shutdown_task

    # Recurring jobs. The reservation reaper must run on a schedule, not just
    # at app startup, so orphaned reservations cannot lock a user's quota for
    # long stretches when requests fail without releasing.
    cron_jobs = [
        cron(
            cleanup_expired_reservations_task,
            hour=None,    # every hour…
            minute=None,  # …at every minute of it
            run_at_startup=True,
            unique=True,
        ),
    ]

    # ARQ Redis connection
    redis_settings: RedisSettings = RedisSettings.from_dsn(get_settings().redis_url)

    max_tries: int = 3
    retry_jobs: bool = True
    job_timeout: int = 120
    max_jobs: int = 10

