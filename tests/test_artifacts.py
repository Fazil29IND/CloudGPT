"""Automated tests for CloudGPT Artifacts System.

Covers:
  - POST /api/artifacts (create artifact)
  - GET /api/artifacts?session_id=... (list session artifacts)
  - GET /api/artifacts/{id}/download (download with Content-Disposition)
  - User isolation (cannot download another user's artifact)
"""

from __future__ import annotations

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

ARTIFACTS_USER_A = "art_user_a@test.cloudgpt.local"
ARTIFACTS_USER_B = "art_user_b@test.cloudgpt.local"


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


def login_user(client: TestClient, email: str, name: str) -> tuple[dict, str]:
    user = db.create_user(
        email=email,
        password="ArtifactPass123",
        name=name,
        email_verified=True,
    )
    res = client.get("/")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post("/login-email", data={"email": email, "csrf_token": csrf})
    res = client.get(f"/password?email={email}")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post("/login", data={"email": email, "password": "ArtifactPass123", "csrf_token": csrf})

    res = client.get("/chat")
    csrf_match = re.search(r'name="csrf-token"\s+content="([^"]+)"', res.text)
    csrf_token = csrf_match.group(1) if csrf_match else csrf
    return user, csrf_token


def test_artifact_lifecycle(client):
    user_a, csrf_a = login_user(client, ARTIFACTS_USER_A, "Artifact User A")
    session_id = f"sess_art_{uuid.uuid4()}"

    # 1. Create artifact
    content_text = "print('Hello CloudGPT Artifact')"
    create_res = client.post(
        "/api/artifacts",
        json={
            "filename": "hello.py",
            "content": content_text,
            "mime": "text/x-python",
            "session_id": session_id,
        },
        headers={"X-CSRF-Token": csrf_a},
    )
    assert create_res.status_code == 200
    art_data = create_res.json()["artifact"]
    artifact_id = art_data["id"]
    assert art_data["filename"] == "hello.py"
    assert art_data["size"] == len(content_text.encode("utf-8"))

    # 2. List artifacts for session
    list_res = client.get(f"/api/artifacts?session_id={session_id}")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) == 1
    assert items[0]["id"] == artifact_id

    # 3. Download artifact
    dl_res = client.get(f"/api/artifacts/{artifact_id}/download")
    assert dl_res.status_code == 200
    assert dl_res.text == content_text
    assert 'attachment; filename="hello.py"' in dl_res.headers.get("Content-Disposition", "")


def test_artifact_user_isolation(client):
    user_a, csrf_a = login_user(client, ARTIFACTS_USER_A, "Artifact User A")
    session_id = f"sess_art_iso_{uuid.uuid4()}"

    create_res = client.post(
        "/api/artifacts",
        json={
            "filename": "confidential.txt",
            "content": "Secret Content",
            "mime": "text/plain",
            "session_id": session_id,
        },
        headers={"X-CSRF-Token": csrf_a},
    )
    assert create_res.status_code == 200
    artifact_id = create_res.json()["artifact"]["id"]

    # Now create client for User B
    import app as app_module
    client_b = TestClient(app_module.app)
    user_b, csrf_b = login_user(client_b, ARTIFACTS_USER_B, "Artifact User B")

    # User B cannot download User A's artifact
    dl_res = client_b.get(f"/api/artifacts/{artifact_id}/download")
    assert dl_res.status_code == 404
