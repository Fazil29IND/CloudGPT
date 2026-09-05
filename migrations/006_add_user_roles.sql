-- Migration 006: Add user role and admin columns
-- Supports database-backed role management instead of env-var-only checks.

ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user';
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;

-- Index for role-based queries
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- Seed admin/developer roles from known developer emails.
-- Actual seeding is done at application startup via sync_admin_roles().
