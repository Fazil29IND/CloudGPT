import time
from unittest.mock import AsyncMock, MagicMock, patch
import jwt
import pytest
from fastapi import HTTPException

from core.rate_limit import SlidingWindowRateLimiter
from core.security import create_jwt_token, decode_jwt_token
from services.billing import (
    BillingConfigurationError,
    CustomerNotFoundError,
    InvalidCheckoutSessionError,
    InvalidWebhookSignatureError,
)


TEST_SECRET = "a" * 32
TEST_SECRET_B = "b" * 32


def test_jwt_valid_roundtrip():
    payload = {"sub": "user-123", "role": "admin"}
    token = create_jwt_token(payload, secret=TEST_SECRET, expires_in=60)
    decoded = decode_jwt_token(token, secret=TEST_SECRET)
    assert decoded["sub"] == "user-123"
    assert decoded["role"] == "admin"


def test_jwt_expired_raises_401():
    payload = {"sub": "user-123", "exp": int(time.time()) - 100}
    token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc_info:
        decode_jwt_token(token, secret=TEST_SECRET)
    assert exc_info.value.status_code == 401
    assert "Token has expired" in exc_info.value.detail


def test_jwt_invalid_algorithm_raises_401():
    payload = {"sub": "user-123"}
    # encode with 'none' or another algorithm unsupported
    token = jwt.encode(payload, "", algorithm="none")
    with pytest.raises(HTTPException) as exc_info:
        decode_jwt_token(token, secret=TEST_SECRET)
    assert exc_info.value.status_code == 401
    assert "Invalid token algorithm" in exc_info.value.detail or "Invalid token" in exc_info.value.detail


def test_jwt_invalid_signature_raises_401():
    token = create_jwt_token({"sub": "user-123"}, secret=TEST_SECRET)
    with pytest.raises(HTTPException) as exc_info:
        decode_jwt_token(token, secret=TEST_SECRET_B)
    assert exc_info.value.status_code == 401
    assert "Invalid token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_rate_limiter_redis_reconnect_resets_local_state():
    limiter = SlidingWindowRateLimiter()

    # Simulate fallback to local
    limiter._fallback_since = time.monotonic()
    limiter._events["test-key"].append(time.monotonic())
    assert len(limiter._events["test-key"]) == 1

    # Now mock Redis client as available, with an atomic Lua script that admits
    mock_redis = MagicMock()
    mock_script = MagicMock()
    mock_script.return_value = 1
    mock_redis.register_script.return_value = mock_script

    with patch("core.rate_limit.redis_client") as mock_rc:
        mock_rc.is_available = True
        mock_rc.client = mock_redis

        allowed = await limiter.allowed_async("test-key", 10)
        assert allowed is True
        # Verify local state was flushed upon reconnect
        assert not hasattr(limiter, "_fallback_since")
        assert len(limiter._events) == 0
        # The atomic window script received the right key and limits
        kwargs = mock_script.call_args.kwargs
        assert kwargs["keys"] == ["ratelimit:test-key"]
        assert kwargs["args"][2] == 10


@pytest.mark.asyncio
async def test_rate_limiter_redis_window_full_denies_atomically():
    limiter = SlidingWindowRateLimiter()

    mock_redis = MagicMock()
    mock_script = MagicMock()
    mock_script.return_value = 0  # window full — no admission
    mock_redis.register_script.return_value = mock_script

    with patch("core.rate_limit.redis_client") as mock_rc:
        mock_rc.is_available = True
        mock_rc.client = mock_redis

        assert await limiter.allowed_async("full-key", 1) is False
        # Denied requests must not leak into the local fallback window
        assert "full-key" not in limiter._events


def test_billing_checkout_configuration_error_returns_503(client):
    with patch("api.billing_routes.get_current_user", return_value={"id": 1, "email": "test@example.com"}):
        with patch("api.billing_routes.billing_service.checkout", side_effect=BillingConfigurationError("Stripe key missing")):
            response = client.post("/api/billing/checkout", json={"plan": "pro", "interval": "month"}, headers={"X-CSRF-Token": "test"})
            assert response.status_code == 503
            assert response.json()["detail"] == "Billing provider not configured"


def test_billing_checkout_provider_url_error_returns_502(client):
    with patch("api.billing_routes.get_current_user", return_value={"id": 1, "email": "test@example.com"}):
        with patch("api.billing_routes.billing_service.checkout", side_effect=InvalidCheckoutSessionError("No URL returned")):
            response = client.post("/api/billing/checkout", json={"plan": "pro", "interval": "month"}, headers={"X-CSRF-Token": "test"})
            assert response.status_code == 502
            assert response.json()["detail"] == "Payment provider did not return checkout URL"


def test_billing_portal_customer_not_found_returns_404(client):
    with patch("api.billing_routes.get_current_user", return_value={"id": 1, "email": "test@example.com"}):
        with patch("api.billing_routes._is_allowed_return_url", return_value=True):
            with patch("api.billing_routes.billing_service.portal", side_effect=CustomerNotFoundError("No billing customer")):
                response = client.post("/api/billing/portal", json={"return_url": "http://localhost:5001/billing"}, headers={"X-CSRF-Token": "test"})
                assert response.status_code == 404
                assert response.json()["detail"] == "No billing customer exists for this account"


def test_billing_webhook_invalid_signature_returns_400(client):
    with patch("api.billing_routes.billing_service.process_webhook_event", side_effect=InvalidWebhookSignatureError("bad sig")):
        response = client.post("/api/billing/webhook", data=b"{}", headers={"stripe-signature": "invalid"})
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid webhook signature"
