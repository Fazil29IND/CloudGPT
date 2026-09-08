"""Task 11: Test 2024-2026 Cloud Services Corpus Expansion files and registration."""
from __future__ import annotations

from pathlib import Path
import pytest

from ingest_services import KNOWLEDGE_FILES

NEW_CORPUS_FILES = [
    "s3_express_one_zone.md",
    "bedrock_agentcore.md",
    "eks_auto_mode.md",
    "graviton4.md",
    "trainium2.md",
    "amazon_q_developer.md",
    "axion_processors.md",
    "vertex_ai_reasoning_engine.md",
    "hyperdisk_ml.md",
    "alloydb_omni.md",
    "container_apps_dynamic_sessions.md",
    "cobalt_100.md",
    "maia_100.md",
    "cilium_ebpf.md",
]

REQUIRED_SECTIONS = [
    "## Overview",
    "## Key Features",
    "## Pricing",
    "## Use Cases",
    "## Limitations",
    "## CLI Examples",
    "## Terraform / IaC",
    "## References",
]


@pytest.mark.parametrize("filename", NEW_CORPUS_FILES)
def test_corpus_file_exists_and_has_required_sections(filename: str):
    """Verify that every new 2024-2026 service file exists and contains all required sections."""
    path = Path("data/senior_engineer_knowledge") / filename
    assert path.exists(), f"Corpus file missing: {path}"

    content = path.read_text(encoding="utf-8")
    assert len(content) > 500, f"Corpus file {filename} is suspiciously short ({len(content)} bytes)"

    for section in REQUIRED_SECTIONS:
        assert section in content, f"Corpus file {filename} missing required section: {section}"

    assert "<placeholder>" not in content.lower(), f"Found placeholder in {filename}"
    assert "# todo" not in content.lower(), f"Found TODO in {filename}"


def test_all_new_files_registered_in_knowledge_files():
    """Verify that all 14 new corpus files are registered in ingest_services.py KNOWLEDGE_FILES."""
    registered_filenames = {kf["filename"] for kf in KNOWLEDGE_FILES}
    for filename in NEW_CORPUS_FILES:
        assert filename in registered_filenames, f"{filename} not registered in KNOWLEDGE_FILES"
