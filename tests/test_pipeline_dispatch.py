from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.chat_routes import PipelineResult, execute_agent_pipeline


@pytest.fixture(autouse=True)
def disable_smalltalk_gate_for_dispatch_tests():
    with patch("router.smalltalk_gate.SmallTalkGate.is_smalltalk", AsyncMock(return_value=False)):
        yield


@pytest.mark.asyncio
async def test_free_tier_uses_current_rag_path():
    fake_context = (
        MagicMock(confidence=0.85, model_dump=lambda: {"intent": "explain"}),
        ["RAG"],
        [],
        [],
        [],
        [],
        [],  # api_data
        None,
        MagicMock(get_sources=lambda: []),
        {"classification": 10.0},
        "none",
    )

    with patch("api.chat_routes._gather_pipeline_context", AsyncMock(return_value=fake_context)) as mock_gather, \
         patch("api.chat_routes._build_pipeline_messages", return_value=[]), \
         patch("api.chat_routes.generate_with_fallback", AsyncMock(return_value=("Free answer", "gemini-flash"))), \
         patch("retrieval.agentic_rag.AgenticRAGPipeline.run", side_effect=AssertionError("Should not call Agentic")), \
         patch("retrieval.adaptive_rag.AdaptiveAdvancedRAGPipeline.run", side_effect=AssertionError("Should not call Adaptive")):
        result = await execute_agent_pipeline("Query", tier="Free")

    assert mock_gather.called
    assert result.pipeline_type == "current_rag"
    assert result.answer == "Free answer"


@pytest.mark.asyncio
async def test_pro_tier_dispatches_to_agentic_rag():
    expected_result = PipelineResult(
        answer="Pro agentic answer",
        pipeline_type="agentic_rag",
        model_used="claude-sonnet",
    )

    with patch("retrieval.agentic_rag.AgenticRAGPipeline.run", AsyncMock(return_value=expected_result)) as mock_run:
        result = await execute_agent_pipeline("Pro query", tier="Pro", thinking_level="High")

    assert mock_run.called
    assert result.pipeline_type == "agentic_rag"
    assert result.answer == "Pro agentic answer"


@pytest.mark.asyncio
async def test_max_tier_dispatches_to_adaptive_rag():
    expected_result = PipelineResult(
        answer="Max adaptive answer",
        pipeline_type="adaptive_rag",
        model_used="claude-max-thinking",
    )

    with patch("retrieval.adaptive_rag.AdaptiveAdvancedRAGPipeline.run", AsyncMock(return_value=expected_result)) as mock_run:
        result = await execute_agent_pipeline("Max query", tier="Max", thinking_level="Max")

    assert mock_run.called
    assert result.pipeline_type == "adaptive_rag"
    assert result.answer == "Max adaptive answer"


@pytest.mark.asyncio
async def test_developer_tier_dispatches_to_adaptive_rag():
    expected_result = PipelineResult(
        answer="Dev adaptive answer",
        pipeline_type="adaptive_rag",
        model_used="claude-max-thinking",
    )

    with patch("retrieval.adaptive_rag.AdaptiveAdvancedRAGPipeline.run", AsyncMock(return_value=expected_result)) as mock_run:
        result = await execute_agent_pipeline("Dev query", tier="Developer")

    assert mock_run.called
    assert result.pipeline_type == "adaptive_rag"


@pytest.mark.asyncio
async def test_dispatch_forwards_all_kwargs():
    expected_result = PipelineResult(pipeline_type="agentic_rag")
    mock_emit = AsyncMock()

    with patch("retrieval.agentic_rag.AgenticRAGPipeline.run", AsyncMock(return_value=expected_result)) as mock_run:
        await execute_agent_pipeline(
            query="test q",
            provider_filter="aws",
            tier="Pro",
            chat_history=[{"role": "user", "content": "hi"}],
            attachment_texts=[{"content": "att"}],
            emit_event=mock_emit,
            stream=True,
            thinking_level="High",
        )

    mock_run.assert_called_once_with(
        query="test q",
        provider_filter="aws",
        tier="Pro",
        chat_history=[{"role": "user", "content": "hi"}],
        attachment_texts=[{"content": "att"}],
        stream=True,
        thinking_level="High",
        emit_event=mock_emit,
        session_summary=None,
        user_memories=None,
    )


def test_pipeline_result_default_pipeline_type():
    res = PipelineResult()
    assert res.pipeline_type == "current_rag"
