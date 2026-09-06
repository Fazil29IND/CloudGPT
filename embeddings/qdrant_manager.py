"""
Qdrant Vector Database Manager for CloudGPT RAG Pipeline.

Provides dense and filtered vector search, multi-tenant/versioned namespace isolation,
and high-throughput chunk upserts on top of Qdrant (self-hosted, Qdrant Cloud, or in-memory fallback).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

try:
    from qdrant_client import QdrantClient, models
    from qdrant_client.http.exceptions import UnexpectedResponse
except ImportError:
    QdrantClient = None
    models = None
    UnexpectedResponse = Exception


# Index namespaces keep knowledge domains separable:
NAMESPACE_SERVICES = "services-master"
NAMESPACE_ENGINEER_KNOWLEDGE = "senior-engineer-knowledge"
NAMESPACE_TROUBLESHOOTING = "troubleshooting-playbooks"
NAMESPACE_IAC = "iac-templates"
ALL_NAMESPACES = (
    NAMESPACE_SERVICES,
    NAMESPACE_ENGINEER_KNOWLEDGE,
    NAMESPACE_TROUBLESHOOTING,
    NAMESPACE_IAC,
)


class QdrantManager:
    """Manages Qdrant vector database operations for CloudGPT RAG pipeline.

    Supports dense vector similarity search, payload-filtered retrieval, and
    automatic local-memory fallback for hermetic testing and offline development.
    """

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.collection_name = getattr(settings, "qdrant_collection", "cloud-docs")
        self.expected_dimension = getattr(settings, "embedding_dimension", 768)
        self.client: QdrantClient | None = None
        self._is_local_fallback = False

        if QdrantClient is None:
            logger.warning("qdrant-client package is not installed. Run 'pip install qdrant-client'")
            return

        qdrant_url = getattr(settings, "qdrant_url", "http://localhost:6333")
        api_key = getattr(settings, "qdrant_api_key", None)
        prefer_grpc = getattr(settings, "qdrant_prefer_grpc", False)

        # Attempt connection to Qdrant cluster / local daemon
        try:
            client = QdrantClient(
                url=qdrant_url,
                api_key=api_key or None,
                prefer_grpc=prefer_grpc,
                check_compatibility=False,
                timeout=float(getattr(settings, "retrieval_timeout_seconds", 2.0) or 2.0),
            )
            # Health ping to verify remote availability
            client.get_collections()
            self.client = client
            logger.info(
                "QdrantManager connected to remote instance: url=%s, collection=%s, dim=%d",
                qdrant_url,
                self.collection_name,
                self.expected_dimension,
            )
        except Exception as conn_err:
            # Fall back to local on-disk or in-memory instance for testing / standalone mode
            local_path = getattr(settings, "qdrant_local_path", "data/qdrant_storage")
            logger.warning(
                "Remote Qdrant unavailable at %s (%s). Initializing local fallback storage.",
                qdrant_url,
                conn_err,
            )
            try:
                if local_path and local_path != ":memory:":
                    storage_dir = Path(local_path)
                    storage_dir.mkdir(parents=True, exist_ok=True)
                    self.client = QdrantClient(path=str(storage_dir))
                else:
                    self.client = QdrantClient(":memory:")
                self._is_local_fallback = True
                logger.info(
                    "QdrantManager initialized with local fallback storage (path=%s)",
                    local_path,
                )
            except Exception as fallback_err:
                logger.error("Failed to initialize Qdrant local fallback: %s. Using in-memory.", fallback_err)
                self.client = QdrantClient(":memory:")
                self._is_local_fallback = True

    def check_dimension_compatibility(self) -> bool:
        """Verify collection vector dimension matches configured embedding dimension."""
        if not self.client:
            return True
        try:
            if not self.client.collection_exists(self.collection_name):
                return True
            col_info = self.client.get_collection(self.collection_name)
            params = col_info.config.params
            vectors = params.vectors
            dim = None
            if hasattr(vectors, "size"):
                dim = vectors.size
            elif isinstance(vectors, dict) and "" in vectors:
                dim = getattr(vectors[""], "size", None)
            if dim and dim != self.expected_dimension:
                logger.error(
                    "Qdrant collection dimension mismatch: collection=%d, configured=%d",
                    dim,
                    self.expected_dimension,
                )
                return False
            return True
        except Exception as e:
            logger.warning("Could not verify Qdrant collection dimension: %s", e)
            return True

    def active_namespace(self, base: str = "services") -> str:
        """Resolve active versioned namespace (e.g. 'services-v1')."""
        version = getattr(self.settings, "active_corpus_version", "v1")
        clean_base = base
        if clean_base == "services-master":
            clean_base = "services"
        if re.search(r"-v\d+$", clean_base):
            return clean_base
        return f"{clean_base}-{version}"

    # ── Lifecycle & Collection Initialization ───────────────────────────

    async def initialize(self) -> None:
        """Ensure the target collection and payload indices exist in Qdrant."""
        if not self.client:
            logger.warning("Qdrant client not available — skipping initialization.")
            return

        def _init_sync():
            assert models is not None
            if not self.client.collection_exists(self.collection_name):
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=self.expected_dimension,
                        distance=models.Distance.COSINE,
                    ),
                )
                logger.info(
                    "Created Qdrant collection '%s' (dim=%d, distance=COSINE).",
                    self.collection_name,
                    self.expected_dimension,
                )

                # Create payload indices for fast filtered retrieval
                for field in ("namespace", "category", "service", "cloud", "lifecycle_phase"):
                    try:
                        self.client.create_payload_index(
                            collection_name=self.collection_name,
                            field_name=field,
                            field_schema=models.PayloadSchemaType.KEYWORD,
                        )
                    except Exception as idx_err:
                        logger.debug("Payload index creation notice for %s: %s", field, idx_err)
            else:
                logger.info("Qdrant collection '%s' already exists.", self.collection_name)

        try:
            await asyncio.to_thread(_init_sync)
        except Exception as e:
            logger.error("Error initializing Qdrant collection '%s': %s", self.collection_name, e)
            raise

    # ── Upsert ──────────────────────────────────────────────────────────

    async def upsert_chunks(self, chunks: list[dict], namespace: str = NAMESPACE_SERVICES) -> None:
        """Upsert embedded chunks into the Qdrant collection with namespace tags.

        Each chunk dict must contain:
            chunk_id, dense_vector, text, metadata (optional sparse_vector)
        """
        if not self.client:
            logger.warning("Cannot upsert: Qdrant client not available.")
            return

        # Ensure collection exists before upserting
        await self.initialize()

        points: list[Any] = []
        for chunk in chunks:
            raw_id = str(chunk.get("chunk_id", ""))
            try:
                point_id = str(uuid.UUID(raw_id))
            except (ValueError, AttributeError):
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, raw_id))

            chunk_meta = dict(chunk.get("metadata", {}))
            if isinstance(chunk_meta.get("aliases"), (list, tuple)):
                chunk_meta["aliases"] = "|".join(str(a) for a in chunk_meta["aliases"])

            dense_vec = chunk.get("dense_vector", [])
            if len(dense_vec) != self.expected_dimension:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self.expected_dimension}, got {len(dense_vec)}"
                )

            payload = {
                "chunk_id": raw_id,
                "text": chunk.get("text", ""),
                "namespace": namespace,
                **chunk_meta,
            }

            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=dense_vec,
                    payload=payload,
                )
            )

        # Batch upsert to prevent request size overload
        batch_size = 100

        def _batch_upsert():
            for i in range(0, len(points), batch_size):
                batch = points[i : i + batch_size]
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=batch,
                    wait=True,
                )

        await asyncio.to_thread(_batch_upsert)
        logger.info(
            "Upserted %d vectors to Qdrant collection '%s' (namespace=%s).",
            len(chunks),
            self.collection_name,
            namespace,
        )

    # ── Delete ──────────────────────────────────────────────────────────

    async def delete_by_filter(self, filter_conditions: dict) -> None:
        """Delete vectors matching metadata filter conditions."""
        if not self.client:
            return

        qdrant_filter = self._build_qdrant_filter(filter_conditions)
        if not qdrant_filter:
            return

        def _delete_sync():
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(filter=qdrant_filter),
            )

        try:
            await asyncio.to_thread(_delete_sync)
        except Exception as e:
            logger.warning("Error deleting vectors in Qdrant: %s", e)

    # ── Dense Search ────────────────────────────────────────────────────

    async def search_dense(
        self,
        query_vector: list[float],
        filter_conditions: dict | None = None,
        limit: int = 50,
        namespace: str = NAMESPACE_SERVICES,
    ) -> list[dict[str, Any]]:
        """Search Qdrant using dense query vector with namespace scoping and metadata filtering."""
        if not self.client:
            return []

        qdrant_filter = self._build_qdrant_filter(filter_conditions, namespace=namespace)
        search_timeout = float(getattr(self.settings, "retrieval_timeout_seconds", 8.0) or 8.0)

        def _search_sync():
            if not self.client.collection_exists(self.collection_name):
                return []

            # Use query_points (qdrant-client >= 1.9)
            if hasattr(self.client, "query_points"):
                response = self.client.query_points(
                    collection_name=self.collection_name,
                    query=query_vector,
                    query_filter=qdrant_filter,
                    limit=limit,
                    with_payload=True,
                )
                points = response.points
            else:
                points = self.client.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    query_filter=qdrant_filter,
                    limit=limit,
                    with_payload=True,
                )

            matches = []
            for p in points:
                payload = dict(p.payload or {})
                matches.append({
                    "id": p.id,
                    "score": float(p.score),
                    "metadata": payload,
                })
            return matches

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_search_sync),
                timeout=search_timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("qdrant_dense_search_timeout", namespace=namespace)
            return []
        except Exception as e:
            logger.warning("qdrant_dense_search_error", namespace=namespace, error=str(e))
            return []

    # ── Sparse / BM25 Search Compatibility ──────────────────────────────

    async def search_sparse(
        self,
        sparse_vector: dict,
        filter_conditions: dict | None = None,
        limit: int = 50,
        namespace: str = NAMESPACE_SERVICES,
    ) -> list[dict[str, Any]]:
        """Fallback sparse search placeholder for Qdrant hybrid integration."""
        logger.debug("Qdrant sparse search invoked; delegating to local BM25 engine.")
        return []

    # ── Filter Builder ──────────────────────────────────────────────────

    @staticmethod
    def _build_qdrant_filter(
        filter_conditions: dict | None,
        namespace: str | None = None,
    ) -> Any:
        """Construct Qdrant models.Filter from key-value dictionary and namespace."""
        if models is None:
            return None

        must_clauses = []

        if namespace:
            must_clauses.append(
                models.FieldCondition(
                    key="namespace",
                    match=models.MatchValue(value=namespace),
                )
            )

        if filter_conditions:
            for key, value in filter_conditions.items():
                if value is None:
                    continue
                if isinstance(value, (list, tuple)):
                    must_clauses.append(
                        models.FieldCondition(
                            key=key,
                            match=models.MatchAny(any=list(value)),
                        )
                    )
                else:
                    must_clauses.append(
                        models.FieldCondition(
                            key=key,
                            match=models.MatchValue(value=value),
                        )
                    )

        if not must_clauses:
            return None

        return models.Filter(must=must_clauses)

    # ── Stats & Health Check ────────────────────────────────────────────

    async def get_collection_stats(self) -> dict[str, Any]:
        """Retrieve Qdrant collection statistics."""
        if not self.client:
            return {"points_count": 0, "status": "uninitialized"}

        def _stats_sync():
            if not self.client.collection_exists(self.collection_name):
                return {"points_count": 0, "status": "collection_missing"}
            info = self.client.get_collection(self.collection_name)
            points_count = info.points_count or 0
            vectors_count = getattr(info, "vectors_count", points_count)
            return {
                "points_count": points_count,
                "vectors_count": vectors_count,
                "status": "ready",
                "collection": self.collection_name,
            }

        try:
            return await asyncio.wait_for(asyncio.to_thread(_stats_sync), timeout=3.0)
        except Exception as e:
            logger.warning("qdrant_stats_error: %s", e)
            return {"points_count": 0, "status": f"error / {e}"}

    async def health_check(self) -> bool:
        """Verify Qdrant client responsiveness."""
        if not self.client:
            return False

        def _ping():
            self.client.get_collections()
            return True

        try:
            return await asyncio.wait_for(asyncio.to_thread(_ping), timeout=3.0)
        except Exception:
            return False
