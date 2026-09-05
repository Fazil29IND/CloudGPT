"""
Hierarchical Chunk Store for CloudGPT.

Manages in-memory and persistent storage of DocumentChunks across hierarchy levels:
- Level 0: Root / Document Overview
- Level 1: Parent / Topic Section
- Level 2: Child / Atomic Subtopic Leaf

Provides O(1) lookup, parent-child resolution, sibling discovery, and coalescing
to prevent context fragmentation during RAG retrieval.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

from chunking.semantic_chunker import DocumentChunk

logger = logging.getLogger(__name__)


class HierarchicalChunkStore:
    """Thread-safe store for hierarchical DocumentChunks with fast relational lookup."""

    _instance: Optional[HierarchicalChunkStore] = None
    _lock = threading.Lock()

    def __init__(self, chunks_path: Path | str | None = None) -> None:
        self._chunks: dict[str, DocumentChunk] = {}
        self._parents_by_child: dict[str, str] = {}
        self._children_by_parent: dict[str, list[str]] = {}
        self._roots_by_child: dict[str, str] = {}
        self._store_lock = threading.RLock()
        self._loaded_paths: set[str] = set()
        if chunks_path:
            self.load_chunks_file(chunks_path)

    @classmethod
    def get_instance(cls) -> HierarchicalChunkStore:
        """Get the singleton instance of HierarchicalChunkStore."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    # Auto-load available chunk corpora if present
                    cls._instance._auto_load_corpora()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (primarily for testing)."""
        with cls._lock:
            cls._instance = None

    def _auto_load_corpora(self) -> None:
        """Load default chunk files if they exist on disk."""
        base_dir = Path("data/chunks")
        if not base_dir.exists():
            return

        parent_file = base_dir / "parent_chunks.json"
        if parent_file.exists():
            self.load_from_json(parent_file)

        hierarchical_file = base_dir / "hierarchical_chunks.json"
        if hierarchical_file.exists():
            self.load_from_json(hierarchical_file)

        services_file = base_dir / "services_chunks.json"
        if services_file.exists():
            self.load_from_json(services_file)

    def register_chunk(self, chunk: DocumentChunk) -> None:
        """Register a single chunk and record its relational links."""
        with self._store_lock:
            self._chunks[chunk.chunk_id] = chunk

            if chunk.parent_chunk_id:
                self._parents_by_child[chunk.chunk_id] = chunk.parent_chunk_id
                children = self._children_by_parent.setdefault(chunk.parent_chunk_id, [])
                if chunk.chunk_id not in children:
                    children.append(chunk.chunk_id)

            if getattr(chunk, "root_chunk_id", None):
                self._roots_by_child[chunk.chunk_id] = chunk.root_chunk_id

    def register_pair(
        self, parent: DocumentChunk, children: list[DocumentChunk]
    ) -> None:
        """Register a Level 1 parent chunk and all its Level 2 child chunks."""
        with self._store_lock:
            child_ids = [c.chunk_id for c in children]
            parent.child_chunk_ids = child_ids
            parent.hierarchy_level = 1
            self.register_chunk(parent)

            for idx, child in enumerate(children):
                child.parent_chunk_id = parent.chunk_id
                child.hierarchy_level = 2
                child.child_index = idx
                # Populate siblings (excluding self)
                child.sibling_chunk_ids = [cid for cid in child_ids if cid != child.chunk_id]
                self.register_chunk(child)

    def register_tree(
        self,
        root: DocumentChunk,
        parents: list[DocumentChunk],
        children_by_parent: dict[str, list[DocumentChunk]],
    ) -> None:
        """Register a full 3-level tree: Root (Level 0) -> Parents (Level 1) -> Children (Level 2)."""
        with self._store_lock:
            root.hierarchy_level = 0
            root.root_chunk_id = None
            root.parent_chunk_id = None
            parent_ids = [p.chunk_id for p in parents]
            root.child_chunk_ids = parent_ids
            self.register_chunk(root)

            for p in parents:
                p.root_chunk_id = root.chunk_id
                p.parent_chunk_id = root.chunk_id
                p.hierarchy_level = 1
                p.sibling_chunk_ids = [pid for pid in parent_ids if pid != p.chunk_id]
                ch_list = children_by_parent.get(p.chunk_id, [])
                self.register_pair(p, ch_list)

    def get_chunk(self, chunk_id: str) -> Optional[DocumentChunk]:
        """Look up any chunk by its ID."""
        with self._store_lock:
            return self._chunks.get(chunk_id)

    def get_parent(self, chunk_id: str) -> Optional[DocumentChunk]:
        """Retrieve the parent chunk for a given child chunk ID."""
        with self._store_lock:
            parent_id = self._parents_by_child.get(chunk_id)
            if not parent_id:
                chunk = self._chunks.get(chunk_id)
                if chunk and chunk.parent_chunk_id:
                    parent_id = chunk.parent_chunk_id
            return self._chunks.get(parent_id) if parent_id else None

    def get_root(self, chunk_id: str) -> Optional[DocumentChunk]:
        """Retrieve the Level 0 root chunk for a given child or parent chunk ID."""
        with self._store_lock:
            root_id = self._roots_by_child.get(chunk_id)
            if not root_id:
                chunk = self._chunks.get(chunk_id)
                if chunk and getattr(chunk, "root_chunk_id", None):
                    root_id = chunk.root_chunk_id
                elif chunk and chunk.parent_chunk_id:
                    parent = self.get_parent(chunk_id)
                    if parent and getattr(parent, "root_chunk_id", None):
                        root_id = parent.root_chunk_id
            return self._chunks.get(root_id) if root_id else None

    def get_children(self, parent_id: str) -> list[DocumentChunk]:
        """Retrieve all registered child chunks for a parent."""
        with self._store_lock:
            child_ids = self._children_by_parent.get(parent_id, [])
            return [self._chunks[cid] for cid in child_ids if cid in self._chunks]

    def get_siblings(self, chunk_id: str) -> list[DocumentChunk]:
        """Retrieve all sibling chunks that share the same parent."""
        with self._store_lock:
            chunk = self._chunks.get(chunk_id)
            if not chunk or not chunk.parent_chunk_id:
                return []
            child_ids = self._children_by_parent.get(chunk.parent_chunk_id, [])
            return [
                self._chunks[cid]
                for cid in child_ids
                if cid in self._chunks and cid != chunk_id
            ]

    def get_parent_chunk(self, chunk_id: str) -> Optional[DocumentChunk]:
        """Alias for get_parent."""
        return self.get_parent(chunk_id)

    def get_child_chunks(self, parent_id: str) -> list[DocumentChunk]:
        """Alias for get_children."""
        return self.get_children(parent_id)

    def get_sibling_chunks(self, chunk_id: str) -> list[DocumentChunk]:
        """Alias for get_siblings."""
        return self.get_siblings(chunk_id)

    def coalesce_parent_context(self, chunk_id: str) -> Optional[DocumentChunk]:
        """Coalesce a child chunk with its parent and siblings into a comprehensive parent context."""
        with self._store_lock:
            parent = self.get_parent(chunk_id)
            if not parent:
                return self.get_chunk(chunk_id)
            children = self.get_children(parent.chunk_id)
            if not children:
                return parent
            combined_text = parent.text + "\n\n" + "\n\n".join(c.text for c in children)
            return DocumentChunk(
                chunk_id=parent.chunk_id,
                text=combined_text,
                provider=parent.provider,
                category=parent.category,
                service=parent.service,
                document_type=parent.document_type,
                title=parent.title,
                section=parent.section,
                subsection=parent.subsection,
                url=parent.url,
                source=parent.source,
                hierarchy_level=parent.hierarchy_level,
                child_chunk_ids=[c.chunk_id for c in children],
            )

    def resolve_and_coalesce(
        self,
        chunk_dicts_or_objs: list[Any],
        expand_to_parent: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Resolve retrieved leaf candidates to parents and coalesce siblings.

        If multiple retrieved chunks share the same parent chunk, they are merged
        into the complete parent context so that the LLM sees the complete
        tables, architecture diagrams, and code snippets without duplication.
        """
        with self._store_lock:
            seen_parent_ids: set[str] = set()
            coalesced: list[dict[str, Any]] = []

            for item in chunk_dicts_or_objs:
                if isinstance(item, DocumentChunk):
                    cid = item.chunk_id
                    raw_dict = item.model_dump()
                    meta = raw_dict
                    txt = item.text
                    score = getattr(item, "score", 1.0)
                elif hasattr(item, "chunk_id"):
                    cid = getattr(item, "chunk_id", "")
                    txt = getattr(item, "text", "")
                    meta = getattr(item, "metadata", {}) or {}
                    score = getattr(item, "score", 1.0)
                    raw_dict = {
                        "chunk_id": cid,
                        "text": txt,
                        "metadata": meta,
                        "score": score,
                    }
                elif isinstance(item, dict):
                    cid = item.get("chunk_id", "")
                    meta = item.get("metadata", {}) or item
                    txt = item.get("text") or item.get("content", "")
                    score = item.get("score", 1.0)
                    raw_dict = dict(item)
                else:
                    continue

                parent_id = meta.get("parent_chunk_id") or self._parents_by_child.get(cid)
                parent_chunk = self.get_chunk(parent_id) if parent_id else None

                if expand_to_parent and parent_chunk:
                    if parent_chunk.chunk_id in seen_parent_ids:
                        continue
                    seen_parent_ids.add(parent_chunk.chunk_id)

                    p_dict = parent_chunk.model_dump()
                    coalesced.append({
                        "chunk_id": parent_chunk.chunk_id,
                        "content": parent_chunk.text,
                        "text": parent_chunk.text,
                        "provider": parent_chunk.provider,
                        "service": parent_chunk.service,
                        "section": parent_chunk.section,
                        "subsection": parent_chunk.subsection,
                        "title": parent_chunk.title,
                        "url": parent_chunk.url,
                        "score": score,
                        "hierarchy_level": parent_chunk.hierarchy_level,
                        "resolved_from_child_id": cid,
                        "is_coalesced_parent": True,
                        "metadata": {
                            **p_dict,
                            "resolved_from_child_id": cid,
                            "is_coalesced_parent": True,
                        },
                    })
                else:
                    coalesced.append({
                        "chunk_id": cid,
                        "content": txt,
                        "text": txt,
                        "provider": meta.get("provider", "cloud"),
                        "service": meta.get("service", ""),
                        "section": meta.get("section", ""),
                        "subsection": meta.get("subsection", ""),
                        "title": meta.get("title", ""),
                        "url": meta.get("url", ""),
                        "score": score,
                        "hierarchy_level": meta.get("hierarchy_level", 2),
                        "is_coalesced_parent": False,
                        "metadata": meta,
                    })

            return coalesced

    def save_to_json(self, file_path: str | Path) -> int:
        """Serialize all registered chunks to a JSON file."""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._store_lock:
            records = [chunk.model_dump() for chunk in self._chunks.values()]
        path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        logger.info("Saved %d hierarchical chunks to %s", len(records), path)
        return len(records)

    def load_from_json(self, file_path: str | Path) -> int:
        """Load and index chunks from a JSON file."""
        path = Path(file_path)
        if not path.exists():
            return 0
        path_str = str(path.resolve())
        with self._store_lock:
            if path_str in self._loaded_paths:
                return len(self._chunks)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                count = 0
                if isinstance(data, list):
                    for entry in data:
                        chunk_obj = self._parse_chunk_entry(entry)
                        if chunk_obj:
                            self.register_chunk(chunk_obj)
                            count += 1
                self._loaded_paths.add(path_str)
                logger.info("Loaded and indexed %d chunks from %s", count, path)
                return count
            except Exception as err:
                logger.warning("Failed loading chunks from %s: %s", path, err)
                return 0

    def _parse_chunk_entry(self, entry: dict[str, Any]) -> Optional[DocumentChunk]:
        """Convert a raw dictionary or chunk entry into a DocumentChunk."""
        try:
            if "chunk_id" in entry and "text" in entry and "provider" in entry:
                return DocumentChunk(**entry)
            elif "chunk_id" in entry and "text" in entry and "metadata" in entry:
                meta = entry["metadata"]
                return DocumentChunk(
                    chunk_id=entry["chunk_id"],
                    text=entry["text"],
                    provider=meta.get("provider", "multi-cloud"),
                    category=meta.get("category", ""),
                    service=meta.get("service", ""),
                    document_type=meta.get("document_type", "documentation"),
                    title=meta.get("title", ""),
                    section=meta.get("section", ""),
                    subsection=meta.get("subsection", ""),
                    url=meta.get("url", ""),
                    source=meta.get("source", ""),
                    parent_chunk_id=meta.get("parent_chunk_id"),
                    child_index=meta.get("child_index", 0),
                    hierarchy_level=meta.get("hierarchy_level", 1),
                    child_chunk_ids=meta.get("child_chunk_ids", []),
                    sibling_chunk_ids=meta.get("sibling_chunk_ids", []),
                    chunk_type=meta.get("chunk_type", "text"),
                )
        except Exception as e:
            logger.debug("Skipping unparseable chunk entry: %s", e)
        return None

    def __len__(self) -> int:
        with self._store_lock:
            return len(self._chunks)
