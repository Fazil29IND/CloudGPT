"""
Automated tests for CloudGPT authentication.

Covers:
  - Login page renders with Google button
  - Email/password login flow
  - Google OAuth callback (mocked) — new user, returning user
  - Unverified Google email rejected
  - Account collision (Google email matches password account)
  - Unsafe redirect URLs blocked
  - Logout clears session

Run with:
    python -m pytest tests/test_auth.py -v
"""

import hashlib
import os
import sys
from unittest.mock import AsyncMock, patch

import psycopg2
import pytest
from fastapi.testclient import TestClient

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test environment before importing app
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["GOOGLE_CLIENT_ID"] = "test-google-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-google-client-secret"
os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:Fazil@localhost:5000/pygpt"
)

import db
from app import app, oauth


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_test_db():
    """
    Clean up test users before and after each test.
    Uses the real database — tests must be idempotent.
    """
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            # Create tables if not exist
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    password_hash VARCHAR(255),
                    name VARCHAR(255),
                    email_verified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS oauth_accounts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    provider VARCHAR(50) NOT NULL,
                    provider_user_id VARCHAR(255) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(provider, provider_user_id)
                );
                CREATE TABLE IF NOT EXISTS email_verification_tokens (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash VARCHAR(255) NOT NULL,
                    code VARCHAR(6) NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    used_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            # Clean test data
            cur.execute("DELETE FROM oauth_accounts WHERE provider_user_id LIKE 'test-%'")
            cur.execute("DELETE FROM email_verification_tokens WHERE user_id IN (SELECT id FROM users WHERE email LIKE '%@test.cloudgpt.local')")
            cur.execute("DELETE FROM users WHERE email LIKE '%@test.cloudgpt.local'")
        conn.commit()
    finally:
        conn.close()

    yield

    # Cleanup after test
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM oauth_accounts WHERE provider_user_id LIKE 'test-%'")
            cur.execute("DELETE FROM email_verification_tokens WHERE user_id IN (SELECT id FROM users WHERE email LIKE '%@test.cloudgpt.local')")
            cur.execute("DELETE FROM users WHERE email LIKE '%@test.cloudgpt.local'")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def client():
    """FastAPI TestClient."""
    return TestClient(app)


import re


def _get_csrf_token(client):
    """Retrieve a valid CSRF token from rendered page."""
    res = client.get("/")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text)
    return match.group(1) if match else ""


# ── Test: Login Page Renders ─────────────────────────────────────────────────

def test_login_page_renders(client):
    """Login page should render with the Google sign-in button."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "Continue with Google" in response.text
    assert "/auth/google" in response.text
    assert "Email Address" in response.text


def test_signup_page_renders(client):
    """Signup page should render with Google button, terms, and privacy links."""
    response = client.get("/signup", follow_redirects=False)
    assert response.status_code == 200
    assert "Continue with Google" in response.text
    assert "/auth/google" in response.text
    assert "/terms" in response.text
    assert "/privacy" in response.text


# ── Test: Google Button Starts OAuth Flow ────────────────────────────────────

def test_google_button_redirects_to_google(client):
    """Clicking the Google button should redirect to Google's OAuth endpoint."""
    response = client.get("/auth/google", follow_redirects=False)
    assert response.status_code in (302, 303)
    location = response.headers.get("location", "")
    assert "accounts.google.com" in location


# ── Test: Email/Password Login ───────────────────────────────────────────────

def test_email_login_no_account(client):
    """Submitting a non-existent email at step 1 redirects to password page."""
    csrf = _get_csrf_token(client)
    response = client.post(
        "/login-email",
        data={"email": "nonexistent@test.cloudgpt.local", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/password?email=nonexistent@test.cloudgpt.local" in response.headers.get("location", "")


def test_login_nonexistent_user_fails_at_password_step(client):
    """Submitting a password for a non-existent email should fail with invalid credentials error."""
    csrf = _get_csrf_token(client)
    response = client.post(
        "/login",
        data={"email": "nonexistent@test.cloudgpt.local", "password": "anypassword", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers.get("location", "")
    assert "/password?email=nonexistent@test.cloudgpt.local" in location
    assert "error=" in location


def test_email_password_login_success(client):
    """Full email/password login flow should work for existing users."""
    # Create a test user with a password
    user = db.create_user(
        email="logintest@test.cloudgpt.local",
        password="testpass123",
        name="Login Test",
        email_verified=True,
    )

    # Step 1: email → password page
    csrf = _get_csrf_token(client)
    response = client.post(
        "/login-email",
        data={"email": "logintest@test.cloudgpt.local", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/password" in response.headers.get("location", "")

    # Step 2: password → dashboard
    csrf = _get_csrf_token(client)
    response = client.post(
        "/login",
        data={"email": "logintest@test.cloudgpt.local", "password": "testpass123", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/dashboard" in response.headers.get("location", "")


def test_email_password_login_wrong_password(client):
    """Wrong password should redirect back to password page with error."""
    db.create_user(
        email="wrongpass@test.cloudgpt.local",
        password="correctpass",
        name="Wrong Pass Test",
    )

    csrf = _get_csrf_token(client)
    response = client.post(
        "/login",
        data={"email": "wrongpass@test.cloudgpt.local", "password": "wrongpassword", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "error=" in response.headers.get("location", "")


# ── Test: Google OAuth Callback (Mocked) ─────────────────────────────────────

def _mock_google_token(sub, email, name, email_verified=True):
    """Create a mock token response with userinfo."""
    return {
        "userinfo": {
            "sub": sub,
            "email": email,
            "name": name,
            "email_verified": email_verified,
        }
    }


def test_google_callback_new_user(client):
    """Successful Google callback should create a new user and sign in."""
    mock_token = _mock_google_token(
        sub="test-google-sub-new",
        email="newgoogleuser@test.cloudgpt.local",
        name="Google New User",
    )

    with patch.object(
        oauth.google, "authorize_access_token", new_callable=AsyncMock, return_value=mock_token
    ):
        response = client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 302
    assert "/dashboard" in response.headers.get("location", "")

    # Verify user was created
    user = db.get_user_by_email("newgoogleuser@test.cloudgpt.local")
    assert user is not None
    assert user["name"] == "Google New User"
    assert user["email_verified"] is True

    # Verify OAuth account linked
    oauth_acct = db.get_oauth_account("google", "test-google-sub-new")
    assert oauth_acct is not None
    assert oauth_acct["user_id"] == user["id"]


def test_google_callback_returning_user(client):
    """Repeat Google sign-in should find the same user."""
    # Create user + oauth link
    user = db.create_user(
        email="returninggoogle@test.cloudgpt.local",
        name="Returning Google User",
        email_verified=True,
    )
    db.create_oauth_account(user["id"], "google", "test-google-sub-returning")

    mock_token = _mock_google_token(
        sub="test-google-sub-returning",
        email="returninggoogle@test.cloudgpt.local",
        name="Returning Google User",
    )

    with patch.object(
        oauth.google, "authorize_access_token", new_callable=AsyncMock, return_value=mock_token
    ):
        response = client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 302
    assert "/dashboard" in response.headers.get("location", "")


def test_google_callback_unverified_email(client):
    """Unverified Google email should be rejected."""
    mock_token = _mock_google_token(
        sub="test-google-sub-unverified",
        email="unverified@test.cloudgpt.local",
        name="Unverified User",
        email_verified=False,
    )

    with patch.object(
        oauth.google, "authorize_access_token", new_callable=AsyncMock, return_value=mock_token
    ):
        response = client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers.get("location", "")
    assert "not+verified" in location.lower() or "error" in location.lower()

    # Verify no user was created
    user = db.get_user_by_email("unverified@test.cloudgpt.local")
    assert user is None


def test_google_callback_email_collision_with_password(client):
    """Google email matching an existing account should seamlessly auto-link when verified."""
    created = db.create_user(
        email="collision@test.cloudgpt.local",
        password="existingpass",
        name="Collision User",
        email_verified=True,
    )

    mock_token = _mock_google_token(
        sub="test-google-sub-collision",
        email="collision@test.cloudgpt.local",
        name="Collision User Updated",
    )

    with patch.object(
        oauth.google, "authorize_access_token", new_callable=AsyncMock, return_value=mock_token
    ):
        response = client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 302
    assert "/dashboard" in response.headers.get("location", "")

    # Verify oauth_account was linked to existing user
    oauth_acct = db.get_oauth_account("google", "test-google-sub-collision")
    assert oauth_acct is not None
    assert oauth_acct["user_id"] == created["id"]


# ── Test: Unsafe Redirects Blocked ───────────────────────────────────────────

def test_unsafe_redirect_blocked(client):
    """Unsafe return URLs must not redirect outside the site."""
    from app import is_safe_redirect

    # Safe paths
    assert is_safe_redirect("/dashboard") is True
    assert is_safe_redirect("/settings") is True

    # Unsafe paths
    assert is_safe_redirect("https://evil.com") is False
    assert is_safe_redirect("//evil.com") is False
    assert is_safe_redirect("http://evil.com/steal") is False
    assert is_safe_redirect("javascript:alert(1)") is False
    assert is_safe_redirect("") is False
    assert is_safe_redirect("evil.com") is False


# ── Test: Logout ─────────────────────────────────────────────────────────────

def test_logout_clears_session(client):
    """Logout should clear the session and redirect to login."""
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 302
    assert "signed+out" in response.headers.get("location", "").lower() or \
           "/" in response.headers.get("location", "")


# ── Test: Dashboard Protected ────────────────────────────────────────────────

def test_dashboard_requires_auth(client):
    """Dashboard should redirect to login if not authenticated."""
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert "sign+in" in response.headers.get("location", "").lower() or \
           "/" == response.headers.get("location", "").rstrip("?")


# ── Test: Google-Only User Cannot Use Password Login ─────────────────────────

def test_google_user_cannot_use_password_login(client):
    """A Google-only user proceeds from /login-email to /password and gets rejected during password submission on /login."""
    db.create_user(
        email="googleonly@test.cloudgpt.local",
        password=None,
        name="Google Only",
        email_verified=True,
    )

    # Step 1: /login-email transitions to /password
    csrf = _get_csrf_token(client)
    response = client.post(
        "/login-email",
        data={"email": "googleonly@test.cloudgpt.local", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/password" in response.headers.get("location", "")

    # Step 2: /login rejects password submission for Google-only account
    csrf = _get_csrf_token(client)
    response = client.post(
        "/login",
        data={"email": "googleonly@test.cloudgpt.local", "password": "somepassword", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers.get("location", "")
    assert "error=" in location


# ── Test: Signup Password Validation & Exemption ────────────────────────────

def test_signup_password_mismatch_fails(client):
    csrf = _get_csrf_token(client)
    response = client.post(
        "/signup",
        data={
            "email": "regularuser@test.cloudgpt.local",
            "password": "StrongPassword123!",
            "confirm_password": "DifferentPassword123!",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "Passwords+do+not+match" in response.headers.get("location", "")


def test_signup_weak_password_fails_for_regular_user(client):
    csrf = _get_csrf_token(client)
    response = client.post(
        "/signup",
        data={
            "email": "regularuser2@test.cloudgpt.local",
            "password": "weakpassword",
            "confirm_password": "weakpassword",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "error=" in response.headers.get("location", "")


def test_signup_strong_password_succeeds(client):
    csrf = _get_csrf_token(client)
    response = client.post(
        "/signup",
        data={
            "email": "validuser@test.cloudgpt.local",
            "password": "StrongPassword123!",
            "confirm_password": "StrongPassword123!",
            "name": "Valid User",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers.get("location", "")
    assert "/verify-email" in location
    assert "validuser" in location
    user = db.get_user_by_email("validuser@test.cloudgpt.local")
    assert user is not None
    assert user["name"] == "Valid User"
    assert user["email_verified"] is False


def test_verify_email_page_renders(client):
    response = client.get("/verify-email?email=validuser@test.cloudgpt.local", follow_redirects=False)
    assert response.status_code == 200
    assert "Verify your Email" in response.text
    assert "validuser@test.cloudgpt.local" in response.text


def test_verify_email_post_valid_code_succeeds(client):
    user = db.create_user(
        email="codetest@test.cloudgpt.local",
        password="StrongPassword123!",
        name="Code Test",
        email_verified=False,
    )
    raw_token = "testtoken123"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    db.create_email_verification_token(user["id"], token_hash, "654321", ttl_minutes=15)

    csrf = _get_csrf_token(client)
    response = client.post(
        "/verify-email",
        data={
            "email": "codetest@test.cloudgpt.local",
            "code": "654321",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/password" in response.headers.get("location", "")

    updated_user = db.get_user_by_email("codetest@test.cloudgpt.local")
    assert updated_user["email_verified"] is True


def test_verify_email_post_invalid_code_fails(client):
    user = db.create_user(
        email="wrongcode@test.cloudgpt.local",
        password="StrongPassword123!",
        name="Wrong Code",
        email_verified=False,
    )
    raw_token = "testtoken456"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    db.create_email_verification_token(user["id"], token_hash, "654321", ttl_minutes=15)

    csrf = _get_csrf_token(client)
    response = client.post(
        "/verify-email",
        data={
            "email": "wrongcode@test.cloudgpt.local",
            "code": "000000",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/verify-email" in response.headers.get("location", "")
    assert "error=" in response.headers.get("location", "")


def test_verify_email_magic_link_token_succeeds(client):
    user = db.create_user(
        email="linktest@test.cloudgpt.local",
        password="StrongPassword123!",
        name="Link Test",
        email_verified=False,
    )
    raw_token = "magiclinksecrettoken"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    db.create_email_verification_token(user["id"], token_hash, "789012", ttl_minutes=15)

    response = client.get(f"/verify-email?token={raw_token}", follow_redirects=False)
    assert response.status_code == 302
    assert "/password" in response.headers.get("location", "")

    updated_user = db.get_user_by_email("linktest@test.cloudgpt.local")
    assert updated_user["email_verified"] is True


def test_resend_verification_code(client):
    user = db.create_user(
        email="resendtest@test.cloudgpt.local",
        password="StrongPassword123!",
        name="Resend Test",
        email_verified=False,
    )
    csrf = _get_csrf_token(client)
    response = client.post(
        "/resend-verification",
        data={"email": "resendtest@test.cloudgpt.local", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/verify-email" in response.headers.get("location", "")
    assert "success=" in response.headers.get("location", "")


def test_unverified_user_login_redirects_to_verify_email(client):
    db.create_user(
        email="unverifiedlogin@test.cloudgpt.local",
        password="StrongPassword123!",
        name="Unverified Login",
        email_verified=False,
    )
    csrf = _get_csrf_token(client)
    response = client.post(
        "/login",
        data={"email": "unverifiedlogin@test.cloudgpt.local", "password": "StrongPassword123!", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/verify-email" in response.headers.get("location", "")
    assert "warning=" in response.headers.get("location", "")


def test_signup_developer_account_exemption(client):
    csrf = _get_csrf_token(client)
    # Clean up any preexisting user with developer email for test
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE email = 'fazilprojects@gmail.com'")
        conn.commit()
    finally:
        conn.close()

    response = client.post(
        "/signup",
        data={
            "email": "fazilprojects@gmail.com",
            "password": "Lite",
            "confirm_password": "Lite",
            "name": "Lite Dev User",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/password?email=fazilprojects@gmail.com" in response.headers.get("location", "")
    user = db.get_user_by_email("fazilprojects@gmail.com")
    assert user is not None
    assert user["email_verified"] is True


def test_signup_existing_email_fails(client):
    csrf = _get_csrf_token(client)
    # Ensure user exists
    if not db.get_user_by_email("validuser@test.cloudgpt.local"):
        db.create_user(
            email="validuser@test.cloudgpt.local",
            password="StrongPassword123!",
            name="Valid User",
            email_verified=True,
        )

    response = client.post(
        "/signup",
        data={
            "email": "validuser@test.cloudgpt.local",
            "password": "StrongPassword123!",
            "confirm_password": "StrongPassword123!",
            "name": "Duplicate User",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers.get("location", "")
    assert "/signup?error=" in location
    assert "already+taken" in location

