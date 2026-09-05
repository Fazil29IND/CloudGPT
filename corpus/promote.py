"""
Corpus Version Promotion CLI for CloudGPT.

Promotes an ingested corpus version to active status by updating the active namespace pointer
in the environment configuration without downtime.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import get_key, set_key

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("corpus.promote")


def promote_version(new_version: str, env_file: Path = BASE_DIR / ".env") -> bool:
    """Promote ACTIVE_CORPUS_VERSION and CACHE_CORPUS_VERSION in .env."""
    if not env_file.exists():
        env_file.touch()

    current_active = get_key(str(env_file), "ACTIVE_CORPUS_VERSION") or "v1"
    print(f"Current active corpus version: {current_active}")

    # Set active and cache versions
    set_key(str(env_file), "ACTIVE_CORPUS_VERSION", new_version)
    set_key(str(env_file), "CACHE_CORPUS_VERSION", new_version)

    # Record previous version for rollback
    set_key(str(env_file), "PREVIOUS_CORPUS_VERSION", current_active)

    print(f"Successfully promoted active corpus version to: {new_version}")
    print(f"Previous version ({current_active}) saved for rollback safety.\n")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote CloudGPT Active Corpus Version")
    parser.add_argument("--version", required=True, help="New corpus version tag, e.g., v2")
    parser.add_argument("--env", default=str(BASE_DIR / ".env"), help="Path to .env file")
    args = parser.parse_args()

    promote_version(args.version, Path(args.env))


if __name__ == "__main__":
    main()
