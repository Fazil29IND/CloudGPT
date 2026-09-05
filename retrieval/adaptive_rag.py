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
import hashlib
import json
import re
import time
from typing import Any, AsyncGenerator

import structlog

from citations.citation_manager import CitationManager
from config import get_settings
from core.llm_cache import get_cached_retrieval_result, set_cached_retrieval_result
from llm.provider import get_sub_model_provider
from llm.system_prompts import QUERY_TRANSFORM_PROMPT
from metrics import RAG_STAGE_DURATION_SECONDS, RAG_RESULTS_COUNT, PIPELINE_TIMEOUTS_TOTAL
from retrieval.hybrid import RetrievalResult
from router.query_router import QueryClassification, provider_aliases

logger = structlog.get_logger(__name__)

STOPWORDS = {"a", "an", "the", "is", "are", "of", "in", "for", "to", "and", "or", "with", "on", "at", "by"}
RERANK_COMPRESS_THRESHOLD = 0.6


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

        identity: dict[str, Any] = {
            "rewritten_query": query,
            "expanded_queries": [query],
            "hyde_passage": query,
        }

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

            transformed = {
                "rewritten_query": rewritten,
                "expanded_queries": expanded,
                "hyde_passage": hyde,
            }
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
        transformed["_timing_ms"] = round(elapsed * 1000, 2)
        RAG_STAGE_DURATION_SECONDS.labels(stage="transform_query", tier=tier).observe(elapsed)
        logger.info(
            "pipeline.stage_complete",
            stage="transform_query",
            tier=tier,
            duration_ms=transformed["_timing_ms"],
            expanded_count=len(transformed["expanded_queries"]),
        )
        return transformed

    async def _multi_query_retrieve(
        self,
        original_query: str,
        transformed: dict[str, Any],
        provider_filter_dict: dict[str, Any] | None,
        classification: QueryClassification,
        tier: str,
        retriever: Any,
        reranker: Any,
    ) -> tuple[list[RetrievalResult], str]:
        t0 = time.perf_counter()
        prov_key = provider_filter_dict.get("provider") if provider_filter_dict else "all"
        cache_query_key = f"adaptive:{hashlib.sha256(original_query.encode()).hexdigest()[:16]}:{prov_key}:{tier}"

        try:
            cached = await get_cached_retrieval_result(cache_query_key, prov_key)
            if cached:
                elapsed = time.perf_counter() - t0
                RAG_STAGE_DURATION_SECONDS.labels(stage="adaptive_retrieve", tier=tier).observe(elapsed)
                return cached, "cached"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("adaptive_retrieve.cache_check_error", error=str(e))

        query_strings = list(
            dict.fromkeys([transformed["rewritten_query"]] + transformed["expanded_queries"])
        )[:4]

        tasks = [
            retriever.retrieve(q, top_k=self.settings.retrieval_top_k, filters=provider_filter_dict)
            for q in query_strings
        ]

        from api.chat_routes import pipeline as agent_pipeline

        async def _hyde_search() -> list[RetrievalResult]:
            try:
                if not agent_pipeline.embedding_engine or not agent_pipeline.pinecone_manager:
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
                return [
                    RetrievalResult(
                        chunk_id=str(r.get("id", "")),
                        text=r.get("metadata", {}).get("text", ""),
                        score=max(0.0, min(1.0, float(r.get("score", 0.0)) * 0.8)),
                        metadata={k: v for k, v in r.get("metadata", {}).items() if k != "text"},
                    )
                    for r in results
                ]
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

        for batch in all_results:
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

        try:
            await set_cached_retrieval_result(
                cache_query_key, candidates, prov_key, self.settings, ttl_seconds=21600
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

    def _compress_chunk(self, query: str, chunk: RetrievalResult) -> RetrievalResult:
        """Extract the most query-relevant sentences from a high-scoring chunk."""
        if chunk.score < RERANK_COMPRESS_THRESHOLD:
            chunk.metadata["compressed"] = False
            return chunk

        original_text = chunk.text
        original_len = len(original_text)

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
    ) -> list[RetrievalResult]:
        t0 = time.perf_counter()
        if reranker and candidates:
            reranked = await asyncio.to_thread(
                reranker.rerank, query, candidates, self.settings.rerank_top_k
            )
        else:
            reranked = candidates[:self.settings.rerank_top_k]

        compressed = [self._compress_chunk(query, chunk) for chunk in reranked]

        elapsed = time.perf_counter() - t0
        RAG_STAGE_DURATION_SECONDS.labels(stage="rerank_compress", tier=tier).observe(elapsed)
        logger.info(
            "pipeline.stage_complete",
            stage="rerank_compress",
            tier=tier,
            duration_ms=round(elapsed * 1000, 2),
            compressed_count=sum(1 for c in compressed if c.metadata.get("compressed")),
            total_count=len(compressed),
        )
        return compressed

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
    ) -> tuple[str | AsyncGenerator[str, None], str]:
        from api.chat_routes import _build_pipeline_messages, generate_with_fallback
        from llm.provider import calculate_effective_prompt_budget

        rag_results = [
            {
                "provider": c.metadata.get("provider", "cloud"),
                "service": c.metadata.get("service", ""),
                "section": c.metadata.get("section", ""),
                "url": c.metadata.get("url", ""),
                "content": c.text,
                "title": c.metadata.get("title", ""),
            }
            for c in compressed_chunks
        ]

        tier_context_budget = {
            "Free": getattr(self.settings, "prompt_budget_free", self.settings.context_tokens_free),
            "Pro": getattr(self.settings, "prompt_budget_pro", self.settings.context_tokens_pro),
            "Max": getattr(self.settings, "prompt_budget_max", self.settings.context_tokens_max),
        }.get(tier, self.settings.context_tokens_max) if getattr(self.settings, "enable_context_budget", True) else None

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

        for chunk in candidates:
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
        compressed = await self._rerank_and_compress(query, candidates, tier, reranker)
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

        # Stage 4 — Generate
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
        )

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
        )
