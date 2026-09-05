import logging

from . import RetrievalResult
from embeddings.embedding_engine import EmbeddingEngine
from embeddings.pinecone_manager import PineconeManager

logger = logging.getLogger(__name__)


class DenseRetriever:
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
            query_vector = await self.embedding_engine.embed_query(query)
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
                    metadata={k: v for k, v in metadata.items() if k != "text"}
                ))
            return results
        except Exception as e:
            logger.error(f"Error in dense retrieval: {e}")
            return []
