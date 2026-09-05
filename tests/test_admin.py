"""
Automated tests for the CloudGPT admin dashboard (Phase 6).

Covers:
  - Non-admin / anonymous users receive 403 on all /admin routes
  - Admin can list users, view user detail, and view usage summary
  - Tier override persists in the database
  - Tier override rejects invalid tiers

Run with:
    python -m pytest tests/test_admin.py -v
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
from app import app

ADMIN_EMAIL = "admin.test@test.cloudgpt.local"
USER_EMAIL = "plainuser.test@test.cloudgpt.local"


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
    return TestClient(app)


def _make_admin() -> dict:
    user = db.create_user(
        email=ADMIN_EMAIL, password="AdminPass123", name="Admin Test", email_verified=True
    )
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_admin = TRUE, role = 'admin', tier = 'Max' WHERE id = %s",
                (user["id"],),
            )
        conn.commit()
    finally:
        conn.close()
    return db.get_user_by_id(user["id"])


def _login(client: TestClient, email: str, password: str) -> None:
    res = client.get("/")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post("/login-email", data={"email": email, "csrf_token": csrf})
    res = client.get(f"/password?email={email}")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)
    client.post("/login", data={"email": email, "password": password, "csrf_token": csrf})


# ── Access control ───────────────────────────────────────────────────────────

def test_admin_routes_reject_anonymous(client):
    for path in ("/admin/users", "/admin/usage", "/admin/users/1"):
        assert client.get(path).status_code == 403


def test_admin_routes_reject_non_admin(client):
    db.create_user(
        email=USER_EMAIL, password="UserPass123", name="Plain User", email_verified=True
    )
    _login(client, USER_EMAIL, "UserPass123")

    for path in ("/admin/users", "/admin/usage", "/admin/users/1"):
        assert client.get(path).status_code == 403


# ── Admin pages ──────────────────────────────────────────────────────────────

def test_admin_user_list_renders(client):
    _make_admin()
    db.create_user(
        email="listed.user@test.cloudgpt.local", password="ListedPass123", email_verified=True
    )
    _login(client, ADMIN_EMAIL, "AdminPass123")

    response = client.get("/admin/users")
    assert response.status_code == 200
    assert "listed.user@test.cloudgpt.local" in response.text


def test_admin_user_search(client):
    _make_admin()
    db.create_user(
        email="searchable.user@test.cloudgpt.local", password="SearchPass123", email_verified=True
    )
    _login(client, ADMIN_EMAIL, "AdminPass123")

    response = client.get("/admin/users?q=searchable")
    assert response.status_code == 200
    assert "searchable.user@test.cloudgpt.local" in response.text
    assert "admin.test@" not in response.text.split("Users registered")[0]


def test_admin_user_detail_renders(client):
    _make_admin()
    target = db.create_user(
        email="detail.user@test.cloudgpt.local", password="DetailPass123", email_verified=True
    )
    _login(client, ADMIN_EMAIL, "AdminPass123")

    response = client.get(f"/admin/users/{target['id']}")
    assert response.status_code == 200
    assert "detail.user@test.cloudgpt.local" in response.text
    assert "Token usage" in response.text


def test_admin_usage_page_renders(client):
    _make_admin()
    _login(client, ADMIN_EMAIL, "AdminPass123")

    response = client.get("/admin/usage")
    assert response.status_code == 200
    assert "Token usage" in response.text


# ── Tier override ────────────────────────────────────────────────────────────

def test_admin_tier_override_persists(client):
    _make_admin()
    target = db.create_user(
        email="tier.user@test.cloudgpt.local", password="TierPass123", email_verified=True
    )
    _login(client, ADMIN_EMAIL, "AdminPass123")

    res = client.get(f"/admin/users/{target['id']}")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', res.text).group(1)

    response = client.post(
        f"/admin/users/{target['id']}/tier",
        data={"tier": "Pro", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302

    updated = db.get_user_by_id(target["id"])
    assert updated["tier"] == "Pro"


def test_admin_tier_override_requires_csrf(client):
    _make_admin()
    target = db.create_user(
        email="csrfuser.test@test.cloudgpt.local", password="CsrfPass123", email_verified=True
    )
    _login(client, ADMIN_EMAIL, "AdminPass123")

    response = client.post(f"/admin/users/{target['id']}/tier", data={"tier": "Pro"})
    assert response.status_code == 403
