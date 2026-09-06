import logging

from . import RetrievalResult

logger = logging.getLogger(__name__)

class Reranker:
    SOURCE_AUTHORITY = {
        "official": 1.0,
        "community": 0.6
    }

    def __init__(self, model_name: str = 'ms-marco-MiniLM-L-12-v2', provider: str | None = None):
        from config import get_settings
        settings = get_settings()
        self.model_name = model_name or getattr(settings, "reranker_model", 'ms-marco-MiniLM-L-12-v2')
        self.provider = provider or getattr(settings, "rerank_provider", "flashrank")
        self.model = None
        self._pinecone_client = None

    def _get_pinecone_client(self):
        if self._pinecone_client is None:
            try:
                from config import get_settings
                settings = get_settings()
                if settings.pinecone_api_key:
                    from pinecone import Pinecone
                    self._pinecone_client = Pinecone(api_key=settings.pinecone_api_key)
            except Exception as e:
                logger.debug("Pinecone client not initialized for reranking: %s", e)
        return self._pinecone_client

    def _load_model(self) -> None:
        if self.model is None:
            try:
                import tempfile
                from flashrank import Ranker
                logger.info(f"Loading FlashRank model: {self.model_name}")
                self.model = Ranker(model_name=self.model_name, cache_dir=tempfile.gettempdir())
            except ImportError:
                logger.warning("FlashRank package is not installed.")
            except (RuntimeError, OSError) as e:
                logger.error("Error loading FlashRank model: %s", e)
            except Exception as e:
                logger.error("Unexpected error loading FlashRank model: %s", e)

    def _rerank_pinecone(self, query: str, results: list[RetrievalResult], top_k: int = 10) -> list[RetrievalResult] | None:
        """Attempt Pinecone Inference API serverless reranking."""
        pc = self._get_pinecone_client()
        if not pc or not hasattr(pc, "inference"):
            return None
        try:
            passages = [{"id": res.chunk_id, "text": res.text} for res in results]
            resp = pc.inference.rerank(
                model="bge-reranker-v2-m3",
                query=query,
                documents=[p["text"] for p in passages],
                top_n=top_k,
                return_documents=False,
            )
            result_map = {res.chunk_id: res for res in results}
            final_results = []
            for item in getattr(resp, "data", []):
                idx = getattr(item, "index", None)
                if idx is not None and idx < len(results):
                    orig = results[idx]
                    score = float(getattr(item, "score", 0.0))
                    meta = orig.metadata or {}
                    source_type = meta.get("document_type", "community")
                    boost = self.SOURCE_AUTHORITY.get(source_type, 1.0)
                    final_results.append(RetrievalResult(
                        chunk_id=orig.chunk_id,
                        text=orig.text,
                        score=score * boost,
                        metadata=meta,
                    ))
            if final_results:
                final_results.sort(key=lambda x: x.score, reverse=True)
                return final_results[:top_k]
        except (ConnectionError, TimeoutError, OSError) as err:
            logger.warning("Pinecone rerank network/timeout error, falling back to FlashRank: %s", err)
        except ValueError as err:
            logger.warning("Pinecone rerank payload/value error: %s", err)
        except Exception as err:
            logger.warning("Pinecone rerank fallback to FlashRank: %s", err)
        return None

    def rerank(self, query: str, results: list[RetrievalResult], top_k: int = 10) -> list[RetrievalResult]:
        if not results:
            return []
        results = [r for r in results if isinstance(r, RetrievalResult) and not isinstance(r, BaseException)]
        if not results:
            return []

        if self.provider == "pinecone":
            pc_results = self._rerank_pinecone(query, results, top_k)
            if pc_results is not None:
                return pc_results

        self._load_model()

        if self.model is None:
            logger.warning("Reranker model not loaded, returning original results.")
            return results[:top_k]

        try:
            passages = []
            for res in results:
                passages.append({
                    "id": res.chunk_id,
                    "text": res.text,
                    "meta": res.metadata
                })

            from flashrank import RerankRequest
            rerank_request = RerankRequest(query=query, passages=passages)
            reranked_data = self.model.rerank(rerank_request)

            final_results = []
            for item in reranked_data:
                meta = item.get("meta", {})
                source_type = meta.get("document_type", "community")
                boost = self.SOURCE_AUTHORITY.get(source_type, 1.0)

                final_results.append(RetrievalResult(
                    chunk_id=item["id"],
                    text=item["text"],
                    score=item["score"] * boost,
                    metadata=meta
                ))

            final_results.sort(key=lambda x: x.score, reverse=True)
            return final_results[:top_k]
        except ImportError:
            logger.warning("FlashRank package missing during reranking execution, returning unranked candidates.")
            return results[:top_k]
        except (RuntimeError, ValueError) as e:
            logger.warning("Reranking execution failed: %s, returning original results.", e)
            return results[:top_k]
        except Exception as e:
            logger.error(f"Unexpected error during reranking: {e}")
            return results[:top_k]

