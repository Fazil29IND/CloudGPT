"""Artifact management router for CloudGPT.

Provides endpoints to create, list, and download artifacts generated during chat sessions.
All downloads enforce session ownership and Content-Disposition: attachment headers.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import urllib.parse
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from config import get_settings
from core.redis_client import redis_client
from db import create_artifact_record, get_artifact_record, list_artifacts_for_session
from file_processor import BoundedTTLCache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

ARTIFACT_HOT_KEY_PREFIX = "cloudgpt:artifact:"
_MEMORY_ARTIFACTS = BoundedTTLCache(maxsize=200, default_ttl=86400.0)


class CreateArtifactRequest(BaseModel):
    filename: str = Field(..., description="Name of the artifact file with extension")
    content: str = Field(..., description="File content (plain text or base64)")
    mime: str = Field(default="text/plain", description="MIME content type")
    session_id: str | None = Field(default=None, description="Associated chat session ID")
    message_id: int | None = Field(default=None, description="Associated message ID")
    is_base64: bool = Field(default=False, description="Whether content is base64 encoded")


@router.post("")
async def create_artifact(payload: CreateArtifactRequest, request: Request) -> dict[str, Any]:
    """Create a new artifact record and store its contents."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    settings = get_settings()
    if not settings.enable_artifacts:
        raise HTTPException(status_code=403, detail="Artifacts feature is currently disabled")

    try:
        if payload.is_base64:
            raw_bytes = base64.b64decode(payload.content.encode("ascii"))
        else:
            raw_bytes = payload.content.encode("utf-8")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid artifact content encoding")

    storage_key = f"{ARTIFACT_HOT_KEY_PREFIX}{uuid.uuid4()}"
    _MEMORY_ARTIFACTS.set(storage_key, raw_bytes, ttl=settings.attachment_ttl_seconds * 24)

    # Hot store in Redis with TTL
    if redis_client.is_available:
        await redis_client.set(storage_key, base64.b64encode(raw_bytes).decode("ascii"), ex=settings.attachment_ttl_seconds * 24)

    # Durable store in DB (offloaded to threadpool)
    record = await asyncio.to_thread(
        create_artifact_record,
        user_id=user_id,
        session_id=payload.session_id,
        message_id=payload.message_id,
        filename=payload.filename,
        mime=payload.mime,
        size=len(raw_bytes),
        storage_key=storage_key,
    )

    return {"status": "ok", "artifact": record}


@router.get("")
async def list_artifacts(session_id: str, request: Request) -> list[dict[str, Any]]:
    """List all artifacts generated within a chat session."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    return await asyncio.to_thread(list_artifacts_for_session, user_id, session_id)


@router.get("/{artifact_id}/download")
async def download_artifact(artifact_id: str, request: Request) -> Response:
    """Download an artifact with ownership check and attachment headers."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    record = await asyncio.to_thread(get_artifact_record, artifact_id, user_id=user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Artifact not found")

    storage_key = record["storage_key"]
    raw_data: bytes | None = _MEMORY_ARTIFACTS.get(storage_key)

    if raw_data is None and redis_client.is_available:
        cached_b64 = await redis_client.get(storage_key)
        if cached_b64:
            try:
                raw_data = base64.b64decode(cached_b64.encode("ascii"))
            except Exception:
                pass

    if raw_data is None:
        raise HTTPException(status_code=404, detail="Artifact payload has expired or is unavailable")

    mime = record["mime"]
    # Never render HTML/SVG inline to prevent XSS
    if mime in ("text/html", "image/svg+xml", "application/xhtml+xml"):
        mime = "application/octet-stream"

    raw_filename = record.get("filename") or f"artifact_{artifact_id}.bin"
    # Sanitize against directory traversal, CRLF injection, and quotes
    base_name = os.path.basename(raw_filename).replace("\r", "").replace("\n", "").replace('"', "")
    safe_filename = base_name if base_name.strip() else f"artifact_{artifact_id}.bin"
    encoded_filename = urllib.parse.quote(safe_filename, encoding="utf-8")

    headers = {
        "Content-Disposition": f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{encoded_filename}',
        "Content-Type": mime,
    }
    return Response(content=raw_data, media_type=mime, headers=headers)
