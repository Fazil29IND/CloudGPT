from config import Settings
from services.billing import BillingService, PlanOffer
from core.entitlements import resolve_entitlements, PUBLIC_PLAN_KEYS


def test_billing_offers_inr_pricing():
    settings = Settings(
        pro_price_inr=2999,
        max_price_inr=7999,
        max_tokens_5h=500_000,
        max_tokens_week=15_000_000,
    )
    service = BillingService(settings=settings)
    offers = service.offers()

    # Three plans, each monthly plus yearly entries for Pro and Max.
    assert len({o.key for o in offers}) == 3
    assert {o.key for o in offers if o.interval == "year"} == {"pro", "max"}
    pro_year = next(o for o in offers if o.key == "pro" and o.interval == "year")
    assert pro_year.price_inr == 2999 * 10

    # public_offers stays monthly-only for existing consumers.
    assert len(service.public_offers()) == 3

    lite = next(o for o in offers if o.key == "lite")
    pro = next(o for o in offers if o.key == "pro" and o.interval == "month")
    max_plan = next(o for o in offers if o.key == "max" and o.interval == "month")

    assert lite.price_inr == 0
    assert lite.display_name == "Lite"
    assert lite.tokens_5h == 50_000
    assert lite.tokens_week == 300_000

    assert pro.price_inr == 2999
    assert pro.display_name == "Pro"
    assert pro.tokens_5h == 250_000
    assert pro.tokens_week == 2_000_000

    assert max_plan.price_inr == 7999
    assert max_plan.display_name == "Max"
    assert max_plan.tokens_5h == 500_000
    assert max_plan.tokens_week == 15_000_000


def test_plan_offer_public_dict():
    offer = PlanOffer(
        key="pro",
        interval="month",
        display_name="Pro",
        price_id="price_123",
        model_access="Pro",
        tokens_5h=250000,
        tokens_week=2000000,
        features=("Cloud research", "Web search"),
        price_inr=2999,
    )
    data = offer.public_dict()
    assert data["key"] == "pro"
    assert data["price_inr"] == 2999
    assert data["available"] is True
    assert "Cloud research" in data["features"]


def test_public_plan_keys_includes_max():
    assert "max" in PUBLIC_PLAN_KEYS
    assert "pro" in PUBLIC_PLAN_KEYS
    assert "lite" in PUBLIC_PLAN_KEYS


def test_entitlements_max_tier_tokens():
    settings = Settings(
        max_tokens_5h=500_000,
        max_tokens_week=15_000_000,
    )
    ent = resolve_entitlements("max", settings=settings)
    assert ent.plan_key == "max"
    assert ent.model_tier == "Max"
    assert ent.tokens_5h == 500_000
    assert ent.tokens_week == 15_000_000


def test_pricing_page_rendering():
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)
    response = client.get("/pricing")
    assert response.status_code == 200
    html = response.text

    # Check key sections and content
    assert "pricing-body" in html
    assert "Simple, Transparent Pricing" in html
    assert "Lite" in html
    assert "Pro" in html
    assert "Max" in html
    assert "Most Popular" in html
    assert "Best Value" in html
    assert "2,999" in html
    assert "7,999" in html
    assert "Free forever" in html
    assert "Compare All Features" in html
    assert "Frequently Asked Questions" in html
    assert "Bank-Grade Security" in html
    assert "/static/js/pricing.js" in html

    # Comparison table is rendered from enforcement data, not hardcoded claims.
    assert "Tokens / month" in html
    assert "Thinking depth" in html
    assert "Low–Max" in html
    assert "Claude Sonnet" not in html  # stale model-name claims are gone
    assert "7-day trial" not in html    # unenforced claim removed
    # Annual billing toggle and footer links.
    assert "billing-period-toggle" in html
    assert "/terms" in html and "/privacy" in html

    # Verify no emojis in output
    for emoji in ["📊", "📈", "🔒", "⚡", "💬"]:
        assert emoji not in html


def test_pricing_comparison_matches_entitlements():
    from services.billing import billing_service
    from core.entitlements import resolve_entitlements

    comparison = billing_service.pricing_comparison()
    rows = {r["label"]: r for r in comparison["rows"]}

    assert rows["Tokens / month"]["cells"] == [
        f"{resolve_entitlements('lite').tokens_month:,}",
        f"{resolve_entitlements('pro').tokens_month:,}",
        f"{resolve_entitlements('max').tokens_month:,}",
    ]
    # Every row has one cell per plan.
    for row in comparison["rows"]:
        assert len(row["cells"]) == 3


def test_billing_unauthenticated_redirect():
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)
    response = client.get("/billing", follow_redirects=False)
    assert response.status_code == 302
    assert "sign+in" in response.headers.get("location", "").lower()


def test_billing_authenticated_rendering():
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    from app import app

    client = TestClient(app)
    mock_user = {"id": 1, "email": "fazilprojects@gmail.com", "name": "Lite User", "tier": "Lite"}
    with patch("app.get_current_user", return_value=mock_user), \
         patch("db.get_token_usage", return_value={"tier": "Lite", "tokens_used_day": 0, "tokens_used_month": 0, "tokens_used_5h": 0, "tokens_used_week": 0}), \
         patch("db.get_active_subscription", return_value=None):
        response = client.get("/billing")
        assert response.status_code == 200
        html = response.text
        assert "Lite Plan" in html
        assert "Upgrade to Pro — ₹2,999/mo" in html
        assert "Upgrade to Max — ₹7,999/mo" in html
        assert "Preview Lite" not in html
        assert "Preview Pro" not in html


def test_lite_features_contain_current_rag_description():
    from services.billing import billing_service
    lite = next(o for o in billing_service.offers() if o.key == "lite")
    assert any("RAG" in f for f in lite.features)
    # Model access names the model selector + enforced thinking range.
    assert "Lite model" in lite.model_access
    assert "Low" in lite.model_access and "High" in lite.model_access


def test_pro_features_contain_agentic_rag_description():
    from services.billing import billing_service
    pro = next(o for o in billing_service.offers() if o.key == "pro" and o.interval == "month")
    assert any("Agentic RAG" in f for f in pro.features)
    assert "Core model" in pro.model_access
    # Max thinking must NOT be advertised for the Pro plan (Apex-exclusive).
    assert "Max" not in pro.model_access


def test_max_features_contain_adaptive_rag_description():
    from services.billing import billing_service
    max_plan = next(o for o in billing_service.offers() if o.key == "max" and o.interval == "month")
    assert any("Adaptive Advanced RAG" in f for f in max_plan.features)
    assert "Apex model" in max_plan.model_access
    assert "Low–Max" in max_plan.model_access


def test_billing_pro_authenticated_rendering():
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    from app import app

    client = TestClient(app)
    mock_user = {"id": 2, "email": "pro.architect@enterprise.io", "name": "Pro User", "tier": "Pro"}
    with patch("app.get_current_user", return_value=mock_user), \
         patch("db.get_token_usage", return_value={"tier": "Pro", "tokens_used_day": 0, "tokens_used_month": 0, "tokens_used_5h": 12500, "tokens_used_week": 45000}), \
         patch("db.get_active_subscription", return_value={"status": "active", "plan_key": "pro", "payment_provider": "stripe", "current_period_end": "2026-10-01"}):
        response = client.get("/billing")
        assert response.status_code == 200
        html = response.text
        assert "Pro Plan" in html
        assert "Active Subscription" in html
        assert "CloudGPT Pro Workspace" in html
        assert "Upgrade to Max — ₹7,999/mo" in html
        assert "Upgrade to Pro — ₹2,999/mo" not in html
        assert "Preview Lite" not in html
        assert "Preview Pro" not in html
        assert "Claude Sonnet" not in html
        assert "Gemini 3.8 Flash" not in html
        for emoji in ["📊", "📈", "🔒", "⚡", "💬"]:
            assert emoji not in html



