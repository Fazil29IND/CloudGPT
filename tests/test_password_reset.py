"""
Automated tests for the CloudGPT password reset flow (Phase 3).

Covers:
  - Forgot-password page renders
  - Requesting a reset for an existing account creates a token and sends email
  - Anti-enumeration: unknown email gets the same response
  - Full reset flow: request → token → new password → login works
  - Expired token rejected
  - Token reuse prevented
  - CSRF enforced on the reset form
  - Weak password / mismatch rejected

Run with:
    python -m pytest tests/test_password_reset.py -v
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

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
from app import app

TEST_EMAIL = "resettest@test.cloudgpt.local"


@pytest.fixture(autouse=True)
def clean_test_db():
    """Remove test users before and after each test."""
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
    return TestClient(app)


@pytest.fixture
def password_user():
    """A password-based user for reset flows."""
    user = db.create_user(
        email=TEST_EMAIL,
        password="OriginalPass123",
        name="Reset Test",
        email_verified=True,
    )
    return user


def _get_csrf_token(client: TestClient) -> str:
    res = client.get("/forgot-password")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text)
    return match.group(1) if match else ""


def _insert_expired_token(user_id: int) -> str:
    """Insert an already-expired reset token; return the raw token."""
    token = "expired-token-value"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                (user_id, token_hash, datetime.now(timezone.utc) - timedelta(minutes=5)),
            )
        conn.commit()
    finally:
        conn.close()
    return token


# ── Page rendering ───────────────────────────────────────────────────────────

def test_forgot_password_page_renders(client):
    response = client.get("/forgot-password")
    assert response.status_code == 200
    assert "Forgot Password" in response.text
    assert "csrf_token" in response.text


def test_login_page_links_forgot_password(client):
    # Initial email-entry page does not have forgot-password
    res_index = client.get("/")
    assert res_index.status_code == 200
    assert "/forgot-password" not in res_index.text

    # Password page contains forgot-password
    res_pwd = client.get("/password?email=test@test.cloudgpt.local")
    assert res_pwd.status_code == 200
    assert "/forgot-password" in res_pwd.text


# ── Requesting a reset ───────────────────────────────────────────────────────

def test_forgot_password_existing_account_sends_email(client, password_user):
    """An existing password account gets a token stored and an email dispatched."""
    with patch("app.enqueue_task", new_callable=AsyncMock, return_value=None) as mock_enqueue, \
         patch("app.email_service.send_email", new_callable=AsyncMock, return_value=True) as mock_send:
        response = client.post(
            "/forgot-password",
            data={"email": TEST_EMAIL, "csrf_token": _get_csrf_token(client)},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "reset+link+has+been+sent" in response.headers["location"]

    # Delivery is either enqueued to the ARQ worker or sent inline.
    if mock_enqueue.called:
        kwargs = mock_enqueue.call_args.kwargs
    else:
        assert mock_send.called
        kwargs = mock_send.call_args.kwargs
    assert kwargs["to"] == TEST_EMAIL
    assert "reset-password?token=" in kwargs["html_body"]


def test_forgot_password_unknown_email_same_response(client):
    """Anti-enumeration: unknown email yields identical redirect."""
    with patch("app.enqueue_task", new_callable=AsyncMock, return_value=None), \
         patch("app.email_service.send_email", new_callable=AsyncMock, return_value=True) as mock_send:
        response = client.post(
            "/forgot-password",
            data={"email": "ghost@nowhere.test", "csrf_token": _get_csrf_token(client)},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "reset+link+has+been+sent" in response.headers["location"]
    assert not mock_send.called


def test_forgot_password_google_only_account_no_email(client, password_user):
    """OAuth-only accounts (no password) cannot reset a password."""
    db.create_user(
        email="googleonly.reset@test.cloudgpt.local",
        password=None,
        email_verified=True,
    )
    with patch("app.enqueue_task", new_callable=AsyncMock, return_value=None), \
         patch("app.email_service.send_email", new_callable=AsyncMock, return_value=True) as mock_send:
        client.post(
            "/forgot-password",
            data={"email": "googleonly.reset@test.cloudgpt.local", "csrf_token": _get_csrf_token(client)},
            follow_redirects=False,
        )
    assert not mock_send.called


# ── Full reset flow ──────────────────────────────────────────────────────────

def _request_reset_token(client: TestClient) -> str:
    """Drive the forgot-password endpoint and extract the token from the email.

    Works whether delivery is enqueued to the ARQ worker (mocked here) or sent
    inline through the email service.
    """
    with patch("app.enqueue_task", new_callable=AsyncMock, return_value=None) as mock_enqueue, \
         patch("app.email_service.send_email", new_callable=AsyncMock, return_value=True) as send:
        response = client.post(
            "/forgot-password",
            data={"email": TEST_EMAIL, "csrf_token": _get_csrf_token(client)},
            follow_redirects=False,
        )
        assert response.status_code == 302
        if mock_enqueue.called:
            kwargs = mock_enqueue.call_args.kwargs
        else:
            assert send.called, "email was neither enqueued nor sent"
            kwargs = send.call_args.kwargs
        html = kwargs["html_body"]
    match = re.search(r"reset-password\?token=([A-Za-z0-9_\-]+)", html)
    assert match, "reset URL not found in email body"
    return match.group(1)


def test_reset_page_valid_token_shows_form(client, password_user):
    token = _request_reset_token(client)
    response = client.get(f"/reset-password?token={token}")
    assert response.status_code == 200
    assert "Choose a New Password" in response.text
    assert "This password reset link is invalid" not in response.text


def test_reset_page_invalid_token_rejects(client):
    response = client.get("/reset-password?token=not-a-real-token")
    assert response.status_code == 200
    assert "invalid, expired, or already used" in response.text


def test_full_password_reset_flow(client, password_user):
    """Request → email → set new password → login with new password works."""
    token = _request_reset_token(client)

    # Reset page form
    res = client.get(f"/reset-password?token={token}")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)

    response = client.post(
        "/reset-password",
        data={
            "token": token,
            "password": "BrandNewPassword456!",
            "confirm_password": "BrandNewPassword456!",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "password+has+been+reset" in response.headers["location"]

    # Old password no longer valid; new one is
    user = db.get_user_by_email(TEST_EMAIL)
    assert not db.verify_password(user, "OriginalPass123")
    assert db.verify_password(user, "BrandNewPassword456!")


def test_expired_token_rejected(client, password_user):
    token = _insert_expired_token(password_user["id"])
    res = client.get(f"/reset-password?token={token}")
    assert "invalid, expired, or already used" in res.text

    # Even with a valid CSRF token from another page, submission must fail.
    csrf = _get_csrf_token(client)
    response = client.post(
        "/reset-password",
        data={
            "token": token,
            "password": "BrandNewPassword456!",
            "confirm_password": "BrandNewPassword456!",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 400
    assert "invalid, expired, or already used" in response.text


def test_token_single_use(client, password_user):
    """A consumed token cannot be used a second time."""
    token = _request_reset_token(client)

    res = client.get(f"/reset-password?token={token}")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)

    first = client.post(
        "/reset-password",
        data={"token": token, "password": "FirstResetPass123!", "confirm_password": "FirstResetPass123!", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert first.status_code == 302

    # Second use must fail
    res2 = client.get(f"/reset-password?token={token}")
    assert "invalid, expired, or already used" in res2.text

    second = client.post(
        "/reset-password",
        data={"token": token, "password": "SecondResetPass456!", "confirm_password": "SecondResetPass456!", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert second.status_code == 400
    user = db.get_user_by_email(TEST_EMAIL)
    assert db.verify_password(user, "FirstResetPass123!")


def test_reset_requires_csrf(client, password_user):
    token = _request_reset_token(client)
    response = client.post(
        "/reset-password",
        data={"token": token, "password": "BrandNewPassword456!", "confirm_password": "BrandNewPassword456!"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_reset_rejects_mismatched_and_weak_passwords(client, password_user):
    token = _request_reset_token(client)
    res = client.get(f"/reset-password?token={token}")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)

    mismatch = client.post(
        "/reset-password",
        data={"token": token, "password": "BrandNewPassword456!", "confirm_password": "Different789!", "csrf_token": csrf},
    )
    assert mismatch.status_code == 400
    assert "do not match" in mismatch.text

    weak = client.post(
        "/reset-password",
        data={"token": token, "password": "weakpassword", "confirm_password": "weakpassword", "csrf_token": csrf},
    )
    assert weak.status_code == 400

    user = db.get_user_by_email(TEST_EMAIL)
    assert db.verify_password(user, "OriginalPass123")

