"""
Documentation Manifest Schema for CloudGPT Corpus.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ManifestEntry(BaseModel):
    """A canonical document entry tracked in the documentation manifest."""

    entry_id: str = Field(..., description="Unique identifier sha256(provider+canonical_url)[:16]")
    provider: str = Field(..., description="Cloud provider: aws, azure, gcp")
    service: str = Field(..., description="Cloud service name, e.g., s3, compute_engine, aks")
    product_family: str = Field(default="general", description="Category or product group")
    document_type: str = Field(
        default="user_guide",
        description="Type: user_guide, api_ref, troubleshooting, architecture, iac, cli",
    )
    title: str = Field(..., description="Document title")
    canonical_url: str = Field(..., description="Canonical HTTP URL of the document")
    last_seen: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of last check")
    content_hash: Optional[str] = Field(default=None, description="SHA-256 hash of normalized text")
    last_modified: Optional[str] = Field(default=None, description="HTTP Last-Modified header value")
    etag: Optional[str] = Field(default=None, description="HTTP ETag header value")
    http_status: Optional[int] = Field(default=None, description="Last HTTP status code")
    crawl_policy: str = Field(default="allowed", description="Policy: allowed, blocked, rate_limited")
    language: str = Field(default="en", description="Document language")
    corpus_version: str = Field(default="v1", description="Corpus schema version")

    @classmethod
    def create(
        cls,
        provider: str,
        service: str,
        title: str,
        canonical_url: str,
        product_family: str = "general",
        document_type: str = "user_guide",
        corpus_version: str = "v1",
    ) -> ManifestEntry:
        """Helper to create a ManifestEntry with deterministic entry_id."""
        raw_key = f"{provider.lower()}:{canonical_url.strip().lower()}"
        entry_id = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
        return cls(
            entry_id=entry_id,
            provider=provider.lower(),
            service=service.lower(),
            product_family=product_family.lower(),
            document_type=document_type.lower(),
            title=title.strip(),
            canonical_url=canonical_url.strip(),
            corpus_version=corpus_version,
        )
