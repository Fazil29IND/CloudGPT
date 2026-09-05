-- Migration 012: Cross-session persistent user preference memory
-- Stores user-specific architectural preferences, cloud environments, and constraints.

CREATE TABLE IF NOT EXISTS user_memory (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_key VARCHAR(100) NOT NULL,
    memory_value TEXT NOT NULL,
    category VARCHAR(50) DEFAULT 'general',
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, memory_key)
);

CREATE INDEX IF NOT EXISTS idx_user_memory_user_id ON user_memory(user_id);
