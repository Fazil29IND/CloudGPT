"""
Corpus Ingestion State and Dead-Letter Queue Manager for CloudGPT.

Tracks content hashes, chunker/parser versions for idempotent skip checks,
and maintains the dead-letter queue for failed embeddings/upserts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from metrics import INGESTION_DEAD_LETTER_COUNT

logger = logging.getLogger("corpus.ingestion_state")

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class FailedChunk:
    entry_id: str
    chunk_id: str
    error: str
    timestamp: str = ""
    retry_count: int = 0
    text_preview: str = ""
    namespace: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


class IngestionStateManager:
    """Manages ingestion state and dead-letter queue files."""

    def __init__(
        self,
        state_path: Optional[Path] = None,
        dlq_path: Optional[Path] = None,
    ) -> None:
        metadata_dir = BASE_DIR / "data" / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)

        self.state_path = state_path or (metadata_dir / "ingestion_state.json")
        self.dlq_path = dlq_path or (metadata_dir / "dead_letter_queue.json")

        self.state: dict[str, dict[str, Any]] = self._load_state()
        self.dlq: list[FailedChunk] = self._load_dlq()

    def _load_state(self) -> dict[str, dict[str, Any]]:
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Could not load ingestion state file: %s", e)
        return {}

    def _load_dlq(self) -> list[FailedChunk]:
        if self.dlq_path.exists():
            try:
                with open(self.dlq_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    chunks = [FailedChunk(**item) for item in data]
                    INGESTION_DEAD_LETTER_COUNT.set(len(chunks))
                    return chunks
            except Exception as e:
                logger.warning("Could not load dead letter queue: %s", e)
        INGESTION_DEAD_LETTER_COUNT.set(0)
        return []

    def should_skip(
        self,
        entry_id: str,
        new_content_hash: str,
        corpus_version: str = "v1",
        parser_version: str = "html-md-v2",
        chunker_version: str = "parent-child-v1",
    ) -> bool:
        """Return True if the document has not changed and can be safely skipped."""
        record = self.state.get(entry_id)
        if not record:
            return False

        return (
            record.get("content_hash") == new_content_hash
            and record.get("corpus_version") == corpus_version
            and record.get("parser_version") == parser_version
            and record.get("chunker_version") == chunker_version
        )

    def record_success(
        self,
        entry_id: str,
        content_hash: str,
        corpus_version: str = "v1",
        parser_version: str = "html-md-v2",
        chunker_version: str = "parent-child-v1",
    ) -> None:
        """Update the state on successful ingestion."""
        self.state[entry_id] = {
            "content_hash": content_hash,
            "corpus_version": corpus_version,
            "parser_version": parser_version,
            "chunker_version": chunker_version,
            "last_ingested": datetime.utcnow().isoformat() + "Z",
        }

    def record_failure(
        self,
        entry_id: str,
        chunk_id: str,
        error: str,
        text: str = "",
        namespace: str = "",
    ) -> None:
        """Add a failed chunk to the dead letter queue."""
        chunk = FailedChunk(
            entry_id=entry_id,
            chunk_id=chunk_id,
            error=str(error),
            text_preview=text[:200],
            namespace=namespace,
        )
        self.dlq.append(chunk)
        INGESTION_DEAD_LETTER_COUNT.set(len(self.dlq))
        logger.error("Added chunk %s to dead-letter queue: %s", chunk_id, error)

    def save(self) -> None:
        """Persist state and DLQ files."""
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)

            with open(self.dlq_path, "w", encoding="utf-8") as f:
                json.dump([asdict(c) for c in self.dlq], f, indent=2)

            INGESTION_DEAD_LETTER_COUNT.set(len(self.dlq))
        except Exception as e:
            logger.error("Failed saving ingestion state or DLQ: %s", e)
