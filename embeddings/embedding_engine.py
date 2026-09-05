import asyncio
import logging
import math
import re
import zlib
from pathlib import Path

from config import get_settings

logger = logging.getLogger(__name__)


class EmbeddingEngine:
    def __init__(
        self,
        provider: str = 'local',
        model_name: str = 'BAAI/bge-small-en-v1.5',
        dimension: int = 384,
    ) -> None:
        self.provider = provider
        self.model_name = model_name
        self.dimension = dimension
        self.model = None
        self._is_fastembed = False
        self._batch_size = 32
        self._bm25_retriever = None
        self._bm25_vocab_size = 30_000

    def _load_model(self) -> None:
        if self.model is not None:
            return

        logger.info(f"Loading embedding model: {self.model_name} (provider={self.provider})")
        if self.provider in ('local', 'fastembed'):
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
            except (ImportError, RuntimeError, OSError) as e2:
                logger.error(f"Failed to load embedding model with sentence_transformers: {e2}")
                raise
            except Exception as e2:
                logger.error(f"Unexpected error loading embedding model with sentence_transformers: {e2}")
                raise
        elif self.provider == 'openai':
            from openai import AsyncOpenAI
            settings = get_settings()
            self.model = AsyncOpenAI(api_key=settings.openai_api_key)
        elif self.provider == 'voyage':
            self.model = None
        else:
            raise ValueError(f"Unknown embedding provider: {self.provider}")

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        self._load_model()

        if self.provider in ('local', 'fastembed') and self._is_fastembed:
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
        prefix = "search_query: " if "bge" in self.model_name.lower() else ""
        embeddings = await self.embed_texts([f"{prefix}{query}"])
        return embeddings[0] if embeddings else []

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
