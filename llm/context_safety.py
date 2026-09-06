"""
Enterprise Context Safety & Prompt-Injection Boundaries (llm/context_safety.py).

Provides defensive context boundaries for untrusted user-supplied attachments and
external web search snippets:
1. XML Tag Sanitization: Prevents delimiter-collision attacks by neutralizing reserved XML tags.
2. Isolation Envelopes: Wraps untrusted content in deterministic boundary tags marked
   with 'untrusted_reference_only' directives.
3. Injection Risk Heuristics: Scans candidate snippets for instruction-override patterns
   and flags them in the structured safety status.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


class ContextSafetyEngine:
    """Sanitizes untrusted inputs and establishes security boundaries in LLM prompts."""

    # Reserved structural tags that untrusted inputs must not forge
    RESERVED_TAG_PATTERNS = [
        re.compile(r"<\s*/?\s*system\s*>", re.IGNORECASE),
        re.compile(r"<\s*/?\s*user_query\s*>", re.IGNORECASE),
        re.compile(r"<\s*/?\s*verified_cloud_facts\s*>", re.IGNORECASE),
        re.compile(r"<\s*/?\s*agentic_evidence_matrix\s*>", re.IGNORECASE),
        re.compile(r"<\s*/?\s*well_architected_evidence_matrix\s*>", re.IGNORECASE),
        re.compile(r"<\s*/?\s*cloud_tool_executions\s*>", re.IGNORECASE),
        re.compile(r"<\s*/?\s*retrieval_plan\s*>", re.IGNORECASE),
        re.compile(r"<\s*/?\s*untrusted_content\s*>", re.IGNORECASE),
        re.compile(r"<\s*/?\s*attached_reference_data\s*>", re.IGNORECASE),
    ]

    # Common injection and jailbreak vector heuristics
    INJECTION_PATTERNS = [
        re.compile(r"\bignore\s+(all\s+)?(previous|prior|above|system)\s+instructions\b", re.IGNORECASE),
        re.compile(r"\bsystem\s+override\b", re.IGNORECASE),
        re.compile(r"\bdisregard\s+(all\s+)?(guardrails|rules|guidelines)\b", re.IGNORECASE),
        re.compile(r"\byou\s+are\s+now\s+(in\s+)?(developer|jailbreak|unrestricted|god)\s+mode\b", re.IGNORECASE),
        re.compile(r"\bleak\s+(the\s+)?(system\s+prompt|instructions)\b", re.IGNORECASE),
        re.compile(r"\boutput\s+the\s+(system\s+prompt|raw\s+instructions)\b", re.IGNORECASE),
    ]

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """Neutralizes forged XML boundary tags inside untrusted text."""
        if not text:
            return ""

        sanitized = text
        for pat in cls.RESERVED_TAG_PATTERNS:
            sanitized = pat.sub(lambda m: f"[{m.group(0).replace('<', '&lt;').replace('>', '&gt;')}]", sanitized)

        return sanitized

    @classmethod
    def scan_for_injection_risk(cls, text: str) -> tuple[bool, list[str]]:
        """Scans candidate text for prompt-injection indicators."""
        if not text:
            return False, []

        matches: list[str] = []
        for pat in cls.INJECTION_PATTERNS:
            found = pat.findall(text)
            if found:
                matches.append(pat.pattern)

        return len(matches) > 0, matches

    @classmethod
    def wrap_untrusted_content(
        cls,
        content: str,
        source_type: str = "attachment",
        filename: str | None = None,
        boundary_id: str | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """
        Wraps untrusted reference data in a cryptographic boundary container.
        Returns:
            - wrapped_text: str
            - boundary_id: str
            - safety_meta: dict
        """
        if not boundary_id:
            boundary_id = hashlib.sha256((content[:64] + (filename or "")).encode("utf-8", errors="ignore")).hexdigest()[:8]

        has_risk, detected_patterns = cls.scan_for_injection_risk(content)
        sanitized = cls.sanitize_text(content)

        file_attr = f' filename="{filename}"' if filename else ""
        boundary_tag = f'<untrusted_content source="{source_type}" boundary_id="bnd_{boundary_id}"{file_attr} security_status="untrusted_reference_only">'
        wrapped_text = f"{boundary_tag}\n{sanitized}\n</untrusted_content>"

        safety_meta = {
            "boundary_id": f"bnd_{boundary_id}",
            "source_type": source_type,
            "filename": filename,
            "sanitized": sanitized != content,
            "has_injection_risk": has_risk,
            "matched_patterns": detected_patterns,
        }

        return wrapped_text, f"bnd_{boundary_id}", safety_meta

    @classmethod
    def sanitize_and_validate(cls, text: str) -> tuple[bool, str, list[str]]:
        has_risk, matches = cls.scan_for_injection_risk(text)
        sanitized = cls.sanitize_text(text)
        return not has_risk, sanitized, matches

    @classmethod
    def detect_injection(cls, text: str) -> tuple[bool, str]:
        """Convenience method returning (has_risk, matched_pattern)."""
        has_risk, matches = cls.scan_for_injection_risk(text)
        return has_risk, (matches[0] if matches else "")


ContextSafetyGuard = ContextSafetyEngine


def detect_injection(text: str) -> tuple[bool, list[str]]:
    """Module-level helper to detect prompt injection."""
    has_risk, matches = ContextSafetyEngine.scan_for_injection_risk(text)
    return has_risk, matches
