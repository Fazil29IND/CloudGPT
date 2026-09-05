-- Migration 010: Message metadata (attachments, artifacts), message feedback, and artifacts table

ALTER TABLE messages ADD COLUMN IF NOT EXISTS attachments jsonb;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS artifacts jsonb;

CREATE TABLE IF NOT EXISTS message_feedback (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    rating SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_message_feedback_user ON message_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_message_feedback_msg ON message_feedback(message_id);

CREATE TABLE IF NOT EXISTS artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL,
    session_id TEXT,
    message_id BIGINT,
    filename TEXT NOT NULL,
    mime TEXT NOT NULL,
    size BIGINT NOT NULL,
    storage_key TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_artifacts_user ON artifacts(user_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);
