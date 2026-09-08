"""Task 10: Test Embedding Engine Voyage AI provider and MRL output dimensionality."""
from __future__ import annotations

import sys
from types import ModuleType
import pytest
from unittest.mock import MagicMock, patch

from config import Settings
from embeddings.embedding_engine import EmbeddingEngine


def test_voyage_missing_dependency_raises_import_error():
    """When voyageai is not installed, initializing voyage provider raises ImportError."""
    with patch.dict(sys.modules, {"voyageai": None}):
        engine = EmbeddingEngine(provider="voyage", model_name="voyage-3")
        with pytest.raises(ImportError, match="voyageai package is required"):
            engine._load_model()


def test_voyage_missing_api_key_raises_value_error():
    """When voyageai is present but VOYAGE_API_KEY is unset, raises ValueError."""
    fake_voyage = ModuleType("voyageai")
    fake_voyage.Client = MagicMock()

    test_settings = Settings(gemini_api_key="test-key", voyage_api_key=None)

    with patch.dict(sys.modules, {"voyageai": fake_voyage}), \
         patch("embeddings.embedding_engine.get_settings", return_value=test_settings):
        engine = EmbeddingEngine(provider="voyage", model_name="voyage-3")
        with pytest.raises(ValueError, match="Voyage API key is missing"):
            engine._load_model()


@pytest.mark.asyncio
async def test_voyage_embed_query_and_texts():
    """Verify Voyage AI client embed call for queries and document batches."""
    fake_client = MagicMock()
    mock_res_query = MagicMock()
    mock_res_query.embeddings = [[0.1, 0.2, 0.3]]
    mock_res_docs = MagicMock()
    mock_res_docs.embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    fake_client.embed.side_effect = [mock_res_query, mock_res_docs]

    fake_voyage = ModuleType("voyageai")
    fake_voyage.Client = MagicMock(return_value=fake_client)

    test_settings = Settings(
        gemini_api_key="test-key",
        voyage_api_key="vy-test-key",
        embedding_provider="voyage",
        embedding_model="voyage-3",
        embedding_dimension=512,
    )

    with patch.dict(sys.modules, {"voyageai": fake_voyage}), \
         patch("embeddings.embedding_engine.get_settings", return_value=test_settings):

        engine = EmbeddingEngine(
            provider="voyage",
            model_name="voyage-3",
            dimension=512,
        )

        query_vec = await engine.embed_query("search cloud vpc")
        assert query_vec == [0.1, 0.2, 0.3]
        fake_client.embed.assert_called_with(
            texts=["search cloud vpc"],
            model="voyage-3",
            input_type="query",
            output_dimension=512,
        )

        docs_vecs = await engine.embed_texts(["doc1", "doc2"])
        assert len(docs_vecs) == 2
        assert docs_vecs[0] == [0.1, 0.2, 0.3]
        fake_client.embed.assert_called_with(
            texts=["doc1", "doc2"],
            model="voyage-3",
            input_type="document",
            output_dimension=512,
        )


def test_gemini_mrl_output_dimension_configured():
    """Verify Gemini provider dimension defaults to configured embedding_dimension."""
    test_settings = Settings(gemini_api_key="test-key", embedding_dimension=384)
    with patch("embeddings.embedding_engine.get_settings", return_value=test_settings):
        engine = EmbeddingEngine(provider="gemini", dimension=384)
        assert engine.dimension == 384
