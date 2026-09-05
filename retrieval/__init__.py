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

from .dense import DenseRetriever
from .bm25 import SparseRetriever
from .hybrid import HybridRetriever
from .reranker import Reranker
from .agentic_rag import AgenticRAGPipeline
from .adaptive_rag import AdaptiveAdvancedRAGPipeline

__all__ = [
    "RetrievalResult",
    "DenseRetriever",
    "SparseRetriever",
    "HybridRetriever",
    "Reranker",
    "AgenticRAGPipeline",
    "AdaptiveAdvancedRAGPipeline",
]
