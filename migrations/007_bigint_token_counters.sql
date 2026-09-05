-- CloudGPT schema migration 007: token counter overflow safety + session_titles dedupe.
--
-- 1. init_db.py defines all four rolling-window token counters as INTEGER.
--    Normalize them to BIGINT for overflow safety (the plan's section 22 fix,
--    extended to the 5h/week windows which init_db also creates as INTEGER).
-- 2. The session_titles table was previously created via runtime DDL inside
--    db.seed_required_users(). It now lives in versioned schema management.

ALTER TABLE users
    ALTER COLUMN tokens_used_day TYPE BIGINT,
    ALTER COLUMN tokens_used_month TYPE BIGINT,
    ALTER COLUMN tokens_used_5h TYPE BIGINT,
    ALTER COLUMN tokens_used_week TYPE BIGINT;

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
