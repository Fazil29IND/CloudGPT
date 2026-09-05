import asyncio
import logging
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

class RetrievalResult(BaseModel):
    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)

from .dense import DenseRetriever, HNSWRetriever, QuakeRetriever, get_default_hnsw_index, get_default_quake_index
from .bm25 import SparseRetriever
from .hybrid import HybridRetriever
from .reranker import Reranker
from .hnsw_index import HNSWIndex
from .quake_index import QuakeIndex
from .agentic_rag import AgenticRAGPipeline
from .adaptive_rag import AdaptiveAdvancedRAGPipeline
from .query_processor import QueryContext, normalize_query, rewrite_contextual_query

__all__ = [
    "RetrievalResult",
    "DenseRetriever",
    "HNSWRetriever",
    "QuakeRetriever",
    "HNSWIndex",
    "QuakeIndex",
    "SparseRetriever",
    "HybridRetriever",
    "Reranker",
    "AgenticRAGPipeline",
    "AdaptiveAdvancedRAGPipeline",
    "get_default_hnsw_index",
    "get_default_quake_index",
    "QueryContext",
    "normalize_query",
    "rewrite_contextual_query",
]
