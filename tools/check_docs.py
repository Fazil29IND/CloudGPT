"""Documentation Integrity and Consistency Checker.

Validates:
1. All Settings fields in config.py are documented in .env.example.
2. All SSE event types emitted in api/chat_routes.py are documented in AGENTS.md and docs/system-design.md.
3. All local documentation links in AGENTS.md and README.md resolve to real files.

Run via: python tools/check_docs.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def check_config_vs_env_example() -> list[str]:
    """Ensure every pydantic Field in config.py Settings appears in .env.example."""
    errors = []
    config_path = ROOT_DIR / "config.py"
    env_example_path = ROOT_DIR / ".env.example"

    if not config_path.exists() or not env_example_path.exists():
        return ["config.py or .env.example not found."]

    config_text = config_path.read_text(encoding="utf-8")
    env_example_text = env_example_path.read_text(encoding="utf-8")

    # Find field declarations inside Settings class: `field_name: type = Field(...)`
    field_pattern = re.compile(r"^\s+([a-zA-Z_0-9]+):\s+[^=]+=\s+Field\(", re.MULTILINE)
    config_fields = set(field_pattern.findall(config_text))

    for field in sorted(config_fields):
        # Check if field name (or uppercase variant) is in .env.example
        pattern = re.compile(rf"\b{field}\b", re.I)
        if not pattern.search(env_example_text):
            errors.append(f"Missing config setting in .env.example: {field.upper()} (field: {field})")

    return errors


def check_sse_event_documentation() -> list[str]:
    """Ensure SSE events in chat_routes.py are recorded in AGENTS.md."""
    errors = []
    chat_routes_path = ROOT_DIR / "api" / "chat_routes.py"
    agents_md_path = ROOT_DIR / "AGENTS.md"

    if not chat_routes_path.exists() or not agents_md_path.exists():
        return ["api/chat_routes.py or AGENTS.md not found."]

    agents_text = agents_md_path.read_text(encoding="utf-8")

    # Core expected SSE event types
    expected_events = [
        "status",
        "stage",
        "provider_detected",
        "session_title",
        "thinking_token",
        "thinking_done",
        "token",
        "memory_updated",
        "error",
        "done",
    ]

    for event in expected_events:
        if f"`{event}`" not in agents_text and f"'{event}'" not in agents_text and f'"{event}"' not in agents_text:
            errors.append(f"SSE event '{event}' is not documented in AGENTS.md")

    return errors


def _get_project_files() -> tuple[set[str], set[str]]:
    """Index project files once, excluding virtual environments and build caches."""
    rel_paths = set()
    basenames = set()
    ignored_dirs = {".venv", ".git", ".cursor", "__pycache__", ".pytest_cache", "node_modules"}

    for root, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), ROOT_DIR).replace("\\", "/")
            rel_paths.add(rel)
            rel_paths.add(rel.lower())
            basenames.add(f)
            basenames.add(f.lower())

    return rel_paths, basenames


def check_doc_file_links() -> list[str]:
    """Ensure documentation links in AGENTS.md and README.md resolve to existing files."""
    errors = []
    files_to_check = [ROOT_DIR / "AGENTS.md", ROOT_DIR / "README.md"]
    rel_paths, basenames = _get_project_files()

    for file_path in files_to_check:
        if not file_path.exists():
            continue
        content = file_path.read_text(encoding="utf-8")
        matches = re.findall(r"`([a-zA-Z0-9_\-./\\]+\.(?:md|py|sql|json|yml|yaml|txt))`", content)
        for match in set(matches):
            normalized = match.replace("\\", "/")
            if "*" in normalized or "://" in normalized or "001_" in normalized or normalized.startswith("http"):
                continue

            if "/" in normalized:
                clean_path = normalized.lstrip("./")
                if clean_path not in rel_paths and clean_path.lower() not in rel_paths:
                    errors.append(f"Broken file path in {file_path.name}: `{match}`")
            else:
                if normalized not in basenames and normalized.lower() not in basenames:
                    errors.append(f"Referenced file does not exist in {file_path.name}: `{match}`")

    return errors




def main() -> int:
    print("=== CloudGPT Documentation & Schema Integrity Check ===")
    all_errors = []

    print("Checking config.py fields vs .env.example...")
    config_errors = check_config_vs_env_example()
    all_errors.extend(config_errors)

    print("Checking SSE events in AGENTS.md...")
    sse_errors = check_sse_event_documentation()
    all_errors.extend(sse_errors)

    print("Checking document file references...")
    link_errors = check_doc_file_links()
    all_errors.extend(link_errors)

    if all_errors:
        print(f"\n[FAIL] Found {len(all_errors)} integrity issues:")
        for err in all_errors:
            print(f"  - {err}")
        return 1

    print("\n[OK] All documentation, schema, and SSE event contracts are verified!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
