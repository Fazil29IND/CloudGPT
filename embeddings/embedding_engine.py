import asyncio
import logging
import math
import re
import time
import zlib
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

# Circuit breaker for Google Gemini Embedding API
_GEMINI_EMBED_COOLDOWN_SECONDS = 300.0
_gemini_embed_cooldown_until: float = 0.0


def is_embed_in_cooldown() -> bool:
    global _gemini_embed_cooldown_until
    return time.monotonic() < _gemini_embed_cooldown_until


def trip_embed_cooldown(seconds: float = _GEMINI_EMBED_COOLDOWN_SECONDS) -> None:
    global _gemini_embed_cooldown_until
    _gemini_embed_cooldown_until = time.monotonic() + seconds


def _l2_norm(vec: list[float]) -> list[float]:
    """Ensure vector is L2-normalized to unit length for metric equality."""
    if not vec:
        return []
    sq = sum(x * x for x in vec)
    if sq == 0.0 or abs(sq - 1.0) < 1e-6:
        return vec
    inv = 1.0 / math.sqrt(sq)
    return [x * inv for x in vec]


class EmbeddingEngine:
    def __init__(
        self,
        provider: str | None = None,
        model_name: str | None = None,
        dimension: int | None = None,
    ) -> None:
        settings = get_settings()
        self.provider = (provider or getattr(settings, "embedding_provider", "gemini")).lower()
        self.model_name = model_name or getattr(settings, "embedding_model", "text-embedding-004")
        self.dimension = dimension if dimension is not None else getattr(settings, "embedding_dimension", 768)
        self.model = None
        self._is_gemini = False
        self._is_fastembed = False
        self._batch_size = 32
        self._semaphore = asyncio.Semaphore(10)
        self._bm25_retriever = None
        self._bm25_vocab_size = 30_000

    def _load_local_model(self) -> None:
        """Load local FastEmbed ONNX or SentenceTransformers fallback."""
        try:
            from fastembed import TextEmbedding
            self.model = TextEmbedding(model_name=self.model_name)
            self._is_fastembed = True
            logger.info(f"FastEmbed ONNX model loaded: {self.model_name}")
            return
        except (ImportError, RuntimeError, OSError) as e:
            logger.warning(f"FastEmbed load failed ({e}), trying sentence_transformers")
        except Exception as e:
            logger.warning(f"FastEmbed unexpected load failure ({e}), trying sentence_transformers")

        try:
            from sentence_transformers import SentenceTransformer
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.model = SentenceTransformer(self.model_name, device=device)
            self._is_fastembed = False
            logger.info(f"SentenceTransformer loaded: {self.model_name} on {device}")
        except (ImportError, RuntimeError, OSError) as e2:
            logger.error(f"Failed to load embedding model with sentence_transformers: {e2}")
            raise
        except Exception as e2:
            logger.error(f"Unexpected error loading embedding model with sentence_transformers: {e2}")
            raise

    def _load_model(self) -> None:
        if self.model is not None:
            return

        logger.info(f"Loading embedding model: {self.model_name} (provider={self.provider})")
        if self.provider in ('gemini', 'google'):
            settings = get_settings()
            if not getattr(settings, "gemini_api_key", None):
                logger.warning("GEMINI_API_KEY missing, falling back to local BAAI embedder for offline/testing")
                self.provider = 'local'
                self.model_name = 'BAAI/bge-small-en-v1.5'
                self._load_local_model()
                return

            try:
                from google import genai
                self.model = genai.Client(api_key=settings.gemini_api_key)
                self._is_gemini = True
                logger.info("Google GenAI embedding client loaded: model=%s (dim=%d)", self.model_name, self.dimension)
                return
            except Exception as e:
                logger.warning("Failed to initialize Google GenAI embedding client (%s), falling back to local", e)
                self.provider = 'local'
                self.model_name = 'BAAI/bge-small-en-v1.5'
                self._load_local_model()
                return
        elif self.provider in ('local', 'fastembed'):
            self._load_local_model()
        elif self.provider == 'openai':
            from openai import AsyncOpenAI
            settings = get_settings()
            self.model = AsyncOpenAI(api_key=settings.openai_api_key)
        elif self.provider == 'voyage':
            self.model = None
        else:
            raise ValueError(f"Unknown embedding provider: {self.provider}")

    async def _call_gemini_embed(self, contents: Any, task_type: str) -> list[list[float]]:
        """Execute Gemini embedding call with rate limit retry, exponential backoff, model fallback, and circuit breaker."""
        if is_embed_in_cooldown():
            logger.debug("Gemini embedding circuit breaker open; bypassing remote API call and failing fast.")
            return []

        from google.genai import types
        config = types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=self.dimension,
        )
        models_to_try = [self.model_name]
        for fallback_m in ("text-embedding-004", "gemini-embedding-001", "embedding-001"):
            if fallback_m not in models_to_try:
                models_to_try.append(fallback_m)

        last_err = None
        for model_id in models_to_try:
            for attempt in range(3):
                try:
                    async with self._semaphore:
                        response = await self.model.aio.models.embed_content(
                            model=model_id,
                            contents=contents,
                            config=config,
                        )
                        if response and response.embeddings:
                            return [_l2_norm(emb.values) for emb in response.embeddings]
                        return []
                except Exception as exc:
                    last_err = exc
                    err_text = str(exc).lower()

                    # Check for daily project quota exhaustion (e.g. Free Tier limit: 1000/day)
                    is_daily_quota = (
                        "resource_exhausted" in err_text
                        or "quota" in err_text
                        or "429" in err_text
                    ) and ("limit: 1000" in err_text or "day" in err_text or "per_day" in err_text or "freetier" in err_text)

                    if is_daily_quota or ("429" in err_text and "resource_exhausted" in err_text):
                        logger.warning(
                            "Gemini embedding daily quota exhausted (%s). Tripping circuit breaker for %.0fs to fail fast.",
                            exc,
                            _GEMINI_EMBED_COOLDOWN_SECONDS,
                        )
                        trip_embed_cooldown(_GEMINI_EMBED_COOLDOWN_SECONDS)
                        return []

                    is_rate_limit = "429" in err_text or "resource_exhausted" in err_text or "quota" in err_text
                    is_transient = "503" in err_text or "unavailable" in err_text or "timeout" in err_text
                    if (is_rate_limit or is_transient) and attempt < 2:
                        backoff = (2 ** attempt) * 0.5 + 0.1
                        logger.warning("Gemini embedding retry attempt %d after %.2fs due to: %s", attempt + 1, backoff, exc)
                        await asyncio.sleep(backoff)
                    elif "not found" in err_text or "404" in err_text:
                        logger.warning("Model %s not found for embedding, trying fallback model", model_id)
                        break
                    else:
                        break

        logger.warning("All Gemini embedding attempts failed (%s); returning empty vectors to trigger degraded sparse retrieval.", last_err)
        return []

    async def embed_texts(self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        if not texts:
            return []

        self._load_model()

        if self.provider in ('gemini', 'google') and self._is_gemini:
            from google.genai import types
            embeddings: list[list[float]] = []
            batch_size = 50
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                contents = [types.Content(parts=[types.Part.from_text(text=t)]) for t in batch]
                batch_vectors = await self._call_gemini_embed(contents, task_type=task_type)
                embeddings.extend(batch_vectors)
            return embeddings
        elif self.provider in ('local', 'fastembed') and self._is_fastembed:
            embeddings = list(await asyncio.to_thread(lambda: list(self.model.embed(texts, batch_size=64))))
            return [e.tolist() if hasattr(e, "tolist") else list(e) for e in embeddings]
        elif self.provider == 'local':
            embeddings = []
            for i in range(0, len(texts), self._batch_size):
                batch = texts[i:i + self._batch_size]
                batch_embeddings = await asyncio.to_thread(
                    self.model.encode, batch, normalize_embeddings=True
                )
                embeddings.extend(batch_embeddings.tolist())
            return embeddings
        elif self.provider == 'openai':
            response = await self.model.embeddings.create(
                input=texts,
                model=self.model_name
            )
            return [data.embedding for data in response.data]
        return []

    async def embed_query(self, query: str) -> list[float]:
        self._load_model()
        if self.provider in ('gemini', 'google') and self._is_gemini:
            vecs = await self._call_gemini_embed(query, task_type="RETRIEVAL_QUERY")
            return vecs[0] if vecs else []

        prefix = "search_query: " if "bge" in self.model_name.lower() else ""
        embeddings = await self.embed_texts([f"{prefix}{query}"])
        return embeddings[0] if embeddings else []

    async def embed_similarity(self, text: str) -> list[float]:
        """Embed text for symmetric semantic similarity (e.g. cosine gate, semantic cache)."""
        self._load_model()
        if self.provider in ('gemini', 'google') and self._is_gemini:
            vecs = await self._call_gemini_embed(text, task_type="SEMANTIC_SIMILARITY")
            return vecs[0] if vecs else []
        return await self.embed_query(text)

    def _load_or_init_bm25(self, corpus_texts: list[str] | None = None) -> None:
        """Load saved BM25 index from data/bm25_index/ or fit on corpus_texts."""
        try:
            import bm25s
            bm25_dir = Path("data/bm25_index")
            if bm25_dir.exists() and corpus_texts is None:
                try:
                    self._bm25_retriever = bm25s.BM25.load(str(bm25_dir), load_corpus=False)
                    return
                except (OSError, ValueError, KeyError) as load_err:
                    logger.warning("Failed loading saved BM25 index: %s", load_err)
                except Exception as load_err:
                    logger.warning("Unexpected error loading saved BM25 index: %s", load_err)

            if corpus_texts:
                tokenized = bm25s.tokenize(corpus_texts, stopwords="en")
                self._bm25_retriever = bm25s.BM25(method="robertson")
                self._bm25_retriever.index(tokenized)
                bm25_dir.mkdir(parents=True, exist_ok=True)
                self._bm25_retriever.save(str(bm25_dir))
                logger.info("Fitted and saved BM25S index to %s", bm25_dir)
        except ImportError as e:
            logger.debug("bm25s package not available: %s", e)
        except (OSError, ValueError) as e:
            logger.warning("BM25 initialization error: %s", e)
        except Exception as e:
            logger.debug("BM25 initialization notice: %s", e)

    def fit_bm25(self, corpus_texts: list[str]) -> None:
        """Fit BM25S on the full corpus texts."""
        self._load_or_init_bm25(corpus_texts)

    async def embed_sparse(self, texts: list[str]) -> list[dict]:
        """Convert texts to BM25S tokenized sparse vectors suitable for Pinecone sparse_values."""
        if not texts:
            return []

        if self._bm25_retriever is None:
            self._load_or_init_bm25()

        try:
            import bm25s
            tokenized_list = bm25s.tokenize(texts, stopwords="en")
        except Exception:
            tokenized_list = None

        result = []
        for i, text in enumerate(texts):
            seen: dict[int, float] = {}
            if tokenized_list is not None:
                try:
                    raw_tokens = tokenized_list[i] if i < len(tokenized_list) else []
                    if isinstance(raw_tokens, (list, tuple)) or hasattr(raw_tokens, "__iter__"):
                        for term in raw_tokens:
                            if not term:
                                continue
                            t_str = str(term).lower()
                            idx = (zlib.crc32(t_str.encode("utf-8")) & 0x7FFFFFFF) % self._bm25_vocab_size
                            seen[idx] = seen.get(idx, 0.0) + 1.0
                except Exception as tok_err:
                    logger.debug("BM25 tokenize extract fallback: %s", tok_err)

            if not seen:
                # Fallback stopword-filtered tokenization
                words = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
                stopwords = {"the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "is", "it", "by", "of", "with"}
                for w in words:
                    if w not in stopwords:
                        idx = (zlib.crc32(w.encode("utf-8")) & 0x7FFFFFFF) % self._bm25_vocab_size
                        seen[idx] = seen.get(idx, 0.0) + 1.0

            sorted_indices = sorted(seen.keys())
            # Sublinear term frequency weighting: 1.0 + ln(tf) for exact lexical discrimination
            sorted_values = [round(1.0 + math.log(seen[k]), 4) for k in sorted_indices]
            result.append({"indices": sorted_indices, "values": sorted_values})

        return result
