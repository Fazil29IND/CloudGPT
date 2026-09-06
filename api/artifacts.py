"""Artifact management router for CloudGPT.

Provides endpoints to create, list, and download artifacts generated during chat sessions.
All downloads enforce session ownership and Content-Disposition: attachment headers.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
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


def extract_artifacts_from_text(text: str) -> list[dict[str, Any]]:
    """Extract downloadable files wrapped in XML tags or annotated code blocks.
    
    Supports:
      1. <cloudgpt_artifact filename="..." title="...">...</cloudgpt_artifact>
      2. <cloudgpt_bundle id="..." title="..."> containing <cloudgpt_artifact>
      3. Code fences with filename annotations: ```terraform filename=main.tf
      4. Code fences with file headers on line 1: # File: main.tf
    """
    if not text:
        return []

    artifacts: list[dict[str, Any]] = []
    seen_filenames: set[str] = set()

    # 1. Parse <cloudgpt_bundle> containers if present
    bundle_pattern = re.compile(r'<cloudgpt_bundle\b([^>]*)>(.*?)</cloudgpt_bundle>', re.DOTALL | re.IGNORECASE)
    for b_match in bundle_pattern.finditer(text):
        b_attrs_raw = b_match.group(1)
        b_body = b_match.group(2)
        b_id_match = re.search(r'\bid=["\']([^"\']+)["\']', b_attrs_raw, re.IGNORECASE)
        b_title_match = re.search(r'\btitle=["\']([^"\']+)["\']', b_attrs_raw, re.IGNORECASE)
        bundle_id = b_id_match.group(1) if b_id_match else None
        bundle_title = b_title_match.group(1) if b_title_match else None

        art_pattern = re.compile(r'<cloudgpt_artifact\b([^>]*)>(.*?)</cloudgpt_artifact>', re.DOTALL | re.IGNORECASE)
        for a_match in art_pattern.finditer(b_body):
            a_attrs = a_match.group(1)
            content = a_match.group(2).strip()
            fn_match = re.search(r'\b(?:filename|file)=["\']([^"\']+)["\']', a_attrs, re.IGNORECASE)
            title_match = re.search(r'\btitle=["\']([^"\']+)["\']', a_attrs, re.IGNORECASE)
            lang_match = re.search(r'\b(?:language|lang)=["\']([^"\']+)["\']', a_attrs, re.IGNORECASE)
            filename = fn_match.group(1) if fn_match else "artifact.txt"
            title = title_match.group(1) if title_match else filename
            language = lang_match.group(1) if lang_match else ""

            if filename in seen_filenames or not content:
                continue
            seen_filenames.add(filename)
            artifacts.append({
                "filename": filename,
                "title": title,
                "language": language,
                "content": content,
                "bundle_id": bundle_id,
                "bundle_title": bundle_title,
            })

    # 2. Parse standalone <cloudgpt_artifact> tags
    standalone_pattern = re.compile(r'<cloudgpt_artifact\b([^>]*)>(.*?)</cloudgpt_artifact>', re.DOTALL | re.IGNORECASE)
    for a_match in standalone_pattern.finditer(text):
        a_attrs = a_match.group(1)
        content = a_match.group(2).strip()
        fn_match = re.search(r'\b(?:filename|file)=["\']([^"\']+)["\']', a_attrs, re.IGNORECASE)
        title_match = re.search(r'\btitle=["\']([^"\']+)["\']', a_attrs, re.IGNORECASE)
        lang_match = re.search(r'\b(?:language|lang)=["\']([^"\']+)["\']', a_attrs, re.IGNORECASE)
        filename = fn_match.group(1) if fn_match else "artifact.txt"
        title = title_match.group(1) if title_match else filename
        language = lang_match.group(1) if lang_match else ""

        if filename in seen_filenames or not content:
            continue
        seen_filenames.add(filename)
        artifacts.append({
            "filename": filename,
            "title": title,
            "language": language,
            "content": content,
            "bundle_id": None,
            "bundle_title": None,
        })

    # 3. Fallback: Parse fenced code blocks with filename metadata if no explicit tags found
    if not artifacts:
        code_fence_pattern = re.compile(r'```([a-zA-Z0-9_\-]+)?\s+(?:filename|file)=["\']?([^\s"\'\n]+)["\']?\n(.*?)```', re.DOTALL)
        for cf_match in code_fence_pattern.finditer(text):
            language = (cf_match.group(1) or "").strip()
            filename = cf_match.group(2).strip()
            content = cf_match.group(3).strip()
            if filename in seen_filenames or not content:
                continue
            seen_filenames.add(filename)
            artifacts.append({
                "filename": filename,
                "title": filename,
                "language": language,
                "content": content,
                "bundle_id": None,
                "bundle_title": None,
            })

    return artifacts


async def store_artifact_record_and_cache(
    *,
    user_id: int,
    session_id: str | None,
    filename: str,
    content: str | bytes,
    mime: str = "text/plain",
    message_id: int | None = None,
) -> dict[str, Any]:
    """Store artifact bytes in hot memory/Redis and durable PostgreSQL."""
    settings = get_settings()
    if isinstance(content, str):
        raw_bytes = content.encode("utf-8")
    else:
        raw_bytes = content

    storage_key = f"{ARTIFACT_HOT_KEY_PREFIX}{uuid.uuid4()}"
    _MEMORY_ARTIFACTS.set(storage_key, raw_bytes, ttl=settings.attachment_ttl_seconds * 24)

    if redis_client.is_available:
        try:
            await redis_client.set(
                storage_key,
                base64.b64encode(raw_bytes).decode("ascii"),
                ex=int(settings.attachment_ttl_seconds * 24),
            )
        except Exception as err:
            logger.warning("Failed storing artifact in redis", error=str(err))

    record = await asyncio.to_thread(
        create_artifact_record,
        user_id=user_id,
        session_id=session_id,
        message_id=message_id,
        filename=filename,
        mime=mime,
        size=len(raw_bytes),
        storage_key=storage_key,
    )
    return record


async def get_artifact_raw_data(record: dict[str, Any]) -> bytes | None:
    """Retrieve raw bytes for an artifact record from memory or Redis."""
    storage_key = record.get("storage_key")
    if not storage_key:
        return None

    raw_data: bytes | None = _MEMORY_ARTIFACTS.get(storage_key)
    if raw_data is None and redis_client.is_available:
        try:
            cached_b64 = await redis_client.get(storage_key)
            if cached_b64:
                raw_data = base64.b64decode(cached_b64.encode("ascii"))
        except Exception:
            pass
    return raw_data


@router.get("")
async def list_artifacts(session_id: str, request: Request) -> list[dict[str, Any]]:
    """List all artifacts generated within a chat session."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    return await asyncio.to_thread(list_artifacts_for_session, user_id, session_id)


@router.get("/session/{session_id}/zip")
async def download_session_zip(session_id: str, request: Request) -> Response:
    """Download all artifacts for a session packaged into an in-memory ZIP archive."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    records = await asyncio.to_thread(list_artifacts_for_session, user_id, session_id)
    if not records:
        raise HTTPException(status_code=404, detail="No artifacts found for this session")

    import io
    import zipfile

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rec in records:
            raw = await get_artifact_raw_data(rec)
            if raw is None:
                continue
            raw_fn = rec.get("filename") or f"artifact_{rec['id']}.txt"
            # Normalize to safe relative path inside zip
            clean_path = os.path.normpath(raw_fn).replace("\\", "/")
            while clean_path.startswith("../") or clean_path.startswith("/"):
                clean_path = clean_path.lstrip("./")
            safe_zip_path = clean_path if clean_path.strip() else f"artifact_{rec['id']}.txt"
            zf.writestr(safe_zip_path, raw)

    zip_bytes = zip_buffer.getvalue()
    if not zip_bytes:
        raise HTTPException(status_code=404, detail="Artifact contents have expired or are unavailable")

    zip_filename = f"cloudgpt-architecture-{session_id[:8]}.zip"
    headers = {
        "Content-Disposition": f'attachment; filename="{zip_filename}"',
        "Content-Type": "application/zip",
    }
    return Response(content=zip_bytes, media_type="application/zip", headers=headers)


@router.get("/{artifact_id}/download")
async def download_artifact(artifact_id: str, request: Request) -> Response:
    """Download an artifact with ownership check and attachment headers."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")

    record = await asyncio.to_thread(get_artifact_record, artifact_id, user_id=user_id)
    if not record:
        raise HTTPException(status_code=404, detail="Artifact not found")

    raw_data = await get_artifact_raw_data(record)
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

