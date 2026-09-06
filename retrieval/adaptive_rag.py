"""Adaptive Advanced RAG Pipeline — Max Tier

Pipeline stages:
  1. Transform Query       — Gemini 2.0 Flash rewrites, expands, and generates HyDE passage
  2. Multi-Query Retrieve  — Parallel fan-out across query variants + HyDE dense vector
  3. Rerank & Compress     — FlashRank + sentence-level context compression
  4. Generate Answer       — Single main LLM pass on compressed high-quality context

All stages fall back gracefully. Internet search runs in parallel with retrieval.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import re
import time
from typing import Any, AsyncGenerator

import structlog

from citations.citation_manager import CitationManager
from chunking.hierarchical_store import HierarchicalChunkStore
from config import get_settings
from core.llm_cache import get_cached_retrieval_result, set_cached_retrieval_result
from llm.provider import get_sub_model_provider
from llm.system_prompts import QUERY_TRANSFORM_PROMPT, ADAPTIVE_REPLAN_PROMPT
from metrics import RAG_STAGE_DURATION_SECONDS, RAG_RESULTS_COUNT, PIPELINE_TIMEOUTS_TOTAL
from retrieval.hybrid import RetrievalResult
from router.query_router import QueryClassification, provider_aliases

logger = structlog.get_logger(__name__)

STOPWORDS = {"a", "an", "the", "is", "are", "of", "in", "for", "to", "and", "or", "with", "on", "at", "by"}
RERANK_COMPRESS_THRESHOLD = 0.6


@dataclass
class AdaptiveQueryRepresentations:
    """Adaptive multi-strategy representations tailored to query complexity and technical nature."""

    strategy: str  # "direct_fast" | "semantic_hyde" | "multi_perspective"
    rewritten_query: str
    expanded_queries: list[str]
    hyde_passage: str
    applied_transformations: list[str]
    perspective_queries: list[dict[str, str]] = field(default_factory=list)
    dense_query: str = ""
    sparse_query: str = ""
    _timing_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "routing_path": self.strategy,
            "strategy": self.strategy,
            "rewritten_query": self.rewritten_query,
            "expanded_queries": self.expanded_queries,
            "hyde_passage": self.hyde_passage,
            "applied_transformations": self.applied_transformations,
            "perspective_queries": self.perspective_queries,
            "dense_query": self.dense_query,
            "sparse_query": self.sparse_query,
            "_timing_ms": self._timing_ms,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.to_dict()


def _stage_event(stage: str, label: str, status: str, elapsed_ms: float | None = None) -> dict:
    ev: dict = {
        "stage": stage,
        "label": label,
        "status": label,
        "stage_status": status,
    }
    if elapsed_ms is not None:
        ev["elapsed_ms"] = round(elapsed_ms, 1)
    return ev


def _validate_scores(scores: Any, expected_len: int) -> list[float]:
    """Defensively validate and clamp LLM-returned relevance scores."""
    if not isinstance(scores, list) or len(scores) != expected_len:
        logger.warning(
            "rag_score_shape_invalid: expected len %d, got %s",
            expected_len,
            type(scores).__name__ if not isinstance(scores, list) else f"list of len {len(scores)}",
        )
        return [1.0] * expected_len  # equal-weight fallback
    validated = []
    for s in scores:
        try:
            val = float(s)
            validated.append(max(0.0, min(1.0, val)))
        except (ValueError, TypeError):
            validated.append(1.0)
    return validated


class AdaptiveAdvancedRAGPipeline:
    """Adaptive Advanced RAG pipeline for Max / Developer-tier users."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def _transform_query(self, query: str, tier: str) -> dict[str, Any]:
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:12]
        t0 = time.perf_counter()
        logger.debug("pipeline.stage_start", stage="transform_query", tier=tier, query_hash=query_hash)

        def _detect_routing_path(q: str) -> str:
            q_strip = q.strip()
            is_cli_cmd = bool(re.match(r"^(aws|az|gcloud|kubectl|terraform)\s+[a-z0-9_-]+", q_strip, re.IGNORECASE))
            has_cli_flag_or_code = bool(re.search(
                r"(--[a-zA-Z0-9_-]+|\b[A-Z][a-zA-Z0-9]+Exception\b|\b[A-Z][a-zA-Z0-9]+Error\b|"
                r"\bError:\s*[A-Z0-9_-]+\b|\b\d{3}\s+(Forbidden|Unauthorized|NotFound)\b|"
                r"\b(status code|exit code)\s+\d+\b)",
                q,
            ))
            if is_cli_cmd or has_cli_flag_or_code:
                return "direct_fast"
            if any(comp in q.lower() for comp in ["compare", " vs ", "versus", "difference between", "trade-off", "migration"]):
                return "multi_perspective"
            return "semantic_hyde"

        routing_path = _detect_routing_path(query)
        selective_enabled = bool(getattr(self.settings, "enable_selective_query_transformation", True))

        identity = AdaptiveQueryRepresentations(
            strategy=routing_path,
            rewritten_query=query,
            expanded_queries=[query],
            hyde_passage=query,
            applied_transformations=["identity"],
            perspective_queries=[],
            dense_query=query,
            sparse_query=query,
        )

        # Apex multi-level stage-aware caching — Stage 1: the transform
        # decision itself is cached keyed by router version, so repeat queries
        # skip the transform LLM entirely.
        _stage_on = bool(getattr(self.settings, "enable_apex_stage_caches", True))
        _router_version = getattr(self.settings, "cache_router_version", "v1")
        if _stage_on:
            try:
                from core.llm_cache import get_cached_decision

                cached_t = await asyncio.wait_for(
                    get_cached_decision("adaptive_transform", query, _router_version, self.settings),
                    timeout=0.5,
                )
                if isinstance(cached_t, dict) and cached_t.get("rewritten_query"):
                    rep = AdaptiveQueryRepresentations(
                        strategy=str(cached_t.get("strategy", routing_path)),
                        rewritten_query=str(cached_t.get("rewritten_query", query)),
                        expanded_queries=list(cached_t.get("expanded_queries", [query]) or [query]),
                        hyde_passage=str(cached_t.get("hyde_passage", query)),
                        applied_transformations=list(cached_t.get("applied_transformations", []) or []),
                        perspective_queries=list(cached_t.get("perspective_queries", []) or []),
                        dense_query=str(cached_t.get("dense_query", query)),
                        sparse_query=str(cached_t.get("sparse_query", query)),
                    )
                    rep._timing_ms = round((time.perf_counter() - t0) * 1000, 2)
                    RAG_STAGE_DURATION_SECONDS.labels(stage="transform_query", tier=tier).observe(
                        time.perf_counter() - t0
                    )
                    logger.info(
                        "pipeline.stage_complete",
                        stage="transform_query",
                        tier=tier,
                        decision_cache="hit",
                        routing_path=rep.strategy,
                    )
                    return rep
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("transform_query.decision_cache_lookup_failed error=%s", e)

        try:
            router_llm = get_sub_model_provider("router", tier)
            budget = float(getattr(self.settings, "adaptive_rag_timeout_seconds", 10.0))
            raw = await asyncio.wait_for(
                router_llm.classify(query, system_prompt=QUERY_TRANSFORM_PROMPT),
                timeout=budget,
            )
            data = json.loads(raw)

            rewritten = data.get("rewritten_query", "").strip() if isinstance(data.get("rewritten_query"), str) else ""
            if not rewritten:
                rewritten = query

            expanded = data.get("expanded_queries", [])
            if not isinstance(expanded, list):
                expanded = [query]
            expanded = [str(e).strip() for e in expanded if str(e).strip()][:3]
            if not expanded:
                expanded = [query]

            hyde = data.get("hyde_passage", "").strip() if isinstance(data.get("hyde_passage"), str) else ""
            if not hyde:
                hyde = query

            applied_trans = []
            perspective_queries: list[dict[str, str]] = []
            if selective_enabled:
                if routing_path == "direct_fast":
                    # Exact syntax/CLI queries: skip hypothetical passage unless explicitly generated
                    if not data.get("hyde_passage") or data.get("hyde_passage") == query:
                        hyde = query
                        applied_trans.append("direct_passthrough")
                    else:
                        applied_trans.extend(["direct_passthrough", "hyde"])
                    if len(expanded) > 1:
                        applied_trans.append("expansion")
                    dense_query = query.strip()
                    sparse_query = query.strip()
                elif routing_path == "semantic_hyde":
                    applied_trans.extend(["rewrite", "hyde"])
                    if len(expanded) > 1:
                        applied_trans.append("expansion")
                    dense_query = f"{rewritten} {hyde}".strip() if hyde != rewritten else rewritten
                    sparse_query = rewritten
                else:  # "multi_perspective"
                    applied_trans.extend(["rewrite", "expansion", "multi_perspective"])
                    p_arch = f"{rewritten} architecture features differences"
                    p_cost = f"{rewritten} pricing cost tiers TCO"
                    p_perf = f"{rewritten} performance scalability SLA high availability"
                    perspective_queries = [
                        {"dimension": "architecture", "query": p_arch},
                        {"dimension": "pricing", "query": p_cost},
                        {"dimension": "performance", "query": p_perf},
                    ]
                    if len(expanded) <= 1:
                        expanded = [p_arch, p_cost, p_perf]
                    dense_query = f"{rewritten} architecture comparison performance pricing".strip()
                    sparse_query = rewritten
            else:
                applied_trans.extend(["rewrite", "expansion", "hyde"])
                dense_query = f"{rewritten} {hyde}".strip()
                sparse_query = rewritten

            transformed = AdaptiveQueryRepresentations(
                strategy=routing_path,
                rewritten_query=rewritten,
                expanded_queries=expanded,
                hyde_passage=hyde,
                applied_transformations=applied_trans,
                perspective_queries=perspective_queries,
                dense_query=dense_query,
                sparse_query=sparse_query,
            )

            # Cache the successful transform decision (identity fallbacks on
            # LLM failure are deliberately not cached).
            if _stage_on:
                try:
                    from core.llm_cache import set_cached_decision

                    await set_cached_decision(
                        "adaptive_transform",
                        query,
                        transformed.to_dict(),
                        _router_version,
                        self.settings,
                        ttl_seconds=int(getattr(self.settings, "core_decision_cache_ttl_seconds", 900)),
                    )
                except Exception as e:
                    logger.debug("transform_query.decision_cache_write_failed error=%s", e)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("transform_query.timeout", tier=tier, query_hash=query_hash)
            PIPELINE_TIMEOUTS_TOTAL.labels(component="adaptive_rag_transform").inc()
            transformed = identity
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning("transform_query.parse_error", error=str(e), tier=tier)
            transformed = identity
        except (ConnectionError, OSError) as e:
            logger.warning("transform_query.network_error", error=str(e), tier=tier)
            transformed = identity
        except Exception as e:
            logger.warning("transform_query.llm_error", error=str(e), tier=tier)
            transformed = identity

        elapsed = time.perf_counter() - t0
        transformed._timing_ms = round(elapsed * 1000, 2)
        RAG_STAGE_DURATION_SECONDS.labels(stage="transform_query", tier=tier).observe(elapsed)
        logger.info(
            "pipeline.stage_complete",
            stage="transform_query",
            tier=tier,
            duration_ms=transformed._timing_ms,
            routing_path=transformed.get("routing_path", "semantic_hyde"),
            transformations=transformed.get("applied_transformations", []),
            expanded_count=len(transformed["expanded_queries"]),
        )
        return transformed

    async def _multi_query_retrieve(
        self,
        original_query: str,
        transformed: dict[str, Any] | AdaptiveQueryRepresentations,
        provider_filter_dict: dict[str, Any] | None,
        classification: QueryClassification,
        tier: str,
        retriever: Any,
        reranker: Any,
        cache_decision: Any | None = None,
    ) -> tuple[list[RetrievalResult], str]:
        t0 = time.perf_counter()
        prov_key = provider_filter_dict.get("provider") if provider_filter_dict else "all"
        # Retrieval cache key includes the corpus version — re-ingesting the
        # corpus invalidates these entries (matches the rag:v2 schema).
        _corpus_version = getattr(self.settings, "cache_corpus_version", "v1")
        cache_query_key = f"adaptive:{_corpus_version}:{hashlib.sha256(original_query.encode()).hexdigest()[:16]}:{prov_key}:{tier}"

        _check_cache = cache_decision is None or getattr(cache_decision, "retrieval_cache", True)
        if _check_cache:
            try:
                cached = await get_cached_retrieval_result(cache_query_key, prov_key)
                if cached:
                    elapsed = time.perf_counter() - t0
                    RAG_STAGE_DURATION_SECONDS.labels(stage="adaptive_retrieve", tier=tier).observe(elapsed)
                    cached_objs = [
                        RetrievalResult(
                            chunk_id=str(r.get("chunk_id", "")),
                            text=str(r.get("text", r.get("content", ""))),
                            score=float(r.get("score", 1.0)),
                            metadata=dict(r.get("metadata", {}) or {}),
                        ) if isinstance(r, dict) else r
                        for r in cached
                    ]
                    return cached_objs, "cached"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("adaptive_retrieve.cache_check_error", error=str(e))

        query_strings = list(
            dict.fromkeys([transformed["rewritten_query"]] + transformed["expanded_queries"])
        )[:4]

        def _compute_adaptive_fusion_weights(q: str, rewritten: str) -> tuple[float, float]:
            """Compute dynamic query-adaptive fusion weights alpha(q) for Quake dense vs BM25 sparse."""
            strategy = transformed.get("routing_path", "")
            if strategy == "direct_fast":
                return 0.3, 0.7
            if strategy == "multi_perspective":
                return 0.55, 0.45
            if strategy == "semantic_hyde":
                return 0.7, 0.3

            combined = f"{q} {rewritten}".lower()
            is_exact = bool(re.search(r"(--[a-zA-Z0-9_-]+|\b[A-Z][a-zA-Z0-9]+Exception\b|\bError:\b|\b\d{3}\s+Forbidden\b|\b\d+\.\d+\.\d+\b)", combined))
            has_cli = any(cmd in combined for cmd in ["aws ", "az ", "gcloud ", "kubectl ", "terraform "])
            is_abstract = any(word in combined for word in ["architecture", "compare", "trade-off", "design", "overview", "pattern", "best practice"])

            if is_exact or has_cli:
                w_dense = 0.3
            elif is_abstract:
                w_dense = 0.7
            else:
                w_dense = 0.55
            return w_dense, 1.0 - w_dense

        w_dense, w_sparse = _compute_adaptive_fusion_weights(original_query, transformed.get("rewritten_query", ""))

        tasks = [
            retriever.retrieve(
                q,
                top_k=self.settings.retrieval_top_k,
                filters=provider_filter_dict,
                expand_to_parents=True,
            )
            for q in query_strings
        ]

        from api.chat_routes import pipeline as agent_pipeline

        async def _hyde_search() -> list[RetrievalResult]:
            # Direct/fast syntax queries bypass hypothetical hallucinated passages
            if transformed.get("routing_path") == "direct_fast" and "hyde" not in transformed.get("applied_transformations", []):
                return []
            try:
                if not getattr(agent_pipeline, "embedding_engine", None):
                    return []

                # 1. Check if agent_pipeline has a configured QuakeRetriever
                quake_ret = getattr(agent_pipeline, "quake_retriever", None)
                if quake_ret:
                    res = await quake_ret.retrieve(
                        query=transformed["hyde_passage"],
                        top_k=max(1, self.settings.retrieval_top_k // 2),
                        filters=provider_filter_dict,
                    )
                    if res:
                        return res

                # 2. Fallback to pinecone_manager
                if not getattr(agent_pipeline, "pinecone_manager", None):
                    return []
                hyde_vec = await agent_pipeline.embedding_engine.embed_query(
                    transformed["hyde_passage"]
                )
                ns = agent_pipeline.pinecone_manager.active_namespace("services")
                results = await agent_pipeline.pinecone_manager.search_dense(
                    query_vector=hyde_vec,
                    filter_conditions=provider_filter_dict,
                    limit=max(1, self.settings.retrieval_top_k // 2),
                    namespace=ns,
                )
                store = HierarchicalChunkStore.get_instance()
                hyde_results = []
                for r in results:
                    cid = str(r.get("id", ""))
                    meta = {k: v for k, v in r.get("metadata", {}).items() if k != "text"}
                    text = r.get("metadata", {}).get("text", "")
                    parent = store.get_parent_chunk(cid)
                    if parent:
                        meta["parent_chunk_id"] = parent.parent_chunk_id
                        meta["hierarchy_level"] = parent.hierarchy_level
                        meta["is_coalesced_parent"] = True
                        text = parent.content
                    hyde_results.append(
                        RetrievalResult(
                            chunk_id=cid,
                            text=text,
                            score=max(0.0, min(1.0, float(r.get("score", 0.0)) * 0.8)),
                            metadata=meta,
                        )
                    )
                return hyde_results
            except asyncio.CancelledError:
                raise
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("adaptive_rag.hyde_timeout")
                return []
            except (ConnectionError, OSError) as e:
                logger.warning("adaptive_rag.hyde_network_error", error=str(e))
                return []
            except Exception as e:
                logger.warning("adaptive_rag.hyde_search_failed", error=str(e))
                return []

        try:
            all_results = await asyncio.wait_for(
                asyncio.gather(*tasks, _hyde_search(), return_exceptions=True),
                timeout=float(getattr(self.settings, "retrieval_timeout_seconds", 8.0)),
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("adaptive_rag.multi_query_timeout")
            PIPELINE_TIMEOUTS_TOTAL.labels(component="adaptive_rag_multi_query").inc()
            all_results = []

        merged: dict[str, RetrievalResult] = {}
        scores: dict[str, float] = {}
        k_rrf = 60

        for b_idx, batch in enumerate(all_results):
            if isinstance(batch, (Exception, BaseException)) or not batch:
                continue
            clean_batch = [r for r in batch if isinstance(r, RetrievalResult)]
            if not clean_batch:
                continue
            sorted_batch = sorted(clean_batch, key=lambda r: r.score, reverse=True)
            # Apply adaptive fusion weight: HyDE batch (last) gets dense weight, query batches get blended weight
            b_weight = w_dense if b_idx == len(all_results) - 1 else 1.0
            for rank, res in enumerate(sorted_batch):
                if res.chunk_id not in scores:
                    scores[res.chunk_id] = 0.0
                    merged[res.chunk_id] = res
                scores[res.chunk_id] += b_weight * (1.0 / (k_rrf + rank + 1))

        for cid in merged:
            merged[cid].score = scores[cid]

        # URL-level deduplication
        url_seen: dict[str, bool] = {}
        deduped: list[RetrievalResult] = []
        for r in sorted(merged.values(), key=lambda x: x.score, reverse=True):
            url = (r.metadata.get("url") or "").strip()
            if url and url in url_seen:
                continue
            if url:
                url_seen[url] = True
            deduped.append(r)

        candidates = deduped[:self.settings.retrieval_top_k]

        if getattr(self.settings, "enable_context_validator", True) and candidates:
            from core.context_validator import ContextValidator
            cv = ContextValidator(self.settings)
            vr = cv.validate_retrieved_chunks(candidates, tier=tier)
            for chunk in candidates:
                if chunk.chunk_id in vr.staleness_flags:
                    chunk.metadata["stale"] = True
            if not vr.is_valid:
                logger.warning("context_validator.adaptive_chunks_rejected", tier=tier)
                candidates = []

        if _check_cache:
            try:
                candidates_dicts = [
                    {
                        "chunk_id": c.chunk_id,
                        "text": c.text,
                        "score": c.score,
                        "metadata": c.metadata,
                    }
                    for c in candidates
                ]
                await set_cached_retrieval_result(
                    cache_query_key, candidates_dicts, prov_key, self.settings, ttl_seconds=21600
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("adaptive_retrieve.cache_set_error", error=str(e))

        elapsed = time.perf_counter() - t0
        RAG_STAGE_DURATION_SECONDS.labels(stage="adaptive_retrieve", tier=tier).observe(elapsed)
        logger.info(
            "pipeline.stage_complete",
            stage="adaptive_retrieve",
            tier=tier,
            duration_ms=round(elapsed * 1000, 2),
            candidate_count=len(candidates),
        )
        return candidates, "adaptive_multi_query"

    def _evaluate_retrieval_feedback(
        self,
        candidates: list[RetrievalResult],
        query: str,
        transformed: dict[str, Any] | AdaptiveQueryRepresentations,
    ) -> tuple[bool, float, str]:
        """Evaluate post-retrieval feedback metrics to determine if re-planning is required."""
        if not candidates:
            return True, 0.0, "zero_candidates_retrieved"

        top_score = max((float(c.score) for c in candidates), default=0.0)
        thresh = float(getattr(self.settings, "adaptive_replan_feedback_threshold", 0.40))

        if top_score < thresh:
            return True, top_score, f"low_relevance_score ({top_score:.3f} < {thresh:.3f})"

        if len(candidates) >= 3:
            spread = float(candidates[0].score) - float(candidates[-1].score)
            if spread < 0.015 and top_score < 0.50:
                return True, top_score, "flat_indistinguishable_candidates"

        # Check technical keyword coverage on top candidates
        q_tokens = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", query) if w.lower() not in STOPWORDS]
        if q_tokens:
            top_texts = " ".join((c.text or "").lower() for c in candidates[:3])
            matched = sum(1 for tok in q_tokens if tok in top_texts)
            coverage = matched / len(q_tokens)
            if coverage < 0.35 and top_score < 0.65:
                return True, top_score, f"keyword_coverage_gap ({coverage:.2f} < 0.35)"

        return False, top_score, "sufficient_retrieval"

    async def _replan_retrieval(
        self,
        query: str,
        transformed: dict[str, Any] | AdaptiveQueryRepresentations,
        feedback_diagnosis: str,
        tier: str,
        retriever: Any,
        reranker: Any,
    ) -> tuple[list[RetrievalResult], str]:
        """Diagnose retrieval failure mode and execute an adaptive re-planned search pass."""
        logger.info(
            "adaptive_rag.replan_retrieval_start",
            diagnosis=feedback_diagnosis,
            tier=tier,
        )
        try:
            router_llm = get_sub_model_provider("router", tier)
            prompt = (
                f"ORIGINAL QUERY: {query}\n"
                f"INITIAL SEARCH: {transformed.get('rewritten_query', query)}\n"
                f"RETRIEVAL FEEDBACK DIAGNOSIS: {feedback_diagnosis}\n"
                f"TASK: Formulate an adjusted, broader search query expanding technical synonyms."
            )
            raw = await asyncio.wait_for(
                router_llm.classify(prompt, system_prompt=ADAPTIVE_REPLAN_PROMPT),
                timeout=5.0,
            )
            data = json.loads(raw)
            replanned_query = data.get("replanned_query", "").strip() or query
        except Exception as e:
            logger.warning("adaptive_rag.replan_llm_error", error=str(e))
            if "zero_candidates" in feedback_diagnosis:
                # Query relaxation: strip punctuation, flags, relax constraints
                relaxed = re.sub(r"(--[a-zA-Z0-9_-]+|\b[A-Z0-9_-]+=[^\s]+)", "", query).strip()
                replanned_query = f"{relaxed} overview documentation" if relaxed else f"{query} overview"
            elif "keyword_coverage" in feedback_diagnosis:
                replanned_query = f"{query} technical architecture reference concepts"
            else:
                replanned_query = f"{query} overview architecture concepts configuration"

        try:
            new_candidates = await retriever.retrieve(
                replanned_query,
                top_k=self.settings.retrieval_top_k,
                expand_to_parents=True,
            )
            if new_candidates and reranker:
                reranked = await asyncio.to_thread(
                    reranker.rerank, query, new_candidates, self.settings.rerank_top_k
                )
                return reranked, "adaptive_replanned_reranked"
            return new_candidates[:self.settings.rerank_top_k], "adaptive_replanned"
        except Exception as e:
            logger.warning("adaptive_rag.replan_execution_error", error=str(e))
            return [], "replan_failed"

    # Adaptive compression thresholds per routing strategy: exact-syntax
    # queries tolerate aggressive extraction (commands must survive), while
    # multi-perspective comparisons need richer per-dimension context.
    _STRATEGY_COMPRESS_THRESHOLD = {
        "direct_fast": 0.5,
        "semantic_hyde": RERANK_COMPRESS_THRESHOLD,
        "multi_perspective": 0.65,
    }

    # Per-strategy evidence caps for adaptive selection after reranking.
    _STRATEGY_EVIDENCE_CAP = {
        "direct_fast": 6,
        "semantic_hyde": 8,
        "multi_perspective": 10,
    }

    def _adaptive_select(self, candidates: list[RetrievalResult], strategy: str | None) -> list[RetrievalResult]:
        """Adaptive evidence selection: per-strategy cap + provider-balanced
        round-robin for multi-perspective comparisons."""
        if not candidates:
            return candidates
        if strategy == "multi_perspective":
            buckets: dict[str, list[RetrievalResult]] = {}
            order: list[str] = []
            for c in candidates:
                prov = str(c.metadata.get("provider", "cloud")).lower()
                if prov not in buckets:
                    buckets[prov] = []
                    order.append(prov)
                buckets[prov].append(c)
            balanced: list[RetrievalResult] = []
            max_len = max((len(b) for b in buckets.values()), default=0)
            for i in range(max_len):
                for prov in order:
                    if i < len(buckets[prov]):
                        balanced.append(buckets[prov][i])
            cap = self._STRATEGY_EVIDENCE_CAP.get(strategy, self.settings.rerank_top_k)
            return balanced[:cap]
        cap = self._STRATEGY_EVIDENCE_CAP.get(strategy, self.settings.rerank_top_k)
        return candidates[:cap]

    def _compress_chunk(self, query: str, chunk: RetrievalResult, strategy: str | None = None) -> RetrievalResult:
        """Adaptive Hierarchical compression: extract query-relevant context or preserve tables/parents.

        The compression trigger threshold adapts to the routing strategy.
        """
        threshold = self._STRATEGY_COMPRESS_THRESHOLD.get(strategy, RERANK_COMPRESS_THRESHOLD)
        if chunk.score < threshold:
            chunk.metadata["compressed"] = False
            return chunk

        original_text = chunk.text
        original_len = len(original_text)

        # 1. Table preservation: Never compress markdown tables
        if chunk.metadata.get("is_table") or ("|" in original_text and "---" in original_text):
            chunk.metadata["compressed"] = False
            chunk.metadata["table_preserved"] = True
            return chunk

        # 2. Coalesced Parent Chunks: If parent contains broad section headers or architecture, retain
        if chunk.metadata.get("is_coalesced_parent") and chunk.score >= 0.8:
            chunk.metadata["compressed"] = False
            chunk.metadata["parent_preserved"] = True
            return chunk

        query_tokens = {
            t.lower() for t in query.split()
            if t.lower() not in STOPWORDS and len(t) > 2
        }

        sentences = re.split(r'(?<=[.?!])\s+|\n{2,}', original_text.strip())
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            chunk.metadata["compressed"] = False
            return chunk

        def sentence_score(s: str) -> int:
            tokens = {t.lower() for t in s.split() if t.lower() not in STOPWORDS}
            return len(tokens & query_tokens)

        kept = []
        for i, sentence in enumerate(sentences):
            if i < 2 or sentence_score(sentence) > 0:
                kept.append(sentence)

        rebuilt = " ".join(kept)

        if len(rebuilt) < 50:
            chunk.metadata["compressed"] = False
            return chunk

        chunk.text = rebuilt
        chunk.metadata["compressed"] = True
        chunk.metadata["original_length"] = original_len
        chunk.metadata["compressed_length"] = len(rebuilt)
        return chunk

    async def _rerank_and_compress(
        self,
        query: str,
        candidates: list[RetrievalResult],
        tier: str,
        reranker: Any,
        transformed: dict[str, Any] | AdaptiveQueryRepresentations | None = None,
        cache_decision: Any | None = None,
    ) -> list[RetrievalResult]:
        """Stage 3: Adaptive Reranking & Selective Context Compression.

        - Rerank query adapts to the routing strategy: exact-syntax queries are
          reranked with the sparse-preserving representation, conceptual queries
          with the rewritten representation.
        - multi_perspective queries are reranked per dimension and merged
          round-robin so no comparison dimension dominates the context.
        - Evidence selection applies per-strategy caps and provider balance.
        - Compression thresholds adapt per strategy.
        """
        t0 = time.perf_counter()

        # Apex multi-level stage-aware caching — Stage 3: the rerank+compress
        # output is cached keyed by (strategy, query, candidate id set), so
        # near-identical retrieval sets skip the cross-encoder entirely.
        strategy = None
        if transformed:
            try:
                strategy = transformed.get("routing_path") or transformed.get("strategy")
            except Exception:
                strategy = None
        stage_key: str | None = None
        _stage_on = bool(
            getattr(self.settings, "enable_apex_stage_caches", True)
            and getattr(self.settings, "enable_adaptive_cache_router", True)
            and (cache_decision is None or getattr(cache_decision, "stage_caches", True))
        )
        if _stage_on and candidates:
            try:
                from core.llm_cache import get_cached_stage

                _cv = getattr(self.settings, "cache_corpus_version", "v1")
                ids_digest = hashlib.sha256(
                    ",".join(sorted(c.chunk_id for c in candidates)).encode()
                ).hexdigest()[:16]
                q_digest = hashlib.sha256(f"{strategy}:{query}".encode()).hexdigest()[:16]
                stage_key = f"rag:v2:stage:rerank:{_cv}:{q_digest}:{ids_digest}"
                cached_stage = await get_cached_stage(stage_key, self.settings)
                if isinstance(cached_stage, list) and cached_stage:
                    restored = [
                        RetrievalResult(
                            chunk_id=str(r.get("chunk_id", "")),
                            text=str(r.get("text", "")),
                            score=float(r.get("score", 0.0)),
                            metadata=dict(r.get("metadata", {}) or {}),
                        )
                        for r in cached_stage
                        if isinstance(r, dict) and r.get("chunk_id")
                    ]
                    if restored:
                        from metrics import CACHE_CASCADE_HITS

                        CACHE_CASCADE_HITS.labels(tier=tier, layer="stage").inc()
                        logger.info(
                            "stage_cache.rerank_hit",
                            tier=tier,
                            strategy=strategy,
                            count=len(restored),
                        )
                        return restored
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("stage_cache.rerank_lookup_failed error=%s", e)
                stage_key = None

        adaptive_enabled = bool(getattr(self.settings, "enable_adaptive_reranking", True) and strategy)

        if not candidates:
            reranked: list[RetrievalResult] = []
        elif adaptive_enabled and strategy == "multi_perspective" and reranker:
            dimension_queries: list[str] = [
                str(p.get("query", "")).strip()
                for p in (transformed.get("perspective_queries") or [])
                if isinstance(p, dict) and p.get("query")
            ] or [query]
            per_dim_batches: list[list[RetrievalResult]] = []
            for dim_query in dimension_queries:
                try:
                    batch = await asyncio.to_thread(
                        reranker.rerank, dim_query, candidates, self.settings.rerank_top_k
                    )
                except Exception as e:
                    logger.warning("adaptive_rerank.dimension_failed", error=str(e))
                    batch = []
                if batch:
                    per_dim_batches.append(batch)
            if per_dim_batches:
                merged: dict[str, RetrievalResult] = {}
                max_len = max(len(b) for b in per_dim_batches)
                for i in range(max_len):
                    for batch in per_dim_batches:
                        if i < len(batch) and batch[i].chunk_id not in merged:
                            merged[batch[i].chunk_id] = batch[i]
                reranked = list(merged.values())
            else:
                reranked = candidates[: self.settings.rerank_top_k]
        else:
            rerank_query = query
            if adaptive_enabled and strategy == "direct_fast":
                rerank_query = str(transformed.get("sparse_query") or "").strip() or query
            elif adaptive_enabled and strategy == "semantic_hyde":
                rerank_query = str(transformed.get("rewritten_query") or "").strip() or query
            if reranker:
                reranked = await asyncio.to_thread(
                    reranker.rerank, rerank_query, candidates, self.settings.rerank_top_k
                )
            else:
                reranked = candidates[: self.settings.rerank_top_k]

        if getattr(self.settings, "enable_adaptive_evidence_selection", True):
            reranked = self._adaptive_select(reranked, strategy)

        compressed = [
            self._compress_chunk(query, chunk, strategy=strategy) for chunk in reranked
        ]

        # Apex multi-level stage-aware caching: write compressed output to stage cache
        if _stage_on and stage_key and compressed:
            try:
                from core.llm_cache import set_cached_stage

                to_cache = [
                    {
                        "chunk_id": c.chunk_id,
                        "text": c.text,
                        "score": c.score,
                        "metadata": c.metadata,
                    }
                    for c in compressed
                ]
                stage_ttl = int(getattr(self.settings, "apex_stage_cache_ttl_seconds", 1800))
                await set_cached_stage(stage_key, to_cache, ttl_seconds=stage_ttl, settings=self.settings)
            except Exception as e:
                logger.debug("stage_cache.rerank_write_failed error=%s", e)

        elapsed = time.perf_counter() - t0
        RAG_STAGE_DURATION_SECONDS.labels(stage="rerank_compress", tier=tier).observe(elapsed)
        logger.info(
            "pipeline.stage_complete",
            stage="rerank_compress",
            tier=tier,
            duration_ms=round(elapsed * 1000, 2),
            strategy=strategy or "legacy",
            compressed_count=sum(1 for c in compressed if c.metadata.get("compressed")),
            total_count=len(compressed),
        )
        return compressed

    async def _validate_and_repair(
        self,
        query: str,
        final_answer: str,
        evidence_chunks: list[RetrievalResult],
        citation_mgr: Any | None,
        tier: str,
        thinking_level: str | None,
        timings: dict[str, float],
        emit_event: Any | None,
    ) -> tuple[str, dict[str, Any]]:
        """Apex Layer 4 — adaptive claim-level verification with retry/abstain policy.

        Deterministic claim entailment first; ambiguous grounding triggers a
        batched evaluator-model claim re-check. Persistently unsupported
        answers are regenerated with validation feedback up to
        ``adaptive_generation_max_retries``; if grounding still falls below
        ``adaptive_abstain_min_support``, the pipeline abstains with an honest
        knowledge-boundary response instead of returning unverified specifics.
        """
        from generation.validator import (
            append_caveat,
            build_abstention_answer,
            build_retry_messages,
            deterministic_cleanup,
            policies_from_settings,
            resolve_claim_verification,
        )
        from metrics import GENERATION_ABSTENTIONS_TOTAL, GENERATION_RETRIES_TOTAL

        if not getattr(self.settings, "enable_adaptive_output_validation", True):
            return final_answer, {}

        t0_val = time.perf_counter()
        policy = policies_from_settings(tier, self.settings)
        chunk_to_source = citation_mgr.source_number_for_chunk if citation_mgr is not None else None

        try:
            report = await resolve_claim_verification(
                final_answer, query, evidence_chunks, citation_mgr, policy, self.settings, chunk_to_source
            )
        except Exception as e:
            logger.warning("adaptive_output_validation.failed", error=str(e))
            return final_answer, {}

        retries_used = 0
        while report.retryable and retries_used < policy.max_retries:
            retries_used += 1
            GENERATION_RETRIES_TOTAL.labels(tier=policy.tier).inc()
            if emit_event and getattr(self.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event(
                    "validate",
                    f"Adaptive validation flagged defects — regenerating (attempt {retries_used})...",
                    "start",
                ))
            try:
                from api.chat_routes import generate_with_fallback

                retry_messages = build_retry_messages(query, final_answer, report, evidence_chunks)
                retry_answer, _ = await asyncio.wait_for(
                    generate_with_fallback(
                        messages=retry_messages, stream=False, tier=tier, thinking_level=thinking_level
                    ),
                    timeout=float(getattr(self.settings, "claim_verification_timeout_seconds", 8.0)) * 3,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("adaptive_validation_retry.failed", error=str(e))
                break
            if not retry_answer or not retry_answer.strip():
                break

            retry_answer = deterministic_cleanup(retry_answer)
            try:
                retry_report = await resolve_claim_verification(
                    retry_answer, query, evidence_chunks, citation_mgr, policy, self.settings, chunk_to_source
                )
            except Exception as e:
                logger.warning("adaptive_validation_retry.revalidate_failed", error=str(e))
                break
            if retry_report.is_valid or len(retry_report.issues) < len(report.issues):
                final_answer = retry_answer
                report = retry_report
            if report.is_valid:
                break

        abstained = False
        if report.abstain_recommended:
            # Policy-based abstention: validated grounding could not be achieved.
            GENERATION_ABSTENTIONS_TOTAL.labels(tier=policy.tier).inc()
            final_answer = build_abstention_answer(query, report)
            abstained = True
            logger.warning(
                "adaptive_rag.abstained",
                claim_support=report.claim_support,
                abstain_floor=policy.abstain_min_support,
                retries_used=retries_used,
                tier=tier,
            )
        elif not report.is_valid and not report.dimensions["grounding"].passed:
            final_answer = append_caveat(final_answer, report)

        report_dict = report.to_dict()
        report_dict["retries_used"] = retries_used
        report_dict["abstained"] = abstained
        timings["validate"] = round((time.perf_counter() - t0_val) * 1000, 2)
        if emit_event and getattr(self.settings, "enable_structured_stage_events", True):
            await emit_event(_stage_event(
                "validate",
                "Adaptive validation passed" if report.is_valid and not abstained
                else ("Abstained — verified answer unavailable" if abstained else "Validated with caveats"),
                "complete",
                elapsed_ms=timings["validate"],
            ))
        logger.info(
            "pipeline.stage_complete",
            stage="validate",
            tier=tier,
            is_valid=report.is_valid,
            claim_support=report.claim_support,
            retries_used=retries_used,
            abstained=abstained,
        )
        return final_answer, report_dict

    async def _generate(
        self,
        query: str,
        compressed_chunks: list[RetrievalResult],
        classification_obj: QueryClassification,
        internet_results: list[dict[str, Any]],
        provider_filter: str | None,
        chat_history: list[dict] | None,
        attachment_texts: list[dict] | None,
        tier: str,
        thinking_level: str | None,
        stream: bool,
        timings: dict[str, float],
        emit_event: Any | None,
        session_summary: str | None = None,
        user_memories: list[dict] | None = None,
        pricing_data: list[dict[str, Any]] | None = None,
        calc_results: Any | None = None,
        transformed: dict[str, Any] | AdaptiveQueryRepresentations | None = None,
        live_verified: bool = False,
        citation_mgr: Any | None = None,
        validation_out: dict[str, Any] | None = None,
    ) -> tuple[str | AsyncGenerator[str, None], str]:
        from api.chat_routes import _build_pipeline_messages, generate_with_fallback
        from llm.provider import calculate_effective_prompt_budget

        # Apex Layer 3 — dynamic context assembly: strategy-ordered evidence
        # and a dynamic budget multiplier for the tier context budget.
        strategy = None
        complexity_score = 0.5
        rag_results: list[dict[str, Any]] = []
        evidence_stats: dict[str, Any] = {}
        if transformed is not None and getattr(self.settings, "enable_dynamic_policy_prompt", True):
            try:
                from generation.assembly import assemble_dynamic_evidence

                strategy = transformed.get("routing_path") or transformed.get("strategy")
                if strategy == "multi_perspective":
                    complexity_score = 0.65
                elif strategy == "direct_fast":
                    complexity_score = 0.35
                rag_results, evidence_stats = assemble_dynamic_evidence(
                    compressed_chunks, strategy, complexity_score
                )
            except Exception as e:
                logger.warning("dynamic_assembly.failed", error=str(e))
                rag_results, evidence_stats = [], {}
        if not rag_results:
            rag_results = [
                {
                    "chunk_id": c.chunk_id,
                    "provider": c.metadata.get("provider", "cloud"),
                    "service": c.metadata.get("service", ""),
                    "section": c.metadata.get("section", ""),
                    "url": c.metadata.get("url", ""),
                    "content": c.text,
                    "title": c.metadata.get("title", ""),
                    "parent_chunk_id": c.metadata.get("parent_chunk_id"),
                    "hierarchy_level": c.metadata.get("hierarchy_level", 1),
                    "is_coalesced_parent": c.metadata.get("is_coalesced_parent", False),
                }
                for c in compressed_chunks
            ]

        tier_context_budget = {
            "Free": getattr(self.settings, "prompt_budget_free", self.settings.context_tokens_free),
            "Pro": getattr(self.settings, "prompt_budget_pro", self.settings.context_tokens_pro),
            "Max": getattr(self.settings, "prompt_budget_max", self.settings.context_tokens_max),
        }.get(tier, self.settings.context_tokens_max) if getattr(self.settings, "enable_context_budget", True) else None

        if tier_context_budget:
            # Dynamic budget: strategy-aware scaling before the model cap calc.
            tier_context_budget = int(tier_context_budget * float(evidence_stats.get("budget_multiplier", 1.0)))
            tier_context_budget = calculate_effective_prompt_budget(
                tier_budget=tier_context_budget,
                model_name=None,
                max_output_tokens=getattr(self.settings, "chat_max_output_tokens", 4096),
            )

        # Apex Layer 4 — dynamic policy-aware prompt.
        policy_digest: str | None = None
        if transformed is not None and getattr(self.settings, "enable_dynamic_policy_prompt", True):
            try:
                from generation.policy import build_policy_digest

                policy_digest = build_policy_digest(
                    strategy=strategy,
                    complexity_score=complexity_score,
                    confidence=float(getattr(classification_obj, "confidence", 0.5)),
                    evidence_stats=evidence_stats,
                    live_verified=live_verified,
                    tier=tier,
                )
            except Exception as e:
                logger.warning("policy_digest.failed", error=str(e))

        messages = _build_pipeline_messages(
            query=query,
            classification=classification_obj,
            rag_results=rag_results,
            web_results=[],
            internet_results=internet_results,
            pricing_data=pricing_data or [],
            calc_results=calc_results,
            provider_filter=provider_filter,
            chat_history=chat_history,
            attachment_texts=attachment_texts,
            max_context_tokens=tier_context_budget,
            session_summary=session_summary,
            user_memories=user_memories,
            tier=tier,
            policy_digest=policy_digest,
        )

        if emit_event:
            if getattr(self.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("generate", "Synthesizing answer with AI...", "start"))
            else:
                await emit_event({"status": "Synthesizing answer with AI..."})

        t0 = time.perf_counter()
        result = await generate_with_fallback(
            messages=messages, stream=stream, tier=tier, thinking_level=thinking_level
        )
        gen_elapsed = time.perf_counter() - t0
        timings["generate"] = round(gen_elapsed * 1000, 2)
        RAG_STAGE_DURATION_SECONDS.labels(stage="adaptive_generate", tier=tier).observe(gen_elapsed)

        if stream:
            # Streaming responses stream directly to the SSE client; the
            # adaptive validation loop runs on the non-streaming path.
            return result

        # Apex Layer 4 — adaptive claim verification + policy-based retry/abstain.
        try:
            answer_text = result[0] if isinstance(result, tuple) else result
            validated_answer, report_dict = await self._validate_and_repair(
                query=query,
                final_answer=str(answer_text or ""),
                evidence_chunks=compressed_chunks,
                citation_mgr=citation_mgr,
                tier=tier,
                thinking_level=thinking_level,
                timings=timings,
                emit_event=emit_event,
            )
            if validation_out is not None:
                validation_out.update(report_dict)
            return (validated_answer, result[1]) if isinstance(result, tuple) else (validated_answer, "gemini")
        except Exception as e:
            logger.warning("adaptive_validation.wiring_error", error=str(e))
            return result

    async def _live_verify(
        self,
        query: str,
        providers: list[str],
        tier: str,
        emit_event: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Synchronous Live Verify escalation — fires agent tool call against provider docs.

        Reuses WebSearchTool with provider-scoped domain lists. Returns web results in the
        same dict shape as internet_results so they slot directly into _build_pipeline_messages.
        """
        from api.chat_routes import pipeline as agent_pipeline

        if emit_event and getattr(self.settings, "enable_structured_stage_events", True):
            await emit_event(_stage_event("live_verify", "Verifying against live provider docs...", "start"))

        t0 = time.perf_counter()
        all_results: list[dict[str, Any]] = []
        domain_map: dict = getattr(self.settings, "live_verify_provider_domains", {})
        max_results: int = getattr(self.settings, "live_verify_max_results", 3)

        # Default to all three providers if none detected
        target_providers = providers if providers else ["aws", "gcp", "azure"]

        for provider in target_providers:
            domains = domain_map.get(provider.lower(), [])
            search_query = f"{provider.upper()} {query} latest documentation"
            try:
                results = await asyncio.wait_for(
                    agent_pipeline.web_search.search(
                        query=search_query,
                        max_results=max_results,
                        domains=domains,
                    ),
                    timeout=float(getattr(self.settings, "budget_web_search_ms", 2000)) / 1000.0,
                )
                for r in results:
                    all_results.append({
                        "title": r.title,
                        "url": r.url,
                        "content": r.content,
                        "source_engine": r.source_engine,
                        "live_verified": True,
                        "provider": provider,
                    })
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                logger.warning("live_verify.timeout", provider=provider, tier=tier)
            except (ConnectionError, OSError) as e:
                logger.warning("live_verify.network_error", provider=provider, error=str(e))
            except Exception as e:
                logger.warning("live_verify.error", provider=provider, error=str(e))

        elapsed = time.perf_counter() - t0
        RAG_STAGE_DURATION_SECONDS.labels(stage="live_verify", tier=tier).observe(elapsed)
        logger.info(
            "pipeline.stage_complete",
            stage="live_verify",
            tier=tier,
            duration_ms=round(elapsed * 1000, 2),
            results_count=len(all_results),
            providers=target_providers,
        )

        if emit_event and getattr(self.settings, "enable_structured_stage_events", True):
            await emit_event(_stage_event(
                "live_verify", "Live verification complete", "complete",
                elapsed_ms=round(elapsed * 1000, 2)
            ))

        return all_results

    def _serve_cached_answer(
        self,
        cached: dict[str, Any],
        query: str,
        tier: str,
        stream: bool,
        emit_event: Any | None,
        pipeline_type: str,
        timings: dict[str, float],
    ) -> Any:
        """Serve an answer-cache hit (exact or semantic) as a PipelineResult."""
        from api.chat_routes import PipelineResult

        ans_text = cached.get("answer", "")
        ans_sources = cached.get("sources", [])
        ans_model = cached.get("model", "cache")

        if stream and emit_event:
            pass  # stage events already emitted by the caller's flow

        def _token_stream() -> AsyncGenerator[str, None]:
            async def _gen() -> AsyncGenerator[str, None]:
                chunk_size = 25
                for i in range(0, len(ans_text), chunk_size):
                    yield ans_text[i:i + chunk_size]
                    await asyncio.sleep(0)
            return _gen()

        timings.setdefault("cache_route", 0.0)
        return PipelineResult(
            answer=ans_text,
            token_stream=_token_stream() if stream else None,
            routes=["RAG"],
            confidence=0.98,
            classification={"intent": "cached", "cache_layer": pipeline_type},
            sources=ans_sources,
            model_used=ans_model,
            pipeline_timings=dict(timings),
            fallback_pass="cache_hit",
            pipeline_type=pipeline_type,
            validation={
                "cached": True,
                "passed": True,
                "dimensions": {
                    "grounding": {"passed": True, "score": 1.0, "details": "served_from_cache"},
                },
            },
        )

    async def run(
        self,
        query: str,
        provider_filter: str | None = None,
        tier: str = "Max",
        chat_history: list[dict] | None = None,
        attachment_texts: list[dict] | None = None,
        stream: bool = False,
        thinking_level: str | None = None,
        emit_event: Any | None = None,
        session_summary: str | None = None,
        user_memories: list[dict] | None = None,
        **kwargs: Any,
    ) -> Any:
        from api.chat_routes import PipelineResult, pipeline as agent_pipeline
        from router.query_router import QueryRouter
        from llm.thinking import default_thinking_for_tier

        if thinking_level is None:
            thinking_level = default_thinking_for_tier(tier)

        citation_mgr = CitationManager()
        timings: dict[str, float] = {}

        # Stage 1 + initial classification in parallel
        if emit_event:
            if getattr(self.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("transform_query", "Transforming and expanding query...", "start"))
            else:
                await emit_event({"status": "Transforming and expanding query..."})

        transform_task = asyncio.create_task(self._transform_query(query, tier))
        router = QueryRouter(
            llm_client=agent_pipeline.get_main_llm(tier),
            router_llm_client=agent_pipeline.get_router_llm(tier),
        )
        classify_task = asyncio.create_task(router.route_query(query))

        t0_t = time.perf_counter()
        transformed, classification = await asyncio.gather(transform_task, classify_task)
        timings["transform_query"] = round((time.perf_counter() - t0_t) * 1000, 2)

        # Small-talk safety net: when the router classifies a gate-missed
        # greeting as chitchat, skip retrieval/rerank entirely.
        if classification.intent == "chitchat":
            from api.chat_routes import run_smalltalk_pipeline

            logger.info("chitchat.bypass", pipeline="adaptive_rag", tier=tier)
            return await run_smalltalk_pipeline(
                query=query,
                tier=tier,
                stream=stream,
                thinking_level=thinking_level,
                emit_event=emit_event,
                chat_history=chat_history,
                session_summary=session_summary,
                user_memories=user_memories,
                provider_filter=provider_filter,
            )

        retriever, reranker = agent_pipeline.get_retrieval()
        provider_filter_dict = None
        if provider_filter:
            aliases = provider_aliases(provider_filter)
            provider_filter_dict = {"provider": aliases if len(aliases) > 1 else provider_filter.lower()}

        # ── Adaptive Cache Router (Apex) ─────────────────────────────────────
        # Runtime query state (strategy, risk, confidence) decides which cache
        # levels to consult and how answer-cache writes are permitted. The
        # pre-dispatch semantic layer is bypassed for Max when the router is
        # enabled (see execute_agent_pipeline), so this is the single
        # cache-control point for the tier.
        cache_decision = None
        _policy_record: dict[str, Any] | None = None
        _router_embedding: list[float] | None = None
        if getattr(self.settings, "enable_adaptive_cache_router", True):
            try:
                from core.cache_policy import (
                    apply_policy_record,
                    get_query_feedback_policy,
                    route_cache_policy,
                )

                t0_cr = time.perf_counter()
                cache_decision = route_cache_policy(
                    query,
                    strategy=transformed.get("routing_path") or transformed.get("strategy"),
                    classification=classification,
                    tier=tier,
                    settings=self.settings,
                )
                if getattr(self.settings, "enable_feedback_cache_policy", True):
                    _policy_record = await get_query_feedback_policy(query)
                    cache_decision = apply_policy_record(cache_decision, _policy_record)
                timings["cache_route"] = round((time.perf_counter() - t0_cr) * 1000, 2)
                logger.info(
                    "adaptive_rag.cache_router",
                    strategy=transformed.get("routing_path"),
                    exact_lookup=cache_decision.exact_lookup,
                    semantic_lookup=cache_decision.semantic_lookup,
                    semantic_threshold=cache_decision.semantic_threshold,
                    answer_write=cache_decision.answer_cache_write,
                    reasons=cache_decision.reasons,
                    tier=tier,
                )

                # ① Exact answer cache (no embedding needed).
                if cache_decision.exact_lookup:
                    try:
                        from core.llm_cache import get_cached_answer
                        from metrics import CACHE_CASCADE_HITS

                        _hh = None
                        if chat_history:
                            _hh = hashlib.sha256(
                                "|".join(
                                    f"{m.get('role', '')}:{m.get('content', '')[:500]}"
                                    for m in chat_history
                                ).encode()
                            ).hexdigest()[:16]
                        _exact_ans = await get_cached_answer(
                            query=query, provider_filter=provider_filter, history_hash=_hh
                        )
                        if isinstance(_exact_ans, dict) and _exact_ans.get("answer"):
                            CACHE_CASCADE_HITS.labels(tier=tier, layer="exact").inc()
                            logger.info("exact_cache.hit", tier=tier, query=query[:60])
                            return self._serve_cached_answer(
                                _exact_ans, query, tier, stream, emit_event, "exact_cache", timings
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning("adaptive_cache.exact_lookup_failed error=%s", e)

                # ② Semantic answer cache at the strategy-aware threshold.
                if cache_decision.semantic_lookup:
                    try:
                        from core.semantic_cache import get_semantic_cache
                        from metrics import CACHE_CASCADE_HITS

                        _router_embedding = await asyncio.wait_for(
                            agent_pipeline.get_embeddings().embed_query(query),
                            timeout=float(getattr(self.settings, "embedding_timeout_seconds", 2.0)),
                        )
                        if _router_embedding:
                            cached_key, sim = get_semantic_cache().get(
                                _router_embedding,
                                threshold=cache_decision.semantic_threshold,
                                intent=getattr(classification, "intent", None),
                            )
                            if cached_key:
                                from core.llm_cache import get_cached_answer

                                cached_ans = await get_cached_answer(
                                    query=cached_key, provider_filter=provider_filter
                                )
                                if isinstance(cached_ans, dict) and cached_ans.get("answer"):
                                    CACHE_CASCADE_HITS.labels(tier=tier, layer="semantic").inc()
                                    logger.info(
                                        "semantic_cache.hit",
                                        tier=tier,
                                        similarity=round(sim, 3),
                                        threshold=cache_decision.semantic_threshold,
                                    )
                                    return self._serve_cached_answer(
                                        cached_ans, query, tier, stream, emit_event,
                                        "semantic_cache", timings,
                                    )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning("adaptive_cache.semantic_lookup_failed error=%s", e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("adaptive_cache_router.failed error=%s", e)
                cache_decision = None

        # Stage 2 + Internet search in parallel
        async def _internet() -> list[dict[str, Any]]:
            if not classification.needs_internet:
                return []
            try:
                results = await agent_pipeline.web_search.search(
                    query=query,
                    max_results=self.settings.web_search_max_results,
                )
                out_results = [
                    {
                        "title": r.title,
                        "url": r.url,
                        "content": r.content,
                        "source_engine": r.source_engine,
                    }
                    for r in results
                ]
                if out_results and getattr(self.settings, "enable_context_validator", True):
                    from core.context_validator import ContextValidator
                    cv = ContextValidator(self.settings)
                    cv.validate_web_results(out_results, tier=tier)
                return out_results
            except asyncio.CancelledError:
                raise
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("adaptive_rag.internet_timeout")
                return []
            except (ConnectionError, OSError) as e:
                logger.warning("adaptive_rag.internet_network_error", error=str(e))
                return []
            except Exception as e:
                logger.warning("adaptive_rag.internet_failed", error=str(e))
                return []

        async def _pricing() -> list[dict[str, Any]]:
            if "PRICING" not in getattr(classification, "routes", []):
                return []
            try:
                from tools.pricing import fetch_cloud_pricing
                return await fetch_cloud_pricing(
                    query=query,
                    providers=getattr(classification, "providers", []),
                    aws_tool=getattr(agent_pipeline, "aws_pricing", None),
                    azure_tool=getattr(agent_pipeline, "azure_pricing", None),
                    gcp_tool=getattr(agent_pipeline, "gcp_pricing", None),
                    timeout=2.5,
                )
            except Exception as e:
                logger.warning("adaptive_rag.pricing_failed", error=str(e))
                return []

        async def _calculator() -> Any:
            if "CALCULATOR" not in getattr(classification, "routes", []):
                return None
            try:
                calc_tool = getattr(agent_pipeline, "calculator", None)
                if calc_tool:
                    math_match = re.search(r"(\d+[\d\s\+\-\*\/\.\(\)]+\d+)", query)
                    if math_match:
                        return await calc_tool.evaluate(math_match.group(1).strip())
            except Exception as e:
                logger.warning("adaptive_rag.calculator_failed", error=str(e))
            return None

        if emit_event:
            if getattr(self.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event(
                    "transform_query", "Query transformed", "complete",
                    elapsed_ms=timings["transform_query"]
                ))
                await emit_event(_stage_event("retrieve", "Running multi-query retrieval with query expansion...", "start"))
            else:
                await emit_event({"status": "Running multi-query retrieval with query expansion..."})

        t0_r = time.perf_counter()
        retrieve_task = asyncio.create_task(
            self._multi_query_retrieve(
                query, transformed, provider_filter_dict,
                classification, tier, retriever, reranker
            )
        )
        internet_task = asyncio.create_task(_internet())
        pricing_task = asyncio.create_task(_pricing())
        calc_task = asyncio.create_task(_calculator())

        (candidates, fallback_pass), internet_results, pricing_data, calc_results = await asyncio.gather(
            retrieve_task, internet_task, pricing_task, calc_task
        )
        timings["multi_query_retrieve"] = round((time.perf_counter() - t0_r) * 1000, 2)

        # Stage 2b — Retrieval Feedback & Re-planning (triggered on low relevance or zero candidates)
        if getattr(self.settings, "enable_retrieval_feedback_replanning", True):
            needs_replan, fb_score, fb_diagnosis = self._evaluate_retrieval_feedback(candidates, query, transformed)
            if needs_replan:
                logger.info(
                    "adaptive_rag.retrieval_feedback_replan_triggered",
                    feedback_score=fb_score,
                    diagnosis=fb_diagnosis,
                    tier=tier,
                )
                if emit_event:
                    if getattr(self.settings, "enable_structured_stage_events", True):
                        await emit_event(_stage_event(
                            "replan", f"Adaptive feedback ({fb_diagnosis}): replanning query...", "start"
                        ))
                    else:
                        await emit_event({"status": f"Refining search query ({fb_diagnosis})..."})
                t0_replan = time.perf_counter()
                replanned_candidates, replan_pass = await self._replan_retrieval(
                    query=query,
                    transformed=transformed,
                    feedback_diagnosis=fb_diagnosis,
                    tier=tier,
                    retriever=retriever,
                    reranker=reranker,
                )
                if replanned_candidates:
                    seen_cids = {c.chunk_id for c in candidates}
                    for c in replanned_candidates:
                        if c.chunk_id not in seen_cids:
                            seen_cids.add(c.chunk_id)
                            candidates.append(c)
                    fallback_pass = f"{fallback_pass}+{replan_pass}"
                timings["retrieval_replan"] = round((time.perf_counter() - t0_replan) * 1000, 2)
                if emit_event and getattr(self.settings, "enable_structured_stage_events", True):
                    await emit_event(_stage_event(
                        "replan", "Adaptive search refined", "complete",
                        elapsed_ms=timings["retrieval_replan"]
                    ))

        for chunk in candidates:
            m = chunk.metadata
            citation_mgr.register_source(
                source_type="rag",
                url=m.get("url", ""),
                provider=m.get("provider", "cloud"),
                service=m.get("service", ""),
                title=m.get("title", "Cloud Documentation"),
                section=m.get("section", ""),
                chunk_id=chunk.chunk_id,
            )
        for r in internet_results:
            citation_mgr.register_source(
                source_type="internet",
                url=r["url"],
                provider="web",
                service="",
                title=r["title"],
                section="Internet Search",
            )
        for p in pricing_data:
            citation_mgr.register_source(
                source_type="pricing",
                url="",
                provider=p.get("provider", "cloud"),
                service=p.get("service", "Compute"),
                title=f"{p.get('provider', '').upper()} Pricing: {p.get('sku', '')}",
                section=f"Hourly: ${p.get('hourly_cost', 0):.4f} {p.get('currency', 'USD')}",
            )

        RAG_RESULTS_COUNT.observe(len(candidates))

        # Stage 3 — Rerank & Compress
        if emit_event:
            if getattr(self.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event(
                    "retrieve", "Documentation retrieved", "complete",
                    elapsed_ms=timings["multi_query_retrieve"]
                ))
                await emit_event(_stage_event("rerank", "Reranking and compressing context...", "start"))
            else:
                await emit_event({"status": "Reranking and compressing context..."})

        t0_rc = time.perf_counter()
        compressed = await self._rerank_and_compress(query, candidates, tier, reranker, transformed=transformed)
        timings["rerank_compress"] = round((time.perf_counter() - t0_rc) * 1000, 2)

        if emit_event and getattr(self.settings, "enable_structured_stage_events", True):
            await emit_event(_stage_event(
                "rerank", "Context ready", "complete",
                elapsed_ms=timings["rerank_compress"]
            ))

        # ── Live Verify Trigger ───────────────────────────────────────────────────
        live_verify_results: list[dict[str, Any]] = []
        if getattr(self.settings, "enable_live_verify", True):
            # Check 1: any stale chunk from Context Validator?
            has_stale = any(c.metadata.get("stale", False) for c in compressed)
            # Check 2: low combined confidence?
            avg_grade = (
                sum(float(c.metadata.get("grade_score", classification.confidence)) for c in compressed)
                / len(compressed)
            ) if compressed else classification.confidence
            cqc_coherence = float(compressed[0].metadata.get("cqc_coherence_score", 1.0)) if compressed else 1.0
            combined_confidence = (avg_grade + classification.confidence + cqc_coherence) / 3.0
            trigger_threshold = getattr(self.settings, "live_verify_confidence_trigger", 0.45)

            if has_stale or combined_confidence < trigger_threshold:
                logger.info(
                    "live_verify.triggered",
                    tier=tier,
                    has_stale=has_stale,
                    combined_confidence=round(combined_confidence, 3),
                    trigger_threshold=trigger_threshold,
                )
                live_verify_results = await self._live_verify(
                    query=query,
                    providers=list(classification.providers),
                    tier=tier,
                    emit_event=emit_event,
                )
                for r in live_verify_results:
                    citation_mgr.register_source(
                        source_type="internet",
                        url=r.get("url", ""),
                        provider=r.get("provider", "web"),
                        service="",
                        title=r.get("title", ""),
                        section="Live Verification (Provider Docs)",
                    )
                # Merge live results into internet_results so _generate picks them up
                internet_results = live_verify_results + internet_results
                timings["live_verify"] = round(sum(
                    v for k, v in timings.items() if "live" in k
                ), 2)

        # Stage 4 — Generate (dynamic policy-aware prompt + adaptive
        # grounded generation with abstention/retry decisions)
        gen_policy = {"mode": "grounded", "reason": "validation_disabled"}
        try:
            from generation.policy import pre_generation_decision

            gen_policy = pre_generation_decision(
                candidates=compressed,
                live_verify_results=live_verify_results,
                classification_confidence=classification.confidence,
                settings=self.settings,
            )
            logger.info(
                "adaptive_rag.generation_policy",
                mode=gen_policy.get("mode"),
                reason=gen_policy.get("reason"),
                tier=tier,
            )
        except Exception as e:
            logger.warning("generation_policy.failed", error=str(e))

        validation_out: dict[str, Any] = {}
        answer_or_stream, model_used = await self._generate(
            query=query,
            compressed_chunks=compressed,
            classification_obj=classification,
            internet_results=internet_results,
            provider_filter=provider_filter,
            chat_history=chat_history,
            attachment_texts=attachment_texts,
            tier=tier,
            thinking_level=thinking_level,
            stream=stream,
            timings=timings,
            emit_event=emit_event,
            session_summary=session_summary,
            user_memories=user_memories,
            pricing_data=pricing_data,
            calc_results=calc_results,
            transformed=transformed,
            live_verified=bool(live_verify_results),
            citation_mgr=citation_mgr,
            validation_out=validation_out,
        )

        # Apex answer-cache write — permitted only when the adaptive cache
        # router allows it and Layer-4 validation did not flag the answer.
        if not stream and cache_decision is not None and cache_decision.answer_cache_write and answer_or_stream:
            try:
                from core.cache_policy import answer_write_ttl, skip_answer_cache_for_validation
                from core.llm_cache import set_cached_answer
                from core.semantic_cache import get_semantic_cache

                if not skip_answer_cache_for_validation(validation_out, self.settings):
                    base_ttl = int(getattr(self.settings, "redis_cache_ttl_seconds", 3600))
                    await set_cached_answer(
                        query=query,
                        response_payload={
                            "answer": answer_or_stream,
                            "sources": [s.model_dump() for s in citation_mgr.get_sources()],
                            "model": model_used,
                        },
                        model=model_used,
                        provider_filter=provider_filter,
                        ttl_seconds=answer_write_ttl(base_ttl, query, _policy_record, self.settings),
                        settings=self.settings,
                    )
                    if _router_embedding:
                        get_semantic_cache().set(
                            _router_embedding, query,
                            intent=getattr(classification, "intent", None),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("adaptive_cache.answer_write_failed error=%s", e)

        sources_dump = [s.model_dump() for s in citation_mgr.get_sources()]

        logger.info(
            "pipeline.complete",
            pipeline_type="adaptive_rag",
            tier=tier,
            total_ms=sum(timings.values()),
            stage_timings=timings,
            model_used=model_used,
            sources_count=len(sources_dump),
            fallback_pass=fallback_pass,
        )

        return PipelineResult(
            answer="" if stream else answer_or_stream,
            token_stream=answer_or_stream if stream else None,
            sources=sources_dump,
            routes=classification.routes,
            confidence=classification.confidence,
            classification=classification.model_dump(),
            model_used=model_used,
            pipeline_timings=timings,
            fallback_pass=fallback_pass,
            pipeline_type="adaptive_rag",
            validation=validation_out,
        )
