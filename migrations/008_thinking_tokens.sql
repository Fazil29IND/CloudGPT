-- CloudGPT schema migration 008: thinking token accounting.
--
-- The Thinking Engine (Low | Medium | High | Max) produces reasoning tokens
-- that are billed against user quota separately from visible answer tokens.
-- The usage ledger records them explicitly for analytics and quota audits.

ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS thinking_tokens BIGINT NOT NULL DEFAULT 0;
