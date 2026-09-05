-- Migration 013: User-configurable settings stored as JSONB
-- Lightweight column appended to users; no FK dependencies.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS settings_json JSONB NOT NULL DEFAULT '{}';

COMMENT ON COLUMN users.settings_json IS
    'User-configurable preferences: response_language, default_thinking, show_thinking, show_token_usage, show_pipeline_stages, always_web_search, compact_messages';
