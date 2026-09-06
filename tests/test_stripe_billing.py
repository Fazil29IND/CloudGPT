import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from config import Settings
from services.billing import (
    BillingService,
    StripeGateway,
    BillingConfigurationError,
)


def test_stripe_gateway_requires_api_key():
    settings = Settings(stripe_secret_key=None)
    with pytest.raises(BillingConfigurationError, match="Stripe is not configured"):
        StripeGateway(settings)


def test_stripe_gateway_create_checkout_with_price_id():
    settings = Settings(
        stripe_secret_key="sk_test_1234567890",
        stripe_publishable_key="pk_test_1234567890",
        billing_success_url="http://localhost:5001/billing?checkout=success",
        billing_cancel_url="http://localhost:5001/billing?checkout=cancelled",
    )
    with patch("stripe.checkout.Session.create") as mock_create:
        mock_create.return_value = MagicMock(url="https://checkout.stripe.com/c/pay/cs_test_abc123")
        gateway = StripeGateway(settings)
        url = gateway.create_checkout(
            customer_id="cus_test_123",
            price_id="price_pro_monthly",
            success_url=settings.billing_success_url,
            cancel_url=settings.billing_cancel_url,
            user_id=42,
            plan_key="pro",
            interval="month",
        )
        assert url == "https://checkout.stripe.com/c/pay/cs_test_abc123"
        mock_create.assert_called_once()
        kwargs = mock_create.call_args[1]
        assert kwargs["customer"] == "cus_test_123"
        assert kwargs["mode"] == "subscription"
        assert kwargs["line_items"] == [{"price": "price_pro_monthly", "quantity": 1}]
        assert kwargs["metadata"]["plan"] == "pro"


def test_stripe_gateway_create_checkout_fallback_price_data():
    settings = Settings(
        stripe_secret_key="sk_test_1234567890",
        billing_success_url="http://localhost:5001/billing?checkout=success",
        billing_cancel_url="http://localhost:5001/billing?checkout=cancelled",
    )
    with patch("stripe.checkout.Session.create") as mock_create:
        mock_create.return_value = MagicMock(url="https://checkout.stripe.com/c/pay/cs_test_fallback")
        gateway = StripeGateway(settings)
        url = gateway.create_checkout(
            customer_id="cus_test_123",
            price_id=None,
            success_url=settings.billing_success_url,
            cancel_url=settings.billing_cancel_url,
            user_id=42,
            plan_key="max",
            interval="month",
            amount_cents=7900,
        )
        assert url == "https://checkout.stripe.com/c/pay/cs_test_fallback"
        kwargs = mock_create.call_args[1]
        assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 7900
        assert kwargs["line_items"][0]["price_data"]["recurring"]["interval"] == "month"


@pytest.mark.asyncio
async def test_stripe_webhook_checkout_session_completed():
    settings = Settings(
        billing_enabled=True,
        payment_provider="stripe",
        stripe_secret_key="sk_test_123",
    )
    service = BillingService(settings=settings)

    event = {
        "id": "evt_test_checkout_completed",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "object": "checkout.session",
                "customer": "cus_999",
                "client_reference_id": "42",
            }
        },
    }

    with patch("db.record_billing_event", return_value=True), \
         patch("db.upsert_billing_customer") as mock_upsert_customer, \
         patch("db.mark_billing_event"):
        processed = await service.process_verified_event(event)
        assert processed is True
        mock_upsert_customer.assert_called_once_with(42, "stripe", "cus_999")


@pytest.mark.asyncio
async def test_stripe_webhook_subscription_lifecycle():
    settings = Settings(
        billing_enabled=True,
        payment_provider="stripe",
        stripe_secret_key="sk_test_123",
    )
    service = BillingService(settings=settings)

    now_ts = int(datetime.now(timezone.utc).timestamp())
    event = {
        "id": "evt_test_sub_created",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_test_abc",
                "object": "subscription",
                "customer": "cus_999",
                "status": "active",
                "current_period_start": now_ts,
                "current_period_end": now_ts + 2592000,
                "metadata": {"plan": "max"},
            }
        },
    }

    with patch("db.record_billing_event", return_value=True), \
         patch("db.get_billing_customer_by_provider_id", return_value={"user_id": 42}), \
         patch("db.upsert_subscription") as mock_upsert_sub, \
         patch("db.mark_billing_event"):
        processed = await service.process_verified_event(event)
        assert processed is True
        mock_upsert_sub.assert_called_once()
        kwargs = mock_upsert_sub.call_args[1]
        assert kwargs["user_id"] == 42
        assert kwargs["plan_key"] == "max"
        assert kwargs["status"] == "active"
