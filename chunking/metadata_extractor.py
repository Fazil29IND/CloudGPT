"""
Metadata Extractor for CloudGPT.

Automatically extracts rich metadata from crawled cloud documentation,
including provider detection, service mapping, document type classification,
deprecation detection, and region extraction.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Provider URL Patterns ────────────────────────────────────────────────────

_PROVIDER_PATTERNS = {
    "aws": [
        r"docs\.aws\.amazon\.com",
        r"aws\.amazon\.com",
        r"calculator\.aws",
        r"repost\.aws",
        r"skillbuilder\.aws",
        r"github\.com/awsdocs",
        r"github\.com/boto",
    ],
    "gcp": [
        r"cloud\.google\.com",
        r"googleapis\.com",
        r"github\.com/GoogleCloudPlatform",
        r"cloudskillsboost\.google",
    ],
    "azure": [
        r"learn\.microsoft\.com.*azure",
        r"azure\.microsoft\.com",
        r"github\.com/MicrosoftDocs/azure",
        r"github\.com/Azure",
        r"portal\.azure\.com",
    ],
}

# ── Document Type Patterns ───────────────────────────────────────────────────

_DOC_TYPE_PATTERNS = {
    "api_reference": [
        r"/api/", r"/rest/", r"api-reference", r"api-doc", r"/openapi",
        r"/swagger", r"/rest-api-specs",
    ],
    "user_guide": [
        r"/userguide/", r"/user-guide/", r"UserGuide", r"/latest/userguide",
    ],
    "developer_guide": [
        r"/developerguide/", r"/developer-guide/", r"DeveloperGuide",
    ],
    "tutorial": [
        r"/tutorials?/", r"/getting-started", r"/quickstart", r"/how-to",
        r"/walkthrough",
    ],
    "faq": [
        r"/faq", r"/faqs", r"/frequently-asked",
    ],
    "release_notes": [
        r"/release-notes", r"/whats-new", r"/new/", r"/updates",
        r"/changelog",
    ],
    "pricing": [
        r"/pricing", r"/calculator", r"/cost",
    ],
    "architecture": [
        r"/architecture", r"/well-architected", r"/best-practices",
        r"/patterns",
    ],
    "security": [
        r"/security", r"/compliance", r"/iam", r"/identity",
    ],
    "cli_reference": [
        r"/cli/", r"/command-line", r"/aws-cli", r"/gcloud",
    ],
    "sdk_reference": [
        r"/sdk/", r"/boto3", r"/google-cloud-python", r"/azure-sdk",
    ],
    "troubleshooting": [
        r"/troubleshoot", r"/errors", r"/debugging",
    ],
    "migration": [
        r"/migrat", r"/transfer",
    ],
}

# ── Deprecation Patterns ─────────────────────────────────────────────────────

_DEPRECATION_PATTERNS = [
    r"(?i)this\s+service\s+(?:is|has\s+been)\s+deprecated",
    r"(?i)(?:is|has\s+been)\s+retired",
    r"(?i)end[\s-]of[\s-](?:life|support)",
    r"(?i)no\s+longer\s+(?:available|supported|maintained)",
    r"(?i)sunset(?:ting|ted)?",
    r"(?i)we\s+recommend\s+(?:using|migrating\s+to)",
    r"(?i)replaced\s+by",
    r"(?i)will\s+be\s+(?:discontinued|removed|retired)",
]

# ── Region Patterns ──────────────────────────────────────────────────────────

_REGION_PATTERNS = {
    "aws": re.compile(
        r"\b(us-east-[12]|us-west-[12]|eu-west-[123]|eu-central-[12]|"
        r"eu-south-[12]|eu-north-1|ap-southeast-[1234]|ap-northeast-[123]|"
        r"ap-south-[12]|ap-east-1|sa-east-1|ca-central-1|me-south-1|"
        r"me-central-1|af-south-1|il-central-1)\b"
    ),
    "gcp": re.compile(
        r"\b(us-central1|us-east[1-4]|us-west[1-4]|us-south1|"
        r"europe-west[1-9]|europe-central2|europe-north1|europe-southwest1|"
        r"asia-east[12]|asia-northeast[123]|asia-south[12]|asia-southeast[12]|"
        r"australia-southeast[12]|southamerica-east[12]|"
        r"northamerica-northeast[12]|me-central[12]|me-west1|"
        r"africa-south1)\b"
    ),
    "azure": re.compile(
        r"\b(eastus|eastus2|westus|westus2|westus3|centralus|northcentralus|"
        r"southcentralus|westcentralus|canadacentral|canadaeast|"
        r"northeurope|westeurope|uksouth|ukwest|francecentral|francesouth|"
        r"germanywestcentral|germanynorth|switzerlandnorth|switzerlandwest|"
        r"norwayeast|norwaywest|swedencentral|polandcentral|italynorth|"
        r"eastasia|southeastasia|japaneast|japanwest|koreacentral|koreasouth|"
        r"centralindia|southindia|westindia|australiaeast|australiasoutheast|"
        r"australiacentral|australiacentral2|brazilsouth|brazilsoutheast|"
        r"southafricanorth|southafricawest|uaenorth|uaecentral|"
        r"israelcentral|qatarcentral)\b"
    ),
}


class ExtractedMetadata(BaseModel):
    """Metadata extracted from a document."""

    provider: str = ""
    category: str = ""
    service: str = ""
    document_type: str = "documentation"
    title: str = ""
    language: str = "en"
    regions_mentioned: list[str] = []
    is_deprecated: bool = False
    deprecation_notice: str = ""
    replacement_service: str = ""
    service_status: str = "active"


class MetadataExtractor:
    """Extracts structured metadata from cloud documentation content and URLs.

    Detects:
    - Cloud provider from URL patterns
    - Service name from URL paths and content
    - Document type (guide, API ref, tutorial, FAQ, etc.)
    - Deprecation/retirement notices
    - Regions mentioned in the content
    """

    def __init__(self, aliases_path: str | None = None) -> None:
        """Initialize with optional aliases mapping.

        Args:
            aliases_path: Path to aliases.json for service rename detection.
        """
        self._aliases: dict[str, dict] = {}
        if aliases_path:
            self._load_aliases(aliases_path)

    def _load_aliases(self, path: str) -> None:
        """Load service aliases from JSON file."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for alias in data.get("aliases", []):
                old_name = alias.get("old_name", "").lower()
                if old_name:
                    self._aliases[old_name] = alias
            logger.info("Loaded %d service aliases", len(self._aliases))
        except Exception as e:
            logger.warning("Failed to load aliases from %s: %s", path, e)

    def extract(self, content: str, url: str, title: str = "") -> ExtractedMetadata:
        """Extract metadata from document content and URL.

        Args:
            content: Cleaned document text.
            url: Source URL of the document.
            title: Document title (from HTML or heading).

        Returns:
            ExtractedMetadata with all detected fields.
        """
        provider = self._detect_provider(url)
        doc_type = self._detect_document_type(url)
        regions = self._extract_regions(content, provider)
        is_deprecated, deprecation_notice, replacement = self._detect_deprecation(content)

        service_status = "active"
        if is_deprecated:
            service_status = "deprecated"
        elif "preview" in content.lower()[:500] or "beta" in content.lower()[:500]:
            service_status = "preview"

        return ExtractedMetadata(
            provider=provider,
            document_type=doc_type,
            title=title,
            regions_mentioned=regions,
            is_deprecated=is_deprecated,
            deprecation_notice=deprecation_notice,
            replacement_service=replacement,
            service_status=service_status,
        )

    def _detect_provider(self, url: str) -> str:
        """Detect cloud provider from URL patterns."""
        url_lower = url.lower()
        for provider, patterns in _PROVIDER_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, url_lower):
                    return provider
        return ""

    def _detect_document_type(self, url: str) -> str:
        """Detect document type from URL patterns."""
        url_lower = url.lower()
        for doc_type, patterns in _DOC_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, url_lower):
                    return doc_type
        return "documentation"

    def _extract_regions(self, content: str, provider: str) -> list[str]:
        """Extract cloud region identifiers mentioned in the content."""
        regions: set[str] = set()

        if provider and provider in _REGION_PATTERNS:
            # Search only for the detected provider's regions
            for match in _REGION_PATTERNS[provider].finditer(content):
                regions.add(match.group(1))
        else:
            # Search all providers
            for pattern in _REGION_PATTERNS.values():
                for match in pattern.finditer(content):
                    regions.add(match.group(1))

        return sorted(regions)

    def _detect_deprecation(self, content: str) -> tuple[bool, str, str]:
        """Detect deprecation or retirement notices in content.

        Returns:
            (is_deprecated, deprecation_notice, replacement_service)
        """
        # Check first ~2000 chars for deprecation notices (usually at the top)
        search_text = content[:2000]

        for pattern in _DEPRECATION_PATTERNS:
            match = re.search(pattern, search_text)
            if match:
                # Extract surrounding context
                start = max(0, match.start() - 50)
                end = min(len(search_text), match.end() + 100)
                notice = search_text[start:end].strip()

                # Try to find replacement service
                replacement = ""
                replace_match = re.search(
                    r"(?:recommend|migrate\s+to|replaced\s+by|use)\s+([A-Z][A-Za-z0-9\s]+?)(?:\.|,|\s+instead|\s+for)",
                    search_text,
                )
                if replace_match:
                    replacement = replace_match.group(1).strip()

                return True, notice, replacement

        return False, "", ""

    def resolve_alias(self, service_name: str) -> tuple[str, str]:
        """Resolve an old service name to its current name.

        Args:
            service_name: Service name to check.

        Returns:
            (current_name, notes) — current_name equals input if no alias found.
        """
        alias = self._aliases.get(service_name.lower())
        if alias:
            return alias["current_name"], alias.get("notes", "")
        return service_name, ""
