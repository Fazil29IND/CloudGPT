"""
Replay Dead-Letter Queue CLI for CloudGPT.

Retries failed embedding and upsert operations from dead_letter_queue.json.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import get_settings
from corpus.ingestion_state import IngestionStateManager
from embeddings.embedding_engine import EmbeddingEngine
from embeddings.pinecone_manager import PineconeManager

logger = logging.getLogger("corpus.replay")


async def replay_dead_letters() -> dict:
    settings = get_settings()
    mgr = IngestionStateManager()

    if not mgr.dlq:
        print("Dead-letter queue is empty. No failed chunks to replay.")
        return {"total": 0, "replayed": 0, "failed": 0}

    print(f"\nFound {len(mgr.dlq)} failed chunks in dead-letter queue. Starting replay...")

    embed_engine = EmbeddingEngine(
        provider=settings.embedding_provider,
        model_name=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    pinecone_mgr = PineconeManager(settings)

    successful_indices = []
    failed_count = 0

    for idx, item in enumerate(mgr.dlq):
        print(f"[{idx+1}/{len(mgr.dlq)}] Retrying chunk: {item.chunk_id} (error was: {item.error})")
        item.retry_count += 1
        try:
            if not item.text_preview:
                print(f"  Skipping {item.chunk_id}: text preview missing.")
                failed_count += 1
                continue

            dense = await embed_engine.embed_texts([item.text_preview])
            sparse = await embed_engine.embed_sparse([item.text_preview])

            chunk_payload = {
                "chunk_id": item.chunk_id,
                "text": item.text_preview,
                "dense_vector": dense[0],
                "sparse_vector": sparse[0] if sparse else {},
                "metadata": {"replayed": True},
            }

            ns = item.namespace or pinecone_mgr.active_namespace("services")
            await pinecone_mgr.upsert_chunks([chunk_payload], namespace=ns)
            successful_indices.append(idx)
            print(f"  Successfully replayed and upserted {item.chunk_id}")
        except Exception as e:
            item.error = f"Retry {item.retry_count} failed: {e}"
            failed_count += 1
            print(f"  Replay failed for {item.chunk_id}: {e}")

    # Remove successful items in reverse order
    for idx in reversed(successful_indices):
        mgr.dlq.pop(idx)

    mgr.save()
    print(f"\nReplay summary: {len(successful_indices)} recovered, {failed_count} still failing.")
    return {"total": len(successful_indices) + failed_count, "replayed": len(successful_indices), "failed": failed_count}


def main() -> None:
    asyncio.run(replay_dead_letters())


if __name__ == "__main__":
    main()
