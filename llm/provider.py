"""
CloudGPT LLM Provider Module.

Multi-provider LLM abstraction with native reasoning ("thinking") support:

- Gemini 2.0 Flash (all tiers, all roles) via google-genai
- Claude / OpenAI kept as dormant fallbacks (no keys required)

Thinking levels (Low | Medium | High | Max) map to provider-native reasoning
budgets (see llm/thinking.py). Thought text is normalised into
``<think>...</think>`` blocks in both streaming and non-streaming output so
the chat pipeline and UI can separate reasoning from the final answer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from config import get_settings
from llm.thinking import THINK_END, THINK_START, profile_for
from metrics import PIPELINE_TIMEOUTS_TOTAL, SUBMODEL_CALLS_TOTAL

logger = logging.getLogger(__name__)

# Models whose APIs accept a thinking/reasoning budget.
_GEMINI_THINKING_PREFIXES = (
    "gemini-3.5",
    "gemini-3.6",
    "gemini-3.7",
    "gemini-3.8",
    "gemini-3.9",
    "gemini-2.5",
    "gemini-2.0-flash-thinking",
)
_OPENAI_REASONING_PREFIXES = ("o1", "o3", "o4")

# Cooldown: models that just failed with overload/quota errors are
# skipped for a cooldown window so user requests don't stall on retries.
# State is process-local for the hot path and synced through Redis
# (core/cooldown_sync.py) so all uvicorn workers observe the same trips.
_MODEL_COOLDOWN_SECONDS = 300.0
_model_cooldowns: dict[str, float] = {}
_last_cooldown_refresh = 0.0


class GeminiQuotaExceeded(Exception):
    """Raised when the Gemini API rejects a request because the quota /
    free-tier allowance for the API key is exhausted (HTTP 429 /
    RESOURCE_EXHAUSTED).

    Deliberately NOT walked down the fallback chain: every model in the chain
    shares the same API key, so a project-level quota exhaustion fails on all
    of them. Callers should surface a dedicated quota message instead of the
    generic error.
    """

    def __init__(self, model_name: str, cause: Exception | None = None) -> None:
        self.model_name = model_name
        self.cause = cause
        super().__init__(
            f"Gemini Free Tier Quota has Reached its Limit (model={model_name})"
        )


def _is_thinking_gemini(model: str) -> bool:
    m = model.lower()
    if "flash-lite" in m or "embedding" in m:
        return False
    if "-thinking" in m:
        return True
    return m.startswith(_GEMINI_THINKING_PREFIXES)


def _is_reasoning_openai(model: str) -> bool:
    return model.lower().startswith(_OPENAI_REASONING_PREFIXES)


def _model_in_cooldown(model: str) -> bool:
    until = _model_cooldowns.get(model)
    if until is None:
        return False
    if until <= time.monotonic():
        _model_cooldowns.pop(model, None)
        return False
    return True


def _trip_model_cooldown(model: str) -> None:
    global _last_cooldown_refresh
    _model_cooldowns[model] = time.monotonic() + _MODEL_COOLDOWN_SECONDS
    from core.cooldown_sync import persist_cooldown, schedule_background

    schedule_background(lambda: persist_cooldown("llmmodelcooldown", model, _MODEL_COOLDOWN_SECONDS))
    # Other workers may have tripped cooldowns while this process was idle.
    _last_cooldown_refresh = 0.0


async def _refresh_cooldowns_from_redis() -> None:
    """Merge cooldowns persisted by other workers into the local dict.

    Throttled to one Redis read per second; remote state only ever extends a
    local cooldown (a later local expiry always wins)."""
    global _last_cooldown_refresh
    now = time.monotonic()
    if now - _last_cooldown_refresh < 1.0:
        return
    _last_cooldown_refresh = now
    try:
        from core.cooldown_sync import fetch_cooldowns

        remote = await fetch_cooldowns("llmmodelcooldown")
    except Exception:
        return
    for model, remaining in remote.items():
        expires_at = now + remaining
        current = _model_cooldowns.get(model)
        if current is None or current < expires_at:
            _model_cooldowns[model] = expires_at


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, Any]],
        stream: bool = False,
        temperature: float = 0.2,
        thinking_level: str | None = None,
        max_output_tokens: int | None = None,
    ) -> str | AsyncIterator[str]:
        """Generate a response from the LLM.

        Args:
            messages: List of message dictionaries (role, content).
            stream: Whether to stream the response as an async iterator.
            temperature: Sampling temperature.
            thinking_level: Optional reasoning level (Low|Medium|High|Max).
                Providers with native reasoning apply the matching budget;
                others ignore it.
            max_output_tokens: Optional cap on generated answer tokens.

        Returns:
            The full generated string if stream=False, otherwise an async
            iterator of tokens (thought text wrapped in <think> tags).
        """
        ...

    @abstractmethod
    async def classify(self, query: str, system_prompt: str) -> str:
        """Use the LLM for structured classification (query routing)."""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
#  Gemini Provider (all tiers — google-genai)
# ═══════════════════════════════════════════════════════════════════════════════


def _safe_float(val: Any, default: float) -> float:
    if val is None or hasattr(val, "_mock_name") or hasattr(val, "_mock_methods"):
        return default
    try:
        f = float(val)
        return f if f > 0 else default
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int) -> int:
    if val is None or hasattr(val, "_mock_name") or hasattr(val, "_mock_methods"):
        return default
    try:
        i = int(val)
        return i if i > 0 else default
    except (TypeError, ValueError):
        return default


def _safe_bool(val: Any, default: bool) -> bool:
    if val is None or hasattr(val, "_mock_name") or hasattr(val, "_mock_methods"):
        return default
    return bool(val)


class GeminiProvider(LLMProvider):
    """Google Gemini provider with thinking budgets and model fallback.

    ``models`` is an ordered list of Gemini model identifiers; when a model is
    rejected by the API (unknown/deprecated/not-accessible) the next one is
    tried automatically before surfacing the error.
    """

    def __init__(self, model: str | None = None, models: list[str] | None = None) -> None:
        self.settings = get_settings()
        if not self.settings.has_gemini:
            raise ValueError("Gemini API key is missing. Please set GEMINI_API_KEY.")

        from google import genai
        from google.genai import types

        self.per_request_timeout = _safe_float(getattr(self.settings, "gemini_request_timeout_seconds", None), 25.0)
        raw_first_chunk = getattr(self.settings, "gemini_first_chunk_timeout_seconds", None)
        if raw_first_chunk is not None and not hasattr(raw_first_chunk, "_mock_name"):
            self.first_chunk_timeout = _safe_float(raw_first_chunk, 6.0)
        self.total_deadline = _safe_float(getattr(self.settings, "gemini_total_fallback_deadline_seconds", None), 25.0)
        stream_timeout_raw = getattr(self.settings, "llm_stream_timeout_seconds", None)
        self.stream_timeout = _safe_float(stream_timeout_raw, 120.0)

        # Build HTTP client with stream_timeout so thinking requests and streams
        # do not abort at the transport layer before the model finishes thinking.
        timeout_ms = int(self.stream_timeout * 1000)
        self.client = genai.Client(
            api_key=self.settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
        if models:
            chain = list(models)
            if model and model not in chain:
                chain.insert(0, model)
        else:
            default_chain = [
                self.settings.gemini_model,
                getattr(self.settings, "gemini_model_fallback_1", "gemini-3.7-flash"),
                getattr(self.settings, "gemini_model_fallback_2", "gemini-3.6-flash"),
                getattr(self.settings, "gemini_model_fallback_3", "gemini-3.5-flash"),
            ]
            chain = [model] + [m for m in default_chain if m != model] if model else default_chain

        self.model_chain = list(dict.fromkeys([m for m in chain if m]))  # de-dup, keep order
        self.model = self.model_chain[0]
        raw_safety_net = getattr(self.settings, "gemini_model_safety_net", None)
        if isinstance(raw_safety_net, str) and raw_safety_net.strip() and not hasattr(raw_safety_net, "_mock_name"):
            self.safety_net = raw_safety_net.strip()
        else:
            self.safety_net = None

        # Provider-reported token usage of the most recent generate() call.
        # Populated from usage_metadata when the API returns it; callers use it
        # for true-input billing. Never raises — billing falls back to estimates.
        self.last_usage: dict[str, int | None] = {"prompt_tokens": None, "total_tokens": None}
        logger.info("GeminiProvider initialized: models=%s", self.model_chain)

    def _get_active_candidates(self, start_model: str | None = None) -> list[str]:
        """Return ordered candidates starting from start_model, filtering out
        models currently in circuit-breaker cooldown unless all are in cooldown."""
        chain = list(self.model_chain)
        safety_net = getattr(self, "safety_net", None)
        if isinstance(safety_net, str) and safety_net and not hasattr(safety_net, "_mock_name"):
            if safety_net not in chain:
                chain.append(safety_net)

        start = chain.index(start_model) if (start_model and start_model in chain) else 0
        slice_chain = chain[start:]
        active = [m for m in slice_chain if not _model_in_cooldown(m)]
        return active if active else slice_chain

    def _next_model(self, failed: str) -> str | None:
        try:
            idx = self.model_chain.index(failed)
        except ValueError:
            return None
        return self.model_chain[idx + 1] if idx + 1 < len(self.model_chain) else None

    @staticmethod
    def _is_quota_error(exc: Exception) -> bool:
        """Quota / rate-limit exhaustion (429 / RESOURCE_EXHAUSTED).

        Checked BEFORE ``_is_model_error``: a quota failure must not walk the
        model chain because every model shares one API key, so the next model
        would fail identically and only burn latency.
        """
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "429",
                "quota",
                "resource_exhausted",
                "resource has been exhausted",
                "rate limit",
            )
        )

    @staticmethod
    def _is_model_error(exc: Exception) -> bool:
        text = str(exc).lower()
        markers = (
            "not found",
            "404",
            "is not supported",
            "unsupported",
            "does not exist",
            "no access",
            "permission denied",
            "429",
            "quota",
            "503",
            "504",
            "deadline_exceeded",
            "timeout",
            "unavailable",
            "overloaded",
            "high demand",
            "internal error",
            "500",
            "invalid_argument",
            "invalid argument",
            "400",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_overload_error(exc: Exception) -> bool:
        """Transient capacity errors that justify tripping the model cooldown."""
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "503",
                "504",
                "unavailable",
                "overloaded",
                "high demand",
                "spikes in demand",
                "temporary",
                "temporarily",
            )
        )

    def _convert_messages(
        self, messages: list[dict[str, Any]], thinking_level: str | None = None
    ) -> tuple[str | None, list[Any]]:
        import base64
        from google.genai import types

        system_instruction = None
        gemini_contents = []
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")
            if role == "system":
                system_instruction = content
            else:
                gemini_role = "user" if role == "user" else "model"
                parts = []
                if content:
                    parts.append(types.Part.from_text(text=content))

                # Multimodal attachments (images, audio, video)
                images = list(msg.get("images") or [])
                media_parts: list[dict[str, Any]] = []
                attachments = msg.get("attachments") or []
                for att in attachments:
                    if not isinstance(att, dict):
                        continue
                    b64_data = att.get("image_base64") or att.get("raw_bytes_base64")
                    c_type = att.get("content_type", "")
                    if att.get("is_image") and b64_data:
                        media_parts.append({
                            "base64": b64_data,
                            "mime_type": c_type or "image/png",
                        })
                    elif (att.get("is_audio") or c_type.startswith("audio/")) and b64_data:
                        media_parts.append({
                            "base64": b64_data,
                            "mime_type": c_type or "audio/mp3",
                        })
                    elif (att.get("is_video") or c_type.startswith("video/")) and b64_data:
                        media_parts.append({
                            "base64": b64_data,
                            "mime_type": c_type or "video/mp4",
                        })
                    elif (att.get("is_pdf") or c_type == "application/pdf" or (att.get("filename") or "").lower().endswith(".pdf")) and b64_data:
                        media_parts.append({
                            "base64": b64_data,
                            "mime_type": "application/pdf",
                        })

                for img in images:
                    if isinstance(img, dict):
                        media_parts.append(img)

                for item in media_parts:
                    try:
                        raw_bytes = item.get("data")
                        if not raw_bytes and item.get("base64"):
                            raw_bytes = base64.b64decode(item["base64"])
                        mime_type = item.get("mime_type") or item.get("content_type") or "application/octet-stream"
                        if raw_bytes:
                            parts.append(types.Part.from_bytes(data=raw_bytes, mime_type=mime_type))
                    except Exception as media_err:
                        logger.warning("Failed to construct multimodal media part for Gemini: %s", media_err)

                if not parts:
                    parts.append(types.Part.from_text(text=""))

                gemini_contents.append(
                    types.Content(
                        role=gemini_role,
                        parts=parts,
                    )
                )

        return system_instruction, gemini_contents

    def _build_config(
        self,
        model: str,
        temperature: float,
        system_instruction: str | None,
        thinking_level: str | None,
        max_output_tokens: int | None,
        is_fallback_candidate: bool = False,
    ) -> Any:
        from google.genai import types

        effective_sys_inst = system_instruction
        enforce_support = _safe_bool(getattr(self.settings, "fallback_enforce_support_persona", None), True)
        if is_fallback_candidate and enforce_support:
            if system_instruction and (
                "Required Output Structure" in system_instruction
                or "CloudGPT Apex" in system_instruction
                or "CloudGPT Core" in system_instruction
            ):
                from llm.system_prompts import get_fallback_system_prompt
                effective_sys_inst = get_fallback_system_prompt()

        effective_temp = _safe_float(temperature, 0.2)
        effective_max_tokens = _safe_int(max_output_tokens, 4096) if max_output_tokens is not None else None

        if is_fallback_candidate:
            raw_cap = getattr(self.settings, "fallback_max_output_tokens", None)
            fb_token_cap = _safe_int(raw_cap, 400)
            effective_max_tokens = min(effective_max_tokens or fb_token_cap, fb_token_cap)
            effective_temp = min(effective_temp, 0.15)

        kwargs: dict[str, Any] = {
            "temperature": effective_temp,
            "system_instruction": effective_sys_inst,
        }

        disable_thinking = is_fallback_candidate and _safe_bool(
            getattr(self.settings, "fallback_disable_thinking", None), True
        )

        if thinking_level and not disable_thinking:
            profile = profile_for(thinking_level)
            if profile.enabled and _is_thinking_gemini(model):
                budget = min(max(profile.budget_tokens, 0), 65535)
                kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_budget=budget,
                    include_thoughts=True,
                )
                effective_max = max(effective_max_tokens or 4096, budget + 8192)
                kwargs["max_output_tokens"] = effective_max
        elif disable_thinking and _is_thinking_gemini(model):
            try:
                kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_budget=0,
                    include_thoughts=False,
                )
            except Exception:
                pass
            if effective_max_tokens:
                kwargs["max_output_tokens"] = effective_max_tokens
        elif effective_max_tokens:
            kwargs["max_output_tokens"] = effective_max_tokens

        return types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _render_chunk(chunk: Any, state: dict[str, bool]) -> list[str]:
        """Convert a stream chunk into tokens, tracking <think> tags."""
        out: list[str] = []
        for candidate in getattr(chunk, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", None)
                if not text:
                    continue
                if getattr(part, "thought", False):
                    if not state.get("opened_think", False):
                        out.append(THINK_START)
                        state["opened_think"] = True
                    out.append(text)
                else:
                    if state.get("opened_think", False):
                        out.append(THINK_END)
                        state["opened_think"] = False
                    out.append(text)
        if not out and getattr(chunk, "text", None):
            out.append(chunk.text)
        return out

    async def generate(
        self,
        messages: list[dict[str, Any]],
        stream: bool = False,
        temperature: float = 0.2,
        thinking_level: str | None = None,
        max_output_tokens: int | None = None,
    ) -> str | AsyncIterator[str]:
        await _refresh_cooldowns_from_redis()
        system_instruction, gemini_contents = self._convert_messages(messages, thinking_level=thinking_level)

        if stream:
            return await self._stream_generate(
                model=self.model,
                contents=gemini_contents,
                temperature=temperature,
                system_instruction=system_instruction,
                thinking_level=thinking_level,
                max_output_tokens=max_output_tokens,
            )

        candidates = self._get_active_candidates(self.model)
        total_deadline = _safe_float(getattr(self, "total_deadline", None), 25.0)
        per_req_timeout = _safe_float(getattr(self, "per_request_timeout", None), 25.0)
        deadline = time.monotonic() + total_deadline
        last_exc: Exception | None = None

        for idx, candidate_model in enumerate(candidates):
            rem = deadline - time.monotonic()
            if rem <= 1.0 and idx > 0 and last_exc is None:
                logger.warning("Gemini generation deadline exhausted before candidate '%s'", candidate_model)
                break

            config = self._build_config(
                candidate_model,
                temperature,
                system_instruction,
                thinking_level,
                max_output_tokens,
                is_fallback_candidate=(idx > 0),
            )
            candidate_budget = max(10.0, rem) if (idx > 0 and last_exc is not None) else rem
            per_model_timeout = min(per_req_timeout, max(2.0, candidate_budget))
            try:
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=candidate_model,
                        contents=gemini_contents,
                        config=config,
                    ),
                    timeout=per_model_timeout,
                )
                self.model = candidate_model
                self._record_usage(response)
                return self._render_response(response)
            except Exception as exc:
                last_exc = exc
                _trip_model_cooldown(candidate_model)
                if self._is_quota_error(exc):
                    logger.warning("Gemini candidate '%s' quota limit — failing fast", candidate_model)
                    raise GeminiQuotaExceeded(candidate_model, cause=exc) from exc

                if isinstance(exc, asyncio.TimeoutError):
                    PIPELINE_TIMEOUTS_TOTAL.labels(component="llm_generate").inc()
                    logger.warning(
                        "Gemini candidate '%s' non-stream timeout (>%ss) — cascading",
                        candidate_model, round(per_model_timeout, 1)
                    )
                elif self._is_model_error(exc) or self._is_overload_error(exc):
                    logger.warning("Gemini candidate '%s' error (%s) — cascading", candidate_model, exc)
                else:
                    logger.error("Gemini candidate '%s' unexpected error (%s)", candidate_model, exc)
                continue

        if last_exc and self._is_quota_error(last_exc):
            raise GeminiQuotaExceeded(candidates[-1] if candidates else "gemini", cause=last_exc) from last_exc
        raise last_exc or RuntimeError("Gemini generation failed on all models in chain")

    def _record_usage(self, response: Any) -> None:
        """Capture provider-reported token usage from a generate_content response."""
        try:
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                self.last_usage = {
                    "prompt_tokens": getattr(usage, "prompt_token_count", None),
                    "total_tokens": getattr(usage, "total_token_count", None),
                    # Provider-reported thinking tokens — preferred over budget
                    # estimates for billing margin math on thinking tiers.
                    "thoughts_tokens": getattr(usage, "thoughts_token_count", None),
                }
        except Exception:
            pass

    def _render_response(self, response: Any) -> str:
        """Concatenate thought + text parts, wrapping thoughts in <think> tags."""
        thinking: list[str] = []
        answer: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", None)
                if not text:
                    continue
                if getattr(part, "thought", False):
                    thinking.append(text)
                else:
                    answer.append(text)
        if not thinking and not answer and getattr(response, "text", None):
            answer.append(response.text)
        rendered = ""
        if thinking:
            rendered += f"{THINK_START}{''.join(thinking)}{THINK_END}"
        rendered += "".join(answer)
        return rendered

    async def _stream_generate(
        self,
        model: str,
        contents: list[Any],
        temperature: float,
        system_instruction: str | None,
        thinking_level: str | None = None,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Open a streaming response with ModelFallbackManager:
        - Request deadline (e.g. 25s total)
        - Circuit breaker check (skips cooled models)
        - Fail-fast TTFT timeout (6.0s)
        - First Chunk Buffer (early drop recovery)
        - Multi-model cascading (3.8 -> 3.7 -> 3.6 -> 3.5 -> 3.5-flash-lite)
        """
        candidates = self._get_active_candidates(model)
        total_deadline = _safe_float(getattr(self, "total_deadline", None), 25.0)
        per_req_timeout = _safe_float(getattr(self, "per_request_timeout", None), 25.0)
        stream_timeout = _safe_float(getattr(self, "stream_timeout", None), 120.0)
        first_chunk_timeout = _safe_float(getattr(self, "first_chunk_timeout", None), stream_timeout)

        deadline = time.monotonic() + total_deadline
        last_exc: Exception | None = None

        for idx, candidate_model in enumerate(candidates):
            rem = deadline - time.monotonic()
            if rem <= 1.0 and idx > 0 and last_exc is None:
                logger.warning("Fallback deadline exhausted, skipping candidate %s", candidate_model)
                break

            candidate_budget = max(8.0, rem) if (idx > 0 and last_exc is not None) else rem
            open_timeout = per_req_timeout if candidate_budget >= (per_req_timeout - 0.2) else max(2.0, candidate_budget)
            if hasattr(self, "first_chunk_timeout"):
                ttft_timeout = min(self.first_chunk_timeout, max(1.5, candidate_budget))
            else:
                ttft_timeout = stream_timeout

            try:
                cfg = self._build_config(
                    candidate_model,
                    temperature,
                    system_instruction,
                    thinking_level,
                    max_output_tokens,
                    is_fallback_candidate=(idx > 0),
                )
                response_stream = await asyncio.wait_for(
                    self.client.aio.models.generate_content_stream(
                        model=candidate_model,
                        contents=contents,
                        config=cfg,
                    ),
                    timeout=open_timeout,
                )
                iterator = response_stream.__aiter__()
                first_chunk = await asyncio.wait_for(
                    iterator.__anext__(),
                    timeout=ttft_timeout,
                )

                # --- First Chunk Buffer & Liveness Check ---
                chunk_state = {"opened_think": False}
                buffered_tokens = self._render_chunk(first_chunk, chunk_state)

                # Commit to candidate
                self.model = candidate_model

                async def _token_stream() -> AsyncIterator[str]:
                    nonlocal iterator, candidate_model, idx
                    active_model = candidate_model
                    active_idx = idx
                    emitted_answer_tokens: list[str] = []
                    stream_deadline = time.monotonic() + stream_timeout

                    for tok in buffered_tokens:
                        if not chunk_state.get("opened_think", False) and tok != THINK_END:
                            emitted_answer_tokens.append(tok)
                        yield tok

                    while True:
                        try:
                            while True:
                                rem_chunk = stream_deadline - time.monotonic()
                                if rem_chunk <= 0.5:
                                    raise TimeoutError("Stream generation timeout reached")
                                try:
                                    chunk = await asyncio.wait_for(
                                        iterator.__anext__(),
                                        timeout=min(per_req_timeout, rem_chunk),
                                    )
                                except StopAsyncIteration:
                                    break

                                usage = getattr(chunk, "usage_metadata", None)
                                if usage is not None and getattr(usage, "prompt_token_count", None):
                                    self.last_usage = {
                                        "prompt_tokens": getattr(usage, "prompt_token_count", None),
                                        "total_tokens": getattr(usage, "total_token_count", None),
                                        "thoughts_tokens": getattr(usage, "thoughts_token_count", None),
                                    }
                                for tok in self._render_chunk(chunk, chunk_state):
                                    if not chunk_state.get("opened_think", False) and tok != THINK_END:
                                        emitted_answer_tokens.append(tok)
                                    yield tok

                            if chunk_state.get("opened_think", False):
                                yield THINK_END
                                chunk_state["opened_think"] = False
                            return

                        except Exception as mid_exc:
                            if isinstance(mid_exc, TimeoutError) and "Stream generation timeout reached" in str(mid_exc):
                                logger.warning("Gemini stream reached max duration (%.1fs)", stream_timeout)
                                if chunk_state.get("opened_think", False):
                                    yield THINK_END
                                    chunk_state["opened_think"] = False
                                return

                            _trip_model_cooldown(active_model)
                            logger.warning(
                                "Gemini candidate '%s' failed mid-stream (%s) — cascading to fallback models",
                                active_model,
                                mid_exc,
                            )
                            if self._is_quota_error(mid_exc):
                                logger.warning("Gemini candidate '%s' quota limit — failing fast", active_model)
                                raise GeminiQuotaExceeded(active_model, cause=mid_exc) from mid_exc

                            # Close open thinking block cleanly if active
                            if chunk_state.get("opened_think", False):
                                yield THINK_END
                                chunk_state["opened_think"] = False

                            # Search remaining candidates in the fallback chain
                            remaining = candidates[active_idx + 1:]
                            fallback_found = False
                            for next_offset, next_model in enumerate(remaining):
                                if _model_in_cooldown(next_model):
                                    continue
                                rem_time = stream_deadline - time.monotonic()
                                if rem_time <= 1.5:
                                    logger.warning("Stream deadline exhausted, skipping fallback candidate %s", next_model)
                                    break
                                fb_budget = max(8.0, rem_time)

                                logger.info(
                                    "Cascading mid-stream from '%s' to fallback candidate '%s'",
                                    active_model,
                                    next_model,
                                )
                                try:
                                    fallback_contents = contents
                                    if emitted_answer_tokens:
                                        partial_ans = "".join(emitted_answer_tokens).strip()
                                        if partial_ans:
                                            try:
                                                from google.genai import types
                                                fallback_contents = list(contents) + [
                                                    types.Content(
                                                        role="model",
                                                        parts=[types.Part.from_text(text=partial_ans)],
                                                    ),
                                                    types.Content(
                                                        role="user",
                                                        parts=[types.Part.from_text(text="Continue directly from where you left off. Do not repeat previous text.")],
                                                    ),
                                                ]
                                            except Exception:
                                                pass

                                    cfg_fb = self._build_config(
                                        next_model,
                                        temperature,
                                        system_instruction,
                                        thinking_level,
                                        max_output_tokens,
                                        is_fallback_candidate=True,
                                    )
                                    fb_stream = await asyncio.wait_for(
                                        self.client.aio.models.generate_content_stream(
                                            model=next_model,
                                            contents=fallback_contents,
                                            config=cfg_fb,
                                        ),
                                        timeout=min(per_req_timeout, max(2.0, fb_budget), 12.0),
                                    )
                                    fb_iter = fb_stream.__aiter__()
                                    fb_first_chunk = await asyncio.wait_for(
                                        fb_iter.__anext__(),
                                        timeout=min(
                                            getattr(self, "first_chunk_timeout", per_req_timeout),
                                            max(1.5, fb_budget),
                                            10.0,
                                        ),
                                    )
                                    fb_buffered = self._render_chunk(fb_first_chunk, chunk_state)
                                    for tok in fb_buffered:
                                        if not chunk_state.get("opened_think", False) and tok != THINK_END:
                                            emitted_answer_tokens.append(tok)
                                        yield tok

                                    iterator = fb_iter
                                    active_model = next_model
                                    active_idx = active_idx + 1 + next_offset
                                    self.model = next_model
                                    fallback_found = True
                                    # Refresh stream deadline for the rescued fallback generation
                                    stream_deadline = max(stream_deadline, time.monotonic() + min(stream_timeout, 60.0))
                                    break
                                except Exception as fb_err:
                                    _trip_model_cooldown(next_model)
                                    err_detail = repr(fb_err) if not str(fb_err).strip() else str(fb_err)
                                    logger.warning("Fallback candidate '%s' failed to start (%s)", next_model, err_detail)
                                    if self._is_quota_error(fb_err):
                                        raise GeminiQuotaExceeded(next_model, cause=fb_err) from fb_err
                                    continue

                            if not fallback_found:
                                if emitted_answer_tokens:
                                    logger.warning(
                                        "Mid-stream exception after tokens were emitted; closing gracefully (cause: %s)",
                                        mid_exc,
                                    )
                                    if chunk_state.get("opened_think", False):
                                        yield THINK_END
                                        chunk_state["opened_think"] = False
                                    yield "\n\n*(Response truncated due to a transient upstream connection issue)*"
                                    return
                                raise mid_exc

                return _token_stream()

            except StopAsyncIteration:
                last_exc = RuntimeError(f"Gemini model '{candidate_model}' returned an empty stream")
                _trip_model_cooldown(candidate_model)
                continue
            except (Exception, asyncio.TimeoutError) as exc:
                last_exc = exc
                _trip_model_cooldown(candidate_model)
                if self._is_quota_error(exc):
                    logger.warning("Gemini candidate '%s' quota limit — failing fast", candidate_model)
                    raise GeminiQuotaExceeded(candidate_model, cause=exc) from exc

                if isinstance(exc, asyncio.TimeoutError):
                    PIPELINE_TIMEOUTS_TOTAL.labels(component="llm_stream").inc()
                    logger.warning(
                        "Gemini candidate '%s' TTFT timeout (>%ss) — cascading",
                        candidate_model, round(ttft_timeout, 1)
                    )
                else:
                    logger.warning(
                        "Gemini candidate '%s' unavailable (%s) — cascading",
                        candidate_model, exc
                    )
                continue

        if last_exc and self._is_quota_error(last_exc):
            raise GeminiQuotaExceeded(candidates[-1] if candidates else "gemini", cause=last_exc) from last_exc
        raise last_exc or RuntimeError("Gemini streaming failed: no model in chain responded")

    async def classify(self, query: str, system_prompt: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]
        system_instruction, gemini_contents = self._convert_messages(messages)

        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=0.0,
            system_instruction=system_instruction,
            response_mime_type="application/json",
        )
        candidates = self._get_active_candidates(self.model)
        for candidate_model in candidates:
            try:
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=candidate_model,
                        contents=gemini_contents,
                        config=config,
                    ),
                    timeout=10.0,
                )
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    logger.info(
                        "Gemini classification tokens: %d total (%s)",
                        response.usage_metadata.total_token_count,
                        candidate_model,
                    )
                return response.text or "{}"
            except Exception as e:
                _trip_model_cooldown(candidate_model)
                logger.warning("Gemini classify on '%s' failed (%s) — trying next", candidate_model, e)
        return "{}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Claude Provider (CORE / APEX PRIMARY — Anthropic extended thinking)
# ═══════════════════════════════════════════════════════════════════════════════


class ClaudeProvider(LLMProvider):
    """Anthropic Claude provider with extended thinking support.

    When a thinking level is supplied, the request enables extended thinking
    with the configured budget. Anthropic requires ``temperature=1`` while
    thinking is enabled and ``max_tokens`` greater than the thinking budget.
    Thought deltas are normalised into ``<think>...</think>`` blocks.
    """

    def __init__(self, model: str | None = None) -> None:
        self.settings = get_settings()
        if not self.settings.has_anthropic:
            raise ValueError(
                "Anthropic API key is missing. Please set ANTHROPIC_API_KEY."
            )

        import anthropic

        self.client = anthropic.AsyncAnthropic(
            api_key=self.settings.anthropic_api_key
        )
        self.model = model or self.settings.anthropic_model
        logger.info("ClaudeProvider initialized: model=%s", self.model)

    def _extract_system(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        system_prompt = ""
        claude_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            else:
                claude_messages.append(
                    {"role": msg["role"], "content": msg["content"]}
                )
        return system_prompt, claude_messages

    @staticmethod
    def _request_params(
        system_prompt: str,
        claude_messages: list[dict[str, Any]],
        temperature: float,
        thinking_level: str | None,
        max_output_tokens: int | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "messages": claude_messages,
        }
        if system_prompt:
            params["system"] = system_prompt

        if thinking_level:
            profile = profile_for(thinking_level)
            if profile.enabled:
                # Thinking requests: temperature locked to 1, room for the
                # reasoning budget plus the visible answer.
                params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": profile.budget_tokens,
                }
                params["temperature"] = 1
                params["max_tokens"] = max(
                    max_output_tokens or 4096, profile.budget_tokens + 8192
                )
                return params

        params["temperature"] = temperature
        params["max_tokens"] = max_output_tokens or 4096
        return params

    async def generate(
        self,
        messages: list[dict[str, Any]],
        stream: bool = False,
        temperature: float = 0.2,
        thinking_level: str | None = None,
        max_output_tokens: int | None = None,
    ) -> str | AsyncIterator[str]:
        system_prompt, claude_messages = self._extract_system(messages)
        params = self._request_params(
            system_prompt, claude_messages, temperature, thinking_level, max_output_tokens
        )
        params["model"] = self.model

        if stream:
            return self._stream_generate(params)

        response = await self.client.messages.create(**params)
        logger.info(
            "Claude token usage: Input=%d, Output=%d",
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        thinking: list[str] = []
        text: list[str] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "thinking":
                thinking.append(getattr(block, "thinking", "") or "")
            elif hasattr(block, "text"):
                text.append(block.text)
        rendered = ""
        if thinking:
            rendered += f"{THINK_START}{''.join(thinking)}{THINK_END}"
        rendered += "".join(text)
        return rendered

    async def _stream_generate(self, params: dict[str, Any]) -> AsyncIterator[str]:
        async with self.client.messages.stream(**params) as stream:
            opened_think = False
            async for event in stream:
                if getattr(event, "type", "") != "content_block_delta":
                    continue
                delta = getattr(event, "delta", None)
                delta_type = getattr(delta, "type", "")
                if delta_type == "thinking_delta":
                    text = getattr(delta, "thinking", "") or ""
                    if text:
                        if not opened_think:
                            yield THINK_START
                            opened_think = True
                        yield text
                elif delta_type == "text_delta":
                    text = getattr(delta, "text", "") or ""
                    if text:
                        if opened_think:
                            yield THINK_END
                            opened_think = False
                        yield text
            if opened_think:
                yield THINK_END

    async def classify(self, query: str, system_prompt: str) -> str:
        messages = [{"role": "user", "content": query}]

        response = await self.client.messages.create(
            model=self.model,
            system=system_prompt,
            messages=messages,
            temperature=0.0,
            max_tokens=4096,
        )
        logger.info(
            "Claude classification tokens: Input=%d, Output=%d",
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        text_blocks = [
            block.text for block in response.content if hasattr(block, "text")
        ]
        return "".join(text_blocks)


# ═══════════════════════════════════════════════════════════════════════════════
#  OpenAI Provider (Apex reasoning candidate + generic fallback)
# ═══════════════════════════════════════════════════════════════════════════════


class OpenAIProvider(LLMProvider):
    """OpenAI provider with o-series reasoning_effort support."""

    def __init__(self, model: str | None = None) -> None:
        self.settings = get_settings()
        if not self.settings.has_openai:
            raise ValueError("OpenAI API key is missing. Please set OPENAI_API_KEY.")

        import openai

        self.client = openai.AsyncOpenAI(api_key=self.settings.openai_api_key)
        self.model = model or self.settings.openai_model
        logger.info("OpenAIProvider initialized: model=%s", self.model)

    async def generate(
        self,
        messages: list[dict[str, Any]],
        stream: bool = False,
        temperature: float = 0.2,
        thinking_level: str | None = None,
        max_output_tokens: int | None = None,
    ) -> str | AsyncIterator[str]:
        if _is_reasoning_openai(self.model):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "stream": stream,
            }
            if thinking_level:
                kwargs["reasoning_effort"] = profile_for(thinking_level).reasoning_effort
            if max_output_tokens:
                kwargs["max_completion_tokens"] = max_output_tokens
            if stream:
                return self._stream_create(kwargs)
            response = await self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""

        if stream:
            return self._stream_generate(messages, temperature)
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            stream=False,
        )
        if response.usage:
            logger.info("OpenAI token usage: %d total tokens", response.usage.total_tokens)
        return response.choices[0].message.content or ""

    async def _stream_create(self, kwargs: dict[str, Any]) -> AsyncIterator[str]:
        response = await self.client.chat.completions.create(**kwargs)
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def _stream_generate(
        self, messages: list[dict[str, Any]], temperature: float
    ) -> AsyncIterator[str]:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def classify(self, query: str, system_prompt: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        if response.usage:
            logger.info(
                "OpenAI classification tokens: %d total", response.usage.total_tokens
            )
        return response.choices[0].message.content or "{}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Factory — tier → ordered provider chain
# ═══════════════════════════════════════════════════════════════════════════════


def get_llm_provider(role: str = "main", tier: str = "Free") -> LLMProvider:
    """Return a GeminiProvider with the correct tier-primary model.

    Fallback chain is the same for every tier:
        gemini-3.8-flash
        → gemini-3.7-flash
        → gemini-3.6-flash
        → gemini-3.5-flash

    Thinking level is set per-request in generate(); the model chain itself
    does not change based on thinking level.
    """
    settings = get_settings()
    t = (tier or "Free").strip().lower()

    if getattr(settings, "enable_tier_model_specialization", False) is True:
        if t in ("max", "apex", "developer", "admin"):
            primary = getattr(settings, "specialized_model_apex", settings.gemini_model_apex)
        elif t in ("pro", "core"):
            primary = getattr(settings, "specialized_model_core", settings.gemini_model_core)
        else:
            primary = getattr(settings, "specialized_model_lite", settings.gemini_model_lite)
    else:
        if t in ("max", "apex", "developer", "admin"):
            primary = settings.gemini_model_apex
        elif t in ("pro", "core"):
            primary = settings.gemini_model_core
        else:
            primary = settings.gemini_model_lite

    chain_raw = [
        primary,
        settings.gemini_model_fallback_1,
        settings.gemini_model_fallback_2,
        settings.gemini_model_fallback_3,
    ]
    seen: set[str] = set()
    chain = [m for m in chain_raw if m and not (m in seen or seen.add(m))]

    try:
        return GeminiProvider(models=chain)
    except ValueError:
        logger.error(
            "Gemini provider unavailable: role=%s tier=%s. Set GEMINI_API_KEY.",
            role, tier,
        )
        raise


def get_sub_model_provider(role: str = "summarizer", tier: str = "Free") -> LLMProvider:
    """Return a GeminiProvider for sub-model roles (router, grader, title, transform).

    Sub-models and agent orchestration components strictly follow the unified
    5-model fallback cascade:
        gemini-3.8-flash
        → gemini-3.7-flash
        → gemini-3.6-flash
        → gemini-3.5-flash
        → gemini-3.5-flash-lite (safety net)
    """
    settings = get_settings()
    sub_primary = getattr(settings, "gemini_model_sub", settings.gemini_model)

    # Full fallback cascade for sub-model & agent orchestration:
    # sub_primary (3.8) → fallback_1 (3.7) → fallback_2 (3.6) → fallback_3 (3.5)
    # The terminal safety net (gemini-3.5-flash-lite) is automatically attached by
    # GeminiProvider._get_active_candidates().
    chain_raw = [
        sub_primary,
        settings.gemini_model_fallback_1,
        settings.gemini_model_fallback_2,
        settings.gemini_model_fallback_3,
    ]
    seen: set[str] = set()
    chain = [m for m in chain_raw if m and not (m in seen or seen.add(m))]

    try:
        provider = GeminiProvider(models=chain)
        logger.info("sub_model_provider.init: role=%s tier=%s models=%s", role, tier, chain)
        return provider
    except ValueError:
        logger.error(
            "Sub-model provider unavailable: role=%s tier=%s. Falling back to tier provider.",
            role, tier,
        )
        return get_llm_provider(role, tier)


def get_evaluator_provider(tier: str = "Free") -> LLMProvider:
    """Return a GeminiProvider for the Answer Evaluator (self-critique) stage.

    Uses gemini_model_evaluator with full fallback chain down to gemini-3.5-flash-lite.
    """
    settings = get_settings()
    evaluator_primary = getattr(settings, "gemini_model_evaluator", settings.gemini_model_fallback_1)

    chain_raw = [
        evaluator_primary,
        settings.gemini_model_fallback_1,
        settings.gemini_model_fallback_2,
        settings.gemini_model_fallback_3,
    ]
    seen: set[str] = set()
    chain = [m for m in chain_raw if m and not (m in seen or seen.add(m))]

    try:
        provider = GeminiProvider(models=chain)
        logger.info("evaluator_provider.init: tier=%s models=%s", tier, chain)
        return provider
    except ValueError:
        logger.error(
            "Evaluator provider unavailable: tier=%s. Falling back to sub-model provider.",
            tier,
        )
        return get_sub_model_provider("verifier", tier)


async def _classify_with_retry(
    provider: LLMProvider,
    query: str,
    system_prompt: str,
    role: str,
    max_retries: int | None = None,
) -> str:
    """Call provider.classify() with retry on malformed JSON.

    On each attempt the raw response is returned as-is. The caller is
    responsible for schema validation; this function only retries on
    json.JSONDecodeError or empty-string responses.

    Args:
        provider: Any LLMProvider instance.
        query: The user query or grading input.
        system_prompt: The classification system prompt.
        role: Label for Prometheus metrics (router | grader | transform).
        max_retries: Override for settings.structured_output_max_retries.

    Returns:
        The raw JSON string from the first successful parse attempt.

    Raises:
        ValueError: All retries exhausted with no valid JSON response.
    """
    import json

    from config import get_settings
    from metrics import STRUCTURED_OUTPUT_ATTEMPTS_TOTAL, STRUCTURED_OUTPUT_FAILURES_TOTAL

    settings = get_settings()
    retries = max_retries if max_retries is not None else settings.structured_output_max_retries
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        STRUCTURED_OUTPUT_ATTEMPTS_TOTAL.labels(role=role).inc()
        SUBMODEL_CALLS_TOTAL.labels(role=role).inc()
        try:
            raw = await provider.classify(query=query, system_prompt=system_prompt)
            if not raw or not raw.strip():
                raise ValueError("Empty response from provider")
            # Validate that it parses as JSON — do not return garbage downstream
            json.loads(raw)
            return raw
        except (json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            failure_reason = "json_decode" if isinstance(exc, json.JSONDecodeError) else "empty"
            STRUCTURED_OUTPUT_FAILURES_TOTAL.labels(role=role, failure_reason=failure_reason).inc()
            logger.warning(
                "structured_output.retry role=%s attempt=%d max_retries=%d error=%s",
                role,
                attempt + 1,
                retries,
                str(exc),
            )
        except asyncio.TimeoutError as exc:
            last_exc = exc
            STRUCTURED_OUTPUT_FAILURES_TOTAL.labels(role=role, failure_reason="timeout").inc()
            logger.warning("structured_output.timeout role=%s attempt=%d", role, attempt + 1)
            break  # timeout is not retryable

    raise ValueError(
        f"structured_output._classify_with_retry exhausted {retries} retries for role={role}. "
        f"Last error: {last_exc}"
    )


MODEL_INPUT_LIMITS: dict[str, int] = {
    "gemini": 1_000_000,
    "claude": 200_000,
    "gpt-4": 128_000,
    "o1": 200_000,
    "o3": 200_000,
    "o4": 200_000,
    "deepseek": 64_000,
    "qwen": 32_000,
    "kimi": 128_000,
}

MODEL_OUTPUT_LIMITS: dict[str, int] = {
    "gemini-2.5-pro": 65_536,
    "gemini-2.5-flash": 65_536,
    "gemini": 8_192,
    "claude-3-7": 64_000,
    "claude-3-5": 8_192,
    "claude": 8_192,
    "o1": 65_536,
    "o3": 100_000,
    "o4": 100_000,
    "gpt-4o": 16_384,
    "gpt-4": 8_192,
    "deepseek-r1": 8_192,
    "deepseek": 8_192,
    "qwen": 8_192,
}


def get_model_input_limit(model_name: str | None) -> int:
    """Return max input context window limit for a model family."""
    if not model_name:
        return 128_000
    m = model_name.lower()
    for prefix, limit in MODEL_INPUT_LIMITS.items():
        if prefix in m:
            return limit
    return 32_000


def get_model_output_reservation(model_name: str | None, thinking_budget: int = 0) -> int:
    """
    Dynamically reserves generation headroom based on target model family
    and active thinking budget to prevent prompt context from crowding out output.
    """
    if not model_name:
        return max(4096, 4096 + thinking_budget)
    m = model_name.lower()
    base_res = 4096
    for prefix, limit in MODEL_OUTPUT_LIMITS.items():
        if prefix in m:
            base_res = min(limit, max(4096, limit // 4))
            break
    return max(base_res, 4096 + thinking_budget)


def calculate_effective_prompt_budget(
    tier_budget: int,
    model_name: str | None = None,
    max_output_tokens: int | None = 4096,
    thinking_budget: int = 0,
    tier: str | None = None,
    has_attachments: bool = False,
    is_deep_workload: bool = False,
) -> int:
    """
    Calculate effective prompt budget ceiling taking into account:
    1. Base tier budget ceiling (Free: 4k-8k, Pro: 8k-32k, Max: 16k-64k, Developer: full model limit).
    2. Dynamic output token headroom reserved per model family and thinking budget.
    3. Model-specific context window input limits (e.g. Gemini 1M, Claude 200k, GPT-4 128k).
    4. For Developer tier, ceiling expands directly to the maximum physical model limit.
    """
    settings = get_settings()
    effective_budget = tier_budget

    # Dynamically calculate output reservation if not explicitly provided or default 4096
    if max_output_tokens is None or max_output_tokens == 4096:
        output_reservation = get_model_output_reservation(model_name, thinking_budget)
    else:
        output_reservation = max_output_tokens + thinking_budget

    tier_norm = tier.capitalize() if tier else ""

    # Developer Tier: Unlimited context capability scaling up to physical model input limit!
    if tier_norm == "Developer":
        model_limit = get_model_input_limit(model_name)
        return max(1000, model_limit - output_reservation)

    if getattr(settings, "enable_dynamic_context_scaling", True) and tier:
        if tier_norm in ("Pro", "Max") and (has_attachments or is_deep_workload):
            if tier_norm == "Pro":
                expanded_cap = getattr(settings, "prompt_budget_pro_expanded", 32000)
            else:
                expanded_cap = getattr(settings, "prompt_budget_max_expanded", 64000)
            effective_budget = max(effective_budget, expanded_cap)

    model_limit = get_model_input_limit(model_name)
    headroom = max(1000, model_limit - output_reservation)
    return min(effective_budget, headroom)



