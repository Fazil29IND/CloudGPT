"""
Database connection pool and helper queries for CloudGPT.

Uses psycopg2 with a SimpleConnectionPool to manage PostgreSQL connections.
All functions operate synchronously — suitable for FastAPI with run_in_executor
or direct use in synchronous contexts.
"""

import json
import logging
from typing import Any, cast
import uuid
from datetime import datetime, timezone
import psycopg2
import psycopg2.pool
import psycopg2.extras
import bcrypt
from config import get_settings
from core.entitlements import Entitlements, normalize_plan
from metrics import DB_POOL_EXHAUSTED_TOTAL

# Register UUID adapter for psycopg2
psycopg2.extras.register_uuid()

logger = logging.getLogger(__name__)

settings = get_settings()
DATABASE_URL = settings.database_url


USER_COLUMNS = """
id, email, password_hash, name, email_verified, tier, role, is_admin, tokens_used_5h,
tokens_used_week, last_token_reset_5h, last_token_reset_week, created_at, updated_at, settings_json
"""


class QuotaExceeded(Exception):
    """Raised when a server-owned quota cannot reserve worst-case usage."""


class DatabaseUnavailableError(Exception):
    """Raised when the database connection pool is exhausted or unavailable."""

# ── Connection Pool ──────────────────────────────────────────────────────────

_pool = None


def get_pool():
    """Get or create the connection pool (lazy singleton)."""
    global _pool
    if _pool is None or _pool.closed:
        min_conn = getattr(settings, "db_pool_min_conn", 2)
        max_conn = getattr(settings, "db_pool_max_conn", 30)
        _pool = psycopg2.pool.ThreadedConnectionPool(
            min_conn, max_conn, DATABASE_URL, options="-c statement_timeout=30000"
        )
    return _pool


def get_connection():
    """Get a connection from the pool."""
    try:
        return get_pool().getconn()
    except psycopg2.pool.PoolError as exc:
        logger.error("db_pool_exhausted: %s", exc)
        DB_POOL_EXHAUSTED_TOTAL.inc()
        raise DatabaseUnavailableError("Database connection pool exhausted") from exc


def put_connection(conn):
    """Return a connection to the pool."""
    get_pool().putconn(conn)


def close_pool():
    """Close all connections in the pool."""
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None


# ── User Queries ─────────────────────────────────────────────────────────────

def get_user_by_id(user_id: int) -> dict | None:
    """Fetch a user by primary key."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_connection(conn)


def get_user_by_email(email: str) -> dict | None:
    """Fetch a user by email address (case-insensitive)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT {USER_COLUMNS} FROM users WHERE LOWER(email) = LOWER(%s)", (email,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_connection(conn)


def create_user(email: str, password: str | None = None, name: str | None = None,
                email_verified: bool = False) -> dict:
    """
    Create a new user. Password is optional (Google-only accounts have no password).
    Returns the created user dict.
    """
    password_hash = None
    if password:
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    is_dev = email.lower() in settings.developer_email_set
    is_admin_user = email.lower() == (settings.admin_email.strip().lower() if settings.admin_email else "")
    tier = "Max" if is_dev else "Lite"
    role = "admin" if is_admin_user else ("developer" if is_dev else "user")

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO users (email, password_hash, name, email_verified, tier, role, is_admin)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING """ + USER_COLUMNS + """
                """,
                (email, password_hash, name, email_verified, tier, role, is_admin_user),
            )
            _row = cur.fetchone()
            assert _row is not None
            user = dict(_row)
            conn.commit()
            return user
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def verify_password(user: dict, password: str) -> bool:
    """Check a plain-text password against the stored bcrypt hash."""
    if not user or not user.get("password_hash"):
        return False
    return bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8"))


def set_user_password(user_id: int, new_password: str) -> bool:
    """Set a new password for a user (bcrypt-hashed) and stamp password_changed_at."""
    password_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET password_hash = %s, password_changed_at = NOW(), updated_at = NOW()
                WHERE id = %s
                """,
                (password_hash, user_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def update_user_profile(user_id: int, name: str | None = None, email_verified: bool = True) -> bool:
    """Update user's name and email verification status from OAuth provider."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if name:
                cur.execute(
                    """
                    UPDATE users
                    SET name = COALESCE(%s, name), email_verified = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (name, email_verified, user_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE users
                    SET email_verified = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (email_verified, user_id),
                )
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)



# ── Password Reset Token Queries ─────────────────────────────────────────────

def create_password_reset_token(user_id: int, token_hash: str, ttl_minutes: int) -> dict:
    """Persist a hashed password-reset token. Returns the stored row."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
                VALUES (%s, %s, NOW() + (%s || ' minutes')::interval)
                RETURNING id, user_id, token_hash, expires_at, used_at, created_at
                """,
                (user_id, token_hash, int(ttl_minutes)),
            )
            _row = cur.fetchone()
            assert _row is not None
            row = dict(_row)
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def get_password_reset_token(token_hash: str) -> dict | None:
    """Fetch an unused, unexpired reset token by its SHA-256 hash."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, user_id, token_hash, expires_at, used_at, created_at
                FROM password_reset_tokens
                WHERE token_hash = %s
                  AND used_at IS NULL
                  AND expires_at > NOW()
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_connection(conn)


def mark_reset_token_used(token_id: int) -> None:
    """Mark a password-reset token as consumed (single use)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE password_reset_tokens SET used_at = NOW() WHERE id = %s",
                (token_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def invalidate_user_reset_tokens(user_id: int) -> None:
    """Invalidate all outstanding reset tokens for a user (e.g. after a reset)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE password_reset_tokens SET used_at = NOW() WHERE user_id = %s AND used_at IS NULL",
                (user_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


# ── Email Verification Tokens ───────────────────────────────────────────────

def create_email_verification_token(user_id: int, token_hash: str, code: str, ttl_minutes: int = 15) -> dict:
    """
    Store an email verification token & 6-digit code for a user with TTL.
    Invalidates any previous active verification tokens for this user.
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "UPDATE email_verification_tokens SET used_at = NOW() WHERE user_id = %s AND used_at IS NULL",
                    (user_id,),
                )
                cur.execute(
                    f"""
                    INSERT INTO email_verification_tokens (user_id, token_hash, code, expires_at)
                    VALUES (%s, %s, %s, NOW() + INTERVAL '{int(ttl_minutes)} minutes')
                    RETURNING id, user_id, token_hash, code, expires_at, used_at, created_at
                    """,
                    (user_id, token_hash, str(code)),
                )
                _row = cur.fetchone()
                assert _row is not None
                return dict(_row)
    finally:
        put_connection(conn)


def get_email_verification_by_code(user_id: int, code: str) -> dict | None:
    """Look up an unused, unexpired verification code for a specific user."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, user_id, token_hash, code, expires_at, used_at, created_at
                FROM email_verification_tokens
                WHERE user_id = %s AND code = %s AND used_at IS NULL AND expires_at > NOW()
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, str(code).strip()),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_connection(conn)


def get_email_verification_by_token(token_hash: str) -> dict | None:
    """Look up an unused, unexpired verification token by SHA-256 hash."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, user_id, token_hash, code, expires_at, used_at, created_at
                FROM email_verification_tokens
                WHERE token_hash = %s AND used_at IS NULL AND expires_at > NOW()
                LIMIT 1
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_connection(conn)


def mark_email_verification_used(token_id: int) -> None:
    """Mark an email verification token/code as consumed."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE email_verification_tokens SET used_at = NOW() WHERE id = %s",
                (token_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def mark_user_email_verified(user_id: int) -> bool:
    """Update user record to email_verified = TRUE."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET email_verified = TRUE, updated_at = NOW() WHERE id = %s",
                (user_id,),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)



def effective_tier(db_tier: str | None) -> str:
    """Map a stored database tier to the model access tier ('Max', 'Pro', 'Free')."""
    normalized = normalize_plan(db_tier)
    if normalized == "max":
        return "Max"
    if normalized == "pro":
        return "Pro"
    return "Free"


def get_token_usage(user_id: int) -> dict:
    """Fetch current token usage and reset windows if necessary."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                UPDATE users 
                SET tokens_used_day = CASE WHEN NOW() > COALESCE(last_token_reset_day, NOW()) + INTERVAL '1 day' THEN 0 ELSE COALESCE(tokens_used_day, 0) END,
                    last_token_reset_day = CASE WHEN NOW() > COALESCE(last_token_reset_day, NOW()) + INTERVAL '1 day' THEN NOW() ELSE COALESCE(last_token_reset_day, NOW()) END,
                    tokens_used_month = CASE WHEN NOW() > COALESCE(last_token_reset_month, NOW()) + INTERVAL '30 days' THEN 0 ELSE COALESCE(tokens_used_month, 0) END,
                    last_token_reset_month = CASE WHEN NOW() > COALESCE(last_token_reset_month, NOW()) + INTERVAL '30 days' THEN NOW() ELSE COALESCE(last_token_reset_month, NOW()) END,
                    tokens_used_5h = CASE WHEN NOW() > COALESCE(last_token_reset_5h, NOW()) + INTERVAL '5 hours' THEN 0 ELSE COALESCE(tokens_used_5h, 0) END,
                    last_token_reset_5h = CASE WHEN NOW() > COALESCE(last_token_reset_5h, NOW()) + INTERVAL '5 hours' THEN NOW() ELSE COALESCE(last_token_reset_5h, NOW()) END,
                    tokens_used_week = CASE WHEN NOW() > COALESCE(last_token_reset_week, NOW()) + INTERVAL '7 days' THEN 0 ELSE COALESCE(tokens_used_week, 0) END,
                    last_token_reset_week = CASE WHEN NOW() > COALESCE(last_token_reset_week, NOW()) + INTERVAL '7 days' THEN NOW() ELSE COALESCE(last_token_reset_week, NOW()) END
                WHERE id = %s
                RETURNING tier, tokens_used_day, tokens_used_month, tokens_used_5h, tokens_used_week
            """, (user_id,))
            conn.commit()
            row = cur.fetchone()
            if row:
                return dict(row)
            return {
                "tier": "Lite",
                "tokens_used_day": 0,
                "tokens_used_month": 0,
                "tokens_used_5h": 0,
                "tokens_used_week": 0,
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def increment_token_usage(user_id: int, tokens: int):
    """Increment token usage for a user across day, month, 5h, and week."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users 
                SET tokens_used_day = COALESCE(tokens_used_day, 0) + %s,
                    tokens_used_month = COALESCE(tokens_used_month, 0) + %s,
                    tokens_used_5h = COALESCE(tokens_used_5h, 0) + %s,
                    tokens_used_week = COALESCE(tokens_used_week, 0) + %s
                WHERE id = %s
            """, (tokens, tokens, tokens, tokens, user_id))
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def get_active_subscription(user_id: int) -> dict | None:
    """Return the current verified subscription, including a configured grace window."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, user_id, plan_key, provider, provider_subscription_id, status,
                       current_period_start, current_period_end, cancel_at_period_end,
                       canceled_at, trial_end, grace_period_ends_at
                FROM subscriptions
                WHERE user_id = %s
                  AND status IN ('active', 'trialing', 'past_due')
                  AND (current_period_end IS NULL OR current_period_end >= NOW()
                       OR grace_period_ends_at >= NOW())
                ORDER BY current_period_end DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_connection(conn)


def get_billing_customer(user_id: int, provider: str) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_id, provider, provider_customer_id FROM billing_customers WHERE user_id = %s AND provider = %s",
                (user_id, provider),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_connection(conn)


def get_billing_customer_by_provider_id(provider: str, provider_customer_id: str) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_id, provider, provider_customer_id FROM billing_customers WHERE provider = %s AND provider_customer_id = %s",
                (provider, provider_customer_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_connection(conn)


def upsert_billing_customer(user_id: int, provider: str, provider_customer_id: str) -> dict:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO billing_customers(user_id, provider, provider_customer_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, provider) DO UPDATE
                    SET provider_customer_id = EXCLUDED.provider_customer_id, updated_at = NOW()
                RETURNING id, user_id, provider, provider_customer_id
                """,
                (user_id, provider, provider_customer_id),
            )
            _row = cur.fetchone()
            assert _row is not None
            row = dict(_row)
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def record_billing_event(provider: str, event_id: str, event_type: str, payload: dict) -> bool:
    """Insert an event once; payload must already be a safe, redacted summary."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO billing_events(provider, provider_event_id, event_type, payload, processing_status)
                VALUES (%s, %s, %s, %s::jsonb, 'processing')
                ON CONFLICT (provider, provider_event_id) DO NOTHING
                """,
                (provider, event_id, event_type, json.dumps(payload)),
            )
            inserted = cur.rowcount == 1
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def mark_billing_event(event_id: str, status: str, error_code: str | None = None) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE billing_events SET processing_status = %s, processed_at = NOW(), error_code = %s WHERE provider = 'stripe' AND provider_event_id = %s",
                (status, error_code, event_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def get_billing_event(provider: str, event_id: str) -> dict | None:
    """Fetch a recorded billing event (idempotency check for payments)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, provider, provider_event_id, event_type, processing_status FROM billing_events WHERE provider = %s AND provider_event_id = %s",
                (provider, event_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_connection(conn)


def upsert_subscription(
    *, user_id: int, plan_key: str, provider: str, provider_subscription_id: str,
    status: str, current_period_start: datetime | None, current_period_end: datetime | None,
    cancel_at_period_end: bool = False, canceled_at: datetime | None = None,
    trial_end: datetime | None = None, grace_period_ends_at: datetime | None = None,
) -> None:
    """Persist provider-authoritative subscription state idempotently."""
    if plan_key not in {"lite", "pro", "max"}:
        raise ValueError("Only public plan keys may be persisted")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO subscriptions(
                    user_id, plan_key, provider, provider_subscription_id, status,
                    current_period_start, current_period_end, cancel_at_period_end,
                    canceled_at, trial_end, grace_period_ends_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(provider, provider_subscription_id) DO UPDATE SET
                    plan_key = EXCLUDED.plan_key, status = EXCLUDED.status,
                    current_period_start = EXCLUDED.current_period_start,
                    current_period_end = EXCLUDED.current_period_end,
                    cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                    canceled_at = EXCLUDED.canceled_at, trial_end = EXCLUDED.trial_end,
                    grace_period_ends_at = EXCLUDED.grace_period_ends_at,
                    updated_at = NOW()
                """,
                (user_id, plan_key, provider, provider_subscription_id, status,
                 current_period_start, current_period_end, cancel_at_period_end,
                 canceled_at, trial_end, grace_period_ends_at),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def _normalize_request_uuid(request_id: Any) -> uuid.UUID:
    """Safely normalize any request_id into a valid UUID object without throwing ValueError."""
    if isinstance(request_id, uuid.UUID):
        return request_id
    id_str = str(request_id).strip()
    try:
        return uuid.UUID(id_str)
    except (ValueError, TypeError, AttributeError):
        return uuid.uuid5(uuid.NAMESPACE_DNS, f"cloudgpt-request-{id_str}")


def reserve_usage(user_id: int, request_id: str, entitlements: Entitlements, estimated_tokens: int) -> dict:
    """Atomically reserve worst-case usage so concurrent requests cannot overspend."""
    if estimated_tokens < 0:
        raise ValueError("estimated_tokens must be non-negative")
    request_uuid = _normalize_request_uuid(request_id)
    conn = get_connection()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM quota_reservations WHERE user_id = %s AND request_id = %s FOR UPDATE", (user_id, str(request_uuid)))
                existing = cur.fetchone()
                if existing:
                    return {"request_id": str(request_uuid), "reserved_tokens": existing["reserved_tokens"], "idempotent": True}

                cur.execute(
                    """
                    UPDATE users
                    SET tokens_used_5h = CASE WHEN NOW() >= last_token_reset_5h + INTERVAL '5 hours' THEN 0 ELSE tokens_used_5h END,
                        last_token_reset_5h = CASE WHEN NOW() >= last_token_reset_5h + INTERVAL '5 hours' THEN NOW() ELSE last_token_reset_5h END,
                        tokens_used_week = CASE WHEN NOW() >= last_token_reset_week + INTERVAL '7 days' THEN 0 ELSE tokens_used_week END,
                        last_token_reset_week = CASE WHEN NOW() >= last_token_reset_week + INTERVAL '7 days' THEN NOW() ELSE last_token_reset_week END
                    WHERE id = %s
                    RETURNING tokens_used_5h, tokens_used_week
                    """,
                    (user_id,),
                )
                usage = cur.fetchone()
                if not usage:
                    raise ValueError("Unknown user")
                cur.execute(
                    """
                    SELECT COALESCE(SUM(reserved_tokens), 0) AS reserved
                    FROM (
                        SELECT reserved_tokens FROM quota_reservations
                        WHERE user_id = %s AND status = 'reserved'
                        FOR UPDATE
                    ) locked
                    """,
                    (user_id,),
                )
                _res = cur.fetchone()
                assert _res is not None
                outstanding = int(_res["reserved"])
                if not entitlements.unlimited:
                    if (int(cast(int, usage["tokens_used_5h"])) + outstanding + estimated_tokens > int(cast(int, entitlements.tokens_5h))
                            or int(cast(int, usage["tokens_used_week"])) + outstanding + estimated_tokens > int(cast(int, entitlements.tokens_week))):
                        raise QuotaExceeded("Token quota exhausted")
                cur.execute(
                    "INSERT INTO quota_reservations(user_id, request_id, plan_key, reserved_tokens) VALUES (%s, %s, %s, %s)",
                    (user_id, str(request_uuid), normalize_plan(entitlements.plan_key), estimated_tokens),
                )
        return {"request_id": str(request_uuid), "reserved_tokens": estimated_tokens, "idempotent": False}
    finally:
        put_connection(conn)


def settle_usage(
    user_id: int, request_id: str, entitlements: Entitlements, *, model: str,
    input_tokens: int, output_tokens: int, status: str = "settled",
    thinking_tokens: int = 0, estimated_cost: float | None = None,
) -> dict:
    """Settle or release a reservation exactly once and write a durable usage ledger.

    ``thinking_tokens`` records reasoning tokens produced by the Thinking
    Engine separately from visible answer tokens; both count toward quota.
    ``estimated_cost`` is the model-priced USD cost of the request, written to
    the usage ledger for margin reporting.

    Token windows are written by exactly one writer: ``increment_token_usage``
    adds the request total to all four windows, so this function must NOT also
    add to the 5h/week windows. (Historically it did — every settled request
    was counted twice against those windows, so users hit 5h/week caps at
    roughly twice the intended rate.)
    """
    request_uuid = _normalize_request_uuid(request_id)
    total_tokens = max(0, input_tokens) + max(0, output_tokens) + max(0, thinking_tokens) if status == "settled" else 0
    conn = get_connection()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM quota_reservations WHERE user_id = %s AND request_id = %s FOR UPDATE", (user_id, str(request_uuid)))
                reservation = cur.fetchone()
                if not reservation:
                    raise ValueError("Usage reservation not found")
                if reservation["status"] != "reserved":
                    cur.execute("SELECT tokens_used_5h, tokens_used_week FROM users WHERE id = %s", (user_id,))
                    current = cur.fetchone()
                    return dict(current) if current else {}
                cur.execute("SELECT tokens_used_5h, tokens_used_week FROM users WHERE id = %s", (user_id,))
                _row = cur.fetchone()
                assert _row is not None
                usage = dict(_row)
                reservation_status = "settled" if status == "settled" else ("released" if status == "cancelled" else "failed")
                cur.execute("UPDATE quota_reservations SET status = %s, settled_at = NOW() WHERE id = %s", (reservation_status, reservation["id"]))
                cur.execute(
                    """
                    INSERT INTO usage_events(user_id, request_id, plan_key, model, input_tokens, output_tokens, thinking_tokens, total_tokens, status, estimated_cost)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(user_id, request_id) DO NOTHING
                    """,
                    (user_id, str(request_uuid), normalize_plan(entitlements.plan_key), model, max(0, input_tokens), max(0, output_tokens), max(0, thinking_tokens), total_tokens, status, estimated_cost),
                )
        return usage
    finally:
        put_connection(conn)


def release_reservation(user_id: int, request_id: str) -> bool:
    """Release an active quota reservation upon failure or client disconnect."""
    request_uuid = _normalize_request_uuid(request_id)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE quota_reservations
                SET status = 'released', settled_at = NOW()
                WHERE user_id = %s AND request_id = %s AND status = 'reserved'
                """,
                (user_id, str(request_uuid)),
            )
            released = cur.rowcount > 0
        conn.commit()
        return released
    except Exception:
        conn.rollback()
        logger.exception("Failed to release reservation for user_id=%s request_id=%s", user_id, request_id)
        return False
    finally:
        put_connection(conn)


def cleanup_expired_reservations(ttl_minutes: int = 10) -> int:
    """Reap orphaned reservations in 'reserved' status older than TTL minutes."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE quota_reservations
                SET status = 'released', settled_at = NOW()
                WHERE status = 'reserved'
                  AND created_at < NOW() - INTERVAL '{int(ttl_minutes)} minutes'
                """
            )
            count = cur.rowcount
        conn.commit()
        if count > 0:
            logger.info("Cleaned up %d expired quota reservations", count)
        return count
    except Exception:
        conn.rollback()
        logger.exception("Failed to cleanup expired quota reservations")
        return 0
    finally:
        put_connection(conn)


def export_user_data(user_id: int) -> dict:
    """Export complete user account data, messages, usage ledger and subscriptions for GDPR portability."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
            if not user:
                return {}
            user_data = {k: v for k, v in user.items() if k != "password_hash"}

            cur.execute("SELECT id, session_id, role, content, created_at FROM messages WHERE user_id = %s ORDER BY created_at ASC", (user_id,))
            messages = [dict(m) for m in cur.fetchall()]

            cur.execute("SELECT session_id, title, created_at, updated_at FROM session_titles WHERE user_id = %s", (user_id,))
            sessions = [dict(s) for s in cur.fetchall()]

            cur.execute("SELECT id, plan_key, provider, provider_subscription_id, status, current_period_start, current_period_end, created_at FROM subscriptions WHERE user_id = %s", (user_id,))
            subscriptions = [dict(s) for s in cur.fetchall()]

            cur.execute("SELECT request_id, plan_key, model, input_tokens, output_tokens, total_tokens, status, created_at FROM usage_events WHERE user_id = %s ORDER BY created_at DESC LIMIT 500", (user_id,))
            usage = [dict(u) for u in cur.fetchall()]

            def serialize_val(val):
                if isinstance(val, (datetime, uuid.UUID)):
                    return str(val)
                return val

            return {
                "user": {k: serialize_val(v) for k, v in user_data.items()},
                "sessions": [{k: serialize_val(v) for k, v in s.items()} for s in sessions],
                "messages": [{k: serialize_val(v) for k, v in m.items()} for m in messages],
                "subscriptions": [{k: serialize_val(v) for k, v in s.items()} for s in subscriptions],
                "usage_history": [{k: serialize_val(v) for k, v in u.items()} for u in usage],
                "exported_at": datetime.now(timezone.utc).isoformat(),
            }
    finally:
        put_connection(conn)


def delete_user_account(user_id: int) -> bool:
    """Permanently and atomically delete all data associated with a user."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
                return cur.rowcount > 0
    finally:
        put_connection(conn)


# ── OAuth Account Queries ────────────────────────────────────────────────────




def get_oauth_account(provider: str, provider_user_id: str) -> dict | None:
    """Look up an OAuth link by provider + provider_user_id."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT oa.*, u.email, u.name, u.email_verified
                FROM oauth_accounts oa
                JOIN users u ON u.id = oa.user_id
                WHERE oa.provider = %s AND oa.provider_user_id = %s
                """,
                (provider, provider_user_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_connection(conn)


def create_oauth_account(user_id: int, provider: str, provider_user_id: str) -> dict:
    """
    Link an OAuth provider account to a local user.
    The UNIQUE(provider, provider_user_id) constraint prevents duplicates.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO oauth_accounts (user_id, provider, provider_user_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (provider, provider_user_id) DO UPDATE SET user_id = EXCLUDED.user_id
                RETURNING *
                """,
                (user_id, provider, provider_user_id),
            )
            _row = cur.fetchone()
            assert _row is not None
            account = dict(_row)
            conn.commit()
            return account
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


# ── Chat History Queries ─────────────────────────────────────────────────────

def add_message(
    user_id: int | None,
    session_id: str | None,
    role: str,
    content: str,
    attachments: list[dict] | None = None,
    artifacts: list[dict] | None = None,
) -> int | None:
    """Save a chat message to the database and return its message_id."""
    if not user_id and not session_id:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            att_json = json.dumps(attachments) if attachments is not None else None
            art_json = json.dumps(artifacts) if artifacts is not None else None
            cur.execute(
                "INSERT INTO messages (user_id, session_id, role, content, attachments, artifacts) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (user_id, session_id, role, content, att_json, art_json),
            )
            row = cur.fetchone()
            inserted_id = row[0] if row else None
            conn.commit()
            return inserted_id
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def get_chat_history(user_id: int | None, session_id: str | None, limit: int = 10) -> list[dict]:
    """Retrieve the recent chat history for a user or session."""
    if not user_id and not session_id:
        return []
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if user_id and session_id:
                cur.execute(
                    "SELECT id, role, content, attachments, artifacts, created_at FROM messages "
                    "WHERE user_id = %s AND session_id = %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (user_id, session_id, limit),
                )
            elif user_id:
                cur.execute(
                    "SELECT id, role, content, attachments, artifacts, created_at FROM messages "
                    "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                    (user_id, limit),
                )
            else:
                cur.execute(
                    "SELECT id, role, content, attachments, artifacts, created_at FROM messages "
                    "WHERE session_id = %s ORDER BY created_at DESC LIMIT %s",
                    (session_id, limit),
                )
            rows = cur.fetchall()
            return [
                {
                    "id": r["id"],
                    "role": r["role"],
                    "content": r["content"],
                    "attachments": r.get("attachments"),
                    "artifacts": r.get("artifacts"),
                    "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                }
                for r in reversed(rows)
            ]
    finally:
        put_connection(conn)


def truncate_messages_from(user_id: int, session_id: str, from_message_id: int) -> int:
    """Delete messages for a session starting from a specific message ID onward."""
    if not user_id or not session_id or not from_message_id:
        return 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM messages WHERE user_id = %s AND session_id = %s AND id >= %s",
                (user_id, session_id, from_message_id),
            )
            deleted_count = cur.rowcount
            conn.commit()
            return deleted_count
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def save_message_feedback(user_id: int, message_id: int, rating: int, reason: str | None = None) -> dict[str, Any]:
    """Save or update feedback (rating -1 or 1) for an assistant message."""
    if rating not in (-1, 1):
        raise ValueError("Rating must be -1 or 1")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO message_feedback (user_id, message_id, rating, reason, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (user_id, message_id)
                DO UPDATE SET rating = EXCLUDED.rating, reason = EXCLUDED.reason, created_at = NOW()
                RETURNING id, user_id, message_id, rating, reason
                """,
                (user_id, message_id, rating, reason),
            )
            row = cur.fetchone()
            conn.commit()
            assert row is not None
            return {
                "id": row[0],
                "user_id": row[1],
                "message_id": row[2],
                "rating": row[3],
                "reason": row[4],
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def create_artifact_record(
    *,
    user_id: int,
    session_id: str | None,
    message_id: int | None,
    filename: str,
    mime: str,
    size: int,
    storage_key: str,
) -> dict[str, Any]:
    """Create a new artifact record in the database."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO artifacts (user_id, session_id, message_id, filename, mime, size, storage_key, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id, user_id, session_id, message_id, filename, mime, size, storage_key, created_at
                """,
                (user_id, session_id, message_id, filename, mime, size, storage_key),
            )
            row = cur.fetchone()
            conn.commit()
            assert row is not None
            return {
                "id": str(row[0]),
                "user_id": row[1],
                "session_id": row[2],
                "message_id": row[3],
                "filename": row[4],
                "mime": row[5],
                "size": row[6],
                "storage_key": row[7],
                "created_at": row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]),
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def get_artifact_record(artifact_id: str, user_id: int | None = None) -> dict[str, Any] | None:
    """Retrieve an artifact record by ID, optionally verifying user ownership."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT id, user_id, session_id, message_id, filename, mime, size, storage_key, created_at "
                    "FROM artifacts WHERE id = %s AND user_id = %s",
                    (artifact_id, user_id),
                )
            else:
                cur.execute(
                    "SELECT id, user_id, session_id, message_id, filename, mime, size, storage_key, created_at "
                    "FROM artifacts WHERE id = %s",
                    (artifact_id,),
                )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": str(row["id"]),
                "user_id": row["user_id"],
                "session_id": row["session_id"],
                "message_id": row["message_id"],
                "filename": row["filename"],
                "mime": row["mime"],
                "size": row["size"],
                "storage_key": row["storage_key"],
                "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
            }
    finally:
        put_connection(conn)


def list_artifacts_for_session(user_id: int, session_id: str) -> list[dict[str, Any]]:
    """List artifacts associated with a user's session."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_id, session_id, message_id, filename, mime, size, storage_key, created_at "
                "FROM artifacts WHERE user_id = %s AND session_id = %s ORDER BY created_at ASC",
                (user_id, session_id),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": str(r["id"]),
                    "user_id": r["user_id"],
                    "session_id": r["session_id"],
                    "message_id": r["message_id"],
                    "filename": r["filename"],
                    "mime": r["mime"],
                    "size": r["size"],
                    "storage_key": r["storage_key"],
                    "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                }
                for r in rows
            ]
    finally:
        put_connection(conn)


def set_session_title(user_id: int, session_id: str, title: str) -> None:
    """Persist or update custom AI-generated title for a conversation session."""
    if not user_id or not session_id or not title:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO session_titles (user_id, session_id, title, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (user_id, session_id) DO UPDATE
                SET title = EXCLUDED.title, updated_at = NOW()
                """,
                (user_id, session_id, title.strip()[:255]),
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def get_session_title(user_id: int, session_id: str) -> str | None:
    """Retrieve custom AI-generated title for a conversation session if set."""
    if not user_id or not session_id:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title FROM session_titles WHERE user_id = %s AND session_id = %s",
                (user_id, session_id),
            )
            row = cur.fetchone()
            if row:
                val = row[0] if isinstance(row, (tuple, list)) else row.get("title")
                return str(val) if val else None
            return None
    except Exception:
        return None
    finally:
        put_connection(conn)


def get_session_summary(user_id: int | None, session_id: str) -> str | None:
    """Retrieve rolling technical summary for a conversation session."""
    if not session_id:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if user_id:
                cur.execute(
                    "SELECT summary FROM session_summaries WHERE user_id = %s AND session_id = %s",
                    (user_id, session_id),
                )
            else:
                cur.execute(
                    "SELECT summary FROM session_summaries WHERE session_id = %s ORDER BY updated_at DESC LIMIT 1",
                    (session_id,),
                )
            row = cur.fetchone()
            if row and row[0]:
                return row[0]
            if user_id:
                try:
                    cur.execute(
                        "SELECT summary FROM session_titles WHERE user_id = %s AND session_id = %s",
                        (user_id, session_id),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        return row[0]
                except Exception:
                    pass
            return None
    except Exception as e:
        logger.debug("get_session_summary error: %s", e)
        return None
    finally:
        put_connection(conn)


def update_session_summary(user_id: int | None, session_id: str, summary: str) -> None:
    """Save or update rolling technical summary for a conversation session."""
    if not session_id or not summary:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO session_summaries (user_id, session_id, summary, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (user_id, session_id) DO UPDATE
                SET summary = EXCLUDED.summary, updated_at = NOW()
                """,
                (user_id, session_id, summary.strip()),
            )
            if user_id:
                try:
                    cur.execute(
                        """
                        UPDATE session_titles SET summary = %s, updated_at = NOW()
                        WHERE user_id = %s AND session_id = %s
                        """,
                        (summary.strip(), user_id, session_id),
                    )
                except Exception:
                    pass
            conn.commit()
    except Exception as e:
        conn.rollback()
        logger.debug("update_session_summary error: %s", e)
    finally:
        put_connection(conn)


def get_user_sessions(user_id: int, limit: int = 100, query: str | None = None) -> list[dict]:
    """Retrieve the recent chat sessions for a user, optionally filtered by search query."""
    if not user_id:
        return []
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if query and query.strip():
                search_term = f"%{query.strip()}%"
                cur.execute(
                    """
                    SELECT m.session_id, MIN(m.created_at) AS created_at,
                           COALESCE(
                               st.title,
                               (
                                   SELECT LEFT(first_message.content, 40)
                                   FROM messages AS first_message
                                   WHERE first_message.user_id = m.user_id
                                     AND first_message.session_id = m.session_id
                                     AND first_message.role = 'user'
                                   ORDER BY first_message.created_at ASC, first_message.id ASC
                                   LIMIT 1
                               ),
                               'New Conversation'
                           ) AS title
                    FROM messages m
                    LEFT JOIN session_titles st ON st.user_id = m.user_id AND st.session_id = m.session_id
                    WHERE m.user_id = %s AND m.session_id IS NOT NULL
                      AND (
                          m.session_id IN (
                              SELECT DISTINCT session_id FROM messages
                              WHERE user_id = %s AND content ILIKE %s
                          )
                          OR st.title ILIKE %s
                      )
                    GROUP BY m.user_id, m.session_id, st.title
                    ORDER BY MAX(m.created_at) DESC
                    LIMIT %s
                    """,
                    (user_id, user_id, search_term, search_term, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT m.session_id, MIN(m.created_at) AS created_at,
                           COALESCE(
                               st.title,
                               (
                                   SELECT LEFT(first_message.content, 40)
                                   FROM messages AS first_message
                                   WHERE first_message.user_id = m.user_id
                                     AND first_message.session_id = m.session_id
                                     AND first_message.role = 'user'
                                   ORDER BY first_message.created_at ASC, first_message.id ASC
                                   LIMIT 1
                               ),
                               'New Conversation'
                           ) AS title
                    FROM messages m
                    LEFT JOIN session_titles st ON st.user_id = m.user_id AND st.session_id = m.session_id
                    WHERE m.user_id = %s AND m.session_id IS NOT NULL
                    GROUP BY m.user_id, m.session_id, st.title
                    ORDER BY MAX(m.created_at) DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
            rows = cur.fetchall()
            return [
                {
                    "session_id": r["session_id"],
                    "created_at": r["created_at"],
                    "title": r["title"],
                }
                for r in rows
            ]
    finally:
        put_connection(conn)


def delete_user_session(user_id: int, session_id: str) -> bool:
    """Delete all messages and custom titles associated with a user's session."""
    if not user_id or not session_id:
        return False
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM session_titles WHERE user_id = %s AND session_id = %s",
                    (user_id, session_id),
                )
                cur.execute(
                    "DELETE FROM messages WHERE user_id = %s AND session_id = %s",
                    (user_id, session_id),
                )
                conn.commit()
                return cur.rowcount > 0
    finally:
        put_connection(conn)


# ── Dedicated Accounts Seeding ────────────────────────────────────────────────

# Demo accounts are only seeded when SEED_DEMO_ACCOUNTS=true (development only).
# They are NEVER deleted, and the function uses upsert to avoid overwriting
# existing user data or deleting real users.
DEMO_ACCOUNTS = [
    {
        "email": "demo-lite@cloudgpt.local",
        "password": "DemoLite2026!x",
        "name": "Demo Lite User",
        "tier": "Lite",
    },
    {
        "email": "demo-pro@cloudgpt.local",
        "password": "DemoPro2026!x",
        "name": "Demo Pro User",
        "tier": "Pro",
    },
    {
        "email": "demo-max@cloudgpt.local",
        "password": "DemoMax2026!x",
        "name": "Demo Max User",
        "tier": "Max",
    },
]


def seed_required_users() -> None:
    """
    Upsert demo accounts when SEED_DEMO_ACCOUNTS=true (dev convenience only).

    ⚠️  This function NEVER deletes existing users.
    It only inserts demo accounts if they don't exist, or updates their tier
    if they do. Passwords are only set on initial insert — existing passwords
    are never overwritten to avoid disrupting active sessions.

    Also ensures the session_titles table exists (harmless DDL).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # The session_titles table is created by migration 007; the guarded
            # DDL below only keeps ad-hoc dev databases bootstrapped.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS session_titles (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    session_id VARCHAR(255) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (user_id, session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_session_titles_user_session
                    ON session_titles(user_id, session_id);
                """
            )

            if not settings.seed_demo_accounts:
                conn.commit()
                logger.info("Demo account seeding skipped (SEED_DEMO_ACCOUNTS=false)")
                return

            # Upsert demo accounts (INSERT … ON CONFLICT DO UPDATE tier only)
            seeded = 0
            for acct in DEMO_ACCOUNTS:
                pwd_hash = bcrypt.hashpw(acct["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash, name, email_verified, tier, updated_at)
                    VALUES (%s, %s, %s, TRUE, %s, NOW())
                    ON CONFLICT (email) DO UPDATE SET
                        tier = EXCLUDED.tier,
                        updated_at = NOW()
                    """,
                    (acct["email"].lower(), pwd_hash, acct["name"], acct["tier"]),
                )
                seeded += 1
        conn.commit()
        logger.info("%d demo accounts upserted (no users were deleted)", seeded)
    except Exception:
        conn.rollback()
        logger.exception("Failed to seed demo accounts")
        raise
    finally:
        put_connection(conn)



def sync_admin_roles() -> None:
    """Sync admin and developer roles from environment variables to the database.

    Called at startup to ensure the database reflects the current ADMIN_EMAIL
    and DEVELOPER_EMAILS configuration. Only updates role/is_admin fields;
    never deletes users or changes passwords.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            admin_email = (settings.admin_email.strip().lower() if settings.admin_email else "")
            dev_emails = settings.developer_email_set

            # Reset all admin flags first (in case admin email changed)
            cur.execute("UPDATE users SET is_admin = FALSE WHERE is_admin = TRUE")

            # Set admin
            if admin_email:
                cur.execute(
                    "UPDATE users SET role = 'admin', is_admin = TRUE, tier = 'Max' WHERE LOWER(email) = %s",
                    (admin_email,),
                )

            # Set developers (excluding admin who was already set)
            for dev_email in dev_emails:
                if dev_email != admin_email:
                    cur.execute(
                        "UPDATE users SET role = 'developer', tier = 'Max' WHERE LOWER(email) = %s AND role = 'user'",
                        (dev_email,),
                    )

        conn.commit()
        logger.info("Admin/developer roles synced (%d developer emails configured)", len(dev_emails))
    except Exception:
        conn.rollback()
        logger.exception("Failed to sync admin roles")
        raise
    finally:
        put_connection(conn)


# ── Admin Queries ────────────────────────────────────────────────────────────

def admin_list_users(page: int = 1, per_page: int = 25, query: str | None = None) -> dict:
    """Paginated user list with tier, role, and subscription status for the admin UI."""
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
    offset = (page - 1) * per_page
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            params: list = []
            where = ""
            if query and query.strip():
                where = "WHERE LOWER(u.email) LIKE %s OR LOWER(COALESCE(u.name, '')) LIKE %s"
                like = f"%{query.strip().lower()}%"
                params = [like, like]

            cur.execute(f"SELECT COUNT(*) AS total FROM users u {where}", params)
            _cnt = cur.fetchone()
            assert _cnt is not None
            total = int(_cnt["total"])

            cur.execute(
                f"""
                SELECT u.id, u.email, u.name, u.tier, u.role, u.is_admin,
                       u.tokens_used_day, u.tokens_used_month, u.tokens_used_5h,
                       u.tokens_used_week, u.email_verified, u.created_at,
                       s.plan_key AS subscription_plan, s.status AS subscription_status,
                       s.current_period_end AS subscription_period_end
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT plan_key, status, current_period_end
                    FROM subscriptions sub
                    WHERE sub.user_id = u.id
                    ORDER BY (sub.status IN ('active', 'trialing', 'past_due')) DESC,
                             current_period_end DESC NULLS LAST
                    LIMIT 1
                ) s ON TRUE
                {where}
                ORDER BY u.created_at DESC, u.id DESC
                LIMIT %s OFFSET %s
                """,
                [*params, per_page, offset],
            )
            users = [dict(r) for r in cur.fetchall()]
            return {"users": users, "total": total, "page": page, "per_page": per_page}
    finally:
        put_connection(conn)


def admin_get_user_detail(user_id: int) -> dict | None:
    """Full admin view of one user: profile, usage, subscription, recent sessions."""
    user = get_user_by_id(user_id)
    if not user:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, plan_key, provider, status, current_period_start,
                       current_period_end, cancel_at_period_end, created_at
                FROM subscriptions WHERE user_id = %s
                ORDER BY created_at DESC LIMIT 10
                """,
                (user_id,),
            )
            subscriptions = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT request_id, plan_key, model, input_tokens, output_tokens,
                       total_tokens, status, created_at
                FROM usage_events WHERE user_id = %s
                ORDER BY created_at DESC LIMIT 50
                """,
                (user_id,),
            )
            usage_events = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT session_id, MIN(created_at) AS started_at, COUNT(*) AS message_count
                FROM messages WHERE user_id = %s AND session_id IS NOT NULL
                GROUP BY session_id ORDER BY MAX(created_at) DESC LIMIT 20
                """,
                (user_id,),
            )
            sessions = [dict(r) for r in cur.fetchall()]

            def serialize(val):
                if isinstance(val, (datetime, uuid.UUID)):
                    return str(val)
                return val

            return {
                "user": {k: serialize(v) for k, v in user.items() if k != "password_hash"},
                "subscriptions": [{k: serialize(v) for k, v in s.items()} for s in subscriptions],
                "usage_events": [{k: serialize(v) for k, v in u.items()} for u in usage_events],
                "sessions": [{k: serialize(v) for k, v in s.items()} for s in sessions],
            }
    finally:
        put_connection(conn)


def admin_usage_summary(days: int = 14) -> list[dict]:
    """Aggregate token usage per plan per day for the admin dashboard chart."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT plan_key,
                       DATE(created_at) AS day,
                       COUNT(*) AS requests,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM usage_events
                WHERE created_at >= NOW() - (%s || ' days')::interval
                GROUP BY plan_key, DATE(created_at)
                ORDER BY day DESC, plan_key
                """,
                (max(1, min(days, 90)),),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        put_connection(conn)


def admin_set_user_tier(user_id: int, tier: str) -> dict | None:
    """Manually override a user's tier (admin action). Returns the updated user."""
    tier = tier.strip()
    if tier.lower() not in {"lite", "pro", "max", "free"}:
        raise ValueError("tier must be one of Lite, Pro, Max, Free")
    canonical = tier.lower()
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE users
                SET tier = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING """ + USER_COLUMNS + """
                """,
                (canonical.capitalize(), user_id),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def get_user_memories(user_id: int) -> list[dict[str, Any]]:
    """Retrieve all persistent preferences and memory facts for a user."""
    if not user_id:
        return []
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, user_id, memory_key, memory_value, category, confidence, created_at, updated_at
                FROM user_memory
                WHERE user_id = %s
                ORDER BY updated_at DESC
                """,
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.debug("get_user_memories error: %s", e)
        return []
    finally:
        put_connection(conn)


def upsert_user_memory(
    user_id: int,
    key: str,
    value: str,
    category: str = "general",
    confidence: float = 1.0,
) -> dict[str, Any] | None:
    """Store or update a durable user preference / context fact."""
    if not user_id or not key or not value:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO user_memory (user_id, memory_key, memory_value, category, confidence, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (user_id, memory_key) DO UPDATE
                SET memory_value = EXCLUDED.memory_value,
                    category = EXCLUDED.category,
                    confidence = EXCLUDED.confidence,
                    updated_at = NOW()
                RETURNING id, user_id, memory_key, memory_value, category, confidence, created_at, updated_at
                """,
                (user_id, key.strip()[:100], value.strip(), category[:50], float(confidence)),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        logger.debug("upsert_user_memory error: %s", e)
        return None
    finally:
        put_connection(conn)


def delete_user_memory(user_id: int, memory_id: int) -> bool:
    """Delete a user memory fact by ID."""
    if not user_id or not memory_id:
        return False
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_memory WHERE id = %s AND user_id = %s",
                (memory_id, user_id),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
    except Exception as e:
        conn.rollback()
        logger.debug("delete_user_memory error: %s", e)
        return False
    finally:
        put_connection(conn)


def delete_all_user_memory(user_id: int) -> int:
    """Delete all durable memory facts for a given user."""
    if not user_id:
        return 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_memory WHERE user_id = %s", (user_id,))
            deleted = cur.rowcount
            conn.commit()
            return deleted
    except Exception as e:
        conn.rollback()
        logger.debug("delete_all_user_memory error: %s", e)
        return 0
    finally:
        put_connection(conn)


def update_user_setting(user_id: int, key: str, value: Any) -> bool:
    """Update a specific preference in the user's settings_json."""
    if not user_id or not key:
        return False
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET settings_json = jsonb_set(
                    COALESCE(settings_json, '{}'),
                    %s::text[],
                    %s::jsonb,
                    true
                ), updated_at = NOW()
                WHERE id = %s
                """,
                ([key], json.dumps(value), user_id),
            )
            updated = cur.rowcount > 0
            conn.commit()
            return updated
    except Exception as e:
        conn.rollback()
        logger.debug("update_user_setting error: %s", e)
        return False
    finally:
        put_connection(conn)


