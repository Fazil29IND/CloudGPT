import asyncio
import uuid
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

try:
    from pinecone import Pinecone, ServerlessSpec
except ImportError:
    Pinecone = None
    ServerlessSpec = None


# Index namespaces keep the knowledge domains separable: queries can target
# the service catalog, engineering playbooks, or IaC templates, and deletion
# / re-ingestion can be scoped without touching the other domains.
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


class PineconeManager:
    """Manages Pinecone vector database operations for CloudGPT RAG pipeline.

    Supports dense + sparse hybrid search on Pinecone Serverless.
    Drop-in replacement for the former QdrantManager.
    """

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.index_name = settings.pinecone_index_name
        self.expected_dimension = getattr(settings, "embedding_dimension", 768)
        self._index = None

        if Pinecone is None:
            logger.warning(
                "pinecone package is not installed. Run 'pip install pinecone[grpc]'"
            )
            self.client = None
            return

        api_key = settings.pinecone_api_key
        if not api_key:
            logger.warning(
                "PINECONE_API_KEY not set. Pinecone operations will be unavailable."
            )
            self.client = None
            return

        self.client = Pinecone(api_key=api_key)
        logger.info(
            "PineconeManager initialized: index=%s, cloud=%s, region=%s, dim=%d",
            self.index_name,
            settings.pinecone_cloud,
            settings.pinecone_region,
            self.expected_dimension,
        )

    def check_dimension_compatibility(self) -> bool:
        """Verify index dimension matches configured embedding dimension."""
        if not self.client:
            return True
        try:
            desc = self.client.describe_index(self.index_name)
            dim = getattr(desc, "dimension", None)
            if dim is None and isinstance(desc, dict):
                dim = desc.get("dimension")
            if dim and dim != self.expected_dimension:
                logger.error(
                    "Pinecone index dimension mismatch: index=%d, configured=%d",
                    dim,
                    self.expected_dimension,
                )
                return False
            return True
        except Exception as e:
            logger.warning("Could not verify Pinecone index dimension: %s", e)
            return True

    def active_namespace(self, base: str = "services") -> str:
        """Resolve active versioned Pinecone namespace."""
        import re
        version = getattr(self.settings, "active_corpus_version", "v1")
        clean_base = base
        if clean_base == "services-master":
            clean_base = "services"
        if re.search(r"-v\d+$", clean_base):
            return clean_base
        return f"{clean_base}-{version}"

    # ── Lazy index accessor ─────────────────────────────────────────────

    def _get_index(self):
        """Return a cached Index handle (created on first access)."""
        if self._index is None and self.client is not None:
            self._index = self.client.Index(self.index_name)
        return self._index

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create the Pinecone index if it does not already exist."""
        if not self.client:
            logger.warning("Pinecone client not available — skipping initialization.")
            return

        try:
            existing = [idx.name for idx in self.client.list_indexes()]
            if self.index_name not in existing:
                assert ServerlessSpec is not None
                self.client.create_index(
                    name=self.index_name,
                    dimension=self.settings.embedding_dimension,
                    metric=self.settings.pinecone_metric,
                    spec=ServerlessSpec(
                        cloud=self.settings.pinecone_cloud,
                        region=self.settings.pinecone_region,
                    ),
                )
                logger.info(
                    "Created Pinecone index '%s' (dim=%d, metric=%s).",
                    self.index_name,
                    self.settings.embedding_dimension,
                    self.settings.pinecone_metric,
                )
            else:
                logger.info("Pinecone index '%s' already exists.", self.index_name)

            # Force-refresh the index handle
            self._index = self.client.Index(self.index_name)

        except Exception as e:
            logger.error("Error initializing Pinecone index: %s", e)
            raise

    # ── Upsert ──────────────────────────────────────────────────────────

    async def upsert_chunks(self, chunks: list[dict], namespace: str = NAMESPACE_SERVICES) -> None:
        """Upsert embedded chunks into the Pinecone index (optionally namespaced).

        Each chunk dict must contain:
            chunk_id, dense_vector, sparse_vector, text, metadata
        """
        index = self._get_index()
        if index is None:
            logger.warning("Cannot upsert: Pinecone index not available.")
            return

        vectors = []
        for chunk in chunks:
            raw_id = str(chunk.get("chunk_id", ""))
            # Pinecone IDs: alphanumeric + hyphens, max 512 chars
            try:
                point_id = str(uuid.UUID(raw_id))
            except (ValueError, AttributeError):
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, raw_id))

            sparse = chunk.get("sparse_vector", {})
            sparse_values = None
            if sparse and sparse.get("indices") and sparse.get("values"):
                sparse_values = {
                    "indices": sparse["indices"],
                    "values": sparse["values"],
                }

            chunk_meta = dict(chunk.get("metadata", {}))
            if isinstance(chunk_meta.get("aliases"), (list, tuple)):
                chunk_meta["aliases"] = "|".join(str(a) for a in chunk_meta["aliases"])

            metadata = {
                "chunk_id": raw_id,
                "text": chunk["text"],
                **chunk_meta,
            }

            dense_vec = chunk.get("dense_vector", [])
            expected_dim = getattr(self, "expected_dimension", getattr(getattr(self, "settings", None), "embedding_dimension", 768))
            if len(dense_vec) != expected_dim:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {expected_dim}, got {len(dense_vec)}"
                )

            vec: dict[str, Any] = {
                "id": point_id,
                "values": dense_vec,
                "metadata": metadata,
            }
            if sparse_values:
                vec["sparse_values"] = sparse_values

            vectors.append(vec)

        # Pinecone batch limit is 100 vectors per upsert call
        batch_size = 100
        for i in range(0, len(vectors), batch_size):
            batch = vectors[i : i + batch_size]
            index.upsert(vectors=batch, namespace=namespace)

        logger.info(
            "Upserted %d vectors to Pinecone index '%s' (namespace=%s).",
            len(chunks), self.index_name, namespace,
        )

    # ── Delete ──────────────────────────────────────────────────────────

    async def delete_by_filter(self, filter_conditions: dict) -> None:
        """Delete vectors matching metadata filter conditions."""
        index = self._get_index()
        if index is None:
            return

        pc_filter = self._build_filter(filter_conditions)
        if pc_filter:
            index.delete(filter=pc_filter)

    # ── Dense Search ────────────────────────────────────────────────────

    async def search_dense(
        self,
        query_vector: list[float],
        filter_conditions: dict | None = None,
        limit: int = 50,
        namespace: str = NAMESPACE_SERVICES,
    ) -> list:
        """Search the index using a dense query vector with non-blocking timeout."""
        index = self._get_index()
        if index is None:
            return []

        pc_filter = self._build_filter(filter_conditions)

        kwargs: dict[str, Any] = {
            "vector": query_vector,
            "top_k": limit,
            "include_metadata": True,
            "namespace": namespace,
        }
        if pc_filter:
            kwargs["filter"] = pc_filter

        search_timeout = float(getattr(self.settings, "retrieval_timeout_seconds", 8.0) or 8.0)
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(index.query, **kwargs),
                timeout=search_timeout,
            )
            return response.get("matches", [])
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("pinecone_dense_search_timeout", namespace=namespace)
            return []
        except (ConnectionError, OSError) as e:
            logger.error("pinecone_dense_search_connection_error", namespace=namespace, error=str(e))
            return []
        except ValueError as e:
            logger.error("pinecone_dense_search_value_error", namespace=namespace, error=str(e))
            return []
        except Exception as e:
            logger.warning("pinecone_dense_search_unexpected_error", namespace=namespace, error=str(e))
            return []

    # ── Sparse Search ───────────────────────────────────────────────────

    async def search_sparse(
        self,
        sparse_vector: dict,
        filter_conditions: dict | None = None,
        limit: int = 50,
        namespace: str = NAMESPACE_SERVICES,
    ) -> list:
        """Search using sparse vector (BM25-style) with non-blocking timeout."""
        index = self._get_index()
        if index is None:
            return []

        pc_filter = self._build_filter(filter_conditions)
        zero_vector = [0.0] * self.settings.embedding_dimension

        sparse_values = {
            "indices": sparse_vector.get("indices", []),
            "values": sparse_vector.get("values", []),
        }

        kwargs: dict[str, Any] = {
            "vector": zero_vector,
            "sparse_vector": sparse_values,
            "top_k": limit,
            "include_metadata": True,
            "namespace": namespace,
        }
        if pc_filter:
            kwargs["filter"] = pc_filter

        search_timeout = float(getattr(self.settings, "retrieval_timeout_seconds", 8.0) or 8.0)
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(index.query, **kwargs),
                timeout=search_timeout,
            )
            return response.get("matches", [])
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("pinecone_sparse_search_timeout", namespace=namespace)
            return []
        except (ConnectionError, OSError) as e:
            logger.error("pinecone_sparse_search_connection_error", namespace=namespace, error=str(e))
            return []
        except ValueError as e:
            logger.error("pinecone_sparse_search_value_error", namespace=namespace, error=str(e))
            return []
        except Exception as e:
            if "does not support sparse values" in str(e):
                logger.debug("pinecone_sparse_unsupported_falling_back_to_local_bm25")
            else:
                logger.warning("pinecone_sparse_search_unexpected_error", namespace=namespace, error=str(e))
            return []

    # ── Filter Builder ──────────────────────────────────────────────────

    @staticmethod
    def _build_filter(filter_conditions: dict | None) -> dict | None:
        """Convert a simple filter dictionary to Pinecone metadata filter format."""
        if not filter_conditions:
            return None

        conditions = []
        for key, value in filter_conditions.items():
            if value is None:
                continue
            if isinstance(value, list):
                conditions.append({key: {"$in": value}})
            else:
                conditions.append({key: {"$eq": value}})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    # ── Stats & Health ──────────────────────────────────────────────────

    async def get_collection_stats(self) -> dict:
        """Retrieve index statistics with non-blocking timeout."""
        index = self._get_index()
        if index is None:
            return {"points_count": 0, "status": "uninitialized"}

        try:
            stats = await asyncio.wait_for(
                asyncio.to_thread(index.describe_index_stats),
                timeout=3.0,
            )
            return {
                "points_count": stats.get("total_vector_count", 0),
                "dimension": stats.get("dimension", 0),
                "namespaces": stats.get("namespaces", {}),
                "status": "ready",
            }
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("pinecone_stats_timeout")
            return {"points_count": 0, "status": "timeout"}
        except (ConnectionError, OSError) as e:
            logger.warning("pinecone_stats_connection_error", error=str(e))
            return {"points_count": 0, "status": "connection_error"}
        except Exception as e:
            logger.warning("pinecone_stats_error: %s", e)
            return {"points_count": 0, "status": f"uninitialized / {e}"}

    async def health_check(self) -> bool:
        """Ping the Pinecone index to verify connectivity."""
        if not self.client:
            return False
        try:
            index = self._get_index()
            if index is None:
                return False
            await asyncio.wait_for(
                asyncio.to_thread(index.describe_index_stats),
                timeout=3.0,
            )
            return True
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("pinecone_health_check_timeout")
            return False
        except (ConnectionError, OSError) as e:
            logger.warning("pinecone_health_check_connection_error", error=str(e))
            return False
        except Exception as e:
            logger.warning("pinecone_health_check_failed: %s", e)
            return False

