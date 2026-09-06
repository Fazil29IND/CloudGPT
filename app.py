import asyncio
import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import quote_plus, urlparse

from authlib.integrations.starlette_client import OAuth, OAuthError
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

import db
from config import get_settings
from core.rate_limit import rate_limiter
from core.redis_client import redis_client
from core.security import (
    CSRFMiddleware,
    SecurityHeadersMiddleware,
    csrf_token,
    request_user_fingerprint,
    validate_csrf_token,
    validate_password_rules,
    password_is_strong,
    DEVELOPER_TESTING_EMAILS,
)
from email_service import email_service, render_password_reset_email, render_verification_email
from logging_config import configure_logging, get_logger
from services.billing import billing_service

# ── Configuration ────────────────────────────────────────────────────────────

load_dotenv()
settings = get_settings()

SECRET_KEY = settings.secret_key
GOOGLE_CLIENT_ID = settings.google_client_id
GOOGLE_CLIENT_SECRET = settings.google_client_secret

# ── Logging ──────────────────────────────────────────────────────────────────

# Structured logging: JSON in production, colored console in development.
# Every line carries request_id / user_id via context vars (core.security binds
# them per request; see logging_config.bind_request_context).
configure_logging(log_level=settings.log_level, log_format=settings.log_format)
logger = get_logger("cloudgpt")

# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup and shutdown logic for the FastAPI app."""
    settings.validate_startup()
    await asyncio.to_thread(db.get_pool)
    await asyncio.to_thread(db.seed_required_users)
    await asyncio.to_thread(db.sync_admin_roles)
    # Reap orphaned quota reservations: via the ARQ worker when the task queue
    # is reachable, otherwise inline (single-process deployments).
    if settings.redis_enabled:
        try:
            await enqueue_task("cleanup_expired_reservations_task")
        except Exception:
            await asyncio.to_thread(db.cleanup_expired_reservations)
    else:
        await asyncio.to_thread(db.cleanup_expired_reservations)
    logger.info(
        "Database connection pool initialized",
        seed_demo_accounts=settings.seed_demo_accounts,
    )

    # Initialize Redis connection pool (graceful fallback if unavailable)
    await redis_client.connect()
    if settings.redis_enabled:
        try:
            from core.semantic_cache import warm_semantic_cache
            await warm_semantic_cache()
        except Exception as e:
            logger.debug("Semantic cache warm-up error: %s", e)

    # Pre-register all known pipeline stage × tier label combinations so Grafana
    # dashboards show zero-valued series from startup rather than gaps.
    from metrics import RAG_STAGE_DURATION_SECONDS, CHAT_REQUESTS_TOTAL, PIPELINE_TYPE_GAUGE

    _ALL_STAGES = [
        # Free / current RAG stages
        "classification", "web_search", "retrieval",
        "context_assembly", "llm_generate",
        # Agentic RAG (Pro) stages
        "plan_route", "agentic_retrieve", "grade_evidence",
        "agentic_generate", "agentic_critique",
        # Adaptive RAG (Max) stages
        "transform_query", "adaptive_retrieve", "rerank_compress",
        "adaptive_generate",
    ]
    _ALL_TIERS = ("Free", "Pro", "Max", "Developer")
    _ALL_PIPELINE_TYPES = ("current_rag", "agentic_rag", "adaptive_rag", "unknown")

    for _stage in _ALL_STAGES:
        for _tier in _ALL_TIERS:
            RAG_STAGE_DURATION_SECONDS.labels(stage=_stage, tier=_tier).observe(0)

    for _pt in _ALL_PIPELINE_TYPES:
        PIPELINE_TYPE_GAUGE.labels(pipeline_type=_pt).set(0)
        CHAT_REQUESTS_TOTAL.labels(
            tier="Free", model="Lite", status="ok", pipeline_type=_pt
        ).inc(0)

    yield

    global _ARQ_POOL
    if _ARQ_POOL is not None:
        try:
            await _ARQ_POOL.aclose()
        except Exception:
            pass
        _ARQ_POOL = None

    await redis_client.close()
    await asyncio.to_thread(db.close_pool)
    logger.info("Database connection pool closed")


_ARQ_POOL = None


async def _get_arq_pool():
    global _ARQ_POOL
    if _ARQ_POOL is None:
        from arq import create_pool
        from arq.connections import RedisSettings
        _ARQ_POOL = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _ARQ_POOL


async def enqueue_task(func_name: str, *args, **kwargs) -> None:
    """Enqueue an ARQ job; raises if the task queue is unreachable or redis is disabled."""
    if not settings.redis_enabled:
        raise RuntimeError("Redis is disabled; cannot enqueue background task")
    pool = await _get_arq_pool()
    await pool.enqueue_job(func_name, *args, **kwargs)


# ── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(title="CloudGPT", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

from api import chat_router, artifacts_router
from admin_routes import router as admin_router
from api.billing_routes import router as billing_router
app.include_router(chat_router)
app.include_router(artifacts_router)
app.include_router(billing_router)
app.include_router(admin_router)


@app.exception_handler(db.DatabaseUnavailableError)
async def db_unavailable_handler(request: Request, exc: db.DatabaseUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable"})

# Prometheus HTTP metrics at /metrics (scrape target is internal to the
# compose network; keep it firewalled or behind the proxy in production).
if settings.metrics_enabled:
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    except ImportError:
        logger.warning("prometheus-fastapi-instrumentator not installed; /metrics endpoint disabled")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_host_list or ["127.0.0.1", "*"])
except ImportError:
    pass

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
app.add_middleware(CSRFMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="cloudgpt_session",
    max_age=3600,              # 1 hour
    same_site="lax",
    https_only=(settings.environment.lower() == "production"),
)

templates = Jinja2Templates(directory="templates")

# ── OAuth Setup ──────────────────────────────────────────────────────────────

oauth = OAuth()

oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile",
    },
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def is_safe_redirect(url: str) -> bool:
    """
    Only allow internal redirect paths.
    Reject absolute URLs, protocol-relative URLs, and anything pointing off-site.
    """
    if not url:
        return False
    parsed = urlparse(url)
    # Must be a relative path with no scheme or netloc
    if parsed.scheme or parsed.netloc:
        return False
    # Must start with /
    if not url.startswith("/"):
        return False
    # Block protocol-relative tricks like //evil.com
    if url.startswith("//"):
        return False
    return True


_USER_CACHE: dict[int, tuple[float, dict]] = {}


def invalidate_user_cache(user_id: int) -> None:
    """Evict user from in-memory session cache."""
    _USER_CACHE.pop(user_id, None)


async def get_current_user(request: Request) -> dict | None:
    """Load the current user from session, caching profile to prevent DB connection exhaustion."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    now = time.monotonic()
    cached = _USER_CACHE.get(user_id)
    if cached and now < cached[0]:
        return cached[1]

    # Check Redis session cache if available
    cache_key = f"user:profile:{user_id}"
    if redis_client.is_available:
        try:
            profile = await redis_client.get_json(cache_key)
            if profile and isinstance(profile, dict):
                _USER_CACHE[user_id] = (now + 60.0, profile)
                return profile
        except Exception:
            pass

    user = await asyncio.to_thread(db.get_user_by_id, user_id)
    if user and isinstance(user, dict):
        _USER_CACHE[user_id] = (now + 60.0, user)
        if redis_client.is_available:
            try:
                await redis_client.set_json(cache_key, user, ex=60)
            except Exception:
                pass
    return user


async def auth_rate_limit(request: Request) -> None:
    """Redis-backed (distributed) auth rate limiting. Falls back to in-memory."""
    if not await rate_limiter.allowed_async(f"auth:{request_user_fingerprint(request)}", settings.auth_rate_limit_per_minute):
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")


# Password validation rules and developer account exceptions are imported from core.security




# ── Routes: Pages ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request, error: str = "", success: str = "", warning: str = ""):
    """Render the login page. Redirect to dashboard if already signed in."""
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse(request, "index.html", {
        "error": error,
        "success": success,
        "warning": warning,
        "csrf_token": csrf_token(request),
    })


@app.get("/password", response_class=HTMLResponse)
async def password_page(request: Request, email: str = "", error: str = ""):
    """Render the password entry page."""
    if not email:
        return RedirectResponse(url="/", status_code=302)

    return templates.TemplateResponse(request, "password.html", {
        "email": email,
        "error": error,
        "csrf_token": csrf_token(request),
    })


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Protected starting page of CloudGPT — renders the agent workspace."""
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Please+sign+in+to+continue", status_code=302)

    user_id = request.session.get("user_id")
    from db import get_active_subscription, get_token_usage
    from core.entitlements import resolve_entitlements
    from llm.thinking import clamp_thinking_level, default_thinking_for_tier
    usage = await asyncio.to_thread(get_token_usage, user_id) if user_id else {"tier": "Lite", "tokens_used_day": 0, "tokens_used_month": 0, "tokens_used_5h": 0, "tokens_used_week": 0}
    subscription = await asyncio.to_thread(get_active_subscription, user_id) if user_id else None
    entitlements = resolve_entitlements(usage.get("tier"), subscription, user_email=user.get("email"))
    user_tier = entitlements.plan_key
    user_effective_tier = entitlements.model_tier
    limit_day = entitlements.tokens_day if entitlements.tokens_day is not None else "Unlimited"
    limit_month = entitlements.tokens_month if entitlements.tokens_month is not None else "Unlimited"
    limit_5h = entitlements.tokens_5h if entitlements.tokens_5h is not None else "Unlimited"
    limit_week = entitlements.tokens_week if entitlements.tokens_week is not None else "Unlimited"

    if user_effective_tier in ("Developer", "Admin", "Max"):
        default_model = "Apex"
    elif user_effective_tier in ("Pro", "Core"):
        default_model = "Core"
    else:
        default_model = "Lite"

    raw_default_thinking = default_thinking_for_tier(user_effective_tier)
    default_thinking = clamp_thinking_level(raw_default_thinking, entitlements.allowed_thinking_levels)

    user_settings = user.get("settings_json") or {}

    return templates.TemplateResponse(request, "chat.html", {
        "user_name": user.get("name", ""),
        "user_email": user.get("email", ""),
        "user_tier": user_tier,
        "effective_tier": user_effective_tier,
        "user_settings": user_settings,
        "has_memory": entitlements.has_user_memory,
        "has_password": bool(user.get("password_hash")),
        "tokens_day": usage.get("tokens_used_day", 0),
        "limit_day": limit_day,
        "tokens_month": usage.get("tokens_used_month", 0),
        "limit_month": limit_month,
        "tokens_5h": usage.get("tokens_used_5h", 0),
        "limit_5h": limit_5h,
        "tokens_week": usage.get("tokens_used_week", 0),
        "limit_week": limit_week,
        "allowed_models": entitlements.allowed_models,
        "allowed_thinking_levels": list(entitlements.allowed_thinking_levels),
        "default_model": default_model,
        "default_thinking": default_thinking,
        "is_unlimited": entitlements.unlimited,
        "csrf_token": csrf_token(request),
    })


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    """Alias for dashboard workspace."""
    return RedirectResponse(url="/dashboard", status_code=302)


# ── Routes: Email/Password Auth ─────────────────────────────────────────────

@app.post("/login-email")
async def login_email(request: Request, email: str = Form(...), csrf_token_value: str | None = Form(None, alias="csrf_token")):
    """
    Step 1 of email login: validate email input and proceed to password page.
    Authentication verification is performed after password entry.
    """
    validate_csrf_token(request, csrf_token_value)
    await auth_rate_limit(request)
    email = email.strip().lower()
    if not email:
        return RedirectResponse(url="/?error=Please+enter+your+email", status_code=302)

    return RedirectResponse(url=f"/password?email={email}", status_code=302)


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...), csrf_token_value: str | None = Form(None, alias="csrf_token")):
    """
    Step 2 of email login: verify credentials (user existence and password) and create session.
    Uses a single generic error message to prevent user enumeration.
    """
    validate_csrf_token(request, csrf_token_value)
    await auth_rate_limit(request)
    email = email.strip().lower()
    if not email:
        return RedirectResponse(url="/?error=Please+enter+your+email", status_code=302)

    _generic_error = "Invalid+credentials.+Please+try+again."
    user = await asyncio.to_thread(db.get_user_by_email, email)

    if not user:
        return RedirectResponse(
            url=f"/password?email={email}&error={_generic_error}",
            status_code=302,
        )

    if not user.get("password_hash"):
        return RedirectResponse(
            url=f"/password?email={email}&error={_generic_error}",
            status_code=302,
        )

    if not await asyncio.to_thread(db.verify_password, user, password):
        return RedirectResponse(
            url=f"/password?email={email}&error={_generic_error}",
            status_code=302,
        )

    # If regular user is unverified, require email verification
    if not user.get("email_verified") and email not in (DEVELOPER_TESTING_EMAILS | settings.developer_email_set):
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        code = f"{secrets.randbelow(900000) + 100000}"
        await asyncio.to_thread(db.create_email_verification_token, user["id"], token_hash, code, 15)
        verify_url = f"{_absolute_base_url(request)}/verify-email?token={raw_token}"
        html_body, text_body = render_verification_email(code, verify_url, expires_minutes=15)
        await email_service.send_email(to=email, subject="Verify your CloudGPT account", html_body=html_body, text_body=text_body)
        return RedirectResponse(
            url=f"/verify-email?email={quote_plus(email)}&warning=Please+verify+your+email+address+to+continue.",
            status_code=302,
        )

    # Rotate the session identity after authentication.
    request.session.clear()
    request.session["user_id"] = user["id"]
    logger.info("Email/password login successful for user_id=%s", user["id"])

    # Safe redirect
    next_url = request.query_params.get("next", "/dashboard")
    if not is_safe_redirect(next_url):
        next_url = "/dashboard"

    return RedirectResponse(url=next_url, status_code=302)

# ── Routes: Google OAuth ─────────────────────────────────────────────────────

@app.get("/auth/google")
async def google_login(request: Request):
    """Initiate the Google OAuth 2.0 / OIDC flow."""
    active_client_id = (get_settings().google_client_id or "").strip()
    _placeholder_prefixes = ("replace", "your-", "placeholder", "change-me", "example-", "<")
    if not active_client_id or active_client_id.lower().startswith(_placeholder_prefixes):
        logger.error("Google OAuth not configured — GOOGLE_CLIENT_ID is missing or placeholder")
        return RedirectResponse(
            url="/?error=Google+sign-in+is+not+configured.+Please+contact+the+administrator.",
            status_code=302,
        )

    # Store the return URL in session for post-login redirect
    next_url = request.query_params.get("next", "/dashboard")
    if is_safe_redirect(next_url):
        request.session["next_url"] = next_url

    redirect_uri = request.url_for("google_callback")
    return await oauth.google.authorize_redirect(request, str(redirect_uri))


@app.get("/auth/google/callback")
async def google_callback(request: Request):
    """
    Handle the OAuth callback from Google.

    Account linking rules:
    1. If oauth_accounts has a matching (google, sub) → sign in to that user.
    2. If Google email is verified and matches an existing password-based account →
       do NOT auto-link. Ask user to sign in with password first.
    3. If no match → create new user + oauth_account.
    """
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        error_description = str(e)
        logger.warning("Google OAuth authorization failed")

        if "denied" in error_description.lower() or "cancel" in error_description.lower():
            msg = "Google sign-in was cancelled."
        elif "state" in error_description.lower():
            msg = "Sign-in session expired. Please try again."
        else:
            msg = "Google sign-in failed. Please try again."

        return RedirectResponse(url=f"/?error={msg.replace(' ', '+')}", status_code=302)
    except Exception:
        logger.exception("Unexpected error during Google OAuth callback")
        return RedirectResponse(
            url="/?error=An+unexpected+error+occurred.+Please+try+again.",
            status_code=302,
        )

    # Extract user info from the ID token
    userinfo = token.get("userinfo")
    if not userinfo:
        logger.error("No userinfo in Google token response")
        return RedirectResponse(
            url="/?error=Could+not+retrieve+your+Google+profile.+Please+try+again.",
            status_code=302,
        )

    google_sub = userinfo.get("sub")
    google_email = userinfo.get("email", "").strip().lower()
    google_name = userinfo.get("name", "")
    google_email_verified = userinfo.get("email_verified", False)

    if not google_sub or not google_email:
        logger.error("Missing sub or email in Google userinfo")
        return RedirectResponse(
            url="/?error=Incomplete+Google+profile.+Please+try+again.",
            status_code=302,
        )

    # Require verified email
    if not google_email_verified:
        logger.warning("Unverified Google email rejected: %s", google_email)
        return RedirectResponse(
            url="/?error=Your+Google+email+is+not+verified.+Please+verify+it+and+try+again.",
            status_code=302,
        )

    # ── Account Linking Logic ────────────────────────────────────────────

    # 1. Check if this Google account is already linked
    authenticated_user_id: int | None = None
    oauth_account = await asyncio.to_thread(db.get_oauth_account, "google", google_sub)
    if oauth_account:
        # Sign in to the linked user
        authenticated_user_id = oauth_account["user_id"]
        assert authenticated_user_id is not None
        if google_name and not oauth_account.get("name"):
            await asyncio.to_thread(db.update_user_profile, authenticated_user_id, google_name, True)
        logger.info("Google login: existing OAuth link, user_id=%s", oauth_account["user_id"])
    else:
        # 2. Check if email matches an existing user account
        existing_user = await asyncio.to_thread(db.get_user_by_email, google_email)
        if existing_user:
            # Seamless link for verified Google accounts (industry standard)
            await asyncio.to_thread(db.create_oauth_account, existing_user["id"], "google", google_sub)
            await asyncio.to_thread(
                db.update_user_profile,
                existing_user["id"],
                google_name if not existing_user.get("name") else None,
                True,
            )
            authenticated_user_id = existing_user["id"]
            logger.info("Google login: linked to existing user_id=%s", existing_user["id"])
        else:
            # 3. Create new user + OAuth account
            new_user = await asyncio.to_thread(
                db.create_user,
                email=google_email,
                password=None,
                name=google_name,
                email_verified=True,
            )
            await asyncio.to_thread(db.create_oauth_account, new_user["id"], "google", google_sub)
            authenticated_user_id = new_user["id"]
            logger.info("Google login: new user created, user_id=%s", new_user["id"])

    # Safe redirect to intended destination
    next_url = request.session.pop("next_url", "/dashboard")
    if not is_safe_redirect(next_url):
        next_url = "/dashboard"
    request.session.clear()
    request.session["user_id"] = authenticated_user_id

    return RedirectResponse(url=next_url, status_code=302)


# ── Routes: Logout ───────────────────────────────────────────────────────────

@app.get("/logout")
async def logout_get(request: Request):
    """Clear the session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/?success=You+have+been+signed+out.", status_code=302)


@app.post("/logout")
async def logout(request: Request, csrf_token_value: str | None = Form(None, alias="csrf_token")):
    """Clear the session and redirect to login."""
    validate_csrf_token(request, csrf_token_value)
    request.session.clear()
    return RedirectResponse(url="/?success=You+have+been+signed+out.", status_code=302)


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, error: str = "", email: str = ""):
    if await get_current_user(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request, "signup.html", {"error": error, "email": email, "csrf_token": csrf_token(request)})


@app.post("/signup")
async def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(""),
    name: str = Form(""),
    terms: str | None = Form(None),
    csrf_token_value: str | None = Form(None, alias="csrf_token"),
):
    validate_csrf_token(request, csrf_token_value)
    await auth_rate_limit(request)
    email = email.strip().lower()
    if not email or len(email) > 255:
        return RedirectResponse(url="/signup?error=Please+enter+a+valid+email+address.", status_code=302)

    is_valid, err_msg = validate_password_rules(password, confirm_password, email)
    if not is_valid:
        return RedirectResponse(url=f"/signup?error={quote_plus(err_msg)}&email={quote_plus(email)}", status_code=302)

    if await asyncio.to_thread(db.get_user_by_email, email):
        return RedirectResponse(url=f"/signup?error=That+email+is+already+taken.+Try+signing+in.&email={quote_plus(email)}", status_code=302)

    is_dev = email in (DEVELOPER_TESTING_EMAILS | settings.developer_email_set)
    if is_dev:
        await asyncio.to_thread(db.create_user, email, password, name.strip()[:255] or None, True)
        return RedirectResponse(url=f"/password?email={email}", status_code=302)

    new_user = await asyncio.to_thread(db.create_user, email, password, name.strip()[:255] or None, False)

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    code = f"{secrets.randbelow(900000) + 100000}"

    await asyncio.to_thread(db.create_email_verification_token, new_user["id"], token_hash, code, 15)
    verify_url = f"{_absolute_base_url(request)}/verify-email?token={raw_token}"
    html_body, text_body = render_verification_email(code, verify_url, expires_minutes=15)
    await email_service.send_email(to=email, subject="Verify your CloudGPT account", html_body=html_body, text_body=text_body)

    return RedirectResponse(url=f"/verify-email?email={quote_plus(email)}", status_code=302)


# ── Routes: Email Verification ───────────────────────────────────────────────

@app.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(
    request: Request,
    email: str = "",
    token: str | None = None,
    error: str = "",
    success: str = "",
    warning: str = "",
):
    if await get_current_user(request):
        return RedirectResponse(url="/dashboard", status_code=302)

    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = await asyncio.to_thread(db.get_email_verification_by_token, token_hash)
        if record:
            await asyncio.to_thread(db.mark_email_verification_used, record["id"])
            await asyncio.to_thread(db.mark_user_email_verified, record["user_id"])
            user = await asyncio.to_thread(db.get_user_by_id, record["user_id"])
            user_email = user["email"] if user else email
            return RedirectResponse(
                url=f"/password?email={quote_plus(user_email)}&success=Email+verified+successfully!+Please+enter+your+password+to+sign+in.",
                status_code=302,
            )
        else:
            error = "Invalid or expired verification link. Please enter your code or request a new one."

    return templates.TemplateResponse(
        request,
        "verify_email.html",
        {
            "email": email,
            "error": error,
            "success": success,
            "warning": warning,
            "csrf_token": csrf_token(request),
        },
    )


@app.post("/verify-email")
async def verify_email_post(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    csrf_token_value: str | None = Form(None, alias="csrf_token"),
):
    validate_csrf_token(request, csrf_token_value)
    await auth_rate_limit(request)
    email = email.strip().lower()
    code = code.strip()

    user = await asyncio.to_thread(db.get_user_by_email, email)
    if not user:
        return RedirectResponse(url="/signup?error=Please+create+an+account+first.", status_code=302)

    if user.get("email_verified"):
        return RedirectResponse(url=f"/password?email={quote_plus(email)}&success=Your+email+is+already+verified.+Please+sign+in.", status_code=302)

    record = await asyncio.to_thread(db.get_email_verification_by_code, user["id"], code)
    if not record:
        return RedirectResponse(
            url=f"/verify-email?email={quote_plus(email)}&error=Invalid+or+expired+verification+code.+Please+try+again.",
            status_code=302,
        )

    await asyncio.to_thread(db.mark_email_verification_used, record["id"])
    await asyncio.to_thread(db.mark_user_email_verified, user["id"])

    return RedirectResponse(
        url=f"/password?email={quote_plus(email)}&success=Email+verified+successfully!+Please+enter+your+password+to+sign+in.",
        status_code=302,
    )


@app.post("/resend-verification")
async def resend_verification(
    request: Request,
    email: str = Form(...),
    csrf_token_value: str | None = Form(None, alias="csrf_token"),
):
    validate_csrf_token(request, csrf_token_value)
    await auth_rate_limit(request)
    email = email.strip().lower()

    user = await asyncio.to_thread(db.get_user_by_email, email)
    if not user or user.get("email_verified"):
        return RedirectResponse(url=f"/password?email={quote_plus(email)}", status_code=302)

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    code = f"{secrets.randbelow(900000) + 100000}"

    await asyncio.to_thread(
        db.create_email_verification_token,
        user["id"],
        token_hash,
        code,
        15,
    )

    verify_url = f"{_absolute_base_url(request)}/verify-email?token={raw_token}"
    html_body, text_body = render_verification_email(code, verify_url, expires_minutes=15)
    await email_service.send_email(to=email, subject="Verify your CloudGPT account", html_body=html_body, text_body=text_body)

    return RedirectResponse(
        url=f"/verify-email?email={quote_plus(email)}&success=A+new+verification+code+has+been+sent.",
        status_code=302,
    )



# ── Routes: Password Reset ───────────────────────────────────────────────────

def _absolute_base_url(request: Request) -> str:
    """Best-effort public base URL, honouring reverse-proxy headers (Caddy)."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", request.url.netloc)
    return f"{proto}://{host}"


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, error: str = "", success: str = ""):
    """Render the 'request a reset link' form."""
    return templates.TemplateResponse(request, "forgot_password.html", {
        "csrf_token": csrf_token(request),
        "token_ttl_minutes": settings.password_reset_token_ttl_minutes,
        "error": error,
        "success": success,
    })


@app.post("/forgot-password")
async def forgot_password(request: Request, email: str = Form(...), csrf_token_value: str | None = Form(None, alias="csrf_token")):
    """Generate a single-use reset token and email it.

    Anti-enumeration: the response is identical whether or not the account
    exists, and email delivery is enqueued off the request path.
    """
    validate_csrf_token(request, csrf_token_value)
    await auth_rate_limit(request)

    # Anti-enumeration: identical response regardless of account existence.
    generic_redirect = RedirectResponse(
        url=f"/forgot-password?success={quote_plus('Password reset link has been sent.')}",
        status_code=302,
    )

    email = email.strip().lower()
    if not email or len(email) > 255:
        return generic_redirect

    user = await asyncio.to_thread(db.get_user_by_email, email)
    # Only password accounts can reset; OAuth-only accounts have no password.
    if not user or not user.get("password_hash"):
        return generic_redirect

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    await asyncio.to_thread(
        db.create_password_reset_token,
        user["id"],
        token_hash,
        settings.password_reset_token_ttl_minutes,
    )

    reset_url = f"{_absolute_base_url(request)}/reset-password?token={token}"
    html_body, text_body = render_password_reset_email(reset_url, settings.password_reset_token_ttl_minutes)

    # Prefer the ARQ queue; fall back to inline delivery when unavailable.
    try:
        await enqueue_task("send_email_task", to=email, subject="Reset your CloudGPT password", html_body=html_body, text_body=text_body)
    except Exception:
        await email_service.send_email(to=email, subject="Reset your CloudGPT password", html_body=html_body, text_body=text_body)

    logger.info("Password reset requested", user_id=user["id"])
    return generic_redirect


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    """Validate a reset token and render the new-password form."""
    token_valid = False
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = await asyncio.to_thread(db.get_password_reset_token, token_hash)
        token_valid = record is not None

    return templates.TemplateResponse(request, "reset_password.html", {
        "token": token,
        "token_valid": token_valid,
        "error": "",
        "csrf_token": csrf_token(request),
    })


@app.post("/reset-password")
async def reset_password(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(""),
    csrf_token_value: str | None = Form(None, alias="csrf_token"),
):
    """Consume a reset token and set the new password."""
    validate_csrf_token(request, csrf_token_value)
    await auth_rate_limit(request)

    def _error(msg: str) -> HTMLResponse:
        return templates.TemplateResponse(request, "reset_password.html", {
            "token": token,
            "token_valid": True,
            "error": msg,
            "csrf_token": csrf_token(request),
        }, status_code=400)

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    record = await asyncio.to_thread(db.get_password_reset_token, token_hash)
    if not record:
        return templates.TemplateResponse(request, "reset_password.html", {
            "token": token,
            "token_valid": False,
            "error": "This reset link is invalid, expired, or already used.",
            "csrf_token": csrf_token(request),
        }, status_code=400)

    if password != confirm_password:
        return _error("Passwords do not match.")
    if not password_is_strong(password):
        return _error("Use a 12+ character password with upper, lower, and a number.")

    await asyncio.to_thread(db.set_user_password, record["user_id"], password)
    await asyncio.to_thread(db.mark_reset_token_used, record["id"])
    # Invalidate any other outstanding tokens for the same user.
    await asyncio.to_thread(db.invalidate_user_reset_tokens, record["user_id"])

    logger.info("Password reset completed", user_id=record["user_id"])
    return RedirectResponse(
        url=f"/?success={quote_plus('Your password has been reset. Please sign in.')}",
        status_code=302,
    )


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    # Personalize CTAs with the signed-in user's plan (the Lite CTA must not
    # read "Current Plan" for someone already on Pro).
    current_plan_key = None
    user = await get_current_user(request)
    if user:
        from db import get_token_usage, get_active_subscription
        from core.entitlements import resolve_entitlements
        usage_info = await asyncio.to_thread(get_token_usage, user["id"])
        subscription_info = await asyncio.to_thread(get_active_subscription, user["id"]) if user.get("id") else None
        entitlements = resolve_entitlements(usage_info.get("tier"), subscription_info, user_email=user.get("email"))
        current_plan_key = entitlements.plan_key.lower()

    return templates.TemplateResponse(request, "pricing.html", {
        "plans": billing_service.public_offers(),
        "comparison": billing_service.pricing_comparison(),
        "current_plan_key": current_plan_key,
        "payment_provider": billing_service.provider,
        "csrf_token": csrf_token(request),
    })


# ── Routes: Billing ──────────────────────────────────────────────────────────

@app.get("/billing", response_class=HTMLResponse)
async def billing_page(request: Request):
    """
    Billing dashboard page for authenticated users.
    """
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Please+sign+in+to+view+billing", status_code=302)

    from db import get_token_usage, get_active_subscription
    from core.entitlements import resolve_entitlements
    usage_info = await asyncio.to_thread(get_token_usage, user["id"])
    subscription_info = await asyncio.to_thread(get_active_subscription, user["id"])
    entitlements = resolve_entitlements(usage_info.get("tier"), subscription_info, user_email=user.get("email"))
    tier = entitlements.plan_key

    return templates.TemplateResponse(request, "billing.html", {
        "user": user,
        "current_tier": tier,
        "effective_tier": entitlements.model_tier,
        "entitlements": entitlements,
        "is_developer": (tier.lower() in ("developer", "admin")),
        "usage": usage_info,
        "subscription": subscription_info,
        "plans": billing_service.public_offers(),
        "payment_provider": billing_service.provider,
        "csrf_token": csrf_token(request),
    })


@app.get("/healthz")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readiness() -> dict[str, str]:
    def check_database() -> None:
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            db.put_connection(conn)
    try:
        await asyncio.to_thread(check_database)
    except Exception as exc:
        logger.warning("Readiness database check failed")
        raise HTTPException(status_code=503, detail="A required dependency is unavailable") from exc

    settings = get_settings()
    if getattr(settings, "has_qdrant", False):
        try:
            from embeddings.qdrant_manager import QdrantManager
            qm = QdrantManager(settings)
            is_compatible = await asyncio.to_thread(qm.check_dimension_compatibility)
            if not is_compatible:
                logger.warning("Readiness Qdrant dimension mismatch check failed")
                raise HTTPException(status_code=503, detail="Vector index dimension mismatch")
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Readiness Qdrant check encountered error: %s", exc)
    elif settings.pinecone_api_key:
        try:
            from embeddings.pinecone_manager import PineconeManager
            pm = PineconeManager(settings)
            is_compatible = await asyncio.to_thread(pm.check_dimension_compatibility)
            if not is_compatible:
                logger.warning("Readiness Pinecone dimension mismatch check failed")
                raise HTTPException(status_code=503, detail="Vector index dimension mismatch")
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Readiness Pinecone check encountered error: %s", exc)

    return {"status": "ready"}


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    """Terms of Service page."""
    return templates.TemplateResponse(request, "terms.html", {
        "csrf_token": csrf_token(request),
        "user": await get_current_user(request),
    })


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    """Privacy Policy and data retention disclosures."""
    return templates.TemplateResponse(request, "privacy.html", {
        "csrf_token": csrf_token(request),
        "user": await get_current_user(request),
    })


@app.get("/api/user/export")
async def export_user_data_endpoint(request: Request):
    """GDPR Article 15/20 Data Portability export."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in to export your data.")
    data = await asyncio.to_thread(db.export_user_data, user_id)
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="cloudgpt-data-export-{user_id}.json"'},
    )


@app.post("/api/user/delete-account")
async def delete_account_endpoint(request: Request, csrf_token_value: str | None = Form(None, alias="csrf_token")):
    """GDPR Article 17 Right to Erasure / permanent account deletion."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Please sign in")
    validate_csrf_token(request, csrf_token_value or request.headers.get("X-CSRF-Token"))
    deleted = await asyncio.to_thread(db.delete_user_account, user_id)
    request.session.clear()
    return {"status": "ok", "deleted": deleted}


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5001, reload=True)
