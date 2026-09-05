import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from app import app
import db

def test_delete_user_session_db():
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.rowcount = 2
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("db.get_connection", return_value=mock_conn), patch("db.put_connection"):
        result = db.delete_user_session(user_id=1, session_id="test-session-123")
        assert result is True
        assert mock_cur.execute.call_count == 2
        mock_conn.commit.assert_called_once()


def test_set_session_title_db():
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("db.get_connection", return_value=mock_conn), patch("db.put_connection"):
        db.set_session_title(user_id=1, session_id="test-session-123", title="AWS S3 Storage Classes")
        mock_cur.execute.assert_called_once()
        assert "INSERT INTO session_titles" in mock_cur.execute.call_args[0][0]
        assert mock_cur.execute.call_args[0][1] == (1, "test-session-123", "AWS S3 Storage Classes")
        mock_conn.commit.assert_called_once()


def test_delete_user_session_unauthorized():
    client = TestClient(app)
    response = client.delete("/api/sessions/test-session-123")
    assert response.status_code == 401


def test_get_user_sessions_db_search():
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = [
        {"session_id": "s1", "created_at": "2026-08-30", "title": "AWS S3 Storage Classes"}
    ]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("db.get_connection", return_value=mock_conn), patch("db.put_connection"):
        # Test without search query
        res1 = db.get_user_sessions(user_id=1, limit=50)
        assert len(res1) == 1
        assert res1[0]["title"] == "AWS S3 Storage Classes"

        # Test with search query
        res2 = db.get_user_sessions(user_id=1, limit=50, query="S3")
        assert len(res2) == 1
        assert res2[0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_generate_chat_title():
    from api.chat_routes import pipeline
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value="AWS S3 Storage Classes")
    with patch.object(pipeline, "get_summarizer_llm", return_value=mock_llm):
        title = await pipeline.generate_chat_title("Explain AWS S3 storage classes", "S3 provides multiple storage tiers...")
        assert title == "AWS S3 Storage Classes"


def test_list_sessions_unauthorized():
    client = TestClient(app)
    res = client.get("/api/sessions?q=test&limit=50")
    assert res.status_code == 401


def test_get_session_title_db():
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = {"title": "Kubernetes Pod Security"}
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("db.get_connection", return_value=mock_conn), patch("db.put_connection"):
        title = db.get_session_title(user_id=1, session_id="s-k8s")
        assert title == "Kubernetes Pod Security"
        mock_cur.execute.assert_called_once()
        assert "SELECT title FROM session_titles" in mock_cur.execute.call_args[0][0]


@pytest.mark.asyncio
async def test_generate_chat_title_subsequent_shift():
    from api.chat_routes import pipeline
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value="Azure Functions Pricing")
    with patch.object(pipeline, "get_summarizer_llm", return_value=mock_llm):
        new_title = await pipeline.generate_chat_title(
            "How much does Azure Functions cost?",
            "Azure functions has consumption and premium plans...",
            current_title="AWS S3 Storage Classes"
        )
        assert new_title == "Azure Functions Pricing"


@pytest.mark.asyncio
async def test_generate_chat_title_subsequent_same_topic():
    from api.chat_routes import pipeline
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value="AWS S3 Storage Classes")
    with patch.object(pipeline, "get_summarizer_llm", return_value=mock_llm):
        new_title = await pipeline.generate_chat_title(
            "What about Glacier Flexible Retrieval?",
            "Glacier Flexible Retrieval takes 3-5 hours...",
            current_title="AWS S3 Storage Classes"
        )
        assert new_title == "AWS S3 Storage Classes"


def test_session_messages_unauthorized():
    from app import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    res_unauth = client.get("/api/sessions/sess-abc/messages")
    assert res_unauth.status_code == 401
