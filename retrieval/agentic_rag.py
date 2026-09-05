"""Agentic RAG Pipeline — Pro Tier

Pipeline stages:
  1. Plan & Route      — Gemini 2.0 Flash produces a structured retrieval plan
  2. Hybrid Retrieve   — HybridRetriever with multi-hop fan-out support
  3. Grade Evidence    — Gemini 2.0 Flash batch-scores retrieved chunks for relevance
  4. Generate & Verify — Main LLM generates, then self-critiques the answer

All stages fall back gracefully. The pipeline always returns a PipelineResult
even if individual stages fail. Internet search runs in parallel with retrieval.
"""

from __future__ import annotations

import asyncio
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
from llm.system_prompts import AGENTIC_PLAN_PROMPT, AGENTIC_REFINE_PROMPT, EVIDENCE_GRADE_PROMPT, SELF_CRITIQUE_PROMPT
from metrics import RAG_STAGE_DURATION_SECONDS, RAG_RESULTS_COUNT, PIPELINE_TIMEOUTS_TOTAL
from retrieval.hybrid import RetrievalResult
from router.query_router import QueryClassification, provider_aliases

logger = structlog.get_logger(__name__)


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


class AgenticRAGPipeline:
    """Agentic RAG pipeline for Pro-tier users."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def _plan_and_route(self, query: str, tier: str) -> dict[str, Any]:
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:12]
        t0 = time.perf_counter()
        logger.debug("pipeline.stage_start", stage="plan_route", tier=tier, query_hash=query_hash)

        def _infer_modality(q: str) -> str:
            q_low = q.lower()
            if re.search(r"(--[a-zA-Z0-9_-]+|\b[A-Z][a-zA-Z0-9]+Exception\b|\bError:\b|\b\d{3}\s+Forbidden\b|\b\d+\.\d+\.\d+\b)", q):
                return "sparse"
            if any(cmd in q_low for cmd in ["aws ", "az ", "gcloud ", "kubectl ", "terraform ", "error ", "status code"]):
                return "sparse"
            if any(concept in q_low for concept in ["architecture", "overview", "what is", "trade-off", "design pattern", "best practice"]):
                return "dense"
            return "hybrid"

        default_plan: dict[str, Any] = {
            "intent": "explain",
            "routes": ["RAG"],
            "retrieval_strategy": "broad",
            "retrieval_modality": _infer_modality(query),
            "sub_queries": [],
            "providers": [],
            "needs_internet": False,
            "confidence": 0.7,
            "complexity_score": 0.35,
            "decomposition_applied": False,
        }

        try:
            router_llm = get_sub_model_provider("router", tier)
            budget = float(getattr(self.settings, "agentic_rag_timeout_seconds", 15.0))
            raw = await asyncio.wait_for(
                router_llm.classify(query, system_prompt=AGENTIC_PLAN_PROMPT),
                timeout=budget,
            )
            data = json.loads(raw)
            raw_modality = data.get("retrieval_modality")
            plan = {
                "intent": data.get("intent", "explain"),
                "routes": data.get("routes", ["RAG"]),
                "retrieval_strategy": data.get("retrieval_strategy", "broad"),
                "retrieval_modality": raw_modality if raw_modality in ("dense", "sparse", "hybrid") else _infer_modality(query),
                "sub_queries": data.get("sub_queries", [])[:3] if isinstance(data.get("sub_queries"), list) else [],
                "providers": data.get("providers", []) if isinstance(data.get("providers"), list) else [],
                "needs_internet": bool(data.get("needs_internet", False)),
                "confidence": float(data.get("confidence", 0.8)),
            }
            if plan["retrieval_strategy"] not in ("broad", "narrow", "multi-hop"):
                plan["retrieval_strategy"] = "broad"
            if plan["retrieval_strategy"] != "multi-hop":
                plan["sub_queries"] = []
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("plan_route.timeout", tier=tier, query_hash=query_hash)
            PIPELINE_TIMEOUTS_TOTAL.labels(component="agentic_rag_plan").inc()
            plan = default_plan
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning("plan_route.parse_error", error=str(e), tier=tier)
            plan = default_plan
        except (ConnectionError, OSError) as e:
            logger.warning("plan_route.network_error", error=str(e), tier=tier)
            plan = default_plan
        except Exception as e:
            logger.warning("plan_route.llm_error", error=str(e), tier=tier)
            plan = default_plan

        # Agent-Driven Query Planning: Calculate Query Complexity
        def _calc_complexity(q: str, raw_d: dict[str, Any]) -> float:
            if "complexity_score" in raw_d:
                try:
                    return max(0.0, min(1.0, float(raw_d["complexity_score"])))
                except (ValueError, TypeError):
                    pass
            q_l = q.lower()
            c = 0.35
            if any(ind in q_l for ind in [" vs ", " versus ", "compare", "recommend", "difference between", "better"]):
                c += 0.35
            provs = sum(1 for p in ["aws", "gcp", "azure", "google cloud"] if p in q_l)
            if provs >= 2:
                c += 0.25
            if any(conj in q_l for conj in [" and also ", " along with ", ";", "?", " as well as "]):
                c += 0.15
            if any(arch in q_l for arch in ["architecture", "migration", "failover", "disaster recovery", "trade-off", "high availability"]):
                c += 0.15
            return round(min(1.0, c), 2)

        raw_data = data if "data" in locals() and isinstance(data, dict) else {}
        complexity_score = _calc_complexity(query, raw_data)
        plan["complexity_score"] = complexity_score

        # Conditional Query Decomposition:
        decomp_threshold = float(getattr(self.settings, "agentic_decomposition_complexity_threshold", 0.65))
        decomp_enabled = bool(getattr(self.settings, "enable_conditional_decomposition", True))

        _query_lower = query.lower()
        _comparison_indicators = ["recommend", "which", "compare", "vs ", "versus", "best service", "should i use"]
        is_comparative = any(ind in _query_lower for ind in _comparison_indicators)

        if decomp_enabled:
            # If the LLM already gave sub_queries or query is complex/comparative, enable decomposition
            has_subqueries = bool(plan.get("sub_queries"))
            should_decompose = has_subqueries or complexity_score >= decomp_threshold or (is_comparative and getattr(self.settings, "recommendation_comparison_bias", True))
            if should_decompose:
                if plan["retrieval_strategy"] != "multi-hop":
                    plan["retrieval_strategy"] = "multi-hop"
                if not plan.get("sub_queries"):
                    detected_providers = plan.get("providers") or ["aws", "gcp", "azure"]
                    plan["sub_queries"] = [f"{query} on {p.upper()}" for p in detected_providers[:3]]
                plan["decomposition_applied"] = bool(plan.get("sub_queries"))
            else:
                # Focused atomic query: prevent unnecessary multi-hop fan-out
                plan["sub_queries"] = []
                plan["decomposition_applied"] = False
                if plan["retrieval_strategy"] == "multi-hop":
                    plan["retrieval_strategy"] = "broad"
        else:
            if is_comparative and getattr(self.settings, "recommendation_comparison_bias", True):
                if plan["retrieval_strategy"] != "multi-hop":
                    plan["retrieval_strategy"] = "multi-hop"
                if not plan.get("sub_queries"):
                    detected_providers = plan.get("providers") or ["aws", "gcp", "azure"]
                    plan["sub_queries"] = [f"{query} on {p.upper()}" for p in detected_providers[:3]]
            plan["decomposition_applied"] = bool(plan.get("sub_queries"))

        elapsed = time.perf_counter() - t0
        plan["_timing_ms"] = round(elapsed * 1000, 2)
        RAG_STAGE_DURATION_SECONDS.labels(stage="plan_route", tier=tier).observe(elapsed)
        logger.info(
            "pipeline.stage_complete",
            stage="plan_route",
            tier=tier,
            duration_ms=round(elapsed * 1000, 2),
            intent=plan["intent"],
            strategy=plan["retrieval_strategy"],
            modality=plan.get("retrieval_modality", "hybrid"),
            sub_queries_count=len(plan["sub_queries"]),
            complexity=plan["complexity_score"],
            decomposition_applied=plan["decomposition_applied"],
            needs_internet=plan["needs_internet"],
        )
        return plan

    async def _hybrid_retrieve(
        self,
        query: str,
        sub_queries: list[str],
        retrieval_strategy: str,
        provider_filter: str | None,
        classification: QueryClassification,
        tier: str,
        retriever: Any,
        reranker: Any,
        retrieval_modality: str = "hybrid",
    ) -> tuple[list[RetrievalResult], str]:
        t0 = time.perf_counter()
        cache_query_key = f"agentic:{hashlib.sha256(query.encode()).hexdigest()[:16]}:{provider_filter or 'all'}:{tier}"

        try:
            cached = await get_cached_retrieval_result(cache_query_key, provider_filter)
            if cached:
                elapsed = time.perf_counter() - t0
                RAG_STAGE_DURATION_SECONDS.labels(stage="agentic_retrieve", tier=tier).observe(elapsed)
                cached_objs = [
                    RetrievalResult(
                        chunk_id=str(r.get("chunk_id", "")),
                        text=r.get("text", r.get("content", "")),
                        score=float(r.get("score", 1.0)),
                        metadata=r.get("metadata", {}),
                    ) if isinstance(r, dict) else r
                    for r in cached
                ]
                return cached_objs, "cached"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("agentic_retrieve.cache_check_error", error=str(e))

        prov_filter_dict = None
        if provider_filter:
            aliases = provider_aliases(provider_filter)
            prov_filter_dict = {"provider": aliases if len(aliases) > 1 else provider_filter.lower()}

        dense_ret = getattr(retriever, "dense_retriever", None)
        sparse_ret = getattr(retriever, "sparse_retriever", None)

        if retrieval_strategy == "multi-hop" and sub_queries:
            async def _retrieve_sq(sq_text: str):
                try:
                    return await retriever.retrieve(
                        sq_text,
                        top_k=self.settings.retrieval_top_k,
                        filters=prov_filter_dict,
                        expand_to_parents=True,
                    )
                except TypeError:
                    return await retriever.retrieve(
                        sq_text,
                        top_k=self.settings.retrieval_top_k,
                        filters=prov_filter_dict,
                    )

            tasks = [_retrieve_sq(sq) for sq in sub_queries]
            all_batches = await asyncio.gather(*tasks, return_exceptions=True)

            merged: dict[str, RetrievalResult] = {}
            scores: dict[str, float] = {}
            k_rrf = 60
            for batch in all_batches:
                if isinstance(batch, (Exception, BaseException)) or not batch:
                    continue
                clean_batch = [r for r in batch if isinstance(r, RetrievalResult)]
                if not clean_batch:
                    continue
                sorted_batch = sorted(clean_batch, key=lambda r: r.score, reverse=True)
                for rank, res in enumerate(sorted_batch):
                    if res.chunk_id not in scores:
                        scores[res.chunk_id] = 0.0
                        merged[res.chunk_id] = res
                    scores[res.chunk_id] += 1.0 / (k_rrf + rank + 1)

            for cid in merged:
                merged[cid].score = scores[cid]

            candidates = sorted(merged.values(), key=lambda r: r.score, reverse=True)
            candidates = candidates[:self.settings.retrieval_top_k]

            if candidates and reranker:
                top_results = await asyncio.to_thread(
                    reranker.rerank, query, candidates, self.settings.rerank_top_k
                )
            else:
                top_results = candidates[:self.settings.rerank_top_k]
            fallback_pass = "agentic_multi_hop"
        elif dense_ret and sparse_ret and retrieval_modality in ("dense", "sparse", "hybrid"):
            # Agentic Retrieval Routing:
            # Execute selected modality, applying RRF only when both are used!
            try:
                if retrieval_modality == "dense":
                    candidates = await dense_ret.retrieve(
                        query, top_k=self.settings.retrieval_top_k, filters=prov_filter_dict
                    )
                    fallback_pass = "agentic_dense_hnsw"
                elif retrieval_modality == "sparse":
                    candidates = await sparse_ret.retrieve(
                        query, top_k=self.settings.retrieval_top_k, filters=prov_filter_dict
                    )
                    fallback_pass = "agentic_sparse_bm25"
                else:  # "hybrid" -> Both are used, apply RRF Fusion!
                    dense_task = dense_ret.retrieve(
                        query, top_k=self.settings.retrieval_top_k, filters=prov_filter_dict
                    )
                    sparse_task = sparse_ret.retrieve(
                        query, top_k=self.settings.retrieval_top_k, filters=prov_filter_dict
                    )
                    dense_res, sparse_res = await asyncio.gather(dense_task, sparse_task, return_exceptions=True)

                    d_list = dense_res if isinstance(dense_res, list) else []
                    s_list = sparse_res if isinstance(sparse_res, list) else []

                    # RRF Fusion across dense and sparse
                    k_rrf = 60
                    merged_dict: dict[str, RetrievalResult] = {}
                    scores_dict: dict[str, float] = {}
                    w_dense = getattr(self.settings, "dense_weight", 0.6)
                    w_sparse = getattr(self.settings, "sparse_weight", 0.4)

                    for rank, res in enumerate(sorted(d_list, key=lambda x: x.score, reverse=True)):
                        if res.chunk_id not in scores_dict:
                            scores_dict[res.chunk_id] = 0.0
                            merged_dict[res.chunk_id] = res
                        scores_dict[res.chunk_id] += w_dense * (1.0 / (k_rrf + rank + 1))

                    for rank, res in enumerate(sorted(s_list, key=lambda x: x.score, reverse=True)):
                        if res.chunk_id not in scores_dict:
                            scores_dict[res.chunk_id] = 0.0
                            merged_dict[res.chunk_id] = res
                        scores_dict[res.chunk_id] += w_sparse * (1.0 / (k_rrf + rank + 1))

                    for cid in merged_dict:
                        merged_dict[cid].score = scores_dict[cid]

                    candidates = sorted(merged_dict.values(), key=lambda x: x.score, reverse=True)[:self.settings.retrieval_top_k]
                    fallback_pass = "agentic_hybrid_rrf"

                if candidates and reranker:
                    top_results = await asyncio.to_thread(
                        reranker.rerank, query, candidates, self.settings.rerank_top_k
                    )
                else:
                    top_results = candidates[:self.settings.rerank_top_k]
            except Exception as e:
                logger.warning("agentic_retrieve.modality_routing_error", error=str(e))
                from api.chat_routes import _retrieve_with_fallback
                budget = (self.settings.budget_retrieval_ms + self.settings.budget_reranking_ms) / 1000.0
                top_results, fallback_pass = await asyncio.wait_for(
                    _retrieve_with_fallback(
                        retriever, reranker, query, provider_filter,
                        classification, self.settings,
                        expand_to_parents=True,
                    ),
                    timeout=budget,
                )
        else:
            from api.chat_routes import _retrieve_with_fallback
            budget = (self.settings.budget_retrieval_ms + self.settings.budget_reranking_ms) / 1000.0
            try:
                top_results, fallback_pass = await asyncio.wait_for(
                    _retrieve_with_fallback(
                        retriever, reranker, query, provider_filter,
                        classification, self.settings,
                        expand_to_parents=True,
                    ),
                    timeout=budget,
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                logger.warning("agentic_retrieve.timeout", tier=tier)
                top_results, fallback_pass = [], "timeout"
            except (ConnectionError, OSError) as e:
                logger.warning("agentic_retrieve.connection_error", error=str(e), tier=tier)
                top_results, fallback_pass = [], "connection_error"
            except ValueError as e:
                logger.warning("agentic_retrieve.value_error", error=str(e), tier=tier)
                top_results, fallback_pass = [], "value_error"
            except Exception as e:
                logger.warning("agentic_retrieve.fallback_error", error=str(e))
                top_results, fallback_pass = [], "none"

        try:
            await set_cached_retrieval_result(
                cache_query_key, top_results, provider_filter, self.settings, ttl_seconds=21600
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("agentic_retrieve.cache_set_error", error=str(e))

        if getattr(self.settings, "enable_context_validator", True) and top_results:
            from core.context_validator import ContextValidator
            cv = ContextValidator(self.settings)
            vr = cv.validate_retrieved_chunks(top_results, tier=tier)
            for chunk in top_results:
                if chunk.chunk_id in vr.staleness_flags:
                    chunk.metadata["stale"] = True
            if not vr.is_valid:
                logger.warning("context_validator.agentic_chunks_rejected", tier=tier)
                top_results = []

        elapsed = time.perf_counter() - t0
        RAG_STAGE_DURATION_SECONDS.labels(stage="agentic_retrieve", tier=tier).observe(elapsed)
        logger.info(
            "pipeline.stage_complete",
            stage="agentic_retrieve",
            tier=tier,
            duration_ms=round(elapsed * 1000, 2),
            candidate_count=len(top_results),
            fallback_pass=fallback_pass,
        )
        return top_results, fallback_pass

    async def _grade_evidence(
        self,
        query: str,
        chunks: list[RetrievalResult],
        tier: str,
    ) -> list[RetrievalResult]:
        if not chunks:
            return []

        t0 = time.perf_counter()
        chunk_lines = "\n".join(
            f"[{i}]: {chunk.text[:500]}"
            for i, chunk in enumerate(chunks)
        )
        grading_input = f"QUERY: {query}\nCHUNKS:\n{chunk_lines}\nOUTPUT:"

        try:
            router_llm = get_sub_model_provider("router", tier)
            budget = self.settings.budget_reranking_ms / 1000.0
            from llm.provider import _classify_with_retry

            try:
                raw_response = await asyncio.wait_for(
                    _classify_with_retry(
                        provider=router_llm,
                        query=grading_input,
                        system_prompt=EVIDENCE_GRADE_PROMPT,
                        role="grader",
                    ),
                    timeout=budget,
                )
                parsed_scores = json.loads(raw_response)
            except asyncio.TimeoutError:
                parsed_scores = None
                logger.warning("grade_evidence.timeout", tier=tier)
                PIPELINE_TIMEOUTS_TOTAL.labels(component="agentic_rag_grade").inc()
                return chunks
            except ValueError:
                parsed_scores = None
                logger.warning("grade_evidence.exhausted_retries", tier=tier)

            scores = _validate_scores(parsed_scores, len(chunks))

            graded = []
            for chunk, score in zip(chunks, scores):
                chunk.metadata["grade_score"] = float(score)
                if float(score) >= 0.4:
                    graded.append(chunk)

            if not graded:
                graded = sorted(chunks, key=lambda c: c.score, reverse=True)[:3]

            if graded and getattr(self.settings, "enable_context_quality_controller", True):
                from core.context_quality import ContextQualityController
                _cqc = ContextQualityController(self.settings)
                _cqc_result = _cqc.run(
                    chunks=[
                        {
                            "url": c.metadata.get("url", ""),
                            "provider": c.metadata.get("provider", ""),
                            "service": c.metadata.get("service", ""),
                            "content": c.text,
                            "section": c.metadata.get("section", ""),
                            "title": c.metadata.get("title", ""),
                        }
                        for c in graded
                    ],
                    memory_facts=[],
                    history_snippets=[],
                    tier=tier,
                )
                if graded:
                    graded[0].metadata["cqc_coherence_score"] = _cqc_result.context_coherence_score

            elapsed = time.perf_counter() - t0
            RAG_STAGE_DURATION_SECONDS.labels(stage="grade_evidence", tier=tier).observe(elapsed)
            logger.info(
                "pipeline.stage_complete",
                stage="grade_evidence",
                tier=tier,
                duration_ms=round(elapsed * 1000, 2),
                grade_accepted=len(graded),
                grade_total=len(chunks),
                min_score=min(scores) if scores else 0.0,
                max_score=max(scores) if scores else 0.0,
            )
            return graded

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("grade_evidence.timeout", tier=tier)
            PIPELINE_TIMEOUTS_TOTAL.labels(component="agentic_rag_grade").inc()
            elapsed = time.perf_counter() - t0
            RAG_STAGE_DURATION_SECONDS.labels(stage="grade_evidence", tier=tier).observe(elapsed)
            return chunks
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("grade_evidence.parse_error", error=str(e), tier=tier)
            elapsed = time.perf_counter() - t0
            RAG_STAGE_DURATION_SECONDS.labels(stage="grade_evidence", tier=tier).observe(elapsed)
            return chunks
        except Exception as e:
            logger.warning("grade_evidence.safe_fallback", error=str(e), tier=tier)
            elapsed = time.perf_counter() - t0
            RAG_STAGE_DURATION_SECONDS.labels(stage="grade_evidence", tier=tier).observe(elapsed)
            return chunks

    def _expand_hierarchical_context(
        self,
        chunks: list[RetrievalResult],
        max_expansions: int = 4,
    ) -> list[RetrievalResult]:
        """Navigate hierarchical parent-child relationships to enrich top evidence.

        If a child chunk has high evidence grade score, verify parent resolution.
        If a parent chunk is highly relevant, attach key child/sibling details
        from HierarchicalChunkStore if available.
        """
        if not chunks:
            return chunks

        store = HierarchicalChunkStore.get_instance()
        enriched: list[RetrievalResult] = []
        seen_ids = set()

        expansions = 0
        for chunk in chunks:
            if chunk.chunk_id in seen_ids:
                continue
            seen_ids.add(chunk.chunk_id)
            enriched.append(chunk)

            # If this is a high-confidence chunk, check for sibling/child enrichment
            score = float(chunk.metadata.get("grade_score", chunk.score))
            if score >= 0.6 and expansions < max_expansions:
                siblings = store.get_sibling_chunks(chunk.chunk_id)
                for sib in siblings:
                    if sib.chunk_id not in seen_ids and expansions < max_expansions:
                        seen_ids.add(sib.chunk_id)
                        sib_result = RetrievalResult(
                            chunk_id=sib.chunk_id,
                            text=sib.content,
                            score=chunk.score * 0.9,
                            metadata={
                                **sib.metadata,
                                "service": sib.service,
                                "provider": sib.provider,
                                "section": sib.section,
                                "parent_chunk_id": sib.parent_chunk_id,
                                "hierarchy_level": sib.hierarchy_level,
                                "hierarchical_expanded": True,
                                "grade_score": score * 0.9,
                            },
                        )
                        enriched.append(sib_result)
                        expansions += 1

        return enriched

    def _evaluate_sufficiency(
        self,
        graded_chunks: list[RetrievalResult],
        plan: dict[str, Any],
    ) -> tuple[bool, float, str]:
        """Evaluate if retrieved and graded evidence is sufficient to answer the query."""
        if not graded_chunks:
            return False, 0.0, "zero_candidates_retrieved"

        grade_scores = [
            float(c.metadata.get("grade_score", c.score)) for c in graded_chunks
        ]
        max_score = max(grade_scores) if grade_scores else 0.0
        thresh = float(getattr(self.settings, "agentic_refinement_sufficiency_threshold", 0.55))

        if max_score < thresh:
            return False, max_score, f"low_relevance_evidence (max {max_score:.2f} < {thresh:.2f})"

        high_quality_count = sum(1 for s in grade_scores if s >= thresh)
        if high_quality_count < 1:
            return False, max_score, "insufficient_high_confidence_evidence"

        return True, max_score, "sufficient"

    async def _refine_query(
        self,
        query: str,
        plan: dict[str, Any],
        graded_chunks: list[RetrievalResult],
        tier: str,
    ) -> str:
        """Formulate a targeted follow-up query to fill identified evidence gaps."""
        try:
            router_llm = get_sub_model_provider("router", tier)
            evidence_preview = "\n".join(
                f"[{i}]: {c.text[:250]}" for i, c in enumerate(graded_chunks[:3])
            )
            prompt = (
                f"ORIGINAL QUERY: {query}\n"
                f"INITIAL PLAN INTENT: {plan.get('intent', 'explain')}\n"
                f"CURRENT EVIDENCE SNIPPETS:\n{evidence_preview}\n"
                f"TASK: Generate a single, highly specific technical query to retrieve missing evidence."
            )
            raw = await asyncio.wait_for(
                router_llm.classify(prompt, system_prompt=AGENTIC_REFINE_PROMPT),
                timeout=5.0,
            )
            data = json.loads(raw)
            refined = data.get("refined_query", "").strip()
            if refined:
                return refined
        except Exception as e:
            logger.warning("agentic_rag.refine_query_fallback", error=str(e))

        return f"{query} technical specifications configuration limits"

    async def _generate_and_verify(
        self,
        query: str,
        graded_chunks: list[RetrievalResult],
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
    ) -> tuple[str | AsyncGenerator[str, None], str]:
        from api.chat_routes import _build_pipeline_messages, generate_with_fallback
        from llm.provider import calculate_effective_prompt_budget

        rag_results = [
            {
                "chunk_id": chunk.chunk_id,
                "provider": chunk.metadata.get("provider", "cloud"),
                "service": chunk.metadata.get("service", ""),
                "section": chunk.metadata.get("section", ""),
                "url": chunk.metadata.get("url", ""),
                "content": chunk.text,
                "title": chunk.metadata.get("title", ""),
                "parent_chunk_id": chunk.metadata.get("parent_chunk_id"),
                "hierarchy_level": chunk.metadata.get("hierarchy_level", 1),
                "is_coalesced_parent": chunk.metadata.get("is_coalesced_parent", False),
            }
            for chunk in graded_chunks
        ]

        tier_context_budget = {
            "Free": getattr(self.settings, "prompt_budget_free", self.settings.context_tokens_free),
            "Pro": getattr(self.settings, "prompt_budget_pro", self.settings.context_tokens_pro),
            "Max": getattr(self.settings, "prompt_budget_max", self.settings.context_tokens_max),
        }.get(tier, self.settings.context_tokens_pro) if getattr(self.settings, "enable_context_budget", True) else None

        if tier_context_budget:
            tier_context_budget = calculate_effective_prompt_budget(
                tier_budget=tier_context_budget,
                model_name=None,
                max_output_tokens=getattr(self.settings, "chat_max_output_tokens", 4096),
            )

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
        )

        if emit_event:
            if getattr(self.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("generate", "Synthesizing and verifying answer...", "start"))
            else:
                await emit_event({"status": "Synthesizing and verifying answer..."})

        # Confidence gate — skip self-critique if evidence quality is high
        skip_threshold = getattr(self.settings, "skip_self_critique_threshold", 0.7)
        can_skip_critique = False
        if graded_chunks and len(graded_chunks) >= 3:
            min_evidence_score = min(
                float(c.metadata.get("grade_score", 0.0))
                for c in graded_chunks[:5]
            )
            coherence_score = float(graded_chunks[0].metadata.get("cqc_coherence_score", 1.0))
            combined_score = (min_evidence_score + coherence_score) / 2.0
            if combined_score >= skip_threshold:
                can_skip_critique = True

        if can_skip_critique:
            timings["self_critique"] = 0.0
            if emit_event and getattr(self.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event(
                    "self_critique", "Evidence confidence high — skipping revision",
                    "skipped"
                ))
            t0_gen = time.perf_counter()
            if stream:
                gen_stream, model_used = await generate_with_fallback(
                    messages=messages, stream=True, tier=tier, thinking_level=thinking_level
                )
                timings["generate"] = round((time.perf_counter() - t0_gen) * 1000, 2)
                return gen_stream, model_used

            raw_answer, model_used = await generate_with_fallback(
                messages=messages, stream=False, tier=tier, thinking_level=thinking_level
            )
            gen_elapsed = time.perf_counter() - t0_gen
            timings["generate"] = round(gen_elapsed * 1000, 2)
            RAG_STAGE_DURATION_SECONDS.labels(stage="agentic_generate", tier=tier).observe(gen_elapsed)
            return raw_answer, model_used

        t0_gen = time.perf_counter()
        raw_answer, model_used = await generate_with_fallback(
            messages=messages, stream=False, tier=tier, thinking_level=thinking_level
        )
        gen_elapsed = time.perf_counter() - t0_gen
        timings["generate"] = round(gen_elapsed * 1000, 2)
        RAG_STAGE_DURATION_SECONDS.labels(stage="agentic_generate", tier=tier).observe(gen_elapsed)

        # Self-critique pass
        evidence_summary = "\n".join(
            f"[{i}]: {chunk.text[:300]}"
            for i, chunk in enumerate(graded_chunks[:5])
        )
        critique_input = (
            f"ORIGINAL QUERY: {query}\n\n"
            f"YOUR PREVIOUS ANSWER:\n{raw_answer[:3000]}\n\n"
            f"SOURCE EVIDENCE:\n{evidence_summary}"
        )
        critique_messages = [
            {"role": "system", "content": SELF_CRITIQUE_PROMPT},
            {"role": "user", "content": critique_input},
        ]
        _eval_temp = float(getattr(self.settings, "temperature_evaluator", 0.1))
        from llm.provider import get_evaluator_provider
        _evaluator = get_evaluator_provider(tier)
        t0_crit = time.perf_counter()
        try:
            critique_raw = await asyncio.wait_for(
                _evaluator.generate(
                    messages=critique_messages,
                    stream=False,
                    temperature=_eval_temp,
                    thinking_level="Low",
                ),
                timeout=getattr(self.settings, "self_critique_timeout_seconds", 15.0),
            )
            if not isinstance(critique_raw, str):
                critique_raw = ""
            crit_elapsed = time.perf_counter() - t0_crit
            timings["self_critique"] = round(crit_elapsed * 1000, 2)
            RAG_STAGE_DURATION_SECONDS.labels(stage="agentic_critique", tier=tier).observe(crit_elapsed)

            critique = critique_raw.strip()
            if "NO_REVISION_NEEDED" in critique:
                final_answer = raw_answer
            elif "REVISED ANSWER:" in critique:
                final_answer = critique.split("REVISED ANSWER:", 1)[1].strip()
            else:
                logger.warning("self_critique.unparseable", tier=tier)
                final_answer = raw_answer
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("self_critique.timeout", tier=tier)
            final_answer = raw_answer
        except Exception as e:
            logger.warning("self_critique.failed", error=str(e), tier=tier)
            final_answer = raw_answer
            final_answer = raw_answer

        logger.info(
            "pipeline.stage_complete",
            stage="self_critique",
            tier=tier,
            duration_ms=timings.get("self_critique", 0.0),
            revised=(final_answer != raw_answer),
        )

        if stream:
            async def _answer_stream() -> AsyncGenerator[str, None]:
                chunk_size = 20
                for i in range(0, len(final_answer), chunk_size):
                    yield final_answer[i:i + chunk_size]
                    await asyncio.sleep(0)
            return _answer_stream(), model_used

        return final_answer, model_used

    async def run(
        self,
        query: str,
        provider_filter: str | None = None,
        tier: str = "Pro",
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
        from llm.thinking import default_thinking_for_tier

        if thinking_level is None:
            thinking_level = default_thinking_for_tier(tier)

        citation_mgr = CitationManager()
        timings: dict[str, float] = {}

        # Stage 1 — Plan & Route
        if emit_event:
            if getattr(self.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("plan_route", "Planning retrieval strategy...", "start"))
            else:
                await emit_event({"status": "Planning retrieval strategy..."})

        plan = await self._plan_and_route(query, tier)
        timings["plan_route"] = plan.pop("_timing_ms", 0.0)

        if emit_event and getattr(self.settings, "enable_structured_stage_events", True):
            await emit_event(_stage_event("plan_route", "Planning retrieval strategy complete", "complete", elapsed_ms=timings["plan_route"]))

        classification = QueryClassification(
            intent=plan["intent"],
            routes=plan["routes"],
            providers=plan["providers"],
            services=[],
            categories=[],
            confidence=plan["confidence"],
            reasoning="Agentic plan",
            needs_internet=plan["needs_internet"],
        )
        if provider_filter and provider_filter.lower() not in classification.providers:
            classification.providers.append(provider_filter.lower())

        # Small-talk safety net: when the planner classifies a gate-missed
        # greeting as chitchat, skip retrieval/grading/critique entirely.
        if plan.get("intent") == "chitchat":
            from api.chat_routes import run_smalltalk_pipeline

            logger.info("chitchat.bypass", pipeline="agentic_rag", tier=tier)
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

        # Stage 2 + Internet search in parallel
        retriever, reranker = agent_pipeline.get_retrieval()

        async def _internet() -> list[dict[str, Any]]:
            if not plan["needs_internet"]:
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
            except asyncio.TimeoutError:
                logger.warning("agentic_rag.internet_search_timeout")
                return []
            except (ConnectionError, OSError) as e:
                logger.warning("agentic_rag.internet_search_network_error", error=str(e))
                return []
            except Exception as e:
                logger.warning("agentic_rag.internet_search_failed", error=str(e))
                return []

        async def _pricing() -> list[dict[str, Any]]:
            if "PRICING" not in plan.get("routes", []):
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
                logger.warning("agentic_rag.pricing_failed", error=str(e))
                return []

        async def _calculator() -> Any:
            if "CALCULATOR" not in plan.get("routes", []):
                return None
            try:
                calc_tool = getattr(agent_pipeline, "calculator", None)
                if calc_tool:
                    math_match = re.search(r"(\d+[\d\s\+\-\*\/\.\(\)]+\d+)", query)
                    if math_match:
                        return await calc_tool.evaluate(math_match.group(1).strip())
            except Exception as e:
                logger.warning("agentic_rag.calculator_failed", error=str(e))
            return None

        if emit_event:
            if getattr(self.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("retrieve", "Searching documentation & evidence...", "start"))
            else:
                await emit_event({"status": "Searching documentation & evidence..."})

        t0_ret = time.perf_counter()
        retrieve_coro = self._hybrid_retrieve(
            query=query,
            sub_queries=plan["sub_queries"],
            retrieval_strategy=plan["retrieval_strategy"],
            provider_filter=provider_filter,
            classification=classification,
            tier=tier,
            retriever=retriever,
            reranker=reranker,
            retrieval_modality=plan.get("retrieval_modality", "hybrid"),
        )

        (chunks, fallback_pass), internet_results, pricing_data, calc_results = await asyncio.gather(
            retrieve_coro, _internet(), _pricing(), _calculator()
        )
        timings["hybrid_retrieve"] = round((time.perf_counter() - t0_ret) * 1000, 2)

        for chunk in chunks:
            m = chunk.metadata
            citation_mgr.register_source(
                source_type="rag",
                url=m.get("url", ""),
                provider=m.get("provider", "cloud"),
                service=m.get("service", ""),
                title=m.get("title", "Cloud Documentation"),
                section=m.get("section", ""),
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

        RAG_RESULTS_COUNT.observe(len(chunks))

        # Stage 3 — Grade Evidence
        if emit_event:
            if getattr(self.settings, "enable_structured_stage_events", True):
                await emit_event(_stage_event("retrieve", "Documentation retrieved", "complete", elapsed_ms=timings["hybrid_retrieve"]))
                await emit_event(_stage_event("grade_evidence", "Grading evidence quality...", "start"))
            else:
                await emit_event({"status": "Grading evidence quality..."})

        t0_grade = time.perf_counter()
        graded_chunks = await self._grade_evidence(query, chunks, tier)

        # Stage 3b — Iterative Query Refinement (triggered when evidence sufficiency is below threshold)
        if getattr(self.settings, "enable_iterative_query_refinement", True):
            is_sufficient, max_score, gap_reason = self._evaluate_sufficiency(graded_chunks, plan)
            if not is_sufficient and getattr(self.settings, "agentic_max_refinement_hops", 1) > 0:
                logger.info(
                    "agentic_rag.refinement_triggered",
                    max_score=max_score,
                    gap_reason=gap_reason,
                    tier=tier,
                )
                t0_refine = time.perf_counter()
                refined_query = await self._refine_query(query, plan, graded_chunks, tier)
                if refined_query and refined_query.strip() != query.strip():
                    refine_chunks, _ = await self._hybrid_retrieve(
                        query=refined_query,
                        sub_queries=[],
                        retrieval_strategy="narrow",
                        provider_filter=provider_filter,
                        classification=classification,
                        tier=tier,
                        retriever=retriever,
                        reranker=reranker,
                        retrieval_modality=plan.get("retrieval_modality", "hybrid"),
                    )
                    if refine_chunks:
                        graded_refine = await self._grade_evidence(refined_query, refine_chunks, tier)
                        seen_cids = {c.chunk_id for c in graded_chunks}
                        for c in graded_refine:
                            if c.chunk_id not in seen_cids:
                                seen_cids.add(c.chunk_id)
                                graded_chunks.append(c)
                                m = c.metadata
                                citation_mgr.register_source(
                                    source_type="rag",
                                    url=m.get("url", ""),
                                    provider=m.get("provider", "cloud"),
                                    service=m.get("service", ""),
                                    title=m.get("title", "Cloud Documentation"),
                                    section=m.get("section", ""),
                                )
                        graded_chunks = sorted(
                            graded_chunks,
                            key=lambda x: float(x.metadata.get("grade_score", x.score)),
                            reverse=True,
                        )[:self.settings.retrieval_top_k]
                timings["iterative_refinement"] = round((time.perf_counter() - t0_refine) * 1000, 2)

        graded_chunks = self._expand_hierarchical_context(graded_chunks)
        timings["grade_evidence"] = round((time.perf_counter() - t0_grade) * 1000, 2)

        if emit_event and getattr(self.settings, "enable_structured_stage_events", True):
            await emit_event(_stage_event("grade_evidence", "Evidence graded", "complete", elapsed_ms=timings["grade_evidence"]))

        # Stage 4 — Generate & Self-Critique
        answer_or_stream, model_used = await self._generate_and_verify(
            query=query,
            graded_chunks=graded_chunks,
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
        )

        sources_dump = [s.model_dump() for s in citation_mgr.get_sources()]

        logger.info(
            "pipeline.complete",
            pipeline_type="agentic_rag",
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
            routes=plan["routes"],
            confidence=classification.confidence,
            classification=classification.model_dump(),
            model_used=model_used,
            pipeline_timings=timings,
            fallback_pass=fallback_pass,
            pipeline_type="agentic_rag",
        )
