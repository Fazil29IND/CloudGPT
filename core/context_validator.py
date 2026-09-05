"""Context Validator — RAG v2 Safety & Correctness Gate.

Rule-based, zero-LLM context validation running synchronously before Query Analyzer
and after Context Manager on all tiers. Detects prompt injections, scrubs credentials,
checks tenant isolation, and flags stale retrieved chunks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Sequence

import structlog

from config import Settings, get_settings

logger = structlog.get_logger(__name__)

INJECTION_MARKERS: list[str] = [
    "ignore previous instructions",
    "disregard your system prompt",
    "you are now",
    "act as a",
    "jailbreak",
    "####",
    "<|endoftext|>",
]


@dataclass
class ValidationResult:
    is_valid: bool = True
    issues: list[str] = field(default_factory=list)
    sanitized_text: str | None = None
    staleness_flags: list[str] = field(default_factory=list)
    injection_detected: bool = False


class ContextValidator:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        patterns = getattr(self.settings, "context_validator_secret_patterns", [])
        self._compiled_secret_patterns = [
            re.compile(p) for p in patterns
        ]

    def _detect_injection(self, text: str) -> bool:
        if not text:
            return False
        text_lower = text.lower()
        return any(marker in text_lower for marker in INJECTION_MARKERS)

    def _scrub_secrets(self, text: str) -> tuple[str, list[str]]:
        if not text:
            return text, []
        matched_patterns: list[str] = []
        scrubbed = text
        for pattern in self._compiled_secret_patterns:
            if pattern.search(scrubbed):
                matched_patterns.append(pattern.pattern)
                scrubbed = pattern.sub("[REDACTED]", scrubbed)
        return scrubbed, matched_patterns

    def _flag_staleness(self, chunks: Sequence[Any]) -> list[str]:
        stale_chunk_ids: list[str] = []
        threshold_days = getattr(self.settings, "context_staleness_threshold_days", 90)
        now = datetime.now(timezone.utc)

        for chunk in chunks:
            metadata = (
                chunk.metadata
                if hasattr(chunk, "metadata")
                else (chunk.get("metadata", {}) if isinstance(chunk, dict) else {})
            )
            chunk_id = (
                chunk.chunk_id
                if hasattr(chunk, "chunk_id")
                else (chunk.get("chunk_id", "") if isinstance(chunk, dict) else "")
            )
            last_verified_str = metadata.get("last_verified")
            if not last_verified_str:
                logger.debug("context_validator.chunk_unverified", chunk_id=chunk_id)
                continue

            try:
                dt = datetime.fromisoformat(str(last_verified_str))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_days = (now - dt).days
                if age_days > threshold_days:
                    stale_chunk_ids.append(chunk_id or str(metadata.get("title", "unknown_chunk")))
            except Exception as e:
                logger.debug("context_validator.last_verified_parse_error", chunk_id=chunk_id, error=str(e))
        return stale_chunk_ids

    def _check_tenant_isolation(self, chunks: Sequence[Any], tier: str) -> list[str]:
        """Defense-in-depth: check that chunks do not carry unintended user_id metadata."""
        issues: list[str] = []
        for chunk in chunks:
            metadata = (
                chunk.metadata
                if hasattr(chunk, "metadata")
                else (chunk.get("metadata", {}) if isinstance(chunk, dict) else {})
            )
            if "user_id" in metadata and metadata["user_id"]:
                chunk_id = (
                    chunk.chunk_id
                    if hasattr(chunk, "chunk_id")
                    else (chunk.get("chunk_id", "") if isinstance(chunk, dict) else "")
                )
                logger.warning("context_validator.tenant_isolation_violation", chunk_id=chunk_id, tier=tier)
                issues.append(f"Tenant isolation warning on chunk {chunk_id}")
        return issues

    def validate_query(self, query: str) -> ValidationResult:
        injection = self._detect_injection(query)
        scrubbed, matched_secrets = self._scrub_secrets(query)
        issues: list[str] = []

        is_valid = True
        if injection:
            issues.append("Prompt injection marker detected in query")
            if getattr(self.settings, "context_validator_fail_closed", True):
                is_valid = False

        if matched_secrets:
            issues.append(f"Query contained {len(matched_secrets)} sensitive patterns (scrubbed)")

        logger.info(
            "context_validator.query_validated",
            injection_detected=injection,
            secrets_scrubbed_count=len(matched_secrets),
            is_valid=is_valid,
        )

        return ValidationResult(
            is_valid=is_valid,
            issues=issues,
            sanitized_text=scrubbed,
            staleness_flags=[],
            injection_detected=injection,
        )

    def validate_retrieved_chunks(self, chunks: list[Any], tier: str = "Free") -> ValidationResult:
        staleness_flags = self._flag_staleness(chunks)
        isolation_issues = self._check_tenant_isolation(chunks, tier)

        injection_found = False
        secrets_scrubbed_count = 0
        issues = list(isolation_issues)

        # Mutate chunks in-place to scrub secrets
        for chunk in chunks:
            text = (
                chunk.text
                if hasattr(chunk, "text")
                else (chunk.get("content", "") if isinstance(chunk, dict) else "")
            )
            if self._detect_injection(text):
                injection_found = True
                issues.append("Prompt injection detected in retrieved chunk")

            scrubbed, matched = self._scrub_secrets(text)
            if matched:
                secrets_scrubbed_count += len(matched)
                if hasattr(chunk, "text"):
                    chunk.text = scrubbed
                elif isinstance(chunk, dict):
                    chunk["content"] = scrubbed

        is_valid = True
        if injection_found and getattr(self.settings, "context_validator_fail_closed", True):
            is_valid = False

        logger.info(
            "context_validator.chunks_validated",
            chunks_checked=len(chunks),
            stale_count=len(staleness_flags),
            injection_detected=injection_found,
            secrets_scrubbed_count=secrets_scrubbed_count,
            tier=tier,
            is_valid=is_valid,
        )

        return ValidationResult(
            is_valid=is_valid,
            issues=issues,
            sanitized_text=None,
            staleness_flags=staleness_flags,
            injection_detected=injection_found,
        )

    def validate_web_results(self, results: list[dict[str, Any]], tier: str = "Free") -> ValidationResult:
        injection_found = False
        secrets_scrubbed_count = 0
        issues: list[str] = []

        for r in results:
            content = r.get("content", "")
            if self._detect_injection(content):
                injection_found = True
                issues.append("Prompt injection detected in web search result")

            scrubbed, matched = self._scrub_secrets(content)
            if matched:
                secrets_scrubbed_count += len(matched)
                r["content"] = scrubbed

        is_valid = True
        if injection_found and getattr(self.settings, "context_validator_fail_closed", True):
            is_valid = False

        logger.info(
            "context_validator.web_results_validated",
            chunks_checked=len(results),
            injection_detected=injection_found,
            secrets_scrubbed_count=secrets_scrubbed_count,
            tier=tier,
            is_valid=is_valid,
        )

        return ValidationResult(
            is_valid=is_valid,
            issues=issues,
            sanitized_text=None,
            staleness_flags=[],
            injection_detected=injection_found,
        )
