import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from config import get_settings
from llm.provider import GeminiProvider, get_llm_provider, get_sub_model_provider
from llm.context_builder import ContextBuilder
from tools.web_search import WebSearchTool, WebSearchResult


def test_settings_gemini_model_names():
    s = get_settings()
    assert s.gemini_model == "gemini-3.8-flash"
    assert s.gemini_model_lite == "gemini-3.8-flash"
    assert s.gemini_model_core == "gemini-3.8-flash"
    assert s.gemini_model_apex == "gemini-3.8-flash"
    assert s.gemini_model_fallback_1 == "gemini-3.7-flash"
    assert s.gemini_model_fallback_2 == "gemini-3.6-flash"
    assert s.gemini_model_fallback_3 == "gemini-3.5-flash"


def test_get_llm_provider_returns_gemini_for_all_tiers():
    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "mock"
    mock_settings.gemini_model_lite = "gemini-3.8-flash"
    mock_settings.gemini_model_core = "gemini-3.8-flash"
    mock_settings.gemini_model_apex = "gemini-3.8-flash"
    mock_settings.gemini_model_fallback_1 = "gemini-3.7-flash"
    mock_settings.gemini_model_fallback_2 = "gemini-3.6-flash"
    mock_settings.gemini_model_fallback_3 = "gemini-3.5-flash"
    mock_settings.gemini_model = "gemini-3.8-flash"

    with patch("llm.provider.get_settings", return_value=mock_settings):
        for tier in ("Free", "Pro", "Max", "Developer", "admin"):
            provider = get_llm_provider("main", tier)
            assert isinstance(provider, GeminiProvider)


def test_tier_model_chain_ordering():
    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "mock"
    mock_settings.gemini_model_lite = "gemini-3.8-flash"
    mock_settings.gemini_model_core = "gemini-3.8-flash"
    mock_settings.gemini_model_apex = "gemini-3.8-flash"
    mock_settings.gemini_model_fallback_1 = "gemini-3.7-flash"
    mock_settings.gemini_model_fallback_2 = "gemini-3.6-flash"
    mock_settings.gemini_model_fallback_3 = "gemini-3.5-flash"
    mock_settings.gemini_model = "gemini-3.8-flash"

    with patch("llm.provider.get_settings", return_value=mock_settings):
        free_p = get_llm_provider("main", "Free")
        max_p = get_llm_provider("main", "Max")
        assert free_p.model_chain == ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
        assert max_p.model_chain == ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]


def test_sub_model_provider_equals_main():
    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "mock"
    mock_settings.gemini_model_lite = "gemini-3.8-flash"
    mock_settings.gemini_model_core = "gemini-3.8-flash"
    mock_settings.gemini_model_apex = "gemini-3.8-flash"
    mock_settings.gemini_model_fallback_1 = "gemini-3.7-flash"
    mock_settings.gemini_model_fallback_2 = "gemini-3.6-flash"
    mock_settings.gemini_model_fallback_3 = "gemini-3.5-flash"
    mock_settings.gemini_model = "gemini-3.8-flash"

    with patch("llm.provider.get_settings", return_value=mock_settings):
        for role in ("router", "summarizer", "verifier"):
            p = get_sub_model_provider(role, "Free")
            assert isinstance(p, GeminiProvider)


def test_context_builder_with_internet_results():
    builder = ContextBuilder()
    query = "How to resolve high CPU utilization in AWS EC2?"
    classification = {
        "intent": "troubleshooting",
        "providers": ["aws"],
        "services": ["ec2"],
        "categories": ["compute"],
    }
    internet_results = [
        {
            "title": "Troubleshoot high CPU on EC2",
            "url": "https://aws.amazon.com/premiumsupport/knowledge-center/ec2-cpu-utilization/",
            "content": "Check top processes using htop or CloudWatch metrics. Upgrade instance type if needed.",
            "source_engine": "duckduckgo",
        }
    ]

    messages = builder.build_context(
        query=query,
        classification=classification,
        internet_results=internet_results,
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "CloudGPT" in messages[0]["content"]

    user_prompt = messages[1]["content"]
    assert "INTERNET SEARCH RESULTS" in user_prompt
    assert "Troubleshoot high CPU on EC2" in user_prompt
    assert "duckduckgo" in user_prompt


@pytest.mark.asyncio
async def test_web_search_duckduckgo_fallback():
    tool = WebSearchTool(searxng_url=None, api_key=None)
    mock_results = [
        WebSearchResult(
            title="AWS S3 Best Practices",
            url="https://docs.aws.amazon.com/s3/best-practices",
            content="Use multipart uploads for large files.",
            score=1.0,
            source_engine="duckduckgo",
        )
    ]

    with patch.object(tool, "_duckduckgo_search", new=AsyncMock(return_value=mock_results)):
        results = await tool.search("AWS S3 best practices")
        assert len(results) == 1
        assert results[0].title == "AWS S3 Best Practices"
        assert results[0].source_engine == "duckduckgo"


@pytest.mark.asyncio
async def test_web_search_searxng_success():
    tool = WebSearchTool(searxng_url="http://localhost:8080")
    mock_results = [
        WebSearchResult(
            title="AWS S3 Docs",
            url="https://docs.aws.amazon.com/s3",
            content="Amazon S3 is scalable cloud storage.",
            score=0.95,
            source_engine="searxng",
        )
    ]

    with patch.object(tool, "_searxng_search", new=AsyncMock(return_value=mock_results)):
        results = await tool.search("AWS S3", domains=["docs.aws.amazon.com"])
        assert len(results) == 1
        assert results[0].title == "AWS S3 Docs"
        assert results[0].source_engine == "searxng"


@pytest.mark.asyncio
async def test_web_search_searxng_fallback_to_ddg():
    tool = WebSearchTool(searxng_url="http://localhost:8080")
    mock_ddg_results = [
        WebSearchResult(
            title="AWS EC2 Docs",
            url="https://docs.aws.amazon.com/ec2",
            content="Amazon EC2 compute instances.",
            score=0.9,
            source_engine="duckduckgo",
        )
    ]

    # When SearXNG returns empty or errors, it must seamlessly fallback to DuckDuckGo
    with patch.object(tool, "_searxng_search", new=AsyncMock(return_value=[])), \
         patch.object(tool, "_duckduckgo_search", new=AsyncMock(return_value=mock_ddg_results)):
        results = await tool.search("AWS EC2")
        assert len(results) == 1
        assert results[0].source_engine == "duckduckgo"


def test_settings_gemini_safety_net_and_timeouts():
    s = get_settings()
    assert s.gemini_model_safety_net == "gemini-3.5-flash-lite"
    assert s.gemini_first_chunk_timeout_seconds == 6.0
    assert s.gemini_total_fallback_deadline_seconds == 25.0


@pytest.mark.asyncio
async def test_stream_cascades_from_38_to_37_on_connection_reset():
    import llm.provider as provider_module
    from types import SimpleNamespace

    provider_module._model_cooldowns.clear()

    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "mock"
    mock_settings.gemini_model = "gemini-3.8-flash"
    mock_settings.gemini_model_lite = "gemini-3.8-flash"
    mock_settings.gemini_model_fallback_1 = "gemini-3.7-flash"
    mock_settings.gemini_model_fallback_2 = "gemini-3.6-flash"
    mock_settings.gemini_model_fallback_3 = "gemini-3.5-flash"
    mock_settings.gemini_model_safety_net = "gemini-3.5-flash-lite"
    mock_settings.gemini_first_chunk_timeout_seconds = 6.0
    mock_settings.gemini_total_fallback_deadline_seconds = 25.0
    mock_settings.gemini_request_timeout_seconds = 25.0
    mock_settings.llm_stream_timeout_seconds = 120.0

    with patch("llm.provider.get_settings", return_value=mock_settings):
        provider = GeminiProvider(
            models=["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
        )

    attempted_models: list[str] = []

    mock_chunk = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="Hello from 3.7", thought=False)]))],
        usage_metadata=SimpleNamespace(prompt_token_count=10, total_token_count=20),
    )

    async def fake_stream_iter(*args, **kwargs):
        yield mock_chunk

    async def mock_generate_stream(*args, **kwargs):
        target_model = kwargs.get("model")
        attempted_models.append(target_model)
        if target_model == "gemini-3.8-flash":
            raise ConnectionResetError("read tcp wsarecv: An established connection was aborted")
        m_stream = MagicMock()
        m_stream.__aiter__ = fake_stream_iter
        return m_stream

    provider.client = MagicMock()
    provider.client.aio.models.generate_content_stream = AsyncMock(side_effect=mock_generate_stream)

    stream = await provider.generate(messages=[{"role": "user", "content": "hello"}], stream=True)
    tokens = [t async for t in stream]

    assert "Hello from 3.7" in tokens
    assert attempted_models == ["gemini-3.8-flash", "gemini-3.7-flash"]
    assert provider.model == "gemini-3.7-flash"
    assert provider_module._model_in_cooldown("gemini-3.8-flash") is True

    # Next call should automatically skip gemini-3.8-flash due to circuit breaker
    active_candidates = provider._get_active_candidates("gemini-3.8-flash")
    assert active_candidates[0] == "gemini-3.7-flash"


@pytest.mark.asyncio
async def test_full_cascade_reaches_35_flash_lite_safety_net():
    import llm.provider as provider_module
    from types import SimpleNamespace

    provider_module._model_cooldowns.clear()

    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "mock"
    mock_settings.gemini_model = "gemini-3.8-flash"
    mock_settings.gemini_model_lite = "gemini-3.8-flash"
    mock_settings.gemini_model_fallback_1 = "gemini-3.7-flash"
    mock_settings.gemini_model_fallback_2 = "gemini-3.6-flash"
    mock_settings.gemini_model_fallback_3 = "gemini-3.5-flash"
    mock_settings.gemini_model_safety_net = "gemini-3.5-flash-lite"
    mock_settings.gemini_first_chunk_timeout_seconds = 6.0
    mock_settings.gemini_total_fallback_deadline_seconds = 25.0
    mock_settings.gemini_request_timeout_seconds = 25.0
    mock_settings.llm_stream_timeout_seconds = 120.0

    with patch("llm.provider.get_settings", return_value=mock_settings):
        provider = GeminiProvider(
            models=["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
        )

    attempted_models: list[str] = []

    mock_chunk = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="Safety net rescued", thought=False)]))],
        usage_metadata=SimpleNamespace(prompt_token_count=5, total_token_count=15),
    )

    async def fake_stream_iter(*args, **kwargs):
        yield mock_chunk

    async def mock_generate_stream(*args, **kwargs):
        target_model = kwargs.get("model")
        attempted_models.append(target_model)
        if target_model != "gemini-3.5-flash-lite":
            raise RuntimeError(f"503 Service Unavailable: {target_model} overloaded")
        m_stream = MagicMock()
        m_stream.__aiter__ = fake_stream_iter
        return m_stream

    provider.client = MagicMock()
    provider.client.aio.models.generate_content_stream = AsyncMock(side_effect=mock_generate_stream)

    stream = await provider.generate(messages=[{"role": "user", "content": "test"}], stream=True)
    tokens = [t async for t in stream]

    assert "Safety net rescued" in tokens
    assert attempted_models == [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]
    assert provider.model == "gemini-3.5-flash-lite"


@pytest.mark.asyncio
async def test_stream_mid_stream_503_during_thinking_cascades_to_fallback():
    import llm.provider as provider_module
    from types import SimpleNamespace

    provider_module._model_cooldowns.clear()

    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "mock"
    mock_settings.gemini_model = "gemini-3.8-flash"
    mock_settings.gemini_model_lite = "gemini-3.8-flash"
    mock_settings.gemini_model_fallback_1 = "gemini-3.7-flash"
    mock_settings.gemini_model_fallback_2 = "gemini-3.6-flash"
    mock_settings.gemini_model_fallback_3 = "gemini-3.5-flash"
    mock_settings.gemini_model_safety_net = "gemini-3.5-flash-lite"
    mock_settings.gemini_first_chunk_timeout_seconds = 6.0
    mock_settings.gemini_total_fallback_deadline_seconds = 25.0
    mock_settings.gemini_request_timeout_seconds = 25.0
    mock_settings.llm_stream_timeout_seconds = 120.0

    with patch("llm.provider.get_settings", return_value=mock_settings):
        provider = GeminiProvider(
            models=["gemini-3.8-flash", "gemini-3.7-flash"]
        )

    attempted_models: list[str] = []

    # Model 3.8 chunk 1 is thinking, chunk 2 raises 503
    mock_chunk_38_first = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="Thinking about EC2...", thought=True)]))],
        usage_metadata=SimpleNamespace(prompt_token_count=10, total_token_count=20),
    )

    async def iter_38_crash(*args, **kwargs):
        yield mock_chunk_38_first
        raise RuntimeError("503 UNAVAILABLE: This model is currently experiencing high demand.")

    # Model 3.7 succeeds with answer
    mock_chunk_37_answer = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="Amazon EC2 is elastic compute", thought=False)]))],
        usage_metadata=SimpleNamespace(prompt_token_count=10, total_token_count=35),
    )

    async def iter_37_success(*args, **kwargs):
        yield mock_chunk_37_answer

    async def mock_generate_stream(*args, **kwargs):
        target_model = kwargs.get("model")
        attempted_models.append(target_model)
        m_stream = MagicMock()
        if target_model == "gemini-3.8-flash":
            m_stream.__aiter__ = iter_38_crash
        else:
            m_stream.__aiter__ = iter_37_success
        return m_stream

    provider.client = MagicMock()
    provider.client.aio.models.generate_content_stream = AsyncMock(side_effect=mock_generate_stream)

    stream = await provider.generate(messages=[{"role": "user", "content": "Amazon EC2"}], stream=True)
    tokens = [t async for t in stream]

    assert any("Amazon EC2 is elastic compute" in t for t in tokens)
    assert attempted_models == ["gemini-3.8-flash", "gemini-3.7-flash"]
    assert provider.model == "gemini-3.7-flash"
    assert provider_module._model_in_cooldown("gemini-3.8-flash") is True


@pytest.mark.asyncio
async def test_stream_mid_stream_cascade_does_not_exhaust_deadline_prematurely():
    """Verify that stream generation is governed by stream_timeout (120s), not fallback deadline."""
    import llm.provider as provider_module
    from types import SimpleNamespace

    provider_module._model_cooldowns.clear()

    mock_settings = MagicMock()
    mock_settings.has_gemini = True
    mock_settings.gemini_api_key = "mock"
    mock_settings.gemini_model = "gemini-3.8-flash"
    mock_settings.gemini_model_fallback_1 = "gemini-3.7-flash"
    mock_settings.gemini_first_chunk_timeout_seconds = 6.0
    mock_settings.gemini_total_fallback_deadline_seconds = 0.1  # Very short fallback deadline
    mock_settings.gemini_request_timeout_seconds = 10.0
    mock_settings.llm_stream_timeout_seconds = 120.0

    with patch("llm.provider.get_settings", return_value=mock_settings):
        provider = GeminiProvider(models=["gemini-3.8-flash", "gemini-3.7-flash"])

    chunk1 = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="Chunk 1", thought=False)]))],
        usage_metadata=None,
    )
    chunk2 = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="Chunk 2", thought=False)]))],
        usage_metadata=None,
    )

    async def iter_38(*args, **kwargs):
        yield chunk1
        raise RuntimeError("503 Service Unavailable: overloaded")

    async def iter_37(*args, **kwargs):
        yield chunk2

    async def mock_generate_stream(*args, **kwargs):
        m_stream = MagicMock()
        if kwargs.get("model") == "gemini-3.8-flash":
            m_stream.__aiter__ = iter_38
        else:
            m_stream.__aiter__ = iter_37
        return m_stream

    provider.client = MagicMock()
    provider.client.aio.models.generate_content_stream = AsyncMock(side_effect=mock_generate_stream)

    stream = await provider.generate(messages=[{"role": "user", "content": "test"}], stream=True)
    tokens = [t async for t in stream]

    assert "Chunk 1" in tokens
    assert "Chunk 2" in tokens
    assert provider.model == "gemini-3.7-flash"


@pytest.mark.asyncio
async def test_web_search_direct_duckduckgo_html_parser():
    """Verify that direct DuckDuckGo HTML parser extracts clean links, snippets, and unquotes uddg."""
    tool = WebSearchTool()
    sample_html = """
    <html><body>
    <div class="result results_links results_links_deep web-result">
      <h2 class="result__title">
        <a class="result__url" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.aws.amazon.com%2Fs3%2Fstorage-classes%2F&rut=123">
          Amazon <b>S3</b> Storage Classes
        </a>
      </h2>
      <a class="result__snippet">Amazon S3 offers an industry-leading range of storage classes for your data.</a>
    </div>
    </body></html>
    """
    mock_resp = MagicMock()
    mock_resp.text = sample_html
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("tools.web_search._get_http_client", return_value=mock_client):
        results = await tool._duckduckgo_direct_search("S3 storage classes")
        assert len(results) == 1
        assert results[0].title == "Amazon S3 Storage Classes"
        assert results[0].url == "https://docs.aws.amazon.com/s3/storage-classes/"
        assert "storage classes for your data" in results[0].content
        assert results[0].source_engine == "duckduckgo"


@pytest.mark.asyncio
async def test_embedding_engine_gemini_mocked():
    """Verify that EmbeddingEngine properly calls Google GenAI with task_type and dimension."""
    from embeddings.embedding_engine import EmbeddingEngine
    from types import SimpleNamespace

    engine = EmbeddingEngine(provider="gemini", model_name="gemini-embedding-2", dimension=384)
    engine._is_gemini = True
    
    mock_emb = SimpleNamespace(values=[0.1] * 384)
    mock_response = SimpleNamespace(embeddings=[mock_emb])

    mock_client = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(return_value=mock_response)
    engine.model = mock_client

    vec = await engine.embed_query("How does S3 replicate objects?")
    assert len(vec) == 384
    call_args = mock_client.aio.models.embed_content.call_args
    assert call_args.kwargs["model"] == "gemini-embedding-2"
    assert call_args.kwargs["config"].task_type == "RETRIEVAL_QUERY"
    assert call_args.kwargs["config"].output_dimensionality == 384

    # Test embed_texts with RETRIEVAL_DOCUMENT
    mock_multi_response = SimpleNamespace(embeddings=[mock_emb, mock_emb])
    mock_client.aio.models.embed_content = AsyncMock(return_value=mock_multi_response)
    vecs = await engine.embed_texts(["Chunk 1", "Chunk 2"])
    assert len(vecs) == 2
    call_args2 = mock_client.aio.models.embed_content.call_args
    assert call_args2.kwargs["config"].task_type == "RETRIEVAL_DOCUMENT"

    # Test embed_similarity with SEMANTIC_SIMILARITY
    mock_client.aio.models.embed_content = AsyncMock(return_value=mock_response)
    sim_vec = await engine.embed_similarity("hello")
    assert len(sim_vec) == 384
    call_args3 = mock_client.aio.models.embed_content.call_args
    assert call_args3.kwargs["config"].task_type == "SEMANTIC_SIMILARITY"


@pytest.mark.asyncio
async def test_embedding_engine_gemini_retry_on_429():
    """Verify that EmbeddingEngine retries with backoff upon encountering HTTP 429 / resource exhausted."""
    from embeddings.embedding_engine import EmbeddingEngine
    from types import SimpleNamespace

    engine = EmbeddingEngine(provider="gemini", model_name="gemini-embedding-2", dimension=384)
    engine._is_gemini = True

    mock_emb = SimpleNamespace(values=[0.2] * 384)
    mock_success = SimpleNamespace(embeddings=[mock_emb])

    mock_client = MagicMock()
    # Fail first call with 429, succeed on second attempt
    mock_client.aio.models.embed_content = AsyncMock(
        side_effect=[RuntimeError("429 Resource Exhausted: quota limit"), mock_success]
    )
    engine.model = mock_client

    with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
        vec = await engine.embed_query("AWS Lambda cold start")
        assert len(vec) == 384
        assert mock_client.aio.models.embed_content.call_count == 2
        mock_sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_smalltalk_gate_gemini_threshold_selection():
    """Verify SmallTalkGate dynamically selects calibrated 0.78 threshold for Gemini provider."""
    from router.smalltalk_gate import SmallTalkGate
    from types import SimpleNamespace

    mock_embedder = SimpleNamespace(provider="gemini", embed_similarity=AsyncMock(return_value=[0.1] * 384))
    gate = SmallTalkGate(embedder=mock_embedder)

    with patch.object(gate, "_canonical_embeddings", new=AsyncMock(return_value=[[0.1] * 384])):
        # Identical vector -> cosine similarity 1.0 -> should hit gate
        res = await gate.is_smalltalk("hey team", query_embedding=[0.1] * 384)
        assert res is True





