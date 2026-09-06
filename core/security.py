"""Security helpers: CSRF, request IDs, redacted errors, and origin checks."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import re
import secrets
import uuid
from collections.abc import Callable

import jwt
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from config import get_settings
from logging_config import bind_request_context, unbind_request_context
from metrics import JWT_ERRORS_TOTAL


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT_PATHS = {"/api/billing/webhook", "/auth/google/callback"}


def request_user_fingerprint(request: Request) -> str:
    """An irreversible logging/rate-limit identifier; never return it to users."""
    value = request.session.get("user_id") or (request.client.host if request.client else "unknown")
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def get_request_id(request: Request) -> str:
    """Return the request ID from state, header, or generate a valid standard UUID."""
    if hasattr(request.state, "request_id"):
        return request.state.request_id
    req_id = request.headers.get("X-Request-ID")
    if req_id:
        try:
            req_id = str(uuid.UUID(req_id))
        except (ValueError, AttributeError):
            req_id = str(uuid.uuid4())
    else:
        req_id = str(uuid.uuid4())
    request.state.request_id = req_id
    return req_id


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request) -> None:
    """Validate CSRF for non-safe methods. Fail-closed: reject if no token in session."""
    settings = get_settings()
    if not settings.csrf_enabled or request.method in SAFE_METHODS or request.url.path in CSRF_EXEMPT_PATHS:
        return
    validate_csrf_token(request, request.headers.get(settings.csrf_header_name))


def validate_csrf_token(request: Request, presented: str | None) -> None:
    """Validate a token supplied by a server-rendered HTML form.

    Fail-closed: if no expected token exists in the session, reject the request.
    This prevents CSRF bypass by sending requests before a token is ever set.
    """
    settings = get_settings()
    if not settings.csrf_enabled:
        return
    expected = request.session.get("csrf_token")
    if not expected:
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    if not presented or not hmac.compare_digest(expected, presented):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


class CSRFMiddleware(BaseHTTPMiddleware):
    """Enforce CSRF on all unsafe methods for authenticated sessions, exempting webhooks & callbacks.

    This middleware applies CSRF checking globally so authenticated API requests
    must provide the X-CSRF-Token header matching their session token.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()
        if (
            settings.csrf_enabled
            and request.method not in SAFE_METHODS
            and request.url.path not in CSRF_EXEMPT_PATHS
        ):
            expected = request.session.get("csrf_token")
            presented = request.headers.get(settings.csrf_header_name)
            content_type = request.headers.get("content-type", "")
            is_form = (
                "application/x-www-form-urlencoded" in content_type
                or "multipart/form-data" in content_type
            )

            # If user has an active session (user_id is logged in or csrf_token is set)
            if expected:
                if not is_form:
                    if not presented:
                        raise HTTPException(status_code=403, detail="CSRF token missing")
                    if not hmac.compare_digest(expected, presented):
                        raise HTTPException(status_code=403, detail="Invalid CSRF token")
            elif request.session.get("user_id"):
                # Authenticated session without a csrf_token set: fail closed
                if not is_form:
                    raise HTTPException(status_code=403, detail="CSRF validation failed")

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set conservative browser protections on every response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate and store request ID early for use in logging
        request_id = get_request_id(request)
        # Bind structured-log context so every log line in this request
        # carries the correlation ID (and user id when authenticated).
        bind_request_context(request_id=request_id, user_id=request.session.get("user_id"))

        try:
            response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        finally:
            unbind_request_context()
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; "
            "img-src 'self' data: https: https://*.stripe.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "frame-src 'self' https://js.stripe.com https://hooks.stripe.com https://checkout.stripe.com; "
            "script-src 'self' 'unsafe-inline' https://js.stripe.com; "
            "connect-src 'self' https://api.stripe.com https://checkout.stripe.com",
        )
        response.headers.setdefault("X-Request-ID", request_id)
        return response


def decode_jwt_token(token: str, secret: str | None = None) -> dict:
    """Decode and validate a JWT token with explicit HS256 algorithm and leeway=0."""
    settings = get_settings()
    signing_secret = secret or settings.secret_key
    try:
        return jwt.decode(token, signing_secret, algorithms=["HS256"], leeway=0)
    except jwt.ExpiredSignatureError as exc:
        JWT_ERRORS_TOTAL.labels(error_type="expired").inc()
        raise HTTPException(status_code=401, detail="Token has expired") from exc
    except jwt.InvalidAlgorithmError as exc:
        JWT_ERRORS_TOTAL.labels(error_type="invalid_algorithm").inc()
        raise HTTPException(status_code=401, detail="Invalid token algorithm") from exc
    except jwt.InvalidTokenError as exc:
        JWT_ERRORS_TOTAL.labels(error_type="invalid_token").inc()
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def create_jwt_token(payload: dict, secret: str | None = None, expires_in: int = 3600) -> str:
    """Create a signed JWT token with HS256 and exp claim."""
    settings = get_settings()
    signing_secret = secret or settings.secret_key
    to_encode = payload.copy()
    now = datetime.now(timezone.utc)
    if "exp" not in to_encode:
        to_encode["exp"] = int(now.timestamp()) + expires_in
    if "iat" not in to_encode:
        to_encode["iat"] = int(now.timestamp())
    return jwt.encode(to_encode, signing_secret, algorithm="HS256")


DEVELOPER_TESTING_EMAILS = {
    "fazilprojects@gmail.com",
    "fazilprojects9@gmail.com",
    "shahulrahumath2007.s@gmail.com",
    "amanullahfazil2007.s@gmail.com",
}


def password_is_strong(password: str) -> bool:
    return (
        len(password) >= 8
        and bool(re.search(r"[a-z]", password))
        and bool(re.search(r"[A-Z]", password))
        and bool(re.search(r"\d", password))
        and bool(re.search(r"[!@#$%^&*(),.?\":{}|<>\-_=+\[\]\\/`~]", password))
    )


def validate_password_rules(password: str, confirm_password: str | None = None, email: str = "") -> tuple[bool, str]:
    """
    Validate password conditions.
    Accounts listed in Developer Accounts.txt are exempted from strict rules.
    """
    normalized_email = (email or "").strip().lower()
    exempt_emails = DEVELOPER_TESTING_EMAILS | get_settings().developer_email_set

    if normalized_email in exempt_emails:
        if confirm_password is not None and password != confirm_password:
            return False, "Passwords do not match. Please re-enter."
        if not password:
            return False, "Please enter a password."
        return True, ""

    # Strict rules for all regular accounts
    if confirm_password is not None and password != confirm_password:
        return False, "Passwords do not match. Please re-enter."
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must include at least one uppercase letter (A-Z)."
    if not re.search(r"[a-z]", password):
        return False, "Password must include at least one lowercase letter (a-z)."
    if not re.search(r"\d", password):
        return False, "Password must include at least one number (0-9)."
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>\-_=+\[\]\\/`~]", password):
        return False, "Password must include at least one special character (!@#$%^&*...). "
    return True, ""


_REDACTION_PATTERNS = [
    (re.compile(r"(?i)\b(AKIA[0-9A-Z]{16})\b"), "[REDACTED_AWS_ACCESS_KEY]"),
    (re.compile(r"(?i)\b(ghp_[a-zA-Z0-9]{36})\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?i)(password|secret|bearer)\s*[:=]\s*['\"][^'\"]+['\"]"), r"\1: [REDACTED]"),
]


def sanitize_model_output(text: str) -> str:
    """Sanitize emitted LLM response to prevent secret and credential leakage."""
    if not text:
        return ""
    sanitized = text
    for pattern, replacement in _REDACTION_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized



