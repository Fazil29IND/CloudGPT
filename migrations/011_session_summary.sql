-- Migration 011: Session summary for rolling history compaction
-- Stores compact technical summary of older conversation turns archived from the context window.

ALTER TABLE session_titles ADD COLUMN IF NOT EXISTS summary TEXT;

CREATE TABLE IF NOT EXISTS session_summaries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    session_id VARCHAR(255) NOT NULL,
    summary TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_session_summaries_lookup
    ON session_summaries(user_id, session_id);
