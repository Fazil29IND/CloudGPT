-- CloudGPT schema migration 001: billing, durable usage accounting, and auth hardening.
-- This migration supports existing Lite/Free/Pro/Max rows; public plans are normalized
-- at the application boundary so legacy values remain readable during rollout.

ALTER TABLE users ADD COLUMN IF NOT EXISTS tier VARCHAR(50) NOT NULL DEFAULT 'Lite';
ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_used_5h BIGINT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_used_week BIGINT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_token_reset_5h TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_token_reset_week TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS plans (
    id BIGSERIAL PRIMARY KEY,
    plan_key VARCHAR(20) NOT NULL CHECK (plan_key IN ('lite', 'pro')),
    provider VARCHAR(32) NOT NULL DEFAULT 'stripe',
    interval VARCHAR(16) NOT NULL CHECK (interval IN ('month', 'year', 'none')),
    provider_price_id VARCHAR(255),
    currency VARCHAR(8) NOT NULL DEFAULT 'USD',
    display_name VARCHAR(80) NOT NULL,
    model_access VARCHAR(32) NOT NULL,
    quotas JSONB NOT NULL DEFAULT '{}'::jsonb,
    feature_flags JSONB NOT NULL DEFAULT '{}'::jsonb,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(plan_key, provider, interval),
    UNIQUE(provider, provider_price_id)
);

CREATE TABLE IF NOT EXISTS billing_customers (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL,
    provider_customer_id VARCHAR(255) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, provider),
    UNIQUE(provider, provider_customer_id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_key VARCHAR(20) NOT NULL CHECK (plan_key IN ('lite', 'pro')),
    provider VARCHAR(32) NOT NULL,
    provider_subscription_id VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL CHECK (status IN ('incomplete', 'incomplete_expired', 'trialing', 'active', 'past_due', 'canceled', 'unpaid', 'paused')),
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    canceled_at TIMESTAMPTZ,
    trial_end TIMESTAMPTZ,
    grace_period_ends_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(provider, provider_subscription_id)
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user_status ON subscriptions(user_id, status, current_period_end DESC);

CREATE TABLE IF NOT EXISTS billing_events (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(32) NOT NULL,
    provider_event_id VARCHAR(255) NOT NULL,
    event_type VARCHAR(120) NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    processing_status VARCHAR(24) NOT NULL DEFAULT 'received' CHECK (processing_status IN ('received', 'processing', 'processed', 'failed', 'ignored')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code VARCHAR(80),
    UNIQUE(provider, provider_event_id)
);
CREATE INDEX IF NOT EXISTS idx_billing_events_status ON billing_events(processing_status, received_at);

CREATE TABLE IF NOT EXISTS quota_reservations (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    request_id UUID NOT NULL,
    plan_key VARCHAR(20) NOT NULL CHECK (plan_key IN ('lite', 'pro', 'max')),
    reserved_tokens BIGINT NOT NULL CHECK (reserved_tokens >= 0),
    status VARCHAR(24) NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved', 'settled', 'released', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at TIMESTAMPTZ,
    UNIQUE(user_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_quota_reservations_active ON quota_reservations(user_id, status, created_at);

CREATE TABLE IF NOT EXISTS usage_events (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    request_id UUID NOT NULL,
    plan_key VARCHAR(20) NOT NULL CHECK (plan_key IN ('lite', 'pro', 'max')),
    model VARCHAR(255) NOT NULL,
    input_tokens BIGINT NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens BIGINT NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    total_tokens BIGINT NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    estimated_cost NUMERIC(16, 8),
    actual_cost NUMERIC(16, 8),
    status VARCHAR(24) NOT NULL CHECK (status IN ('settled', 'failed', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_usage_events_user_time ON usage_events(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_owner_session ON messages(user_id, session_id, created_at DESC);
