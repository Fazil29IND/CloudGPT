"""
CloudGPT Baseline Snapshot Capture.

Captures vector database collection statistics, namespace counts,
and local chunk counts to establish a frozen baseline for evaluation diffs.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure root is on path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import get_settings
from embeddings.pinecone_manager import PineconeManager


async def capture_snapshot() -> dict:
    settings = get_settings()
    pinecone_mgr = PineconeManager(settings)

    # 1. Fetch vector stats
    stats = await pinecone_mgr.get_collection_stats()

    # 2. Inspect local chunks
    chunks_path = BASE_DIR / "data" / "chunks" / "services_chunks.json"
    local_chunk_stats = {
        "total_chunks": 0,
        "by_provider": {},
        "by_category": {},
    }

    if chunks_path.exists():
        try:
            with open(chunks_path, "r", encoding="utf-8") as f:
                chunks = json.load(f)
            local_chunk_stats["total_chunks"] = len(chunks)
            for ch in chunks:
                meta = ch.get("metadata", {})
                prov = meta.get("provider", "unknown")
                cat = meta.get("category", "unknown")
                local_chunk_stats["by_provider"][prov] = local_chunk_stats["by_provider"].get(prov, 0) + 1
                local_chunk_stats["by_category"][cat] = local_chunk_stats["by_category"].get(cat, 0) + 1
        except Exception as e:
            local_chunk_stats["error"] = str(e)

    ns_dict = {}
    for k, v in stats.get("namespaces", {}).items():
        if hasattr(v, "vector_count"):
            ns_dict[k] = {"vector_count": v.vector_count}
        elif hasattr(v, "to_dict"):
            ns_dict[k] = v.to_dict()
        elif isinstance(v, dict):
            ns_dict[k] = v
        else:
            ns_dict[k] = str(v)

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_corpus_version": getattr(settings, "active_corpus_version", "v1"),
        "index_name": settings.pinecone_index_name,
        "total_vector_count": stats.get("points_count", 0),
        "namespaces": ns_dict,
        "local_chunks": local_chunk_stats,
    }

    # Save to evaluation directory
    eval_dir = BASE_DIR / "evaluation"
    eval_dir.mkdir(parents=True, exist_ok=True)
    out_file = eval_dir / "baseline_snapshot.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)

    return snapshot


def main() -> None:
    snapshot = asyncio.run(capture_snapshot())
    print("\n" + "=" * 60)
    print("CLOUDGPT BASELINE EVALUATION SNAPSHOT")
    print("=" * 60)
    print(f"Timestamp:             {snapshot['timestamp']}")
    print(f"Active Corpus Version: {snapshot['active_corpus_version']}")
    print(f"Index Name:            {snapshot['index_name']}")
    print(f"Total Vector Count:    {snapshot['total_vector_count']}")
    print("\nNamespaces:")
    namespaces = snapshot.get("namespaces", {})
    if isinstance(namespaces, dict) and namespaces:
        for ns_name, ns_data in namespaces.items():
            cnt = ns_data.get("vector_count", ns_data.get("point_count", 0)) if isinstance(ns_data, dict) else ns_data
            print(f"  - {ns_name:<35}: {cnt} vectors")
    else:
        print("  (No remote namespaces or client offline)")

    print(f"\nLocal Chunks:          {snapshot['local_chunks']['total_chunks']}")
    for prov, count in snapshot['local_chunks'].get('by_provider', {}).items():
        print(f"  - Provider '{prov}': {count} chunks")
    print("=" * 60)
    print("Saved baseline to evaluation/baseline_snapshot.json\n")


if __name__ == "__main__":
    main()
