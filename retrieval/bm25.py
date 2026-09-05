import asyncio
import logging

from . import RetrievalResult
from embeddings.embedding_engine import EmbeddingEngine
from embeddings.pinecone_manager import PineconeManager

logger = logging.getLogger(__name__)


class SparseRetriever:
    def __init__(self, embedding_engine: EmbeddingEngine, pinecone_manager: PineconeManager):
        self.embedding_engine = embedding_engine
        self.pinecone_manager = pinecone_manager

    async def retrieve(
        self,
        query: str,
        top_k: int = 50,
        filters: dict | None = None,
        namespace: str | None = None,
    ) -> list[RetrievalResult]:
        try:
            sparse_vectors = await self.embedding_engine.embed_sparse([query])
            if not sparse_vectors:
                return []

            target_namespace = namespace or self.pinecone_manager.active_namespace("services")
            search_results = await self.pinecone_manager.search_sparse(
                sparse_vector=sparse_vectors[0],
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
                    metadata={k: v for k, v in metadata.items() if k != "text"}
                ))

            # Graceful local BM25S fallback if remote sparse returns empty
            if not results:
                results = await asyncio.to_thread(self._retrieve_local_bm25, query, top_k, filters)

            return results
        except Exception as e:
            logger.error(f"Error in sparse retrieval: {e}")
            return await asyncio.to_thread(self._retrieve_local_bm25, query, top_k, filters)

    def _retrieve_local_bm25(self, query: str, top_k: int = 50, filters: dict | None = None) -> list[RetrievalResult]:
        """Perform fast in-process BM25S lexical retrieval using the fitted index."""
        try:
            import json
            from pathlib import Path
            import bm25s
            from router.query_router import canonical_provider

            bm25_dir = Path("data/bm25_index")
            corpus_file = Path("data/chunks/corpus_chunks.json")
            services_file = Path("data/chunks/services_chunks.json")
            if not bm25_dir.exists():
                return []

            if not hasattr(self, "_local_bm25") or self._local_bm25 is None:
                self._local_bm25 = bm25s.BM25.load(str(bm25_dir), load_corpus=False)
                if corpus_file.exists():
                    with open(corpus_file, "r", encoding="utf-8") as f:
                        self._local_chunks = json.load(f)
                elif services_file.exists():
                    with open(services_file, "r", encoding="utf-8") as f:
                        chunks = json.load(f)
                    # Merge senior engineer knowledge if present to match BM25 index size
                    knowledge_dir = Path("data/senior_engineer_knowledge")
                    if knowledge_dir.exists():
                        try:
                            from ingest_services import build_knowledge_chunks
                            kn = build_knowledge_chunks(knowledge_dir)
                            for ns_chunks in kn.values():
                                chunks.extend(ns_chunks)
                        except Exception:
                            pass
                    self._local_chunks = chunks
                    try:
                        corpus_file.parent.mkdir(parents=True, exist_ok=True)
                        with open(corpus_file, "w", encoding="utf-8") as cf:
                            json.dump(chunks, cf)
                    except Exception:
                        pass
                else:
                    return []

            tokens = bm25s.tokenize([query])
            doc_ids, scores = self._local_bm25.retrieve(tokens, k=min(top_k, len(self._local_chunks)))
            results = []
            for idx, doc_idx in enumerate(doc_ids[0]):
                if doc_idx >= len(self._local_chunks):
                    continue
                chunk = self._local_chunks[doc_idx]
                meta = chunk.get("metadata", {})
                score = float(scores[0, idx])
                if score <= 0.0:
                    continue

                # Apply filters if provided
                if filters:
                    skip = False
                    for fk, fv in filters.items():
                        if fv is None:
                            continue
                        if fk == "provider":
                            meta_p = (meta.get("provider") or "").lower()
                            if isinstance(fv, list):
                                norm_targets = set(canonical_provider(p) for p in fv)
                            else:
                                norm_targets = {canonical_provider(str(fv))}
                            norm_meta = canonical_provider(meta_p)
                            # Multi-cloud / cross-cloud chunks match all providers
                            if norm_meta in ("multi-cloud", "cross-cloud", "all"):
                                continue
                            if norm_meta not in norm_targets and meta_p not in norm_targets:
                                skip = True
                                break
                        else:
                            if meta.get(fk) != fv:
                                skip = True
                                break
                    if skip:
                        continue

                results.append(RetrievalResult(
                    chunk_id=chunk.get("chunk_id", str(doc_idx)),
                    text=chunk.get("text", ""),
                    score=score,
                    metadata=meta,
                ))
            return results
        except Exception as err:
            logger.debug("Local BM25 fallback notice: %s", err)
            return []
