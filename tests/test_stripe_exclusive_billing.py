"""Tests for Stripe-Exclusive Billing Architecture.

Verifies complete removal of Razorpay, USD plan offers, hosted Stripe Checkout redirection,
Stripe Customer Portal creation, and signature-verified webhook subscription lifecycle.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from config import Settings
from services.billing import BillingService


def test_billing_service_provider_is_stripe():
    """Verify BillingService exclusively defaults to Stripe and rejects other providers."""
    settings = Settings(
        billing_enabled=True,
        payment_provider="stripe",
        stripe_secret_key="sk_test_12345",
    )
    service = BillingService(settings=settings)
    assert service.provider == "stripe"
    assert service.enabled is True

    # When provider is "none", enabled must be False even if billing_enabled is True
    settings_none = Settings(
        billing_enabled=True,
        payment_provider="none",
    )
    service_none = BillingService(settings=settings_none)
    assert service_none.enabled is False

    # Attempting to configure razorpay or any unknown provider must raise validation error
    with pytest.raises(Exception):
        Settings(billing_enabled=True, payment_provider="razorpay")
    with pytest.raises(Exception):
        Settings(billing_enabled=True, payment_provider="other")


def test_public_offers_reflect_usd_pricing():
    """Verify plan catalog presents standard USD pricing ($0 Lite, $29 Pro, $79 Max)."""
    settings = Settings(
        pro_price_usd=29,
        max_price_usd=79,
    )
    service = BillingService(settings=settings)
    offers = service.public_offers()
    offer_map = {o["key"]: o for o in offers}

    assert offer_map["lite"]["price_usd"] == 0
    assert offer_map["pro"]["price_usd"] == 29
    assert offer_map["max"]["price_usd"] == 79


def test_pricing_comparison_reflects_usd_cells():
    """Verify /pricing comparison table data uses USD rates."""
    settings = Settings(
        pro_price_usd=29,
        max_price_usd=79,
    )
    service = BillingService(settings=settings)
    comparison = service.pricing_comparison()
    price_row = next(r for r in comparison["rows"] if r["label"] == "Monthly price")
    assert price_row["cells"] == [0, 29, 79]


@pytest.mark.asyncio
async def test_checkout_returns_stripe_redirect_url():
    """Verify checkout creates Stripe checkout session and returns hosted URL."""
    settings = Settings(
        billing_enabled=True,
        payment_provider="stripe",
        stripe_secret_key="sk_test_123456",
        billing_success_url="http://localhost:5001/billing?checkout=success",
        billing_cancel_url="http://localhost:5001/billing?checkout=cancelled",
    )
    service = BillingService(settings=settings)

    mock_user = {"id": 101, "email": "architect@example.com"}

    with patch("stripe.checkout.Session.create") as mock_stripe_session, \
         patch("db.get_billing_customer", return_value={"provider_customer_id": "cus_101"}):
        mock_stripe_session.return_value = MagicMock(url="https://checkout.stripe.com/c/pay/cs_test_mock123")

        url = await service.checkout(user=mock_user, plan_key="pro", interval="month")
        assert url == "https://checkout.stripe.com/c/pay/cs_test_mock123"
        mock_stripe_session.assert_called_once()
        call_kwargs = mock_stripe_session.call_args[1]
        assert call_kwargs["customer"] == "cus_101"
        assert call_kwargs["mode"] == "subscription"
        assert call_kwargs["metadata"]["plan"] == "pro"


@pytest.mark.asyncio
async def test_portal_returns_stripe_portal_url():
    """Verify portal creates Stripe Customer Portal session and returns URL."""
    settings = Settings(
        billing_enabled=True,
        payment_provider="stripe",
        stripe_secret_key="sk_test_123456",
    )
    service = BillingService(settings=settings)

    with patch("stripe.billing_portal.Session.create") as mock_portal_create, \
         patch("db.get_billing_customer", return_value={"provider_customer_id": "cus_101"}):
        mock_portal_create.return_value = MagicMock(url="https://billing.stripe.com/p/session/portal_test_mock")

        url = await service.portal(user_id=101, return_url="http://localhost:5001/billing")
        assert url == "https://billing.stripe.com/p/session/portal_test_mock"


@pytest.mark.asyncio
async def test_stripe_webhook_subscription_lifecycle():
    """Verify verified Stripe webhook activates subscription in database."""
    settings = Settings(
        billing_enabled=True,
        payment_provider="stripe",
        stripe_secret_key="sk_test_123",
        stripe_webhook_secret="whsec_test_secret",
    )
    service = BillingService(settings=settings)

    now_ts = int(datetime.now(timezone.utc).timestamp())
    event = {
        "id": "evt_test_subscription_created",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_test_exclusive_pro",
                "object": "subscription",
                "customer": "cus_101",
                "status": "active",
                "current_period_start": now_ts,
                "current_period_end": now_ts + 2592000,
                "metadata": {"plan": "pro"},
            }
        },
    }

    with patch("db.record_billing_event", return_value=True), \
         patch("db.get_billing_customer_by_provider_id", return_value={"user_id": 101}), \
         patch("db.upsert_subscription") as mock_upsert_sub, \
         patch("db.mark_billing_event"):
        processed = await service.process_verified_event(event)
        assert processed is True
        mock_upsert_sub.assert_called_once()
        kwargs = mock_upsert_sub.call_args[1]
        assert kwargs["user_id"] == 101
        assert kwargs["plan_key"] == "pro"
        assert kwargs["status"] == "active"
        assert kwargs["provider"] == "stripe"
