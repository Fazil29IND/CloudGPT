"""
Database schema initialization for CloudGPT.

Bootstrap legacy tables, then run versioned migrations:
    python init_db.py
"""

import psycopg2
from config import get_settings

SCHEMA_SQL = """
-- Users table
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255),
    name            VARCHAR(255),
    email_verified  BOOLEAN DEFAULT FALSE,
    tier            VARCHAR(50) DEFAULT 'Lite',
    tokens_used_day INTEGER DEFAULT 0,
    tokens_used_month INTEGER DEFAULT 0,
    last_token_reset_day TIMESTAMPTZ DEFAULT NOW(),
    last_token_reset_month TIMESTAMPTZ DEFAULT NOW(),
    tokens_used_5h  INTEGER DEFAULT 0,
    tokens_used_week INTEGER DEFAULT 0,
    last_token_reset_5h TIMESTAMPTZ DEFAULT NOW(),
    last_token_reset_week TIMESTAMPTZ DEFAULT NOW(),
    settings_json   JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- OAuth accounts table (Google, etc.)
CREATE TABLE IF NOT EXISTS oauth_accounts (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider            VARCHAR(50) NOT NULL,
    provider_user_id    VARCHAR(255) NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(provider, provider_user_id)
);

-- Index for fast user lookups on oauth_accounts
CREATE INDEX IF NOT EXISTS idx_oauth_accounts_user_id
    ON oauth_accounts(user_id);

-- Conversation messages table
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    session_id VARCHAR(255),
    role VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    attachments JSONB,
    artifacts JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);

-- Email verification tokens table
CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL,
    code VARCHAR(6) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_verification_tokens_user ON email_verification_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_email_verification_tokens_hash ON email_verification_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_email_verification_tokens_code ON email_verification_tokens(user_id, code);
"""

MIGRATION_SQL = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS tier VARCHAR(50) DEFAULT 'Lite';
ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_used_day INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_used_month INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_token_reset_day TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_token_reset_month TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_used_5h INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_used_week INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_token_reset_5h TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_token_reset_week TIMESTAMPTZ DEFAULT NOW();
"""


def init_db():
    """Create all tables if they do not already exist."""
    conn = psycopg2.connect(get_settings().database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(MIGRATION_SQL)
        conn.commit()
        print("[OK] Legacy schema initialized successfully.")
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Error initializing database: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    from migrate import run_migrations
    run_migrations()
