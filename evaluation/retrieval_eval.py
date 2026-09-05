"""
CloudGPT Retrieval Alpha Tuning and Golden Set Evaluation.

Evaluates dense/sparse alpha weighting combinations across 30+ golden questions
to compute Recall@10 and Precision@5 benchmarks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import get_settings
from embeddings.embedding_engine import EmbeddingEngine
from embeddings.pinecone_manager import PineconeManager
from retrieval.bm25 import SparseRetriever
from retrieval.dense import DenseRetriever
from retrieval.hybrid import HybridRetriever
from retrieval.reranker import Reranker

logger = logging.getLogger("evaluation.retrieval_eval")


async def evaluate_alphas(alphas: list[float] | None = None, limit: int | None = None) -> dict:
    if alphas is None:
        alphas = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

    golden_path = BASE_DIR / "evaluation" / "golden_set.json"
    if not golden_path.exists():
        print(f"Error: golden set not found at {golden_path}")
        return {}

    with open(golden_path, "r", encoding="utf-8") as f:
        golden_set = json.load(f)

    if limit is not None and limit > 0:
        golden_set = golden_set[:limit]

    settings = get_settings()
    embed_engine = EmbeddingEngine(
        provider=settings.embedding_provider,
        model_name=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    pinecone_mgr = PineconeManager(settings)
    dense = DenseRetriever(embed_engine, pinecone_mgr)
    sparse = SparseRetriever(embed_engine, pinecone_mgr)
    reranker = Reranker(model_name=settings.reranker_model)

    results = {}

    print(f"\nEvaluating {len(golden_set)} labeled questions across alphas: {alphas}...\n")

    for alpha in alphas:
        retriever = HybridRetriever(
            dense_retriever=dense,
            sparse_retriever=sparse,
            dense_weight=alpha,
            sparse_weight=1.0 - alpha,
        )

        recalls = []
        precisions = []

        for item in golden_set:
            q = item["question"]
            expected = [k.lower() for k in item["expected_keywords"]]

            candidates = await retriever.retrieve(q, top_k=50)
            if candidates:
                top_reranked = reranker.rerank(q, candidates, top_k=10)
            else:
                top_reranked = []

            # Check keyword hits in top-10 reranked chunks
            combined_text = " ".join(r.text.lower() for r in top_reranked)
            hits = sum(1 for kw in expected if kw in combined_text)
            
            recall_val = hits / len(expected) if expected else 1.0
            recalls.append(min(1.0, recall_val))
            precisions.append(min(1.0, hits / 5.0))

        avg_recall = sum(recalls) / len(recalls) if recalls else 0.0
        avg_prec = sum(precisions) / len(precisions) if precisions else 0.0

        results[alpha] = {
            "recall_at_10": round(avg_recall * 100, 2),
            "precision_at_5": round(avg_prec * 100, 2),
        }

    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="CloudGPT Retrieval Alpha Tuning Evaluation")
    parser.add_argument("--alphas", nargs="+", type=float, default=None, help="Specific alphas to evaluate, e.g. 0.3 0.7")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of golden set questions to evaluate")
    args = parser.parse_args()

    results = asyncio.run(evaluate_alphas(alphas=args.alphas, limit=args.limit))
    print("\n" + "=" * 55)
    print("RETRIEVAL EVALUATION RESULTS (ALPHA TUNING)")
    print("=" * 55)
    print(f"{'Alpha (Dense)':<16} | {'Recall@10':<14} | {'Precision@5':<12}")
    print("-" * 55)

    best_alpha = None
    best_score = -1.0

    for alpha, metric in results.items():
        rec = metric["recall_at_10"]
        prec = metric["precision_at_5"]
        if rec > best_score:
            best_score = rec
            best_alpha = alpha
        print(f"{alpha:<16.1f} | {rec:>11.2f}% | {prec:>9.2f}%")

    print("=" * 55)
    print(f"Optimal Alpha: {best_alpha} (Recall@10: {best_score:.2f}%)\n")


if __name__ == "__main__":
    main()
