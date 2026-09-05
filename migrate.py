"""Minimal, versioned PostgreSQL migration runner.

Run `python migrate.py` during deployment before starting application workers.
It records file names in schema_migrations and is safe to repeat.
"""

from __future__ import annotations

from pathlib import Path

import psycopg2

from config import get_settings


MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def run_migrations() -> None:
    # Existing installations pre-date a migration framework. Bootstrap the
    # original tables first so this command also works from a clean checkout.
    from init_db import init_db
    init_db()
    settings = get_settings()
    conn = psycopg2.connect(settings.database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (filename VARCHAR(255) PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                cur.execute("SELECT 1 FROM schema_migrations WHERE filename = %s", (migration.name,))
                if cur.fetchone():
                    continue
                cur.execute(migration.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO schema_migrations(filename) VALUES (%s)", (migration.name,))
                print(f"Applied {migration.name}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migrations()
