"""
Index builder for CloudGPT HNSW and Quake vector indexes.

Builds and persists HNSW and Quake index artifacts from services and knowledge chunks.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from config import get_settings
from retrieval.hnsw_index import HNSWIndex
from retrieval.quake_index import QuakeIndex

logger = logging.getLogger(__name__)


def build_vector_indexes(
    chunks: list[dict[str, Any]],
    embeddings: list[list[float]] | np.ndarray,
    hnsw_out_path: str | Path | None = None,
    quake_out_path: str | Path | None = None,
    dimension: int | None = None,
) -> tuple[HNSWIndex, QuakeIndex]:
    """Build and persist both HNSW and Quake vector indexes from chunks and embeddings."""
    settings = get_settings()
    emb_arr = np.asarray(embeddings, dtype=np.float32)
    dim = dimension or emb_arr.shape[1]

    hnsw_path = Path(hnsw_out_path or getattr(settings, "hnsw_index_path", "data/hnsw_index/services_hnsw.json"))
    quake_path = Path(quake_out_path or getattr(settings, "quake_index_path", "data/quake_index/services_quake.json"))

    hnsw_idx = HNSWIndex(
        dimension=dim,
        m=getattr(settings, "hnsw_m", 16),
        ef_construction=getattr(settings, "hnsw_ef_construction", 64),
        ef_search=getattr(settings, "hnsw_ef_search", 32),
    )

    quake_idx = QuakeIndex(
        dimension=dim,
        num_partitions=getattr(settings, "quake_num_partitions", 16),
        min_probe=getattr(settings, "quake_min_probe", 1),
        max_probe=getattr(settings, "quake_max_probe", 5),
        split_threshold=getattr(settings, "quake_split_threshold", 50),
    )

    logger.info("Building HNSW and Quake indexes for %d items (dim=%d)...", len(chunks), dim)
    for i, chunk in enumerate(chunks):
        cid = chunk.get("chunk_id", f"chunk_{i}")
        text = chunk.get("text", "")
        meta = chunk.get("metadata", {})
        vec = emb_arr[i]

        hnsw_idx.add_item(node_id=cid, vector=vec, text=text, metadata=meta)
        quake_idx.add_item(item_id=cid, vector=vec, text=text, metadata=meta)

    hnsw_idx.save(hnsw_path)
    quake_idx.save(quake_path)

    logger.info("Successfully built and saved HNSW -> %s, Quake -> %s", hnsw_path, quake_path)
    return hnsw_idx, quake_idx
