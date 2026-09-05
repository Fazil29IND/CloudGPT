"""
Hybrid Retriever for CloudGPT.

Combines Dense (vector semantic) and Sparse (BM25 tokenized lexical) retrieval
across multi-domain versioned namespaces using Reciprocal Rank Fusion (RRF),
query-type-aware dynamic weighting, and URL-level deduplication.
"""

from __future__ import annotations

import asyncio
import re

import structlog


from config import get_settings
from metrics import RAG_FALLBACK_TOTAL
from . import RetrievalResult
from .bm25 import SparseRetriever
from .dense import DenseRetriever

logger = structlog.get_logger(__name__)



class HybridRetriever:
    def __init__(
        self,
        dense_retriever: DenseRetriever,
        sparse_retriever: SparseRetriever,
        dense_weight: float = 0.6,
        sparse_weight: float = 0.4,
    ) -> None:
        self.dense_retriever = dense_retriever
        self.sparse_retriever = sparse_retriever
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight

    def _is_exact_technical_query(self, query: str) -> bool:
        """Detect exact technical queries like CLI commands, error codes, and flags."""
        q = query.strip()
        if re.search(r"(--[a-zA-Z0-9_-]+|\b[A-Z][a-zA-Z0-9]+Exception\b|\bError:\b|\b\d{3}\s+Forbidden\b|\b\d+\.\d+\.\d+\b)", q):
            return True
        if any(cmd in q.lower() for cmd in ["aws ", "az ", "gcloud ", "kubectl ", "terraform "]):
            return True
        return False

    def _get_effective_weights(self, query: str) -> tuple[float, float]:
        """Compute query-adaptive dense and sparse weights based on intent/syntax."""
        settings = get_settings()
        if self._is_exact_technical_query(query):
            w_dense = getattr(settings, "dense_weight_exact", 0.3)
            return w_dense, 1.0 - w_dense
        else:
            w_dense = getattr(settings, "dense_weight_nlq", 0.7)
            return w_dense, 1.0 - w_dense

    def _select_namespaces(self, filters: dict | None = None) -> list[str]:
        """Determine target versioned namespaces based on filters and active corpus version."""
        mgr = self.dense_retriever.pinecone_manager
        settings = get_settings()
        version = getattr(settings, "active_corpus_version", "v1")
        active_services = mgr.active_namespace("services")

        if filters:
            doc_type = filters.get("document_type")
            if doc_type == "troubleshooting_playbook":
                ns = [mgr.active_namespace("troubleshooting-playbooks")]
                if version == "v1":
                    ns.append("troubleshooting-playbooks")
                return ns
            if doc_type == "iac_template":
                ns = [mgr.active_namespace("iac-templates")]
                if version == "v1":
                    ns.append("iac-templates")
                return ns

        # General queries: v2+ prunes legacy namespaces for lower latency & zero phantom reads
        if version == "v1":
            all_ns = [
                active_services,
                "services-master",
                "",
                mgr.active_namespace("senior-engineer-knowledge"),
                mgr.active_namespace("troubleshooting-playbooks"),
                mgr.active_namespace("iac-templates"),
            ]
        else:
            all_ns = [
                active_services,
                mgr.active_namespace("senior-engineer-knowledge"),
                mgr.active_namespace("troubleshooting-playbooks"),
                mgr.active_namespace("iac-templates"),
            ]

        # Deduplicate while preserving priority order
        seen = set()
        deduped = []
        for ns in all_ns:
            if ns not in seen:
                seen.add(ns)
                deduped.append(ns)

        return deduped

    async def retrieve(
        self,
        query: str,
        top_k: int = 50,
        filters: dict | None = None,
        namespace: str | None = None,
        namespaces: list[str] | None = None,
        expand_to_parents: bool = False,
    ) -> list[RetrievalResult]:
        """Concurrently query dense and sparse retrievers across target namespaces."""
        try:
            target_namespaces = namespaces or ([namespace] if namespace else self._select_namespaces(filters))
            dense_weight, sparse_weight = self._get_effective_weights(query)

            # Concurrent fan-out across all target namespaces with per-namespace fault isolation
            dense_tasks = [
                self.dense_retriever.retrieve(query, top_k=top_k, filters=filters, namespace=ns)
                for ns in target_namespaces
            ]
            sparse_tasks = [
                self.sparse_retriever.retrieve(query, top_k=top_k, filters=filters, namespace=ns)
                for ns in target_namespaces
            ]

            dense_raw_batches, sparse_raw_batches = await asyncio.gather(
                asyncio.gather(*dense_tasks, return_exceptions=True),
                asyncio.gather(*sparse_tasks, return_exceptions=True),
            )

            all_dense_batches: list[list[RetrievalResult]] = []
            for idx, batch in enumerate(dense_raw_batches):
                ns = target_namespaces[idx] if idx < len(target_namespaces) else "unknown"
                if isinstance(batch, (TimeoutError, asyncio.TimeoutError)):
                    logger.warning("dense_retrieval_namespace_timeout", namespace=ns)
                elif isinstance(batch, (ConnectionError, OSError)):
                    logger.warning("dense_retrieval_namespace_network_error", namespace=ns, error=str(batch))
                elif isinstance(batch, Exception):
                    logger.warning("dense_retrieval_namespace_error", namespace=ns, error=str(batch))
                elif isinstance(batch, list):
                    all_dense_batches.append(batch)

            all_sparse_batches: list[list[RetrievalResult]] = []
            for idx, batch in enumerate(sparse_raw_batches):
                ns = target_namespaces[idx] if idx < len(target_namespaces) else "unknown"
                if isinstance(batch, (TimeoutError, asyncio.TimeoutError)):
                    logger.warning("sparse_retrieval_namespace_timeout", namespace=ns)
                elif isinstance(batch, (ConnectionError, OSError)):
                    logger.warning("sparse_retrieval_namespace_network_error", namespace=ns, error=str(batch))
                elif isinstance(batch, Exception):
                    logger.warning("sparse_retrieval_namespace_error", namespace=ns, error=str(batch))
                elif isinstance(batch, list):
                    all_sparse_batches.append(batch)

            # Flatten and deduplicate candidates within dense and sparse lists
            dense_candidates: dict[str, RetrievalResult] = {}
            for batch in all_dense_batches:
                for res in batch:
                    if res.chunk_id not in dense_candidates or res.score > dense_candidates[res.chunk_id].score:
                        dense_candidates[res.chunk_id] = res

            sparse_candidates: dict[str, RetrievalResult] = {}
            for batch in all_sparse_batches:
                for res in batch:
                    if res.chunk_id not in sparse_candidates or res.score > sparse_candidates[res.chunk_id].score:
                        sparse_candidates[res.chunk_id] = res

            # Graceful degraded fallback if one retriever produces no candidates
            if not dense_candidates and sparse_candidates:
                RAG_FALLBACK_TOTAL.labels(fallback_type="dense_to_sparse").inc()
                logger.info("hybrid_retrieval_fallback_sparse_only", query_prefix=query[:40])
                dense_weight = 0.0
                sparse_weight = 1.0
            elif not sparse_candidates and dense_candidates:
                RAG_FALLBACK_TOTAL.labels(fallback_type="sparse_to_dense").inc()
                logger.info("hybrid_retrieval_fallback_dense_only", query_prefix=query[:40])
                dense_weight = 1.0
                sparse_weight = 0.0
            elif not dense_candidates and not sparse_candidates:
                RAG_FALLBACK_TOTAL.labels(fallback_type="retrieval_empty").inc()
                logger.warning("hybrid_retrieval_empty_candidates", query_prefix=query[:40])
                return []

            # Sort candidate lists for RRF rank computation
            sorted_dense = sorted(dense_candidates.values(), key=lambda x: x.score, reverse=True)
            sorted_sparse = sorted(sparse_candidates.values(), key=lambda x: x.score, reverse=True)

            scores: dict[str, float] = {}
            merged_results: dict[str, RetrievalResult] = {}
            k_rrf = 60

            for rank, res in enumerate(sorted_dense):
                if res.chunk_id not in scores:
                    scores[res.chunk_id] = 0.0
                    merged_results[res.chunk_id] = res
                scores[res.chunk_id] += dense_weight * (1.0 / (k_rrf + rank + 1))

            for rank, res in enumerate(sorted_sparse):
                if res.chunk_id not in scores:
                    scores[res.chunk_id] = 0.0
                    merged_results[res.chunk_id] = res
                scores[res.chunk_id] += sparse_weight * (1.0 / (k_rrf + rank + 1))

            for chunk_id in merged_results:
                merged_results[chunk_id].score = scores[chunk_id]

            ranked_list = sorted(merged_results.values(), key=lambda x: x.score, reverse=True)

            # Deduplicate by canonical_url (keep highest RRF scoring chunk per URL)
            url_seen: dict[str, RetrievalResult] = {}
            deduped: list[RetrievalResult] = []
            for r in ranked_list:
                url = (r.metadata.get("url") or "").strip()
                if url:
                    if url in url_seen:
                        continue
                    url_seen[url] = r
                deduped.append(r)

            final_results = deduped[:top_k]

            if expand_to_parents:
                from chunking.hierarchical_store import HierarchicalChunkStore
                store = HierarchicalChunkStore.get_instance()
                resolved_results: list[RetrievalResult] = []
                seen_parent_ids: set[str] = set()

                for r in final_results:
                    parent = store.get_parent(r.chunk_id)
                    if not parent and r.metadata.get("parent_chunk_id"):
                        parent = store.get_chunk(r.metadata["parent_chunk_id"])

                    if parent:
                        if parent.chunk_id in seen_parent_ids:
                            continue
                        seen_parent_ids.add(parent.chunk_id)
                        p_meta = parent.model_dump()
                        p_meta.update({
                            "resolved_from_child_id": r.chunk_id,
                            "is_coalesced_parent": True,
                        })
                        resolved_results.append(RetrievalResult(
                            chunk_id=parent.chunk_id,
                            text=parent.text,
                            score=r.score,
                            metadata=p_meta,
                        ))
                    else:
                        resolved_results.append(r)

                return resolved_results

            return final_results

        except (TimeoutError, asyncio.TimeoutError) as e:
            RAG_FALLBACK_TOTAL.labels(fallback_type="timeout").inc()
            logger.error("hybrid_retrieval_timeout", error=str(e), query_prefix=query[:40])
            return []
        except (ConnectionError, OSError) as e:
            RAG_FALLBACK_TOTAL.labels(fallback_type="network_error").inc()
            logger.error("hybrid_retrieval_network_error", error=str(e), query_prefix=query[:40])
            return []
        except ValueError as e:
            logger.error("hybrid_retrieval_value_error", error=str(e))
            return []
        except Exception as e:
            logger.exception("hybrid_retrieval_unexpected_error", error=str(e))
            return []

