"""Chunking module for CloudGPT.

Provides semantic document chunking with heading-aware splitting
and rich metadata extraction for cloud documentation.
"""

from .semantic_chunker import SemanticChunker
from .metadata_extractor import MetadataExtractor

__all__ = ["SemanticChunker", "MetadataExtractor"]
