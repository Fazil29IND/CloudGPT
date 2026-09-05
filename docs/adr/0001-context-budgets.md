# ADR 0001: Unified Context Budgets and Attention Engineering

## Status
Accepted

## Context
CloudGPT serves AWS, GCP, and Azure multi-cloud architecture and pricing queries.
Previously, the context assembly pipeline suffered from:
1. **Unbounded Prompt Growth**: Context budget was only enforced on RAG chunks, leaving attachments, web search, pricing API data, calculator results, and history unconstrained. Under heavy multimodal or multi-turn usage, prompts could overflow model context windows or cause high latency and excessive token costs.
2. **History Truncation Blind Spot**: Session history was retrieved using a fixed message count (`limit=6`), without awareness of token size or rolling compaction. Older context was abruptly dropped without summarization.
3. **Cross-Source Redundancy**: RAG chunks, always-on internet results, and route-based web results frequently duplicated canonical documentation URLs, wasting context tokens.
4. **Attention Degradation ("Lost in the Middle")**: System instructions, security guardrails, and the user's specific request were placed before large blobs of untrusted reference data, leading to instruction dilution and susceptibility to prompt injection from retrieved sources.

## Decision
1. **Global Prompt Budget by Tier**:
   Enforce total prompt ceilings:
   - Free (Lite): 4,000 tokens
   - Pro (Core): 7,000 tokens
   - Max (Apex / Developer): 12,000 tokens
   Implemented via `TokenBudget` in `llm/context_builder.py` with priority allocation and best-fit packing.
2. **Compact Serialisation**:
   Use `json.dumps(..., separators=(',', ':'))` for all structured pricing, API, and calculator tool outputs to eliminate JSON whitespace overhead.
3. **Cross-Source Canonical URL Deduplication**:
   Track canonical URLs (`https://...` stripped of trailing slashes and query fragments) across RAG, internet, and web search results. Deduplicate redundant chunks before prompt packing.
4. **Attention Engineering & Guardrails**:
   - Primacy: Untrusted data guardrail explicitly placed at the top of context blocks.
   - Attachments & Constraints: Formatting and negative constraints placed immediately after attachment payloads.
   - Recency Reinforcement: `CURRENT USER REQUEST: {query}` placed at the very end of the prompt context immediately before LLM generation.
5. **Session History Budgeting & Rolling Compaction**:
   Enforce a dedicated `history_token_budget` (1,500 tokens). Turns exceeding the budget are trimmed whole-message newest-first, and dropped turns are summarized into persistent `session_summaries` via a fast sub-model (`compact_history_turns`).
6. **Cross-Session Durable User Memory**:
   Store verified user preferences and infrastructure facts in `user_memory`, cached in Redis and injected within a bounded 300-token section.

## Consequences
- **Positive**:
  - Deterministic prompt sizes, eliminating window overflow across all model families.
  - Drastically lower token usage and reduced time-to-first-token (TTFT).
  - High fidelity long conversations with rolling memory instead of hard cutoff at 6 turns.
  - Zero redundant URL citations across search and RAG sources.
- **Observability**:
  - Prometheus metrics (`cloudgpt_prompt_section_tokens`, `cloudgpt_prompt_total_tokens`, `cloudgpt_prompt_budget_drops_total`) track all prompt sections in real time.
