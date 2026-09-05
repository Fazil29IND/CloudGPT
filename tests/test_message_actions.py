"""Automated tests for CloudGPT Message Actions & Feedback.

Covers:
  - db.truncate_messages_from and /api/sessions/{id}/truncate
  - db.save_message_feedback and /api/feedback
  - /api/attachments/{id}/download
"""

from __future__ import annotations

import base64
import os
import re
import sys
import uuid
import psycopg2
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:Fazil@localhost:5000/pygpt"
)
os.environ["ENVIRONMENT"] = "development"

import db
from core.redis_client import redis_client
from file_processor import ATTACHMENT_KEY_PREFIX

ACTIONS_EMAIL = "actionsuser@test.cloudgpt.local"


@pytest.fixture(autouse=True)
def clean_test_db():
    yield
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE email LIKE %s", ("%@test.cloudgpt.local",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def client():
    import app as app_module
    return TestClient(app_module.app)


@pytest.fixture
def logged_in_client(client):
    """A TestClient authenticated as a test user."""
    user = db.create_user(
        email=ACTIONS_EMAIL,
        password="ActionsPass123",
        name="Actions User",
        email_verified=True,
    )
    res = client.get("/")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post("/login-email", data={"email": ACTIONS_EMAIL, "csrf_token": csrf})
    res = client.get(f"/password?email={ACTIONS_EMAIL}")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post("/login", data={"email": ACTIONS_EMAIL, "password": "ActionsPass123", "csrf_token": csrf})

    res = client.get("/chat")
    csrf_match = re.search(r'name="csrf-token"\s+content="([^"]+)"', res.text)
    csrf_token = csrf_match.group(1) if csrf_match else csrf
    return client, user, csrf_token


def test_truncate_messages_from_db():
    user = db.create_user(
        email="truncate_db@test.cloudgpt.local",
        password="Pass1234",
        name="Truncate DB User",
        email_verified=True,
    )
    user_id = user["id"]
    session_id = f"sess_{uuid.uuid4()}"

    m1_id = db.add_message(user_id, session_id, "user", "Prompt 1")
    m2_id = db.add_message(user_id, session_id, "assistant", "Answer 1")
    m3_id = db.add_message(user_id, session_id, "user", "Prompt 2")
    m4_id = db.add_message(user_id, session_id, "assistant", "Answer 2")

    history = db.get_chat_history(user_id, session_id)
    assert len(history) == 4

    deleted = db.truncate_messages_from(user_id, session_id, m3_id)
    assert deleted == 2

    history_after = db.get_chat_history(user_id, session_id)
    assert len(history_after) == 2
    assert [m["content"] for m in history_after] == ["Prompt 1", "Answer 1"]


def test_truncate_session_api(logged_in_client):
    client, user, csrf = logged_in_client
    session_id = f"sess_api_{uuid.uuid4()}"

    m1 = db.add_message(user["id"], session_id, "user", "API Prompt 1")
    m2 = db.add_message(user["id"], session_id, "assistant", "API Answer 1")
    m3 = db.add_message(user["id"], session_id, "user", "API Prompt 2")

    res = client.post(
        f"/api/sessions/{session_id}/truncate",
        json={"message_id": m3},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["deleted"] == 1

    remaining = db.get_chat_history(user["id"], session_id)
    assert len(remaining) == 2
    assert remaining[0]["id"] == m1
    assert remaining[1]["id"] == m2


def test_save_feedback_api(logged_in_client):
    client, user, csrf = logged_in_client
    session_id = f"sess_fb_{uuid.uuid4()}"

    m1 = db.add_message(user["id"], session_id, "user", "Question")
    m2 = db.add_message(user["id"], session_id, "assistant", "Response to evaluate")

    # Post like (+1)
    res = client.post(
        "/api/feedback",
        json={"message_id": m2, "rating": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["feedback"]["rating"] == 1

    # Update to dislike (-1) - upsert test
    res = client.post(
        "/api/feedback",
        json={"message_id": m2, "rating": -1, "reason": "Not helpful"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["feedback"]["rating"] == -1
    assert res.json()["feedback"]["reason"] == "Not helpful"


@pytest.mark.asyncio
async def test_download_attachment_api(logged_in_client):
    client, user, csrf = logged_in_client
    att_id = str(uuid.uuid4())
    content = b"Sample attachment binary content"

    payload = {
        "attachment_id": att_id,
        "user_id": user["id"],
        "filename": "sample.bin",
        "size": len(content),
        "content_type": "application/octet-stream",
        "content": "[Attachment: sample.bin]",
        "kind": "file",
        "raw_bytes_base64": base64.b64encode(content).decode("ascii"),
    }
    await redis_client.set_json(f"{ATTACHMENT_KEY_PREFIX}{att_id}", payload, ex=300)
    from file_processor import _MEMORY_STAGED
    _MEMORY_STAGED[att_id] = payload

    res = client.get(f"/api/attachments/{att_id}/download")
    assert res.status_code == 200
    assert res.content == content
    assert 'attachment; filename="sample.bin"' in res.headers.get("Content-Disposition", "")
