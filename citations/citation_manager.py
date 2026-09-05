from __future__ import annotations

import re
from pydantic import BaseModel


class CitationSource(BaseModel):
    """Model representing a citation source."""
    source_number: int
    source_type: str  # rag, web, pricing, api
    url: str | None = None
    provider: str | None = None
    service: str | None = None
    title: str | None = None
    section: str | None = None


class ValidationResult(BaseModel):
    """Model representing the result of citation validation."""
    is_valid: bool
    invalid_references: list[int]
    missing_citations: bool


class CitationManager:
    """Manages citations for the generated answers."""

    def __init__(self) -> None:
        self._sources: list[CitationSource] = []
        self._next_source_number = 1

    def reset(self) -> None:
        """Reset the citation manager for a new query."""
        self._sources.clear()
        self._next_source_number = 1

    def register_source(
        self,
        source_type: str,
        url: str | None = None,
        provider: str | None = None,
        service: str | None = None,
        title: str | None = None,
        section: str | None = None,
    ) -> int:
        """
        Register a new source and get its citation number.
        
        Returns:
            The integer source number.
        """
        source_number = self._next_source_number
        self._next_source_number += 1

        source = CitationSource(
            source_number=source_number,
            source_type=source_type,
            url=url,
            provider=provider,
            service=service,
            title=title,
            section=section,
        )
        self._sources.append(source)
        return source_number

    def get_sources(self) -> list[CitationSource]:
        """Get all registered sources."""
        return self._sources

    def format_citations(self) -> str:
        """
        Format the list of registered sources for the answer footer as clean numbered references.
        
        Returns:
            A formatted markdown string of references.
        """
        if not self._sources:
            return ""

        lines = ["\n## References"]
        for idx, source in enumerate(self._sources, 1):
            title = source.title or source.section or (f"{source.provider} {source.service}".strip() if (source.provider or source.service) else "Documentation Resource")
            if source.url:
                lines.append(f"{idx}. [{title}]({source.url})")
            else:
                lines.append(f"{idx}. {title}")

        return "\n".join(lines)

    def validate_answer_citations(self, answer: str) -> ValidationResult:
        """
        Validate that any [SOURCE N] references in the answer refer to registered sources,
        and assess citation grounding (verifying referenced URLs, natural attribution, or ## References).
        
        Args:
            answer: The generated answer from the LLM.
            
        Returns:
            A ValidationResult indicating if the citations and references are valid.
        """
        # 1. Check for legacy [SOURCE N] references in the answer (if any)
        pattern = r"\[SOURCE (\d+)\]"
        matches = re.findall(pattern, answer)

        referenced_numbers = {int(m) for m in matches}
        registered_numbers = {s.source_number for s in self._sources}

        invalid_references = list(referenced_numbers - registered_numbers)

        # 2. Check for markdown URL references matching registered sources
        answer_urls = set(re.findall(r"https?://[^\s\)\"'>]+", answer))
        registered_urls = {s.url.strip() for s in self._sources if s.url and s.url.startswith("http")}

        # 3. Groundedness verification: check references block, matching URLs, or service attribution
        has_references_section = "## References" in answer or "### References" in answer
        has_url_citations = bool(answer_urls & registered_urls)
        has_natural_attribution = any(
            (s.service and len(s.service) > 2 and s.service.lower() in answer.lower())
            or (s.provider and len(s.provider) > 2 and s.provider.lower() in answer.lower())
            for s in self._sources
        )

        missing_citations = (
            len(self._sources) > 0
            and not matches
            and not has_references_section
            and not has_url_citations
            and not has_natural_attribution
        )
        is_valid = len(invalid_references) == 0

        return ValidationResult(
            is_valid=is_valid,
            invalid_references=invalid_references,
            missing_citations=missing_citations,
        )

