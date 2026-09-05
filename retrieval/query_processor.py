"""
Query Processor for CloudGPT Hybrid RAG (Lite).

Implements:
1. Query Normalization: Canonical cloud entity/acronym mapping, noise stripping,
   and preservation of exact CLI flags, error codes, IP/CIDR ranges, and version strings.
2. Contextual Query Rewriting: Multi-turn conversational pronoun and anaphora resolution
   using prior conversation turns.
3. Original Query Preservation: Retains raw user phrasing alongside normalized and
   rewritten representations in a structured QueryContext.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Canonical Cloud Acronym and Terminology Dictionary
CLOUD_CANONICAL_SYNONYMS: dict[str, str] = {
    r"\bk8s\b": "kubernetes",
    r"\bs3\b": "aws s3 amazon simple storage service",
    r"\bs3 bucket\b": "aws s3 amazon simple storage service bucket",
    r"\bec2\b": "aws ec2 elastic compute cloud",
    r"\brds\b": "aws rds relational database service",
    r"\biam\b": "identity and access management iam",
    r"\bvpc\b": "virtual private cloud vpc",
    r"\bgcs\b": "google cloud storage gcs",
    r"\bgke\b": "google kubernetes engine gke",
    r"\baks\b": "azure kubernetes service aks",
    r"\basg\b": "auto scaling group asg",
    r"\baz\b": "availability zone az",
    r"\bvm\b": "virtual machine vm",
    r"\bvms\b": "virtual machines vms",
    r"\bdynamodb\b": "aws dynamodb nosql database",
    r"\bcosmos db\b": "azure cosmos db",
    r"\bcloud run\b": "google cloud run serverless container",
    r"\bcloud sql\b": "google cloud sql relational database",
    r"\bbigquery\b": "google cloud bigquery analytics data warehouse",
    r"\blambda\b": "aws lambda serverless function compute",
    r"\bfargate\b": "aws fargate serverless container compute",
    r"\bebs\b": "aws elastic block store ebs",
    r"\belb\b": "aws elastic load balancing elb",
    r"\balb\b": "application load balancer alb",
    r"\bnlb\b": "network load balancer nlb",
    r"\bsns\b": "aws simple notification service sns",
    r"\bsqs\b": "aws simple queue service sqs",
    r"\bcloudwatch\b": "aws cloudwatch monitoring logging",
    r"\bblob storage\b": "azure blob storage object storage",
    r"\bazure ad\b": "microsoft entra id azure active directory",
    r"\bentra id\b": "microsoft entra id azure active directory",
}

# Regex to detect exact technical queries (CLI flags, exception classes, error codes, HTTP codes, IPs)
EXACT_TECHNICAL_PATTERN = re.compile(
    r"(--[a-zA-Z0-9_-]+|\b[A-Z][a-zA-Z0-9]+Exception\b|\b[A-Z][a-zA-Z0-9]+Error\b|"
    r"\bError:\s*[A-Z0-9_-]+\b|\b\d{3}\s+(Forbidden|Unauthorized|NotFound|Bad Request|Internal Server Error)\b|"
    r"\b\d+\.\d+\.\d+\b|\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(/\d{1,2})?\b|"
    r"\b(aws|az|gcloud|kubectl|terraform)\s+[a-z0-9_-]+)",
    re.IGNORECASE,
)

# Pronouns & anaphoric markers requiring multi-turn resolution
ANAPHORA_PATTERN = re.compile(
    r"\b(it|its|they|them|their|this|that|these|those|the service|the former|the latter|"
    r"the first one|the second one|that one|previous one)\b",
    re.IGNORECASE,
)

# Known major cloud providers
CLOUD_PROVIDERS = ("aws", "gcp", "azure")


@dataclass
class QueryContext:
    """Encapsulates the complete query lifecycle and preserved representations."""
    original_query: str
    normalized_query: str
    rewritten_query: str
    is_rewritten: bool = False
    is_exact_technical: bool = False
    extracted_entities: list[str] = field(default_factory=list)
    detected_providers: list[str] = field(default_factory=list)

    @property
    def effective_search_query(self) -> str:
        """Returns rewritten query if contextual rewriting was applied, else normalized query."""
        return self.rewritten_query if self.is_rewritten else self.normalized_query


def normalize_query(query: str, expand_acronyms: bool = True) -> str:
    """
    Normalize user query while strictly preserving technical tokens.

    - Retains case-sensitive CLI flags, exception names, error codes, version numbers, CIDRs.
    - Trims noise punctuation (e.g. leading/trailing '?', '!', quotes, multiple dots).
    - Maps common cloud acronyms and slang into canonical terminology.
    - Normalizes excessive whitespace.
    """
    if not query or not query.strip():
        return ""

    q = query.strip()

    # Detect if query has exact technical entities
    has_exact_technical = bool(EXACT_TECHNICAL_PATTERN.search(q))

    # Strip conversational noise punctuation from query edges while keeping internal hyphens/dots
    # Keep leading dashes if it's a CLI flag (e.g. --profile)
    if not q.startswith("-"):
        q = re.sub(r"^[\s\.,;!?:\"\'`\(\)\[\]{}]+", "", q)
    q = re.sub(r"[\s\.,;!?:\"\'`\(\)\[\]{}]+$", "", q)

    # Collapse multiple whitespaces
    q = re.sub(r"\s+", " ", q)

    if expand_acronyms and not has_exact_technical:
        for pattern, replacement in CLOUD_CANONICAL_SYNONYMS.items():
            # Apply regex substitution on word boundaries
            q = re.sub(pattern, replacement, q, flags=re.IGNORECASE)

    return q.strip()


def _extract_recent_cloud_entities(chat_history: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Extract cloud service names and providers from recent conversation history."""
    extracted_services: list[str] = []
    extracted_providers: list[str] = []

    # Look back through the last 4 messages (2 turns)
    recent_messages = chat_history[-4:] if len(chat_history) > 4 else chat_history

    for msg in reversed(recent_messages):
        content = msg.get("content", "") or msg.get("text", "")
        if not content or not isinstance(content, str):
            continue

        c_low = content.lower()

        # Check providers
        for p in CLOUD_PROVIDERS:
            if p in c_low and p not in extracted_providers:
                extracted_providers.append(p)

        # Check common cloud services
        candidate_services = [
            "s3", "ec2", "lambda", "rds", "dynamodb", "ecs", "eks", "fargate",
            "cloud storage", "gcs", "compute engine", "cloud run", "gke", "cloud sql", "bigquery",
            "azure vm", "blob storage", "aks", "cosmos db", "azure functions", "vpc", "iam",
        ]
        for s in candidate_services:
            if re.search(r"\b" + re.escape(s) + r"\b", c_low):
                if s not in extracted_services:
                    extracted_services.append(s)

    return extracted_services, extracted_providers


def rewrite_contextual_query(
    query: str,
    chat_history: list[dict[str, Any]] | None = None,
    expand_acronyms: bool = True,
) -> QueryContext:
    """
    Perform contextual query rewriting with original query preservation.

    - Resolves conversational anaphora and pronouns using chat history.
    - If the query is already self-contained or no history exists, leaves the core intent intact.
    - Preserves original_query, normalized_query, and rewritten_query in QueryContext.
    """
    raw_query = query.strip() if query else ""
    norm_query = normalize_query(raw_query, expand_acronyms=expand_acronyms)
    is_exact = bool(EXACT_TECHNICAL_PATTERN.search(raw_query))

    detected_providers = [p for p in CLOUD_PROVIDERS if p in raw_query.lower()]

    if not chat_history or not raw_query:
        return QueryContext(
            original_query=raw_query,
            normalized_query=norm_query,
            rewritten_query=norm_query,
            is_rewritten=False,
            is_exact_technical=is_exact,
            extracted_entities=[],
            detected_providers=detected_providers,
        )

    # Check for anaphoric pronouns or conversational ellipsis
    has_anaphora = bool(ANAPHORA_PATTERN.search(raw_query))
    is_short_fragment = len(raw_query.split()) <= 4 and any(
        kw in raw_query.lower() for kw in ["pricing", "cost", "limits", "quota", "how to configure", "how does it work", "why", "difference"]
    )

    if not has_anaphora and not is_short_fragment:
        return QueryContext(
            original_query=raw_query,
            normalized_query=norm_query,
            rewritten_query=norm_query,
            is_rewritten=False,
            is_exact_technical=is_exact,
            extracted_entities=[],
            detected_providers=detected_providers,
        )

    # Contextual resolution using prior history
    recent_services, recent_providers = _extract_recent_cloud_entities(chat_history)

    if not recent_services and not recent_providers:
        return QueryContext(
            original_query=raw_query,
            normalized_query=norm_query,
            rewritten_query=norm_query,
            is_rewritten=False,
            is_exact_technical=is_exact,
            extracted_entities=[],
            detected_providers=detected_providers,
        )

    # Construct contextually rewritten query
    primary_entity = recent_services[0] if recent_services else ""
    primary_provider = recent_providers[0] if recent_providers else ""

    context_prefix = ""
    if primary_provider and primary_provider not in primary_entity.lower():
        context_prefix = f"{primary_provider.upper()} "
    if primary_entity:
        context_prefix += f"{primary_entity} "

    context_prefix = context_prefix.strip()

    rewritten = raw_query
    if has_anaphora:
        # Replace the first occurrence of ambiguous pronouns with the specific entity
        rewritten = ANAPHORA_PATTERN.sub(context_prefix, raw_query, count=1)
    elif is_short_fragment and context_prefix.lower() not in raw_query.lower():
        rewritten = f"{context_prefix} {raw_query}"

    rewritten_norm = normalize_query(rewritten, expand_acronyms=expand_acronyms)

    logger.debug(
        "query_processor.contextual_rewrite",
        original=raw_query,
        rewritten=rewritten_norm,
        entity=context_prefix,
    )

    return QueryContext(
        original_query=raw_query,
        normalized_query=norm_query,
        rewritten_query=rewritten_norm,
        is_rewritten=(rewritten_norm != norm_query),
        is_exact_technical=is_exact,
        extracted_entities=recent_services,
        detected_providers=list(set(detected_providers + recent_providers)),
    )
