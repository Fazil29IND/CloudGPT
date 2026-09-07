"""
CloudGPT Services.Md Ingestion & RAG Indexing Pipeline.

Parses Services.Md, extracts all 15 Lifecycle Phases, 21 Service Categories,
144 Purpose Rows, 5 Strategic Comparison deep-dives, and 700+ service entries
across AWS, GCP, and Azure. Enriches them with metadata from sources/sources.json,
creates token-budgeted semantic chunks, fits the BM25S lexical retriever,
and indexes them idempotently into versioned Pinecone namespaces.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from config import get_settings
from chunking.semantic_chunker import SemanticChunker
from corpus.ingestion_state import IngestionStateManager
from embeddings.embedding_engine import EmbeddingEngine
from embeddings.qdrant_manager import QdrantManager
from embeddings.pinecone_manager import PineconeManager
from sources.generate_manifest import split_cell_services

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
logger = logging.getLogger("ingest_services")

# Senior Cloud Engineer knowledge corpus: each file is ingested into a
# dedicated Pinecone namespace with domain metadata so retrieval can target
# playbooks, IaC templates, or the broader engineering knowledge base.
KNOWLEDGE_FILES: list[dict[str, Any]] = [
    {
        "filename": "architecture_patterns.md",
        "namespace_base": "senior-engineer-knowledge",
        "domain": "architecture",
        "document_type": "architecture_patterns",
        "title": "Architecture & Resilience Patterns (Multi-Region, DR, Zero-Trust)",
    },
    {
        "filename": "troubleshooting_playbooks.md",
        "namespace_base": "troubleshooting-playbooks",
        "domain": "troubleshooting",
        "document_type": "troubleshooting_playbook",
        "title": "Production Troubleshooting Playbooks (K8s, IAM, VPC-SC, DNS, Redis)",
    },
    {
        "filename": "iac_templates.md",
        "namespace_base": "iac-templates",
        "domain": "iac",
        "document_type": "iac_template",
        "title": "Production IaC Templates (Terraform, Bicep, CloudFormation, Helm)",
    },
    {
        "filename": "finops_cost_optimization.md",
        "namespace_base": "senior-engineer-knowledge",
        "domain": "finops",
        "document_type": "finops_guide",
        "title": "FinOps & Cost Optimization (Commitments, Egress, Lifecycles, Idle Sweeps)",
    },
    {
        "filename": "cloud_cli_cheat_sheets.md",
        "namespace_base": "senior-engineer-knowledge",
        "domain": "cli",
        "document_type": "cli_cheatsheet",
        "title": "Cloud CLI Cheat Sheets (AWS CLI, gcloud, az)",
    },
    {
        "filename": "module_catalog.md",
        "namespace_base": "iac-templates",
        "domain": "iac",
        "document_type": "module_catalog",
        "title": "Verified Module Catalog (terraform-aws-modules, Azure AVM, Google modules) — Usage Recipes & Gotchas",
    },
    {
        "filename": "implementation_runbooks.md",
        "namespace_base": "senior-engineer-knowledge",
        "domain": "architecture",
        "document_type": "implementation_runbook",
        "title": "Implementation Runbooks (Provision → Verify → Operate per Service Family)",
    },
    {
        "filename": "postmortems.md",
        "namespace_base": "troubleshooting-playbooks",
        "domain": "troubleshooting",
        "document_type": "postmortem",
        "title": "Failure-Class Postmortems (Quota, State Corruption, Lockout, DNS, KMS, Drift, Egress, Regional)",
    },
    {
        "filename": "security_hardening.md",
        "namespace_base": "senior-engineer-knowledge",
        "domain": "security",
        "document_type": "security_hardening",
        "title": "Security Hardening Aligned to IaC Scanners (Checkov Rule Classes → Correct Fixes)",
    },
    {
        "filename": "migration_and_cutover.md",
        "namespace_base": "senior-engineer-knowledge",
        "domain": "architecture",
        "document_type": "migration_playbook",
        "title": "Migration & Cutover Playbooks (DB Cutover, Blue/Green, Canary, Storage, Landing Zones)",
    },
    {
        "filename": "platform_cicd.md",
        "namespace_base": "senior-engineer-knowledge",
        "domain": "architecture",
        "document_type": "platform_cicd",
        "title": "Platform & CI/CD for Infrastructure (OIDC, Plan/Apply Separation, Drift, Test Pyramid)",
    },
]


def parse_services_md(md_path: Path) -> dict[str, Any]:
    """Parse Services.Md into structured lifecycle phases, catalog categories, and strategic sections."""
    with open(md_path, "r", encoding="utf-8") as f:
        raw_content = f.read()

    lines = raw_content.splitlines()
    current_section = ""
    current_category = ""

    lifecycle_phases: list[dict[str, Any]] = []
    catalog_categories: list[dict[str, Any]] = []
    catalog_by_name: dict[str, dict[str, Any]] = {}
    strategy_sections: list[dict[str, Any]] = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        if line.startswith("## "):
            current_section = line.replace("## ", "").strip()
            i += 1
            continue

        if line.startswith("### "):
            header = line.replace("### ", "").strip()
            if "Categorized" in current_section or "Catalog" in current_section:
                current_category = header
                if current_category not in catalog_by_name:
                    cat_obj = {
                        "category": current_category,
                        "rows": [],
                        "raw_text": f"### {header}\n",
                    }
                    catalog_categories.append(cat_obj)
                    catalog_by_name[current_category] = cat_obj
                i += 1
                continue
            elif "Platform Architecture" in current_section or "Strategic Comparison" in current_section:
                strat_title = header
                strat_lines = []
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith("### ") and not lines[j].strip().startswith("## "):
                    strat_lines.append(lines[j])
                    j += 1
                strategy_sections.append({
                    "title": strat_title,
                    "content": "\n".join(strat_lines).strip()
                })
                i = j
                continue

        if line.startswith("|") and not line.startswith("| :---") and not line.startswith("| Phase") and not line.startswith("| Purpose"):
            cols = [c.strip() for c in line.split("|")[1:-1]]

            # 1. Lifecycle Table
            if "Lifecycle" in current_section and len(cols) >= 5:
                phase_title = cols[0].replace("**", "").strip()
                phase_desc = cols[1]
                lifecycle_phases.append({
                    "phase": phase_title,
                    "description": phase_desc,
                    "aws": split_cell_services(cols[2]),
                    "gcp": split_cell_services(cols[3]),
                    "azure": split_cell_services(cols[4]),
                    "aws_raw": cols[2],
                    "gcp_raw": cols[3],
                    "azure_raw": cols[4],
                })
            # 2. Categorized Catalog Tables
            elif ("Categorized" in current_section or "Catalog" in current_section) and len(cols) >= 4:
                purpose = cols[0]
                row_data = {
                    "category": current_category,
                    "purpose": purpose,
                    "aws": split_cell_services(cols[1]),
                    "gcp": split_cell_services(cols[2]),
                    "azure": split_cell_services(cols[3]),
                    "aws_raw": cols[1],
                    "gcp_raw": cols[2],
                    "azure_raw": cols[3],
                }
                if current_category in catalog_by_name:
                    catalog_by_name[current_category]["rows"].append(row_data)
                    catalog_by_name[current_category]["raw_text"] += f"{line}\n"

        i += 1

    return {
        "raw_content": raw_content,
        "lifecycle_phases": lifecycle_phases,
        "catalog_categories": catalog_categories,
        "strategy_sections": strategy_sections,
    }


def build_all_rag_chunks(
    parsed_data: dict[str, Any],
    sources_json_path: Path,
) -> list[dict[str, Any]]:
    """Build comprehensive semantic chunks across all sections of Services.Md."""
    all_chunks: list[dict[str, Any]] = []
    chunker = SemanticChunker(max_chunk_chars=1800, min_chunk_chars=200, overlap_chars=200)

    # 1. Semantic Chunks from Services.Md document
    doc_chunks = chunker.chunk_document(
        content=parsed_data["raw_content"],
        provider="multi-cloud",
        category="Cloud Services Reference",
        service="All Cloud Services",
        url="https://cloud.google.com/ / https://aws.amazon.com/ / https://azure.microsoft.com/",
        source="Services.Md",
        document_type="comparison_matrix",
        title="AWS vs Google Cloud vs Azure Services Master Reference (2026)",
    )

    for chunk in doc_chunks:
        all_chunks.append({
            "chunk_id": chunk.chunk_id,
            "text": chunk.text,
            "metadata": {
                "provider": chunk.provider,
                "category": chunk.category,
                "service": chunk.service,
                "document_type": chunk.document_type,
                "title": chunk.title,
                "section": chunk.section,
                "subsection": chunk.subsection,
                "url": chunk.url,
                "source": "Services.Md",
                "service_status": "active",
                "content_hash": chunk.content_hash,
            },
        })

    # 2. Lifecycle Phase Matrix Chunks (15 chunks)
    for idx, lp in enumerate(parsed_data["lifecycle_phases"]):
        phase_name = lp["phase"]
        desc = lp["description"]
        clean_slug = re.sub(r"[^a-z0-9]+", "-", phase_name.lower()).strip("-")

        text = (
            f"# Cloud Architecture Lifecycle — Phase {phase_name}\n\n"
            f"**Phase Objective:** {desc}\n\n"
            f"### Cloud Provider Architecture Mapping:\n"
            f"- **AWS:** {lp['aws_raw']}\n"
            f"- **Google Cloud:** {lp['gcp_raw']}\n"
            f"- **Azure:** {lp['azure_raw']}\n\n"
            f"**Phase Analysis:** In Phase '{phase_name}' ({desc}), AWS provides {', '.join(lp['aws'])}; "
            f"Google Cloud utilizes {', '.join(lp['gcp'])}; and Azure deploys {', '.join(lp['azure'])}."
        )

        all_chunks.append({
            "chunk_id": f"lifecycle-phase-{idx:02d}-{clean_slug}",
            "text": text,
            "metadata": {
                "provider": "multi-cloud",
                "category": f"Lifecycle: {phase_name}",
                "service": "Architecture Lifecycle",
                "document_type": "lifecycle_matrix",
                "title": f"Lifecycle Phase: {phase_name}",
                "section": "1. Cloud Architecture Lifecycle",
                "subsection": phase_name,
                "url": "https://aws.amazon.com/ / https://cloud.google.com/ / https://azure.microsoft.com/",
                "source": "Services.Md",
                "service_status": "active",
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        })

    # 3. Category Catalog Matrix Chunks (21 chunks)
    for idx, cat in enumerate(parsed_data["catalog_categories"]):
        cat_name = cat["category"]
        clean_slug = re.sub(r"[^a-z0-9]+", "-", cat_name.lower()).strip("-")

        rows_text = []
        for r in cat["rows"]:
            rows_text.append(
                f"- **Purpose:** {r['purpose']}\n"
                f"  * AWS: {r['aws_raw']}\n"
                f"  * Google Cloud: {r['gcp_raw']}\n"
                f"  * Azure: {r['azure_raw']}"
            )

        text = (
            f"# Cloud Service Catalog Category: {cat_name}\n\n"
            f"## Purpose & Capability Comparison Across AWS, GCP, and Azure:\n\n"
            + "\n\n".join(rows_text) + "\n\n"
            f"**Summary:** Across {len(cat['rows'])} specialized capabilities in {cat_name}, "
            f"organizations can select equivalent architectures across AWS, Google Cloud, and Azure."
        )

        all_chunks.append({
            "chunk_id": f"category-catalog-{idx:02d}-{clean_slug}",
            "text": text,
            "metadata": {
                "provider": "multi-cloud",
                "category": cat_name,
                "service": "Category Comparison",
                "document_type": "category_catalog",
                "title": f"Category Catalog: {cat_name} (AWS vs GCP vs Azure)",
                "section": "2. Categorized Cloud Service Catalog",
                "subsection": cat_name,
                "url": "https://aws.amazon.com/ / https://cloud.google.com/ / https://azure.microsoft.com/",
                "source": "Services.Md",
                "service_status": "active",
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        })

    # 4. Strategic Architecture Deep Dives (5 chunks)
    for idx, strat in enumerate(parsed_data["strategy_sections"]):
        strat_title = strat["title"]
        clean_slug = re.sub(r"[^a-z0-9]+", "-", strat_title.lower()).strip("-")

        text = (
            f"# Platform Architecture & Strategic Comparison: {strat_title}\n\n"
            f"{strat['content']}"
        )

        all_chunks.append({
            "chunk_id": f"strategic-comparison-{idx:02d}-{clean_slug}",
            "text": text,
            "metadata": {
                "provider": "multi-cloud",
                "category": "Platform Architecture & Strategy",
                "service": strat_title,
                "document_type": "strategic_analysis",
                "title": f"Strategy: {strat_title}",
                "section": "3. Platform Architecture & Strategic Comparison",
                "subsection": strat_title,
                "url": "https://aws.amazon.com/ / https://cloud.google.com/ / https://azure.microsoft.com/",
                "source": "Services.Md",
                "service_status": "active",
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        })

    # 5. Individual Service Knowledge Cards (from sources.json / parsed catalogs)
    if sources_json_path.exists():
        with open(sources_json_path, "r", encoding="utf-8") as f:
            sources_list = json.load(f)

        for idx, item in enumerate(sources_list):
            provider = item["provider"]
            service_name = item["service"]
            category = item["category"]
            purpose = item.get("purpose", "")
            status = item.get("service_status", "active")
            aliases = item.get("aliases", [])
            url = item.get("urls", [{}])[0].get("url", "")

            clean_id = item["id"]

            card_text = (
                f"Service: {service_name}\n"
                f"Cloud Provider: {provider}\n"
                f"Domain / Category: {category}\n"
            )
            if purpose:
                card_text += f"Core Purpose / Workload Role: {purpose}\n"
            card_text += (
                f"Lifecycle / Availability Status: {status}\n"
                f"Official Documentation: {url}\n"
            )
            if aliases:
                card_text += f"Aliases, Former Names & Related Keywords: {', '.join(aliases)}\n"
            card_text += (
                f"Overview: {provider} provides {service_name} for {purpose or category.lower()} in cloud architectures.\n"
            )

            all_chunks.append({
                "chunk_id": f"svc-card-{clean_id}-{idx:04d}",
                "text": card_text,
                "metadata": {
                    "provider": provider.lower().replace(" ", "-"),
                    "category": category,
                    "service": service_name,
                    "purpose": purpose,
                    "document_type": "service_card",
                    "title": f"{provider} {service_name} ({category})",
                    "section": category,
                    "subsection": service_name,
                    "url": url,
                    "source": "Services.Md",
                    "service_status": status,
                    "aliases": "|".join(aliases) if isinstance(aliases, list) else (aliases or ""),
                    "content_hash": hashlib.sha256(card_text.encode("utf-8")).hexdigest(),
                }
            })

    return all_chunks


def build_knowledge_chunks(knowledge_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Chunk the Senior Cloud Engineer knowledge corpus using parent-child chunking.

    Returns a mapping of namespace_base -> list of embedded-ready chunk dicts.
    """
    chunker = SemanticChunker(
        max_chunk_chars=1800,
        min_chunk_chars=200,
        overlap_chars=200,
        parent_token_budget=1200,
        child_token_budget=450,
        child_token_overlap=50,
    )
    by_namespace: dict[str, list[dict[str, Any]]] = {}

    for spec in KNOWLEDGE_FILES:
        path = knowledge_dir / spec["filename"]
        if not path.exists():
            logger.warning("Knowledge file missing, skipping: %s", path)
            continue

        content = path.read_text(encoding="utf-8")
        pairs = chunker.chunk_document_with_parents(
            content=content,
            provider="multi-cloud",
            category=spec["domain"],
            service=spec["document_type"],
            url="",
            source=spec["filename"],
            document_type=spec["document_type"],
            title=spec["title"],
        )

        ns_chunks = by_namespace.setdefault(spec["namespace_base"], [])
        for parent, children in pairs:
            # Include child chunks (and optionally parent chunk)
            for chunk in children:
                ns_chunks.append({
                    "chunk_id": f"kb-{chunk.chunk_id}",
                    "text": chunk.text,
                    "metadata": {
                        "provider": chunk.provider,
                        "category": chunk.category,
                        "service": chunk.service,
                        "document_type": chunk.document_type,
                        "title": chunk.title,
                        "section": chunk.section,
                        "subsection": chunk.subsection,
                        "url": chunk.url,
                        "source": spec["filename"],
                        "domain": spec["domain"],
                        "difficulty_tier": "senior",
                        "service_status": "active",
                        "content_hash": chunk.content_hash,
                        "parent_chunk_id": chunk.parent_chunk_id,
                        "child_index": chunk.child_index,
                        "parser_version": chunk.parser_version,
                        "chunker_version": chunk.chunker_version,
                    },
                })
        logger.info(
            "Chunked knowledge file %s into %d child chunks (namespace_base=%s)",
            spec["filename"], len(ns_chunks), spec["namespace_base"],
        )

    return by_namespace


async def ingest_services_rag(
    dry_run: bool = False,
    corpus_version: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    dimension: int | None = None,
) -> dict[str, Any]:
    """Run full idempotent ingestion of Services.Md into Pinecone RAG vector database."""
    settings = get_settings()
    if corpus_version:
        settings.active_corpus_version = corpus_version
    base_dir = Path(__file__).resolve().parent
    md_path = base_dir / "Services.Md"
    sources_json_path = base_dir / "sources" / "sources.json"
    knowledge_dir = base_dir / "data" / "senior_engineer_knowledge"

    logger.info("Starting Services.Md RAG ingestion pipeline (dry_run=%s)...", dry_run)
    logger.info("Reading Services.Md from: %s", md_path)

    parsed_data = parse_services_md(md_path)
    all_chunks = build_all_rag_chunks(parsed_data, sources_json_path)
    knowledge_by_namespace = build_knowledge_chunks(knowledge_dir)

    # 4. Save chunks and metadata to data/ directories
    data_dir = base_dir / "data"
    chunks_file = data_dir / "chunks" / "services_chunks.json"
    metadata_file = data_dir / "metadata" / "services_metadata.json"

    chunks_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)

    with open(chunks_file, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2)

    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump({
            "total_chunks": len(all_chunks),
            "lifecycle_phases_count": len(parsed_data["lifecycle_phases"]),
            "catalog_categories_count": len(parsed_data["catalog_categories"]),
            "strategy_sections_count": len(parsed_data["strategy_sections"]),
            "categories": [c["category"] for c in parsed_data["catalog_categories"]],
        }, f, indent=2)

    total_chunks = len(all_chunks) + sum(len(c) for c in knowledge_by_namespace.values())

    if dry_run:
        print("\n" + "=" * 65)
        print(f"{'Namespace':<30} | {'Chunks':<8} | {'Avg Chars':<10} | {'Status'}")
        print("-" * 65)
        print(f"{'services':<30} | {len(all_chunks):<8} | {int(sum(len(c['text']) for c in all_chunks)/len(all_chunks)):<10} | Ready")
        for ns, n_chunks in knowledge_by_namespace.items():
            avg_c = int(sum(len(c['text']) for c in n_chunks) / max(1, len(n_chunks)))
            print(f"{ns:<30} | {len(n_chunks):<8} | {avg_c:<10} | Ready")
        print("=" * 65)
        print(f"Total Chunks: {total_chunks} across {1 + len(knowledge_by_namespace)} namespaces.\n")
        return {"status": "dry_run", "total_chunks": total_chunks}

    # 5. Fit BM25S Lexical Index over all corpus texts
    all_texts = [chunk["text"] for chunk in all_chunks]
    for ns_chunks in knowledge_by_namespace.values():
        all_texts.extend([chunk["text"] for chunk in ns_chunks])

    logger.info("Fitting BM25S lexical index on %d total texts...", len(all_texts))
    embedding_engine = EmbeddingEngine(
        provider=provider or settings.embedding_provider,
        model_name=model or settings.embedding_model,
        dimension=dimension if dimension is not None else settings.embedding_dimension,
    )
    embedding_engine.fit_bm25(all_texts)

    # 6. Initialize Vector Database (Qdrant primary with Pinecone compatibility)
    state_mgr = IngestionStateManager()
    if hasattr(settings, "has_qdrant") and settings.has_qdrant:
        vector_mgr = QdrantManager(settings)
    elif getattr(settings, "pinecone_api_key", None):
        vector_mgr = PineconeManager(settings)
    else:
        vector_mgr = QdrantManager(settings)

    await vector_mgr.initialize()

    # Ingest Services namespace
    services_ns = vector_mgr.active_namespace("services")
    logger.info("Upserting %d points into vector index (namespace=%s)...", len(all_chunks), services_ns)

    embed_batch = 64
    batch_size = 50

    texts = [chunk["text"] for chunk in all_chunks]
    dense_vectors = []
    sparse_vectors = []
    for i in range(0, len(texts), embed_batch):
        b_texts = texts[i : i + embed_batch]
        d_batch = await embedding_engine.embed_texts(b_texts)
        s_batch = await embedding_engine.embed_sparse(b_texts)
        dense_vectors.extend(d_batch)
        sparse_vectors.extend(s_batch)

    for i, chunk in enumerate(all_chunks):
        chunk["dense_vector"] = dense_vectors[i]
        chunk["sparse_vector"] = sparse_vectors[i]

    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i : i + batch_size]
        try:
            await vector_mgr.upsert_chunks(batch, namespace=services_ns)
        except Exception as upsert_err:
            for c in batch:
                state_mgr.record_failure(c["chunk_id"], c["chunk_id"], str(upsert_err), c["text"], services_ns)

    # Ingest Knowledge namespaces
    knowledge_total = 0
    for ns_base, ns_chunks in knowledge_by_namespace.items():
        target_ns = vector_mgr.active_namespace(ns_base)
        logger.info("Embedding %d knowledge chunks for namespace=%s...", len(ns_chunks), target_ns)
        ns_texts = [chunk["text"] for chunk in ns_chunks]
        ns_dense = []
        ns_sparse = []
        for i in range(0, len(ns_texts), embed_batch):
            b_texts = ns_texts[i : i + embed_batch]
            ns_dense.extend(await embedding_engine.embed_texts(b_texts))
            ns_sparse.extend(await embedding_engine.embed_sparse(b_texts))
        for i, chunk in enumerate(ns_chunks):
            chunk["dense_vector"] = ns_dense[i]
            chunk["sparse_vector"] = ns_sparse[i]

        for i in range(0, len(ns_chunks), batch_size):
            batch = ns_chunks[i : i + batch_size]
            try:
                await vector_mgr.upsert_chunks(batch, namespace=target_ns)
            except Exception as upsert_err:
                for c in batch:
                    state_mgr.record_failure(c["chunk_id"], c["chunk_id"], str(upsert_err), c["text"], target_ns)
        knowledge_total += len(ns_chunks)

    state_mgr.record_success(
        entry_id="services_master",
        content_hash=hashlib.sha256(parsed_data["raw_content"].encode("utf-8")).hexdigest(),
        corpus_version=getattr(settings, "active_corpus_version", "v1"),
    )
    state_mgr.save()

    stats = await vector_mgr.get_collection_stats()
    logger.info("Vector index updated successfully! Stats: %s", stats)

    return {
        "status": "success",
        "lifecycle_phases_indexed": len(parsed_data["lifecycle_phases"]),
        "categories_indexed": len(parsed_data["catalog_categories"]),
        "strategy_sections_indexed": len(parsed_data["strategy_sections"]),
        "total_chunks_indexed": len(all_chunks),
        "knowledge_chunks_indexed": knowledge_total,
        "namespaces": [services_ns] + [vector_mgr.active_namespace(ns) for ns in knowledge_by_namespace.keys()],
        "vector_count": stats.get("points_count", len(all_chunks) + knowledge_total),
        "index_name": getattr(settings, "qdrant_collection_name", getattr(settings, "pinecone_index_name", "cloudgpt-services")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="CloudGPT Services.Md Ingestion CLI")
    parser.add_argument("--dry-run", action="store_true", help="Chunk and print stats without vector DB upserts")
    parser.add_argument("--corpus-version", type=str, default=None, help="Target Pinecone corpus version (e.g. v2)")
    parser.add_argument("--provider", type=str, default=None, help="Embedding provider: gemini | local")
    parser.add_argument("--model", type=str, default=None, help="Embedding model name (e.g. gemini-embedding-2)")
    parser.add_argument("--dimension", type=int, default=None, help="Embedding dimension (e.g. 384 or 768)")
    args = parser.parse_args()

    result = asyncio.run(ingest_services_rag(
        dry_run=args.dry_run,
        corpus_version=args.corpus_version,
        provider=args.provider,
        model=args.model,
        dimension=args.dimension,
    ))
    print("\n" + "=" * 60)
    print("INGESTION OPERATION COMPLETED")
    print("=" * 60)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
