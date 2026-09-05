import asyncio
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config import get_settings
from services.billing import (
    BillingConfigurationError,
    CustomerNotFoundError,
    InvalidCheckoutSessionError,
    InvalidWebhookSignatureError,
    billing_service,
)
import db

logger = logging.getLogger(__name__)


def _is_allowed_return_url(url: str) -> bool:
    """Validate that a return URL originates from an allowed domain."""
    settings = get_settings()
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed_origins = set(settings.cors_origin_list)
    billing_origin = urlparse(settings.billing_success_url)
    if billing_origin.scheme and billing_origin.netloc:
        allowed_origins.add(f"{billing_origin.scheme}://{billing_origin.netloc}")
    return origin in allowed_origins

router = APIRouter(prefix="/api/billing", tags=["billing"])

class CheckoutRequest(BaseModel):
    plan: str
    interval: str

class PortalRequest(BaseModel):
    return_url: str

async def get_current_user(request: Request) -> dict:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = await asyncio.to_thread(db.get_user_by_id, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user

@router.get("/plans")
async def get_plans():
    return billing_service.public_offers()

@router.get("/subscription")
async def get_subscription(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = await asyncio.to_thread(db.get_user_by_id, user_id)
    subscription = await asyncio.to_thread(db.get_active_subscription, user_id)
    usage = await asyncio.to_thread(db.get_token_usage, user_id)
    from core.entitlements import resolve_entitlements
    entitlements = resolve_entitlements(usage.get("tier"), subscription, user_email=user.get("email") if user else None)

    return {
        "plan": entitlements.plan_key.lower(),
        "display_plan": entitlements.plan_key,
        "is_unlimited": entitlements.unlimited,
        "status": subscription.get("status", "active") if subscription else "active",
        "subscription": subscription,
        "usage": {
            "tokens_used_day": usage.get("tokens_used_day", 0),
            "tokens_used_month": usage.get("tokens_used_month", 0),
            "tokens_used_5h": usage.get("tokens_used_5h", 0),
            "tokens_used_week": usage.get("tokens_used_week", 0),
        },
        "limits": {
            "tokens_day": entitlements.tokens_day,
            "tokens_month": entitlements.tokens_month,
            "tokens_5h": entitlements.tokens_5h,
            "tokens_week": entitlements.tokens_week,
        }
    }

@router.post("/checkout")
async def create_checkout(data: CheckoutRequest, request: Request):
    user = await get_current_user(request)
    try:
        result = await billing_service.checkout(user=user, plan_key=data.plan, interval=data.interval)
        # Stripe returns a redirect URL; RazorPay returns an order payload dict.
        if isinstance(result, dict):
            return result
        return {"url": result}
    except BillingConfigurationError as e:
        logger.error(f"Billing configuration error: {e}")
        raise HTTPException(status_code=503, detail="Billing provider not configured")
    except InvalidCheckoutSessionError as e:
        logger.error(f"Invalid checkout session: {e}")
        raise HTTPException(status_code=502, detail="Payment provider did not return checkout URL")
    except ValueError as e:
        logger.warning(f"Checkout creation rejected: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Checkout session creation failed")
        raise HTTPException(status_code=500, detail="Unable to create checkout session. Please try again.")


class VerifyRequest(BaseModel):
    order_id: str
    payment_id: str
    signature: str
    plan: str
    interval: str = "month"


@router.post("/verify")
async def verify_payment(data: VerifyRequest, request: Request):
    """HMAC-verify a RazorPay checkout callback and activate the subscription."""
    user = await get_current_user(request)
    try:
        result = await billing_service.verify_and_activate(
            user_id=user["id"],
            order_id=data.order_id,
            payment_id=data.payment_id,
            signature=data.signature,
            plan_key=data.plan,
            interval=data.interval,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Payment verification failed")
        raise HTTPException(status_code=400, detail="Payment verification failed. Please contact support if you were charged.")


@router.post("/portal")
async def create_portal(data: PortalRequest, request: Request):
    user = await get_current_user(request)
    if not _is_allowed_return_url(data.return_url):
        raise HTTPException(status_code=400, detail="Invalid return URL")
    try:
        url = await billing_service.portal(user_id=user["id"], return_url=data.return_url)
        return {"url": url}
    except CustomerNotFoundError as e:
        logger.warning(f"Billing portal customer not found: {e}")
        raise HTTPException(status_code=404, detail="No billing customer exists for this account")
    except BillingConfigurationError as e:
        logger.error(f"Billing configuration error: {e}")
        raise HTTPException(status_code=503, detail="Billing provider not configured")
    except ValueError as e:
        if "no billing customer exists" in str(e).lower():
            raise HTTPException(status_code=404, detail="No billing customer exists for this account")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Billing portal creation failed")
        raise HTTPException(status_code=500, detail="Unable to open billing portal. Please try again.")


@router.post("/webhook")
async def payment_webhook(request: Request):
    """Provider webhook: RazorPay (X-Razorpay-Signature) or Stripe (stripe-signature)."""
    payload = await request.body()
    provider = billing_service.provider
    if provider == "razorpay":
        signature = request.headers.get("x-razorpay-signature")
    else:
        signature = request.headers.get("stripe-signature")
    try:
        await billing_service.process_webhook_event(payload=payload, signature=signature)
    except InvalidWebhookSignatureError as e:
        logger.warning(f"Webhook signature verification failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    except BillingConfigurationError as e:
        logger.error(f"Webhook configuration error: {e}")
        raise HTTPException(status_code=503, detail="Billing provider not configured")
    except ValueError as e:
        logger.warning(f"Webhook validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Webhook processing failed")
        raise HTTPException(status_code=500, detail="Webhook processing error")

    return {"status": "success"}

