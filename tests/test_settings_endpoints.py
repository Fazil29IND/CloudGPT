"""Automated tests for CloudGPT Settings Panel & Preferences.

Covers:
  - POST /api/user/profile (display name update)
  - POST /api/user/change-password (password change rules and validation)
  - DELETE /api/user/memory (bulk memory clear)
  - POST /api/user/preferences (settings_json key/value persistence)
  - HTML rendering of Settings button and modal in chat.html
"""

from __future__ import annotations

import os
import re
import sys
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
import app as app_module

SETTINGS_EMAIL = "settingsuser@test.cloudgpt.local"
SETTINGS_OAUTH_EMAIL = "settingsoauth@test.cloudgpt.local"


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
    return TestClient(app_module.app)


@pytest.fixture
def logged_in_client(client):
    """A TestClient authenticated as a test user with a password."""
    user = db.create_user(
        email=SETTINGS_EMAIL,
        password="ValidPass123!",
        name="Initial Name",
        email_verified=True,
    )
    res = client.get("/")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post("/login-email", data={"email": SETTINGS_EMAIL, "csrf_token": csrf})
    res = client.get(f"/password?email={SETTINGS_EMAIL}")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post("/login", data={"email": SETTINGS_EMAIL, "password": "ValidPass123!", "csrf_token": csrf})

    res = client.get("/chat")
    csrf_match = re.search(r'name="csrf-token"\s+content="([^"]+)"', res.text)
    csrf_token = csrf_match.group(1) if csrf_match else csrf
    return client, user, csrf_token


def test_update_user_profile(logged_in_client):
    client, user, csrf_token = logged_in_client

    # Empty name fails validation
    res = client.post(
        "/api/user/profile",
        json={"name": "   "},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res.status_code == 422

    # Valid name update
    res = client.post(
        "/api/user/profile",
        json={"name": "Jane Cloud Architect"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res.status_code == 200
    assert res.json() == {"success": True, "name": "Jane Cloud Architect"}

    # Verify updated in DB
    updated_user = db.get_user_by_id(user["id"])
    assert updated_user["name"] == "Jane Cloud Architect"


def test_change_password_flow(logged_in_client):
    client, user, csrf_token = logged_in_client

    # 1. Wrong current password
    res = client.post(
        "/api/user/change-password",
        json={
            "current_password": "WrongPassword123!",
            "new_password": "NewSecretPass456!",
            "confirm_password": "NewSecretPass456!",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res.status_code == 400
    assert "Current password is incorrect" in res.json()["detail"]

    # 2. Passwords do not match
    res = client.post(
        "/api/user/change-password",
        json={
            "current_password": "ValidPass123!",
            "new_password": "NewSecretPass456!",
            "confirm_password": "MismatchedPassword!",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res.status_code == 422
    assert "Passwords do not match" in res.json()["detail"]

    # 3. Weak password (missing symbol or uppercase or number)
    res = client.post(
        "/api/user/change-password",
        json={
            "current_password": "ValidPass123!",
            "new_password": "weakpassword",
            "confirm_password": "weakpassword",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res.status_code == 422

    # 4. Successful password change
    res = client.post(
        "/api/user/change-password",
        json={
            "current_password": "ValidPass123!",
            "new_password": "NewSecretPass456!",
            "confirm_password": "NewSecretPass456!",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}

    # Verify old password no longer verifies
    refreshed_user = db.get_user_by_id(user["id"])
    assert not db.verify_password(refreshed_user, "ValidPass123!")
    assert db.verify_password(refreshed_user, "NewSecretPass456!")


def test_change_password_oauth_rejected(client):
    from unittest.mock import AsyncMock, patch

    mock_token = {
        "userinfo": {
            "sub": "test-oauth-sub-123",
            "email": SETTINGS_OAUTH_EMAIL,
            "name": "OAuth User",
            "email_verified": True,
        }
    }
    with patch.object(
        app_module.oauth.google, "authorize_access_token", new_callable=AsyncMock, return_value=mock_token
    ):
        login_res = client.get("/auth/google/callback", follow_redirects=False)
        assert login_res.status_code == 302

    res = client.get("/chat")
    csrf_match = re.search(r'name="csrf-token"\s+content="([^"]+)"', res.text)
    csrf_token = csrf_match.group(1) if csrf_match else ""

    # Attempt to change password for OAuth account
    res = client.post(
        "/api/user/change-password",
        json={
            "current_password": "AnyPassword123!",
            "new_password": "NewSecretPass456!",
            "confirm_password": "NewSecretPass456!",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res.status_code == 400
    assert "Password change not available for OAuth accounts" in res.json()["detail"]



def test_bulk_clear_user_memory(logged_in_client):
    client, user, csrf_token = logged_in_client
    user_id = user["id"]

    # Seed memories for this user
    db.upsert_user_memory(user_id, "primary_cloud", "AWS", "cloud_infrastructure")
    db.upsert_user_memory(user_id, "primary_region", "us-east-1", "cloud_infrastructure")
    db.upsert_user_memory(user_id, "iac_tool", "Terraform", "devops_tools")

    memories = db.get_user_memories(user_id)
    assert len(memories) == 3

    # Clear all memories
    res = client.delete("/api/user/memory", headers={"X-CSRF-Token": csrf_token})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["deleted"] == 3

    # Verify memories are empty now
    assert len(db.get_user_memories(user_id)) == 0


def test_user_preferences_persistence(logged_in_client):
    client, user, csrf_token = logged_in_client
    user_id = user["id"]

    # 1. Reject unknown preference key
    res = client.post(
        "/api/user/preferences",
        json={"key": "unsupported_key", "value": "xyz"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res.status_code == 422
    assert "Unknown preference key" in res.json()["detail"]

    # 2. Save valid preference keys
    prefs_to_test = [
        ("response_language", "fr"),
        ("show_token_usage", False),
        ("compact_messages", True),
        ("default_thinking", "High"),
    ]
    for key, val in prefs_to_test:
        res = client.post(
            "/api/user/preferences",
            json={"key": key, "value": val},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        assert res.json() == {"success": True}

    # Verify stored in DB settings_json
    refreshed_user = db.get_user_by_id(user_id)
    settings = refreshed_user.get("settings_json") or {}
    assert settings["response_language"] == "fr"
    assert settings["show_token_usage"] is False
    assert settings["compact_messages"] is True
    assert settings["default_thinking"] == "High"


def test_unauthenticated_rejected(client):
    assert client.post("/api/user/profile", json={"name": "A"}).status_code == 401
    assert client.post("/api/user/change-password", json={"current_password": "a", "new_password": "b", "confirm_password": "b"}).status_code == 401
    assert client.delete("/api/user/memory").status_code == 401
    assert client.post("/api/user/preferences", json={"key": "compact_messages", "value": True}).status_code == 401


def test_dashboard_renders_settings_ui(logged_in_client):
    client, user, csrf_token = logged_in_client

    res = client.get("/dashboard")
    assert res.status_code == 200
    html = res.text

    # Settings button is present in sidebar
    assert 'id="settings-btn"' in html
    # Header memory button is removed
    assert 'id="memory-btn"' not in html
    assert 'class="header-actions"' not in html
    # Settings modal is present
    assert 'id="settings-modal"' in html
    # Settings modal has all 5 tabs
    assert 'data-tab="account"' in html
    assert 'data-tab="memory"' in html
    assert 'data-tab="preferences"' in html
    assert 'data-tab="subscription"' in html
    assert 'data-tab="about"' in html
    # Preferences data attribute is present
    assert 'data-user-settings=' in html
    # Sign out form is inside settings modal
    assert 'class="settings-logout-form"' in html
