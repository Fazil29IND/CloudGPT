"""Hosted billing integration with a provider boundary and verified lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import structlog

from config import Settings, get_settings
from core.entitlements import Entitlements, get_allowed_thinking_for_model, resolve_entitlements
import db
from metrics import BILLING_EVENTS_TOTAL, BILLING_WEBHOOK_ERRORS_TOTAL

logger = structlog.get_logger(__name__)

try:  # Kept optional so tests and billing-disabled development need no Stripe SDK.
    import stripe
except ImportError:  # pragma: no cover - exercised only in minimal installs
    stripe = None


class BillingServiceError(Exception):
    """Base error for all billing service operations."""


class BillingConfigurationError(BillingServiceError, RuntimeError):
    """Raised when billing is misconfigured or disabled."""


class BillingWebhookError(BillingServiceError):
    """Base error for webhook processing failures."""


class InvalidWebhookSignatureError(BillingWebhookError):
    """Raised when webhook signature verification fails."""


class MissingEventIdError(BillingWebhookError):
    """Raised when a webhook payload lacks an event ID or identity."""


class UnhandledWebhookEventError(BillingWebhookError):
    """Raised when an unhandled or unsupported webhook event is received."""


class CustomerNotFoundError(BillingServiceError):
    """Raised when a billing customer record cannot be found."""


class InvalidCheckoutSessionError(BillingServiceError):
    """Raised when checkout session creation parameters are invalid."""


class SubscriptionNotFoundError(BillingServiceError):
    """Raised when a subscription cannot be located."""


class PaymentGateway(Protocol):
    provider: str

    def create_customer(self, *, email: str, user_id: int) -> str: ...
    def create_checkout(self, *, customer_id: str, price_id: str, success_url: str, cancel_url: str, user_id: int) -> str: ...
    def create_portal(self, *, customer_id: str, return_url: str) -> str: ...
    def verify_webhook(self, *, payload: bytes, signature: str | None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PlanOffer:
    key: str
    interval: str
    display_name: str
    price_id: str | None
    model_access: str
    tokens_5h: int | None
    tokens_week: int | None
    features: tuple[str, ...]
    price_inr: int | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "interval": self.interval,
            "display_name": self.display_name,
            "available": self.price_id is not None or self.key == "lite",
            "model_access": self.model_access,
            "tokens_5h": self.tokens_5h,
            "tokens_week": self.tokens_week,
            "features": list(self.features),
            "price_inr": self.price_inr,
        }


class StripeGateway:
    provider = "stripe"

    def __init__(self, settings: Settings) -> None:
        if stripe is None:
            raise BillingConfigurationError("Stripe package is not installed")
        if not settings.stripe_secret_key:
            raise BillingConfigurationError("Stripe is not configured")
        stripe.api_key = settings.stripe_secret_key
        self.webhook_secret = settings.stripe_webhook_secret

    def create_customer(self, *, email: str, user_id: int) -> str:
        customer = stripe.Customer.create(email=email, metadata={"cloudgpt_user_id": str(user_id)})
        return str(customer.id)

    def create_checkout(
        self,
        *,
        customer_id: str,
        price_id: str | None,
        success_url: str,
        cancel_url: str,
        user_id: int,
        plan_key: str = "pro",
        interval: str = "month",
        amount_cents: int = 2900,
        currency: str = "usd",
    ) -> str:
        if price_id:
            line_items = [{"price": price_id, "quantity": 1}]
        else:
            line_items = [
                {
                    "price_data": {
                        "currency": currency,
                        "unit_amount": amount_cents,
                        "product_data": {
                            "name": f"CloudGPT {plan_key.capitalize()} Plan",
                            "description": f"CloudGPT {plan_key.capitalize()} Subscription ({interval})",
                        },
                        "recurring": {"interval": interval},
                    },
                    "quantity": 1,
                }
            ]

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=line_items,
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=str(user_id),
            metadata={"cloudgpt_user_id": str(user_id), "plan": plan_key, "interval": interval},
            subscription_data={"metadata": {"cloudgpt_user_id": str(user_id), "plan": plan_key, "interval": interval}},
            allow_promotion_codes=True,
        )
        if not session.url:
            raise RuntimeError("Payment provider did not return a checkout URL")
        return str(session.url)

    def create_portal(self, *, customer_id: str, return_url: str) -> str:
        session = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
        return str(session.url)

    def verify_webhook(self, *, payload: bytes, signature: str | None) -> dict[str, Any]:
        if stripe is None:
            raise BillingConfigurationError("Stripe package is not installed")
        if not self.webhook_secret or not signature:
            raise BillingConfigurationError("Webhook verification is not configured")
        try:
            event = stripe.Webhook.construct_event(payload, signature, self.webhook_secret)
            return event.to_dict_recursive() if hasattr(event, "to_dict_recursive") else dict(event)
        except getattr(stripe, "error", object) and getattr(getattr(stripe, "error", None), "SignatureVerificationError", Exception) as exc:
            raise InvalidWebhookSignatureError(f"Stripe signature verification failed: {exc}") from exc
        except Exception as exc:
            raise BillingWebhookError(f"Stripe webhook construction failed: {exc}") from exc


class RazorpayGateway:
    """RazorPay payment gateway (INR orders + HMAC-verified callbacks).

    Flow (workflow-preserving): the checkout endpoint creates a RazorPay
    *order* server-side and returns its parameters; the frontend opens the
    RazorPay checkout modal; the payment handler posts the payment id +
    signature back to /api/billing/verify, which HMAC-verifies and activates
    the subscription. Webhooks keep state in sync for captures/refunds.
    """

    provider = "razorpay"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.razorpay_key_id or not settings.razorpay_key_secret:
            raise BillingConfigurationError(
                "RazorPay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
            )
        import razorpay

        self.client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
        self.key_id = settings.razorpay_key_id
        self.key_secret = settings.razorpay_key_secret
        self.webhook_secret = settings.razorpay_webhook_secret

    def create_customer(self, *, email: str, user_id: int) -> str:
        customer = self.client.customer.create({
            "email": email,
            "notes": {"cloudgpt_user_id": str(user_id)},
        })
        return str(customer["id"])

    def create_order(self, *, amount_inr: int, receipt: str, notes: dict[str, str]) -> dict[str, Any]:
        """Create a RazorPay order. ``amount_inr`` is rupees; API wants paise."""
        order = self.client.order.create({
            "amount": int(amount_inr * 100),
            "currency": "INR",
            "receipt": receipt[:40],
            "notes": notes,
        })
        return order

    def verify_payment_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        import hmac
        import hashlib

        expected = hmac.new(
            self.key_secret.encode("utf-8"),
            f"{order_id}|{payment_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def verify_webhook(self, *, payload: bytes, signature: str | None) -> dict[str, Any]:
        import hmac
        import hashlib
        import json

        if not self.webhook_secret or not signature:
            raise BillingConfigurationError("Webhook verification is not configured")
        expected = hmac.new(self.webhook_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise InvalidWebhookSignatureError("Invalid Razorpay webhook signature")
        try:
            return json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise BillingWebhookError(f"Invalid JSON payload in Razorpay webhook: {exc}") from exc



class BillingService:
    def __init__(self, settings: Settings | None = None, gateway: PaymentGateway | None = None) -> None:
        self.settings = settings or get_settings()
        self.gateway = gateway

    @property
    def enabled(self) -> bool:
        return self.settings.billing_enabled and self.settings.payment_provider in ("stripe", "razorpay")

    @property
    def provider(self) -> str:
        return self.settings.payment_provider

    def offers(self) -> list[PlanOffer]:
        return [
            PlanOffer(
                "lite",
                "none",
                "Lite",
                None,
                "Lite model · Thinking: Low–High",
                self.settings.lite_tokens_5h,
                self.settings.lite_tokens_week,
                (
                    "Multi-Cloud Hybrid RAG: Instant retrieval across AWS, GCP & Azure docs",
                    "Live Web Cross-Verification & Architectural Spec Lookups",
                    "848+ Verified Cloud Services Coverage & Cost Estimation Tools",
                    "Multi-Modal Diagnostics: Architecture diagram & config uploads (2/chat)",
                ),
                price_inr=0,
            ),
            PlanOffer(
                "pro",
                "month",
                "Pro",
                self.settings.stripe_price_pro_monthly,
                "Core model · Thinking: Low–High",
                self.settings.pro_tokens_5h,
                self.settings.pro_tokens_week,
                (
                    "Agentic RAG: Autonomous multi-step cloud architecture design & validation",
                    "Cross-Cloud Migration & Parity Analysis (AWS vs GCP vs Azure)",
                    "Persistent Architecture Memory: Retains infrastructure state across sessions",
                    "Production Config & Log Diagnostics: Up to 5 architecture uploads per chat",
                    "Audio Briefings: High-fidelity voice synthesis (50,000 chars/day)",
                ),
                price_inr=self.settings.pro_price_inr,
            ),
            PlanOffer(
                "pro",
                "year",
                "Pro",
                self.settings.stripe_price_pro_yearly,
                "Core model · Thinking: Low–High",
                self.settings.pro_tokens_5h,
                self.settings.pro_tokens_week,
                (
                    "Agentic RAG: Autonomous multi-step cloud architecture design & validation",
                    "Cross-Cloud Migration & Parity Analysis (AWS vs GCP vs Azure)",
                    "Persistent Architecture Memory: Retains infrastructure state across sessions",
                    "Production Config & Log Diagnostics: Up to 5 architecture uploads per chat",
                    "Audio Briefings: High-fidelity voice synthesis (50,000 chars/day)",
                ),
                price_inr=self.settings.pro_price_inr * 10,
            ),
            PlanOffer(
                "max",
                "month",
                "Max",
                getattr(self.settings, 'stripe_price_max_monthly', None),
                "Apex model · Thinking: Low–Max",
                getattr(self.settings, 'max_tokens_5h', 500_000),
                getattr(self.settings, 'max_tokens_week', 15_000_000),
                (
                    "Adaptive Advanced RAG: Enterprise-grade cross-cloud synthesis & HyDE analysis",
                    "Apex Deep Reasoning: Maximum chain-of-thought (~65k budget) for complex designs",
                    "Complex Infrastructure Audits: Up to 10 architecture & terraform uploads per chat",
                    "Executive Voice Briefings: Enterprise audio synthesis (200,000 chars/day)",
                    "Dedicated SLA & Zero-Throttle Priority Compute Allocation",
                ),
                price_inr=self.settings.max_price_inr,
            ),
            PlanOffer(
                "max",
                "year",
                "Max",
                getattr(self.settings, 'stripe_price_max_yearly', None),
                "Apex model · Thinking: Low–Max",
                getattr(self.settings, 'max_tokens_5h', 500_000),
                getattr(self.settings, 'max_tokens_week', 15_000_000),
                (
                    "Adaptive Advanced RAG: Enterprise-grade cross-cloud synthesis & HyDE analysis",
                    "Apex Deep Reasoning: Maximum chain-of-thought (~65k budget) for complex designs",
                    "Complex Infrastructure Audits: Up to 10 architecture & terraform uploads per chat",
                    "Executive Voice Briefings: Enterprise audio synthesis (200,000 chars/day)",
                    "Dedicated SLA & Zero-Throttle Priority Compute Allocation",
                ),
                price_inr=self.settings.max_price_inr * 10,
            ),
        ]

    def public_offers(self) -> list[dict[str, Any]]:
        """Public plan catalog for billing surfaces (monthly cadence only)."""
        return [
            offer.public_dict()
            for offer in self.offers()
            if offer.interval in ("none", "month")
        ]

    def pricing_comparison(self) -> dict[str, Any]:
        """Comparison-table data for /pricing, rendered from enforcement code
        (entitlements + settings) so the page can never contradict it."""

        def _ent(plan: str) -> Entitlements:
            return resolve_entitlements(plan)

        lite, pro, maxe = _ent("lite"), _ent("pro"), _ent("max")

        def _fmt(value: int | None) -> str:
            return f"{value:,}" if value is not None else "Unlimited"

        def _bool(value: bool) -> bool:
            return bool(value)

        def _thinking(plan: str) -> str:
            levels = get_allowed_thinking_for_model(
                resolve_entitlements(plan).model_tier,
                {"lite": "Lite", "pro": "Core", "max": "Apex"}[plan],
            )
            return " · ".join(levels)

        rows: list[dict[str, Any]] = [
            {
                "label": "Monthly price",
                "type": "price",
                "cells": [0, self.settings.pro_price_inr, self.settings.max_price_inr],
            },
            {
                "label": "Model selector",
                "type": "text",
                "cells": ["Lite", "Core + Lite", "Apex + Core + Lite"],
            },
            {
                "label": "Thinking depth",
                "type": "text",
                "cells": [_thinking("lite"), _thinking("pro"), _thinking("max")],
            },
            {
                "label": "Tokens / 5 hours",
                "type": "text",
                "cells": [_fmt(lite.tokens_5h), _fmt(pro.tokens_5h), _fmt(maxe.tokens_5h)],
            },
            {
                "label": "Tokens / day",
                "type": "text",
                "cells": [_fmt(lite.tokens_day), _fmt(pro.tokens_day), _fmt(maxe.tokens_day)],
            },
            {
                "label": "Tokens / week",
                "type": "text",
                "cells": [_fmt(lite.tokens_week), _fmt(pro.tokens_week), _fmt(maxe.tokens_week)],
            },
            {
                "label": "Tokens / month",
                "type": "text",
                "cells": [_fmt(lite.tokens_month), _fmt(pro.tokens_month), _fmt(maxe.tokens_month)],
            },
            {
                "label": "File uploads per chat",
                "type": "text",
                "cells": [str(lite.max_files), str(pro.max_files), str(maxe.max_files)],
            },
            {
                "label": "Max upload size",
                "type": "text",
                "cells": [
                    f"{lite.max_file_bytes // (1024 * 1024)} MB",
                    f"{pro.max_file_bytes // (1024 * 1024)} MB",
                    f"{maxe.max_file_bytes // (1024 * 1024)} MB",
                ],
            },
            {
                "label": "User memory across sessions",
                "type": "bool",
                "cells": [_bool(lite.has_user_memory), _bool(pro.has_user_memory), _bool(maxe.has_user_memory)],
            },
            {
                "label": "Cloud API access",
                "type": "bool",
                "cells": [_bool(lite.cloud_api), _bool(pro.cloud_api), _bool(maxe.cloud_api)],
            },
            {
                "label": "Text-to-speech chars / day",
                "type": "text",
                "cells": [
                    f"{lite.tts_chars_per_day:,}",
                    f"{pro.tts_chars_per_day:,}",
                    f"{maxe.tts_chars_per_day:,}",
                ],
            },
            {
                "label": "Hybrid RAG + web search",
                "type": "bool",
                "cells": [_bool(lite.rag and lite.web_search), _bool(pro.rag and pro.web_search), _bool(maxe.rag and maxe.web_search)],
            },
        ]
        return {"rows": rows}

    def _gateway(self) -> Any:
        if not self.enabled:
            raise BillingConfigurationError("Billing is not enabled")
        if self.gateway is None:
            if self.provider == "razorpay":
                self.gateway = RazorpayGateway(self.settings)
            else:
                self.gateway = StripeGateway(self.settings)
        return self.gateway

    def _offer(self, plan_key: str, interval: str) -> PlanOffer:
        for offer in self.offers():
            if offer.key == plan_key and offer.interval == interval:
                return offer
        raise ValueError("Unknown plan or billing interval")

    async def checkout(self, *, user: dict, plan_key: str, interval: str) -> Any:
        """Create a checkout session.

        Stripe returns a redirect URL string; RazorPay returns an order
        payload dict consumed by the frontend checkout modal.
        """
        if plan_key not in {"lite", "pro", "max"} or interval not in {"month", "year"}:
            raise ValueError("Unknown plan or billing interval")
        offer = self._offer(plan_key, interval)

        if self.provider == "razorpay":
            if not offer.price_inr:
                raise BillingConfigurationError("This plan is not available for checkout")
            gateway = self._gateway()
            customer = await asyncio.to_thread(db.get_billing_customer, user["id"], gateway.provider)
            if customer:
                customer_id = customer["provider_customer_id"]
            else:
                customer_id = await asyncio.to_thread(
                    gateway.create_customer, email=user["email"], user_id=user["id"]
                )
                await asyncio.to_thread(db.upsert_billing_customer, user["id"], gateway.provider, customer_id)
            order = await asyncio.to_thread(
                gateway.create_order,
                amount_inr=offer.price_inr,
                receipt=f"u{user['id']}-{plan_key}-{interval}",
                notes={
                    "cloudgpt_user_id": str(user["id"]),
                    "plan": plan_key,
                    "interval": interval,
                },
            )
            return {
                "provider": "razorpay",
                "order_id": order["id"],
                "amount": order["amount"],
                "currency": order.get("currency", "INR"),
                "key_id": gateway.key_id,
                "name": "CloudGPT",
                "description": f"{offer.display_name} plan — {offer.model_access}",
                "prefill": {"email": user.get("email", ""), "name": user.get("name") or ""},
                "customer_id": customer_id,
                "theme_color": "#09090b",
            }

        # Stripe path
        gateway = self._gateway()
        customer = await asyncio.to_thread(db.get_billing_customer, user["id"], gateway.provider)
        if customer:
            customer_id = customer["provider_customer_id"]
        else:
            customer_id = await asyncio.to_thread(gateway.create_customer, email=user["email"], user_id=user["id"])
            await asyncio.to_thread(db.upsert_billing_customer, user["id"], gateway.provider, customer_id)

        amount_cents = 2900 if plan_key == "pro" else 7900
        if interval == "year":
            amount_cents *= 10

        return await asyncio.to_thread(
            gateway.create_checkout,
            customer_id=customer_id,
            price_id=offer.price_id,
            success_url=self.settings.billing_success_url,
            cancel_url=self.settings.billing_cancel_url,
            user_id=user["id"],
            plan_key=plan_key,
            interval=interval,
            amount_cents=amount_cents,
        )

    async def verify_and_activate(
        self, *, user_id: int, order_id: str, payment_id: str, signature: str,
        plan_key: str, interval: str,
    ) -> dict[str, Any]:
        """HMAC-verify a RazorPay checkout callback and activate the plan.

        Idempotent: a payment id already recorded as settled is a no-op.
        """
        if plan_key not in {"pro", "max"}:
            raise ValueError("Unknown plan for activation")
        if interval not in {"month", "year"}:
            raise ValueError("Unknown billing interval")
        gateway = self._gateway()
        if not await asyncio.to_thread(
            gateway.verify_payment_signature,
            order_id=order_id, payment_id=payment_id, signature=signature,
        ):
            raise ValueError("Payment signature verification failed")

        already = await asyncio.to_thread(db.get_billing_event, gateway.provider, payment_id)
        if not already:
            await asyncio.to_thread(
                db.record_billing_event, gateway.provider, payment_id,
                "payment.verified",
                {"order_id": order_id, "plan": plan_key, "interval": interval},
            )

        period_days = 30 if interval == "month" else 365
        now = datetime.now(timezone.utc)
        await asyncio.to_thread(
            db.upsert_subscription,
            user_id=user_id, plan_key=plan_key, provider=gateway.provider,
            provider_subscription_id=payment_id, status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=period_days),
        )
        return {"status": "ok", "plan": plan_key, "interval": interval, "provider": gateway.provider}

    async def portal(self, *, user_id: int, return_url: str) -> str:
        gateway = self._gateway()
        if self.provider == "razorpay":
            raise BillingConfigurationError(
                "RazorPay does not provide a self-serve billing portal. Contact support to manage your subscription."
            )
        customer = await asyncio.to_thread(db.get_billing_customer, user_id, gateway.provider)
        if not customer:
            raise ValueError("No billing customer exists for this account")
        return await asyncio.to_thread(gateway.create_portal, customer_id=customer["provider_customer_id"], return_url=return_url)

    def _safe_event_payload(self, event: dict[str, Any]) -> dict[str, Any]:
        obj = event.get("data", {}).get("object", {})
        return {
            "object_id": obj.get("id"),
            "object": obj.get("object"),
            "customer": obj.get("customer"),
            "subscription": obj.get("subscription"),
            "status": obj.get("status"),
        }

    def _plan_from_subscription(self, subscription: dict[str, Any]) -> str | None:
        meta = subscription.get("metadata") or {}
        meta_plan = meta.get("plan") or meta.get("cloudgpt_plan")
        if meta_plan and str(meta_plan).lower() in ("pro", "max", "lite"):
            return str(meta_plan).lower()

        prices = subscription.get("items", {}).get("data", [])
        price_id = next((item.get("price", {}).get("id") for item in prices if item.get("price", {}).get("id")), None)
        for offer in self.offers():
            if offer.price_id and offer.price_id == price_id:
                return offer.key
        return "pro"

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        if not value:
            return None
        return datetime.fromtimestamp(int(value), tz=timezone.utc)

    async def process_verified_event(self, event: dict[str, Any]) -> bool:
        """Apply a verified provider webhook event once. Only verified events
        affect access."""
        provider = self.provider
        if provider == "razorpay":
            return await self._process_razorpay_event(event)
        return await self._process_stripe_event(event)

    def _razorpay_event_identity(self, event: dict[str, Any]) -> tuple[str, str]:
        event_type = str(event.get("event") or "")
        event_id = (
            event.get("payload", {})
            .get("payment", {})
            .get("entity", {})
            .get("id")
            or event.get("id")
            or ""
        )
        return event_id and f"{event_type}:{event_id}" or event_id, event_type

    async def process_webhook_event(self, *, payload: bytes, signature: str | None) -> bool:
        """Verify and apply a provider webhook event with audit logging and metrics."""
        gateway = self._gateway()
        event = gateway.verify_webhook(payload=payload, signature=signature)
        return await self.process_verified_event(event)

    async def _process_razorpay_event(self, event: dict[str, Any]) -> bool:
        """Apply a RazorPay webhook event (payment.captured / order.paid).

        Access is granted from the order notes the checkout flow attached.
        """
        event_id, event_type = self._razorpay_event_identity(event)
        if not event_id or not event_type:
            BILLING_WEBHOOK_ERRORS_TOTAL.labels(reason="missing_event_identity").inc()
            raise MissingEventIdError("Invalid billing event: missing event ID or type")
        inserted = await asyncio.to_thread(
            db.record_billing_event, "razorpay", event_id, event_type, {}
        )
        if not inserted:
            logger.info("razorpay_webhook_event_duplicate", event_id=event_id, event_type=event_type)
            BILLING_EVENTS_TOTAL.labels(provider="razorpay", event_type=event_type, status="duplicate").inc()
            return False
        try:
            if event_type in ("payment.captured", "order.paid"):
                entity = (
                    event.get("payload", {}).get("payment", {}).get("entity")
                    or event.get("payload", {}).get("order", {}).get("entity")
                    or {}
                )
                notes = entity.get("notes") or {}
                user_id = str(notes.get("cloudgpt_user_id", ""))
                plan_key = str(notes.get("plan", ""))
                interval = str(notes.get("interval", "month"))
                if user_id.isdigit() and plan_key in {"pro", "max"}:
                    payment_id = str(entity.get("id") or entity.get("order_id") or event_id)
                    period_days = 365 if interval == "year" else 30
                    now = datetime.now(timezone.utc)
                    await asyncio.to_thread(
                        db.upsert_subscription,
                        user_id=int(user_id), plan_key=plan_key, provider="razorpay",
                        provider_subscription_id=payment_id, status="active",
                        current_period_start=now,
                        current_period_end=now + timedelta(days=period_days),
                    )
                    logger.info("razorpay_subscription_activated", user_id=user_id, plan=plan_key, payment_id=payment_id)
            await asyncio.to_thread(db.mark_billing_event, event_id, "processed")
            BILLING_EVENTS_TOTAL.labels(provider="razorpay", event_type=event_type, status="processed").inc()
            return True
        except Exception as exc:
            BILLING_EVENTS_TOTAL.labels(provider="razorpay", event_type=event_type, status="failed").inc()
            BILLING_WEBHOOK_ERRORS_TOTAL.labels(reason="razorpay_process_failed").inc()
            logger.exception("razorpay_webhook_processing_failed", event_id=event_id, event_type=event_type, error=str(exc))
            await asyncio.to_thread(db.mark_billing_event, event_id, "failed", "processing_error")
            raise

    async def _process_stripe_event(self, event: dict[str, Any]) -> bool:
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")
        if not event_id or not event_type:
            BILLING_WEBHOOK_ERRORS_TOTAL.labels(reason="missing_event_identity").inc()
            raise MissingEventIdError("Invalid billing event: missing event ID or type")
        inserted = await asyncio.to_thread(db.record_billing_event, "stripe", event_id, event_type, self._safe_event_payload(event))
        if not inserted:
            logger.info("stripe_webhook_event_duplicate", event_id=event_id, event_type=event_type)
            BILLING_EVENTS_TOTAL.labels(provider="stripe", event_type=event_type, status="duplicate").inc()
            return False
        try:
            obj = event.get("data", {}).get("object", {})
            subscription = obj if obj.get("object") == "subscription" else None
            if event_type == "checkout.session.completed":
                # The checkout event alone never grants access; subscription events
                # carry provider-authoritative plan/status/period fields.
                customer_id = obj.get("customer")
                user_id = obj.get("client_reference_id") or obj.get("metadata", {}).get("cloudgpt_user_id")
                if customer_id and user_id and str(user_id).isdigit():
                    await asyncio.to_thread(db.upsert_billing_customer, int(user_id), "stripe", str(customer_id))
                    logger.info("stripe_checkout_customer_saved", user_id=user_id, customer_id=customer_id)
            elif subscription and event_type in {
                "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted",
            }:
                customer = await asyncio.to_thread(db.get_billing_customer_by_provider_id, "stripe", str(subscription.get("customer", "")))
                plan_key = self._plan_from_subscription(subscription)
                if customer and plan_key:
                    status = str(subscription.get("status", "incomplete"))
                    period_end = self._timestamp(subscription.get("current_period_end"))
                    grace = None
                    if status == "past_due" and period_end:
                        grace = period_end + timedelta(hours=self.settings.billing_grace_period_hours)
                    await asyncio.to_thread(
                        db.upsert_subscription,
                        user_id=customer["user_id"], plan_key=plan_key, provider="stripe",
                        provider_subscription_id=str(subscription["id"]), status=status,
                        current_period_start=self._timestamp(subscription.get("current_period_start")),
                        current_period_end=period_end,
                        cancel_at_period_end=bool(subscription.get("cancel_at_period_end", False)),
                        canceled_at=self._timestamp(subscription.get("canceled_at")),
                        trial_end=self._timestamp(subscription.get("trial_end")),
                        grace_period_ends_at=grace,
                    )
                    logger.info("stripe_subscription_updated", user_id=customer["user_id"], plan=plan_key, status=status)
            elif event_type == "invoice.payment_failed":
                invoice_sub_id = obj.get("subscription")
                customer_id = obj.get("customer")
                if customer_id and invoice_sub_id:
                    customer = await asyncio.to_thread(db.get_billing_customer_by_provider_id, "stripe", str(customer_id))
                    if customer:
                        sub_record = await asyncio.to_thread(db.get_active_subscription, customer["user_id"])
                        if sub_record:
                            now = datetime.now(timezone.utc)
                            grace = now + timedelta(hours=self.settings.billing_grace_period_hours)
                            await asyncio.to_thread(
                                db.upsert_subscription,
                                user_id=customer["user_id"],
                                plan_key=sub_record.get("plan_key", "lite"),
                                provider="stripe",
                                provider_subscription_id=str(invoice_sub_id),
                                status="past_due",
                                current_period_start=sub_record.get("current_period_start"),
                                current_period_end=sub_record.get("current_period_end"),
                                grace_period_ends_at=grace,
                            )
                            logger.warning("stripe_invoice_payment_failed", user_id=customer["user_id"], grace_ends=str(grace))
            elif event_type == "invoice.payment_succeeded":
                invoice_sub_id = obj.get("subscription")
                customer_id = obj.get("customer")
                if customer_id and invoice_sub_id:
                    customer = await asyncio.to_thread(db.get_billing_customer_by_provider_id, "stripe", str(customer_id))
                    if customer:
                        sub_record = await asyncio.to_thread(db.get_active_subscription, customer["user_id"])
                        if sub_record and sub_record.get("status") == "past_due":
                            await asyncio.to_thread(
                                db.upsert_subscription,
                                user_id=customer["user_id"],
                                plan_key=sub_record.get("plan_key", "pro"),
                                provider="stripe",
                                provider_subscription_id=str(invoice_sub_id),
                                status="active",
                                current_period_start=sub_record.get("current_period_start"),
                                current_period_end=sub_record.get("current_period_end"),
                                grace_period_ends_at=None,
                            )
                            logger.info("stripe_invoice_payment_succeeded", user_id=customer["user_id"])
            await asyncio.to_thread(db.mark_billing_event, event_id, "processed")
            BILLING_EVENTS_TOTAL.labels(provider="stripe", event_type=event_type, status="processed").inc()
            return True
        except Exception as exc:
            BILLING_EVENTS_TOTAL.labels(provider="stripe", event_type=event_type, status="failed").inc()
            BILLING_WEBHOOK_ERRORS_TOTAL.labels(reason="stripe_process_failed").inc()
            logger.exception("stripe_webhook_processing_failed", event_id=event_id, event_type=event_type, error=str(exc))
            await asyncio.to_thread(db.mark_billing_event, event_id, "failed", "processing_error")
            raise


billing_service = BillingService()

