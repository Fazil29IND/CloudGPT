"""
Dense vector retrievers for CloudGPT.

Supports:
- HNSWRetriever: Native multi-layer Hierarchical Navigable Small World vector search (Lite & Core tiers)
  with Pinecone Serverless HNSW fallback.
- QuakeRetriever: Adaptive Indexing vector search with dynamic Voronoi partitioning, cost-based
  n_probe tuning, and online skew adaptation (Apex tier).
- DenseRetriever: Backward-compatible facade bridging HNSW and Pinecone.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from . import RetrievalResult
from .hnsw_index import HNSWIndex
from .quake_index import QuakeIndex
from config import get_settings
from embeddings.embedding_engine import EmbeddingEngine
from embeddings.pinecone_manager import PineconeManager

logger = logging.getLogger(__name__)

_GLOBAL_HNSW_INDEX: HNSWIndex | None = None
_GLOBAL_QUAKE_INDEX: QuakeIndex | None = None


def get_default_hnsw_index(settings: Any | None = None) -> HNSWIndex:
    """Retrieve or lazily initialize the singleton HNSW vector index."""
    global _GLOBAL_HNSW_INDEX
    if _GLOBAL_HNSW_INDEX is not None:
        return _GLOBAL_HNSW_INDEX

    s = settings or get_settings()
    idx_path = Path(getattr(s, "hnsw_index_path", "data/hnsw_index/services_hnsw.json"))
    dim = getattr(s, "embedding_dimension", 384)
    m = getattr(s, "hnsw_m", 16)
    ef_c = getattr(s, "hnsw_ef_construction", 64)
    ef_s = getattr(s, "hnsw_ef_search", 32)

    if idx_path.exists():
        try:
            _GLOBAL_HNSW_INDEX = HNSWIndex.load(idx_path)
            return _GLOBAL_HNSW_INDEX
        except Exception as err:
            logger.warning("Failed to load HNSW index from %s: %s", idx_path, err)

    _GLOBAL_HNSW_INDEX = HNSWIndex(dimension=dim, m=m, ef_construction=ef_c, ef_search=ef_s)
    return _GLOBAL_HNSW_INDEX


def get_default_quake_index(settings: Any | None = None) -> QuakeIndex:
    """Retrieve or lazily initialize the singleton Quake adaptive vector index."""
    global _GLOBAL_QUAKE_INDEX
    if _GLOBAL_QUAKE_INDEX is not None:
        return _GLOBAL_QUAKE_INDEX

    s = settings or get_settings()
    idx_path = Path(getattr(s, "quake_index_path", "data/quake_index/services_quake.json"))
    dim = getattr(s, "embedding_dimension", 384)
    n_part = getattr(s, "quake_num_partitions", 16)
    min_p = getattr(s, "quake_min_probe", 1)
    max_p = getattr(s, "quake_max_probe", 5)
    split_th = getattr(s, "quake_split_threshold", 50)

    if idx_path.exists():
        try:
            _GLOBAL_QUAKE_INDEX = QuakeIndex.load(idx_path)
            return _GLOBAL_QUAKE_INDEX
        except Exception as err:
            logger.warning("Failed to load Quake index from %s: %s", idx_path, err)

    _GLOBAL_QUAKE_INDEX = QuakeIndex(
        dimension=dim,
        num_partitions=n_part,
        min_probe=min_p,
        max_probe=max_p,
        split_threshold=split_th,
    )
    return _GLOBAL_QUAKE_INDEX


class HNSWRetriever:
    """Dense vector retriever powered by Hierarchical Navigable Small World (HNSW) graph search."""

    def __init__(
        self,
        embedding_engine: EmbeddingEngine,
        hnsw_index: HNSWIndex | None = None,
        pinecone_manager: PineconeManager | None = None,
    ) -> None:
        self.embedding_engine = embedding_engine
        self.pinecone_manager = pinecone_manager
        self._hnsw_index = hnsw_index

    @property
    def hnsw_index(self) -> HNSWIndex:
        if self._hnsw_index is None:
            self._hnsw_index = get_default_hnsw_index()
        return self._hnsw_index

    async def retrieve(
        self,
        query: str,
        top_k: int = 50,
        filters: dict | None = None,
        namespace: str | None = None,
        expand_to_parents: bool = False,
    ) -> list[RetrievalResult]:
        """Perform sub-millisecond HNSW vector search with graceful remote fallback."""
        try:
            query_vector = await self.embedding_engine.embed_query(query)
            if not query_vector:
                return []

            # 1. Native in-memory/persisted HNSW graph search
            if len(self.hnsw_index.nodes) > 0:
                raw_matches = await asyncio.to_thread(
                    self.hnsw_index.search,
                    query_vector=query_vector,
                    top_k=top_k,
                    filters=filters,
                )
                if raw_matches:
                    results = []
                    for cid, score, text, meta in raw_matches:
                        results.append(RetrievalResult(
                            chunk_id=cid,
                            text=text,
                            score=score,
                            metadata=meta,
                        ))
                    return results

            # 2. Pinecone Serverless HNSW fallback if native index is empty or not yet seeded
            if self.pinecone_manager and hasattr(self.pinecone_manager, "search_dense"):
                target_namespace = namespace or self.pinecone_manager.active_namespace("services")
                search_results = await self.pinecone_manager.search_dense(
                    query_vector=query_vector,
                    filter_conditions=filters,
                    limit=top_k,
                    namespace=target_namespace,
                )
                results = []
                for res in search_results:
                    metadata = res.get("metadata", {})
                    results.append(RetrievalResult(
                        chunk_id=str(res.get("id", "")),
                        text=metadata.get("text", ""),
                        score=float(res.get("score", 0.0)),
                        metadata={k: v for k, v in metadata.items() if k != "text"},
                    ))
                return results

            return []
        except Exception as e:
            logger.error(f"Error in HNSW retrieval: {e}")
            return []


class QuakeRetriever:
    """Adaptive dense vector retriever powered by Quake (Mohoney et al., 2025).

    Utilizes dynamic centroid partitioning, cost-based probe optimization, and continuous
    workload skew adaptation.
    """

    def __init__(
        self,
        embedding_engine: EmbeddingEngine,
        quake_index: QuakeIndex | None = None,
        pinecone_manager: PineconeManager | None = None,
    ) -> None:
        self.embedding_engine = embedding_engine
        self.pinecone_manager = pinecone_manager
        self._quake_index = quake_index

    @property
    def quake_index(self) -> QuakeIndex:
        if self._quake_index is None:
            self._quake_index = get_default_quake_index()
        return self._quake_index

    async def retrieve(
        self,
        query: str,
        top_k: int = 50,
        filters: dict | None = None,
        namespace: str | None = None,
        vector_override: list[float] | None = None,
        adapt: bool = True,
        expand_to_parents: bool = False,
    ) -> list[RetrievalResult]:
        """Perform adaptive vector search using Quake dynamic partitioning."""
        try:
            if vector_override:
                query_vector = vector_override
            else:
                query_vector = await self.embedding_engine.embed_query(query)

            if not query_vector:
                return []

            # 1. Native Quake Adaptive Index search
            if len(self.quake_index) > 0:
                raw_matches = await asyncio.to_thread(
                    self.quake_index.search,
                    query_vector=query_vector,
                    top_k=top_k,
                    filters=filters,
                    adapt=adapt,
                )
                if raw_matches:
                    results = []
                    for cid, score, text, meta in raw_matches:
                        results.append(RetrievalResult(
                            chunk_id=cid,
                            text=text,
                            score=score,
                            metadata=meta,
                        ))
                    return results

            # 2. Remote fallback to Pinecone if Quake index is empty
            if self.pinecone_manager and hasattr(self.pinecone_manager, "search_dense"):
                target_namespace = namespace or self.pinecone_manager.active_namespace("services")
                search_results = await self.pinecone_manager.search_dense(
                    query_vector=query_vector,
                    filter_conditions=filters,
                    limit=top_k,
                    namespace=target_namespace,
                )
                results = []
                for res in search_results:
                    metadata = res.get("metadata", {})
                    results.append(RetrievalResult(
                        chunk_id=str(res.get("id", "")),
                        text=metadata.get("text", ""),
                        score=float(res.get("score", 0.0)),
                        metadata={k: v for k, v in metadata.items() if k != "text"},
                    ))
                return results

            return []
        except Exception as e:
            logger.error(f"Error in Quake retrieval: {e}")
            return []


class DenseRetriever:
    """Backward-compatible dense retriever facade combining HNSW and Pinecone."""

    def __init__(
        self,
        embedding_engine: EmbeddingEngine,
        pinecone_manager: PineconeManager,
        hnsw_index: HNSWIndex | None = None,
    ) -> None:
        self.embedding_engine = embedding_engine
        self.pinecone_manager = pinecone_manager
        self.hnsw_retriever = HNSWRetriever(
            embedding_engine=embedding_engine,
            hnsw_index=hnsw_index,
            pinecone_manager=pinecone_manager,
        )

    async def retrieve(
        self,
        query: str,
        top_k: int = 50,
        filters: dict | None = None,
        namespace: str | None = None,
        expand_to_parents: bool = False,
    ) -> list[RetrievalResult]:
        return await self.hnsw_retriever.retrieve(
            query=query,
            top_k=top_k,
            filters=filters,
            namespace=namespace,
            expand_to_parents=expand_to_parents,
        )
