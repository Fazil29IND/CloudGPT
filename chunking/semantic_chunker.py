"""
Semantic Document Chunker for CloudGPT.

Splits cloud documentation into semantically meaningful parent-child chunks
preserving heading hierarchy, code blocks, tables, and technical context.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import tiktoken
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DocumentChunk(BaseModel):
    """A single semantically meaningful chunk of documentation."""

    chunk_id: str
    text: str
    provider: str
    category: str = ""
    service: str = ""
    document_type: str = "documentation"
    title: str = ""
    section: str = ""
    subsection: str = ""
    url: str = ""
    source: str = ""
    language: str = "en"
    region: str = "global"
    first_crawled: str = ""
    last_crawled: str = ""
    last_modified: str | None = None
    content_hash: str = ""
    heading_breadcrumb: str = ""
    char_count: int = 0
    token_count: int = 0
    service_status: str = "active"
    parent_chunk_id: Optional[str] = None
    child_index: int = 0
    parser_version: str = "html-md-v2"
    chunker_version: str = "parent-child-v1"
    root_chunk_id: Optional[str] = None
    hierarchy_level: int = 1
    child_chunk_ids: list[str] = Field(default_factory=list)
    sibling_chunk_ids: list[str] = Field(default_factory=list)
    chunk_type: str = "text"
    semantic_density: float = 1.0

    @property
    def content(self) -> str:
        return self.text

    @property
    def is_table(self) -> bool:
        return self.chunk_type == "table" or ("|" in self.text and "---" in self.text)

    @property
    def is_parent(self) -> bool:
        return bool(self.child_chunk_ids) or self.parent_chunk_id is None

    @property
    def metadata(self) -> dict[str, Any]:
        return self.model_dump()


@dataclass
class _HeadingNode:
    """Internal representation of a heading-based document section."""

    level: int
    title: str
    content_lines: list[str] = field(default_factory=list)
    children: list[_HeadingNode] = field(default_factory=list)


class SemanticChunker:
    """Heading-aware and token-budgeted parent-child chunker for cloud documentation.

    Strategy:
        1. Parse document into heading sections (H1 -> H2 -> H3 -> H4).
        2. Create Parent Chunks (approx 1200 tokens) representing complete sub-topics.
        3. Split each Parent Chunk into Child Chunks (approx 450 tokens with 50-token overlap),
           respecting code block and table boundaries.
        4. Maintain linkage via `parent_chunk_id` and `child_index`.
    """

    def __init__(
        self,
        max_chunk_chars: int = 1800,
        min_chunk_chars: int = 200,
        overlap_chars: int = 200,
        tokenizer: str = "cl100k_base",
        parent_token_budget: int = 1200,
        child_token_budget: int = 450,
        child_token_overlap: int = 50,
    ) -> None:
        self.max_chunk_chars = max_chunk_chars
        self.min_chunk_chars = min_chunk_chars
        self.overlap_chars = overlap_chars
        self.parent_token_budget = parent_token_budget
        self.child_token_budget = child_token_budget
        self.child_token_overlap = child_token_overlap

        try:
            self._tokenizer = tiktoken.get_encoding(tokenizer)
        except Exception:
            logger.warning("Could not initialize tiktoken tokenizer '%s', using fallback", tokenizer)
            self._tokenizer = None

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._tokenizer:
            return len(self._tokenizer.encode(text))
        return max(1, len(text) // 4)

    # ── Parent-Child Chunking API ───────────────────────────────────────

    def chunk_document_with_parents(
        self,
        content: str,
        provider: str = "",
        category: str = "",
        service: str = "",
        url: str = "",
        source: str = "",
        document_type: str = "documentation",
        title: str = "",
        last_crawled: str = "",
        service_status: str = "active",
    ) -> list[tuple[DocumentChunk, list[DocumentChunk]]]:
        """Split document into (ParentChunk, [ChildChunk, ...]) pairs.

        Returns:
            List of tuples where each tuple is (parent_chunk, list_of_child_chunks).
        """
        if not content or not content.strip():
            return []

        sections = self._parse_headings(content)
        pairs: list[tuple[DocumentChunk, list[DocumentChunk]]] = []

        for p_idx, (breadcrumb, sec_text, _lvl) in enumerate(sections):
            if not sec_text.strip():
                continue

            parts = [p.strip() for p in breadcrumb.split(" > ") if p.strip()]
            section = parts[1] if len(parts) > 1 else ""
            subsection = parts[2] if len(parts) > 2 else ""
            chunk_title = parts[0] if parts else title

            # Ensure parent is within parent_token_budget
            parent_text = sec_text
            if breadcrumb and not sec_text.startswith("# "):
                parent_text = f"[{breadcrumb}]\n\n{sec_text}"

            parent_id = self._generate_chunk_id(provider, service, section or "main", p_idx) + "-parent"
            parent_hash = hashlib.sha256(parent_text.encode("utf-8")).hexdigest()

            parent_chunk = DocumentChunk(
                chunk_id=parent_id,
                text=parent_text,
                provider=provider,
                category=category,
                service=service,
                document_type=document_type,
                title=chunk_title or title,
                section=section,
                subsection=subsection,
                url=url,
                source=source,
                language="en",
                region="global",
                first_crawled=last_crawled,
                last_crawled=last_crawled,
                content_hash=parent_hash,
                heading_breadcrumb=breadcrumb,
                char_count=len(parent_text),
                token_count=self._count_tokens(parent_text),
                service_status=service_status,
                parent_chunk_id=None,
                child_index=0,
                parser_version="html-md-v2",
                chunker_version="parent-child-v1",
            )

            # Generate child chunks
            child_texts = self._split_into_token_chunks(
                sec_text,
                target_tokens=self.child_token_budget,
                overlap_tokens=self.child_token_overlap,
            )

            children: list[DocumentChunk] = []
            for c_idx, c_text in enumerate(child_texts):
                if not c_text.strip():
                    continue

                child_contextualized = c_text
                if breadcrumb and not c_text.startswith("# "):
                    child_contextualized = f"[{breadcrumb}]\n\n{c_text}"

                child_id = f"{parent_id}-c{c_idx:03d}"
                child_hash = hashlib.sha256(child_contextualized.encode("utf-8")).hexdigest()

                child_chunk = DocumentChunk(
                    chunk_id=child_id,
                    text=child_contextualized,
                    provider=provider,
                    category=category,
                    service=service,
                    document_type=document_type,
                    title=chunk_title or title,
                    section=section,
                    subsection=subsection,
                    url=url,
                    source=source,
                    language="en",
                    region="global",
                    first_crawled=last_crawled,
                    last_crawled=last_crawled,
                    content_hash=child_hash,
                    heading_breadcrumb=breadcrumb,
                    char_count=len(child_contextualized),
                    token_count=self._count_tokens(child_contextualized),
                    service_status=service_status,
                    parent_chunk_id=parent_id,
                    child_index=c_idx,
                    parser_version="html-md-v2",
                    chunker_version="parent-child-v1",
                )
                children.append(child_chunk)

            # Link hierarchical relationships
            child_ids = [c.chunk_id for c in children]
            parent_chunk.child_chunk_ids = child_ids
            parent_chunk.hierarchy_level = 1
            parent_chunk.chunk_type = self._classify_content_type(parent_chunk.text)
            parent_chunk.semantic_density = self._calculate_semantic_density(parent_chunk.text)
            for c in children:
                c.hierarchy_level = 2
                c.sibling_chunk_ids = [cid for cid in child_ids if cid != c.chunk_id]
                c.chunk_type = self._classify_content_type(c.text)
                c.semantic_density = self._calculate_semantic_density(c.text)

            try:
                from chunking.hierarchical_store import HierarchicalChunkStore
                HierarchicalChunkStore.get_instance().register_pair(parent_chunk, children)
            except Exception as e:
                logger.debug("Auto-registration of parent-child pair skipped: %s", e)

            pairs.append((parent_chunk, children))

        return pairs

    # ── Hierarchical 3-Tier Chunking API (Agentic RAG / Pro) ────────────

    def chunk_document_hierarchical(
        self,
        content: str,
        provider: str = "",
        category: str = "",
        service: str = "",
        url: str = "",
        source: str = "",
        document_type: str = "documentation",
        title: str = "",
        last_crawled: str = "",
        service_status: str = "active",
        root_token_budget: int = 2500,
    ) -> tuple[DocumentChunk, list[DocumentChunk], dict[str, list[DocumentChunk]]]:
        """
        Produce a 3-tier semantic hierarchy for Agentic RAG:
        Level 0 (Root Chunk): Overall document/service scope and architectural role.
        Level 1 (Parent Chunks): Key sections / feature topics / major tables.
        Level 2 (Child Chunks): Fine-grained code, commands, limits, parameter details.

        Returns:
            (root_chunk, parent_chunks, children_by_parent_id)
        """
        pairs = self.chunk_document_with_parents(
            content=content,
            provider=provider,
            category=category,
            service=service,
            url=url,
            source=source,
            document_type=document_type,
            title=title,
            last_crawled=last_crawled,
            service_status=service_status,
        )

        root_id = self._generate_chunk_id(provider, service, "root", 0) + "-root"
        root_summary_parts = [
            f"# {title or service or 'Cloud Architecture Reference'}",
            f"Provider: {provider} | Category: {category} | Service: {service}",
            f"Source: {source} | URL: {url}",
            "\n## Document Structure & Key Topics:",
        ]
        for p_idx, (p_chunk, _) in enumerate(pairs[:15]):
            root_summary_parts.append(f"- Topic {p_idx + 1}: {p_chunk.section or p_chunk.title or p_chunk.heading_breadcrumb}")

        if pairs:
            root_summary_parts.append(f"\n## Architecture Overview:\n{pairs[0][0].text[:800]}")

        root_text = "\n".join(root_summary_parts)
        root_hash = hashlib.sha256(root_text.encode("utf-8")).hexdigest()

        parent_chunks = [p for p, _ in pairs]
        parent_ids = [p.chunk_id for p in parent_chunks]
        children_by_parent = {p.chunk_id: ch for p, ch in pairs}

        root_chunk = DocumentChunk(
            chunk_id=root_id,
            text=root_text,
            provider=provider,
            category=category,
            service=service,
            document_type=document_type,
            title=title or service,
            section="root_overview",
            subsection="",
            url=url,
            source=source,
            language="en",
            region="global",
            first_crawled=last_crawled,
            last_crawled=last_crawled,
            content_hash=root_hash,
            heading_breadcrumb=title or service,
            char_count=len(root_text),
            token_count=self._count_tokens(root_text),
            service_status=service_status,
            parent_chunk_id=None,
            root_chunk_id=None,
            hierarchy_level=0,
            child_chunk_ids=parent_ids,
            child_index=0,
            chunk_type="composite",
            semantic_density=1.2,
            parser_version="html-md-v2",
            chunker_version="hierarchical-v2",
        )

        for p in parent_chunks:
            p.root_chunk_id = root_id
            p.parent_chunk_id = root_id
            for ch in children_by_parent.get(p.chunk_id, []):
                ch.root_chunk_id = root_id

        try:
            from chunking.hierarchical_store import HierarchicalChunkStore
            HierarchicalChunkStore.get_instance().register_tree(root_chunk, parent_chunks, children_by_parent)
        except Exception as e:
            logger.debug("Tree registration in HierarchicalChunkStore skipped: %s", e)

        return root_chunk, parent_chunks, children_by_parent

    # ── Adaptive Semantic Chunking API (Adaptive Agentic RAG / Max) ────

    def chunk_document_adaptive(
        self,
        content: str,
        provider: str = "",
        category: str = "",
        service: str = "",
        url: str = "",
        source: str = "",
        document_type: str = "documentation",
        title: str = "",
        last_crawled: str = "",
        service_status: str = "active",
    ) -> list[DocumentChunk]:
        """
        Adaptive Hierarchical Semantic Chunking:
        Dynamically modulates chunk boundaries and token allocations based on
        syntactic and semantic content characteristics:
        - Never slices markdown tables or code blocks across chunk boundaries.
        - High-density code/CLI blocks are packed with tight precision (250-350 tokens).
        - Prose documentation is grouped by semantic heading paragraphs (400-600 tokens).
        - Enriches each chunk with chunk_type, semantic_density, and parent/sibling linkage.
        """
        if not content or not content.strip():
            return []

        sections = self._parse_headings(content)
        adaptive_chunks: list[DocumentChunk] = []

        for p_idx, (breadcrumb, sec_text, level) in enumerate(sections):
            if not sec_text.strip():
                continue

            parts = [p.strip() for p in breadcrumb.split(" > ") if p.strip()]
            section = parts[1] if len(parts) > 1 else (parts[0] if parts else "")
            subsection = parts[2] if len(parts) > 2 else ""
            chunk_title = parts[0] if parts else title

            content_type = self._classify_content_type(sec_text)
            density = self._calculate_semantic_density(sec_text)

            if content_type in ("code", "table"):
                target_budget = min(self.child_token_budget, 300)
                overlap = 30
            elif content_type == "composite":
                target_budget = 400
                overlap = 40
            else:
                target_budget = self.child_token_budget
                overlap = self.child_token_overlap

            parent_text = sec_text
            if breadcrumb and not sec_text.startswith("# "):
                parent_text = f"[{breadcrumb}]\n\n{sec_text}"

            parent_id = self._generate_chunk_id(provider, service, section or "main", p_idx) + "-adapt-parent"
            parent_hash = hashlib.sha256(parent_text.encode("utf-8")).hexdigest()

            parent_chunk = DocumentChunk(
                chunk_id=parent_id,
                text=parent_text,
                provider=provider,
                category=category,
                service=service,
                document_type=document_type,
                title=chunk_title or title,
                section=section,
                subsection=subsection,
                url=url,
                source=source,
                language="en",
                region="global",
                first_crawled=last_crawled,
                last_crawled=last_crawled,
                content_hash=parent_hash,
                heading_breadcrumb=breadcrumb,
                char_count=len(parent_text),
                token_count=self._count_tokens(parent_text),
                service_status=service_status,
                parent_chunk_id=None,
                child_index=0,
                hierarchy_level=1,
                chunk_type=content_type,
                semantic_density=density,
                parser_version="html-md-v2",
                chunker_version="adaptive-hierarchical-v1",
            )

            child_texts = self._split_into_token_chunks(
                sec_text,
                target_tokens=target_budget,
                overlap_tokens=overlap,
            )

            children: list[DocumentChunk] = []
            for c_idx, c_text in enumerate(child_texts):
                if not c_text.strip():
                    continue

                c_ctx = c_text
                if breadcrumb and not c_text.startswith("# "):
                    c_ctx = f"[{breadcrumb}]\n\n{c_text}"

                c_id = f"{parent_id}-c{c_idx:03d}"
                c_hash = hashlib.sha256(c_ctx.encode("utf-8")).hexdigest()
                c_type = self._classify_content_type(c_ctx)
                c_density = self._calculate_semantic_density(c_ctx)

                child_chunk = DocumentChunk(
                    chunk_id=c_id,
                    text=c_ctx,
                    provider=provider,
                    category=category,
                    service=service,
                    document_type=document_type,
                    title=chunk_title or title,
                    section=section,
                    subsection=subsection,
                    url=url,
                    source=source,
                    language="en",
                    region="global",
                    first_crawled=last_crawled,
                    last_crawled=last_crawled,
                    content_hash=c_hash,
                    heading_breadcrumb=breadcrumb,
                    char_count=len(c_ctx),
                    token_count=self._count_tokens(c_ctx),
                    service_status=service_status,
                    parent_chunk_id=parent_id,
                    child_index=c_idx,
                    hierarchy_level=2,
                    chunk_type=c_type,
                    semantic_density=c_density,
                    parser_version="html-md-v2",
                    chunker_version="adaptive-hierarchical-v1",
                )
                children.append(child_chunk)

            child_ids = [c.chunk_id for c in children]
            parent_chunk.child_chunk_ids = child_ids
            for c in children:
                c.sibling_chunk_ids = [cid for cid in child_ids if cid != c.chunk_id]

            try:
                from chunking.hierarchical_store import HierarchicalChunkStore
                HierarchicalChunkStore.get_instance().register_pair(parent_chunk, children)
            except Exception as e:
                logger.debug("Auto-registration of adaptive pair skipped: %s", e)

            adaptive_chunks.extend(children if children else [parent_chunk])

        return adaptive_chunks

    def _classify_content_type(self, text: str) -> str:
        """Classify chunk content as text, code, table, or composite."""
        has_code = bool(re.search(r"```[\s\S]*?```", text))
        has_table = bool(re.search(r"(?:^\|.+\|$\n?)+", text, re.MULTILINE))
        has_cli = bool(re.search(r"(?:aws|gcloud|az|kubectl|terraform)\s+[a-z0-9\-]+", text, re.IGNORECASE))
        if has_code and has_table:
            return "composite"
        if has_code or has_cli:
            return "code"
        if has_table:
            return "table"
        return "text"

    def _calculate_semantic_density(self, text: str) -> float:
        """Measure technical density (commands, flags, identifiers, parameters)."""
        tokens = self._count_tokens(text)
        if tokens == 0:
            return 1.0
        tech_matches = len(re.findall(r"(`[^`]+`|--[a-z0-9\-]+|[A-Z0-9_]{3,}|\b(?:aws|gcp|azure|arn|vpc|iam|ec2|s3)\b)", text, re.IGNORECASE))
        has_table = 20 if "|" in text else 0
        has_code = 30 if "```" in text else 0
        score = 1.0 + min(2.0, (tech_matches * 2 + has_table + has_code) / max(tokens, 1))
        return round(score, 2)

    # ── Legacy & Flat Chunking API ──────────────────────────────────────

    def chunk_document(
        self,
        content: str,
        provider: str = "",
        category: str = "",
        service: str = "",
        url: str = "",
        source: str = "",
        document_type: str = "documentation",
        title: str = "",
        last_crawled: str = "",
        service_status: str = "active",
    ) -> list[DocumentChunk]:
        """Split a cleaned document into semantic chunks with full metadata."""
        if not content or not content.strip():
            logger.warning("Empty content provided for chunking: url=%s", url)
            return []

        # Step 1: Parse heading structure
        sections = self._parse_headings(content)

        # Step 2: Build chunks from sections
        raw_chunks = self._sections_to_chunks(sections)

        # Step 3: Split oversized chunks, merge undersized
        sized_chunks = self._apply_size_constraints(raw_chunks)

        # Step 4: Build DocumentChunk objects with metadata
        chunks: list[DocumentChunk] = []
        for idx, (breadcrumb, text) in enumerate(sized_chunks):
            if not text.strip():
                continue

            parts = [p.strip() for p in breadcrumb.split(" > ") if p.strip()]
            section = parts[1] if len(parts) > 1 else ""
            subsection = parts[2] if len(parts) > 2 else ""
            chunk_title = parts[0] if parts else title

            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunk_id = self._generate_chunk_id(provider, service, section, idx)

            contextualized_text = text
            if breadcrumb and not text.startswith("# "):
                contextualized_text = f"[{breadcrumb}]\n\n{text}"

            chunks.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    text=contextualized_text,
                    provider=provider,
                    category=category,
                    service=service,
                    document_type=document_type,
                    title=chunk_title or title,
                    section=section,
                    subsection=subsection,
                    url=url,
                    source=source,
                    language="en",
                    region="global",
                    first_crawled=last_crawled,
                    last_crawled=last_crawled,
                    content_hash=content_hash,
                    heading_breadcrumb=breadcrumb,
                    char_count=len(contextualized_text),
                    token_count=self._count_tokens(contextualized_text),
                    service_status=service_status,
                    parent_chunk_id=None,
                    child_index=idx,
                    parser_version="html-md-v2",
                    chunker_version="parent-child-v1",
                )
            )

        logger.info(
            "Chunked document: url=%s, chunks=%d, provider=%s, service=%s",
            url,
            len(chunks),
            provider,
            service,
        )
        return chunks

    # ── Token-Based Boundary Splitting ──────────────────────────────────

    def _split_into_token_chunks(
        self,
        text: str,
        target_tokens: int = 450,
        overlap_tokens: int = 50,
    ) -> list[str]:
        """Split text cleanly on code/table/paragraph boundaries within token budget."""
        total_tokens = self._count_tokens(text)
        if total_tokens <= target_tokens:
            return [text]

        protected = self._find_protected_regions(text)
        paragraphs = text.split("\n\n")

        chunks: list[str] = []
        current_chunk = ""

        for p in paragraphs:
            candidate = current_chunk + ("\n\n" if current_chunk else "") + p
            cand_tokens = self._count_tokens(candidate)

            if cand_tokens > target_tokens and current_chunk:
                chunks.append(current_chunk.strip())
                # Overlap tail
                tail_tokens = self._count_tokens(current_chunk)
                if tail_tokens > overlap_tokens and self._tokenizer:
                    enc = self._tokenizer.encode(current_chunk)
                    overlap_str = self._tokenizer.decode(enc[-overlap_tokens:])
                    current_chunk = overlap_str + "\n\n" + p
                else:
                    current_chunk = p
            else:
                current_chunk = candidate

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks if chunks else [text]

    # ── Heading Parser ──────────────────────────────────────────────────

    def _parse_headings(self, content: str) -> list[tuple[str, str, int]]:
        """Parse markdown content into (breadcrumb, section_text, level) tuples."""
        heading_re = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
        sections: list[tuple[str, str, int]] = []

        heading_stack: list[tuple[int, str]] = []
        matches = list(heading_re.finditer(content))

        if not matches:
            return [("", content.strip(), 0)]

        for i, match in enumerate(matches):
            if i == 0 and match.start() > 0:
                pre_text = content[:match.start()].strip()
                if pre_text:
                    sections.append(("", pre_text, 0))

            level = len(match.group(1))
            title = match.group(2).strip()

            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            if len(heading_stack) >= 50:
                logger.warning("heading_stack_depth_exceeded", extra={"depth": len(heading_stack)})
                heading_stack.pop(0)
            heading_stack.append((level, title))

            current_breadcrumb = " > ".join(t for _, t in heading_stack)

            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            section_text = content[start:end].strip()

            heading_line = match.group(0)
            full_text = f"{heading_line}\n\n{section_text}" if section_text else heading_line

            sections.append((current_breadcrumb, full_text, level))

        return sections

    def _sections_to_chunks(
        self, sections: list[tuple[str, str, int]]
    ) -> list[tuple[str, str]]:
        return [(breadcrumb, text) for breadcrumb, text, _level in sections if text.strip()]

    def _apply_size_constraints(
        self, chunks: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []

        for breadcrumb, text in chunks:
            if len(text) <= self.max_chunk_chars:
                result.append((breadcrumb, text))
            else:
                sub_chunks = self._safe_split(text)
                for sub in sub_chunks:
                    result.append((breadcrumb, sub))

        merged: list[tuple[str, str]] = []
        for breadcrumb, text in result:
            if (
                merged
                and len(text) < self.min_chunk_chars
                and len(merged[-1][1]) + len(text) <= self.max_chunk_chars
                and merged[-1][0] == breadcrumb
            ):
                prev_bc, prev_text = merged[-1]
                merged[-1] = (prev_bc, prev_text + "\n\n" + text)
            else:
                merged.append((breadcrumb, text))

        return merged

    def _safe_split(self, text: str) -> list[str]:
        protected = self._find_protected_regions(text)
        separators = ["\n\n", "\n", ". ", " "]
        for sep in separators:
            parts = self._split_with_protection(text, sep, protected)
            if parts and all(len(p) <= self.max_chunk_chars for p in parts):
                return parts

        chunks = []
        for i in range(0, len(text), self.max_chunk_chars - self.overlap_chars):
            chunk = text[i : i + self.max_chunk_chars]
            if chunk.strip():
                chunks.append(chunk.strip())
        return chunks if chunks else [text]

    def _split_with_protection(
        self, text: str, separator: str, protected: list[tuple[int, int]]
    ) -> list[str]:
        if not text:
            return []

        def _is_inside_protected(pos: int) -> bool:
            return any(start < pos < end for start, end in protected)

        parts: list[str] = []
        current = ""
        current_offset = 0

        segments = text.split(separator)
        for segment in segments:
            candidate = current + (separator if current else "") + segment
            split_pos = current_offset + len(current)

            if len(candidate) > self.max_chunk_chars and current:
                # If the proposed split point is inside a code block or table, avoid breaking it
                if not _is_inside_protected(split_pos):
                    parts.append(current.strip())
                    overlap_text = current[-self.overlap_chars :] if self.overlap_chars else ""
                    current_offset += len(current) - len(overlap_text)
                    current = (overlap_text + separator if overlap_text else "") + segment
                else:
                    current = candidate
            else:
                current = candidate

        if current.strip():
            parts.append(current.strip())

        return parts

    def _find_protected_regions(self, text: str) -> list[tuple[int, int]]:
        regions: list[tuple[int, int]] = []
        for match in re.finditer(r"```[\s\S]*?```", text):
            regions.append((match.start(), match.end()))
        table_re = re.compile(r"(?:^\|.+\|$\n?)+", re.MULTILINE)
        for match in table_re.finditer(text):
            regions.append((match.start(), match.end()))
        return regions

    @staticmethod
    def _generate_chunk_id(
        provider: str, service: str, section: str, index: int
    ) -> str:
        safe_section = re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")[:40]
        safe_service = re.sub(r"[^a-z0-9]+", "-", service.lower()).strip("-")[:20]
        provider_prefix = provider.lower()[:5] if provider else "unknown"
        return f"{provider_prefix}-{safe_service}-{safe_section}-{index:05d}"
