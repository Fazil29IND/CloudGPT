"""
Corpus Version Rollback CLI for CloudGPT.

Reverts ACTIVE_CORPUS_VERSION to the previous version in .env if an issue is detected.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import get_key, set_key

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("corpus.rollback")


def rollback_version(target_version: str | None = None, env_file: Path = BASE_DIR / ".env") -> bool:
    """Roll back ACTIVE_CORPUS_VERSION in .env."""
    if not env_file.exists():
        print(f"Error: .env file not found at {env_file}")
        return False

    current_active = get_key(str(env_file), "ACTIVE_CORPUS_VERSION") or "v1"
    previous = target_version or get_key(str(env_file), "PREVIOUS_CORPUS_VERSION")

    if not previous:
        print("No PREVIOUS_CORPUS_VERSION found in .env and no --version specified.")
        print("Defaulting rollback target to 'v1'.")
        previous = "v1"

    if previous == current_active:
        print(f"Active version is already {current_active}. Nothing to rollback.")
        return True

    print(f"Rolling back active corpus version: {current_active} -> {previous}")
    set_key(str(env_file), "ACTIVE_CORPUS_VERSION", previous)
    set_key(str(env_file), "CACHE_CORPUS_VERSION", previous)
    set_key(str(env_file), "PREVIOUS_CORPUS_VERSION", current_active)

    print(f"Rollback complete. Active corpus version is now: {previous}\n")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Roll back CloudGPT Active Corpus Version")
    parser.add_argument("--version", default=None, help="Explicit target version to rollback to")
    parser.add_argument("--env", default=str(BASE_DIR / ".env"), help="Path to .env file")
    args = parser.parse_args()

    rollback_version(args.version, Path(args.env))


if __name__ == "__main__":
    main()
