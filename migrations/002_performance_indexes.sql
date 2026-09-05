-- Migration 002: High-concurrency performance indexes and webhook idempotency ledger

-- Fast session message retrieval
CREATE INDEX IF NOT EXISTS idx_messages_user_session_created 
    ON messages(user_id, session_id, created_at DESC);

-- Fast usage aggregation
CREATE INDEX IF NOT EXISTS idx_usage_events_user_created 
    ON usage_events(user_id, created_at DESC);

-- Fast quota reservation lookups & orphan reap
CREATE INDEX IF NOT EXISTS idx_quota_reservations_status_created 
    ON quota_reservations(user_id, status, created_at);

-- Webhook idempotency ledger preventing duplicate fulfillment on replay
CREATE TABLE IF NOT EXISTS processed_webhook_events (
    event_id VARCHAR(255) PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    payload_hash VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_processed_webhook_events_date 
    ON processed_webhook_events(processed_at);
