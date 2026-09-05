import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from config import get_settings
from llm.system_prompts import QUERY_CLASSIFICATION_PROMPT

logger = logging.getLogger(__name__)


class QueryClassification(BaseModel):
    intent: str = Field(..., description="explain, compare, price, live_resource, calculate, recent_info, architecture, troubleshoot, error_fix, problem_solving, chitchat, status, billing, identity")
    routes: list[str] = Field(..., description="List of routes: RAG, WEB, INTERNET, PRICING, CLOUD_API, CALCULATOR")
    providers: list[str] = Field(..., description="List of cloud providers: aws, gcp, azure")
    services: list[str] = Field(..., description="List of cloud services detected in query")
    categories: list[str] = Field(..., description="List of service categories (compute, storage, etc.)")
    confidence: float = Field(..., description="Confidence score 0.0-1.0")
    reasoning: str = Field(..., description="Brief explanation of classification")
    needs_internet: bool = Field(default=False, description="Whether the query needs current web information")
    requires_provider_comparison: bool = Field(
        default=False,
        description="True when the query asks to compare or recommend across AWS/GCP/Azure.",
    )
    recommendation_type: str = Field(
        default="none",
        description="'service_selection' | 'architecture' | 'migration' | 'cost_comparison' | 'none'",
    )
    risk_level: str = Field(
        default="low",
        description="'low' | 'medium' | 'high' — escalates to Answer Evaluator when >= medium.",
    )


SERVICE_NAMES = {
    # AWS
    "ec2": ("aws", "compute"),
    "s3": ("aws", "storage"),
    "lambda": ("aws", "compute"),
    "rds": ("aws", "database"),
    "dynamodb": ("aws", "database"),
    "fargate": ("aws", "compute"),
    "ecs": ("aws", "compute"),
    "eks": ("aws", "compute"),
    "cloudformation": ("aws", "management"),
    "route53": ("aws", "networking"),
    "route 53": ("aws", "networking"),
    "cloudfront": ("aws", "networking"),
    "sqs": ("aws", "messaging"),
    "sns": ("aws", "messaging"),
    "redshift": ("aws", "analytics"),
    "aws iam": ("aws", "security"),
    "ebs": ("aws", "storage"),
    "elb": ("aws", "networking"),
    "cloudwatch": ("aws", "monitoring"),
    "athena": ("aws", "analytics"),
    "glue": ("aws", "analytics"),
    "sagemaker": ("aws", "ai"),
    "bedrock": ("aws", "ai"),
    "aurora": ("aws", "database"),
    "cognito": ("aws", "security"),
    "kinesis": ("aws", "analytics"),
    "aws": ("aws", "general"),
    "amazon web services": ("aws", "general"),

    # GCP
    "gce": ("gcp", "compute"),
    "compute engine": ("gcp", "compute"),
    "cloud storage": ("gcp", "storage"),
    "gcs": ("gcp", "storage"),
    "cloud run": ("gcp", "compute"),
    "cloud sql": ("gcp", "database"),
    "bigquery": ("gcp", "analytics"),
    "gke": ("gcp", "compute"),
    "google kubernetes engine": ("gcp", "compute"),
    "cloud functions": ("gcp", "compute"),
    "firestore": ("gcp", "database"),
    "cloud spanner": ("gcp", "database"),
    "pubsub": ("gcp", "messaging"),
    "pub/sub": ("gcp", "messaging"),
    "dataproc": ("gcp", "analytics"),
    "dataflow": ("gcp", "analytics"),
    "vertex ai": ("gcp", "ai"),
    "cloud build": ("gcp", "devops"),
    "cloud armor": ("gcp", "security"),
    "cloud iam": ("gcp", "security"),
    "gcp": ("gcp", "general"),
    "google cloud": ("gcp", "general"),

    # Azure
    "azure vm": ("azure", "compute"),
    "azure virtual machine": ("azure", "compute"),
    "azure virtual machines": ("azure", "compute"),
    "blob storage": ("azure", "storage"),
    "azure sql": ("azure", "database"),
    "azure functions": ("azure", "compute"),
    "aks": ("azure", "compute"),
    "azure kubernetes": ("azure", "compute"),
    "cosmos db": ("azure", "database"),
    "azure app service": ("azure", "compute"),
    "app service": ("azure", "compute"),
    "azure devops": ("azure", "devops"),
    "synapse": ("azure", "analytics"),
    "event hubs": ("azure", "messaging"),
    "bicep": ("azure", "management"),
    "arm template": ("azure", "management"),
    "azure openai": ("azure", "ai"),
    "entra id": ("azure", "security"),
    "azure ad": ("azure", "security"),
    "key vault": ("azure", "security"),
    "logic apps": ("azure", "integration"),
    "azure rbac": ("azure", "security"),
    "azure": ("azure", "general"),
    "microsoft azure": ("azure", "general"),
}


def canonical_provider(provider: str | None) -> str | None:
    """Normalize a provider identifier to its canonical metadata string ('aws', 'google-cloud', 'azure')."""
    if not provider:
        return None
    p = str(provider).strip().lower()
    if p in ("gcp", "google", "google cloud", "google-cloud", "google_cloud"):
        return "google-cloud"
    if p in ("aws", "amazon", "amazon web services"):
        return "aws"
    if p in ("azure", "microsoft", "microsoft azure", "ms azure"):
        return "azure"
    return p


def provider_aliases(provider: str | None) -> list[str]:
    """Return all known aliases for a provider identifier."""
    if not provider:
        return []
    p = str(provider).strip().lower()
    if p in ("gcp", "google", "google cloud", "google-cloud", "google_cloud"):
        return ["gcp", "google-cloud", "google cloud"]
    if p in ("aws", "amazon", "amazon web services"):
        return ["aws", "amazon"]
    if p in ("azure", "microsoft", "microsoft azure"):
        return ["azure", "microsoft"]
    return [p]


class QueryRouter:
    """The central intelligence of the system for routing queries.

    Uses Gemini 2.0 Flash for structured query classification and routing.
    """

    def __init__(self, llm_client: Any = None, router_llm_client: Any = None):
        """Initialize QueryRouter with optional LLM clients.

        Args:
            llm_client: Fallback LLM client (Gemini 2.0 Flash).
            router_llm_client: Dedicated client for routing (Gemini 2.0 Flash).
                If not provided, falls back to llm_client, then rule-based.
        """
        self.settings = get_settings()
        self.llm_client = llm_client
        self.router_llm_client = router_llm_client

    async def route_query(self, query: str) -> QueryClassification:
        """Route the query using sub-model LLM (primary) or rule-based fallback.

        The router may request INTERNET, while the server plan policy decides whether
        to execute a web search.
        """
        # Try router sub-model first, then fallback LLM, then rules
        routing_client = self.router_llm_client or self.llm_client

        if routing_client:
            from llm.provider import _classify_with_retry
            try:
                classification_json = await _classify_with_retry(
                    provider=routing_client,
                    query=query,
                    system_prompt=QUERY_CLASSIFICATION_PROMPT,
                    role="router",
                )
                data = json.loads(classification_json)

                routes = data.get("routes", ["RAG"])
                detected_provs = data.get("providers", [])
                req_comp = bool(data.get("requires_provider_comparison", False))
                intent = data.get("intent", "explain")

                # Multi-cloud parity: if query is an open cloud question without a single provider specified,
                # expand to all three major clouds
                if intent != "chitchat":
                    if not detected_provs:
                        detected_provs = ["aws", "gcp", "azure"]
                        req_comp = True
                    elif len(detected_provs) > 1:
                        req_comp = True

                return QueryClassification(
                    intent=intent,
                    routes=routes,
                    providers=detected_provs,
                    services=data.get("services", []),
                    categories=data.get("categories", []),
                    confidence=float(data.get("confidence", 0.9)),
                    reasoning=data.get("reasoning", "Sub-model classified"),
                    needs_internet=("INTERNET" in routes or "WEB" in routes or bool(data.get("needs_internet", False))),
                    requires_provider_comparison=req_comp,
                    recommendation_type=str(data.get("recommendation_type", "none")),
                    risk_level=str(data.get("risk_level", "low")),
                )
            except ValueError as e:
                logger.warning(f"LLM routing failed after retries, falling back to rule-based: {e}")
                return self._rule_based_route(query)
            except Exception as e:
                logger.warning(f"LLM routing failed, falling back to rule-based: {e}")

        return self._rule_based_route(query)

    def _rule_based_route(self, query: str) -> QueryClassification:
        """Fallback rule-based routing with freshness-gated web search."""
        query_lower = query.lower()

        # Small-talk safety net: greetings/social messages need no routes —
        # the pipeline generates directly without retrieval or tools.
        from router.smalltalk_gate import is_smalltalk_regex_query
        if is_smalltalk_regex_query(query, self.settings):
            return QueryClassification(
                intent="chitchat",
                routes=[],
                providers=[],
                services=[],
                categories=[],
                confidence=0.95,
                reasoning="Greeting/small talk detected by rule-based router",
                needs_internet=False,
            )

        routes = []
        intent = "explain"

        freshness_keywords = [
            "latest", "new", "2024", "2025", "2026", "current", "now",
            "today", "incident", "outage", "status", "recently", "this week",
            "just announced", "what changed"
        ]
        has_freshness = any(kw in query_lower for kw in freshness_keywords)

        # Intent & Routes matching
        if any(kw in query_lower for kw in ['outage', 'is down', 'down?', 'service down', 'health status', 'status check']):
            intent = "status"
            routes.append("WEB")
        elif any(kw in query_lower for kw in ['unexpected charge', 'billing dispute', 'refund', 'charged me', 'my bill', 'invoice']):
            intent = "billing"
            routes.append("RAG")
        elif any(kw in query_lower for kw in ['reset password', 'reset 2fa', 'reset mfa', 'bypass 2fa', 'password reset']):
            intent = "identity"
            routes.append("RAG")
        elif any(kw in query_lower for kw in ['calculate', 'multiply', '×', 'math']):
            intent = "calculate"
            routes.append("CALCULATOR")
        elif any(kw in query_lower for kw in ['how to fix', 'how to resolve', 'workaround', 'solution for']):
            intent = "problem_solving"
            routes.append("RAG")
        elif any(kw in query_lower for kw in ['error', 'fail', 'broken', 'not working', 'issue', 'bug', 'crash', 'exception', 'timeout']):
            intent = "troubleshooting"
            routes.append("RAG")
        elif any(kw in query_lower for kw in ['cost', 'price', 'pricing', 'how much', 'per hour', 'monthly cost', 'total cost']):
            intent = "price"
            routes.append("PRICING")
        elif any(kw in query_lower for kw in ['running', 'my account', 'my instances', 'currently deployed', 'list my']):
            intent = "live_resource"
            routes.append("CLOUD_API")
        elif has_freshness or any(kw in query_lower for kw in ['recently', 'this week', 'latest', 'new', 'just announced', 'what changed']):
            intent = "recent_info"
            routes.append("WEB")
        elif any(kw in query_lower for kw in ['vs', 'versus', 'compare', 'equivalent', 'alternative']):
            intent = "compare"
            routes.append("RAG")
        elif any(kw in query_lower for kw in ['architect', 'design', 'pattern', 'best practice', 'multi-region', 'ha', 'disaster recovery']):
            intent = "architecture"
            routes.append("RAG")
        else:
            routes.append("RAG")

        # Gated INTERNET route
        force_web = getattr(self.settings, "always_web_search", False)
        if force_web:
            routes.append("INTERNET")
        elif intent in {"recent_info", "troubleshooting", "problem_solving", "status"}:
            routes.append("INTERNET")
        elif has_freshness:
            routes.append("INTERNET")

        # Provider detection
        providers = []
        if any(p in query_lower for p in ['aws', 'amazon']):
            providers.append('aws')
        if any(p in query_lower for p in ['gcp', 'google cloud']):
            providers.append('gcp')
        if any(p in query_lower for p in ['azure', 'microsoft']):
            providers.append('azure')

        # Service detection
        services = []
        categories = set()
        for svc, (prov, cat) in SERVICE_NAMES.items():
            if svc in query_lower:
                services.append(svc)
                categories.add(cat)
                if prov not in providers:
                    providers.append(prov)

        # Recommendation comparison detection
        comparison_keywords = [
            "recommend", "which service", "best service", "should i use", "compare",
            "vs", "versus", "alternative", "equivalent", "choose between", "difference between",
        ]
        has_comparison_kw = any(kw in query_lower for kw in comparison_keywords) or (len(providers) > 1 and " or " in query_lower)

        # Multi-cloud parity: if no provider is singled out, treat as general cross-cloud query
        # unless it is a support, billing, identity, status, or chitchat inquiry.
        is_support_intent = intent in ("status", "incident", "billing", "identity", "chitchat")
        if is_support_intent:
            requires_provider_comparison = False
            if not providers:
                providers = ["aws", "gcp", "azure"]
        elif not providers:
            providers = ["aws", "gcp", "azure"]
            requires_provider_comparison = True
        elif len(providers) > 1:
            requires_provider_comparison = True
        else:
            requires_provider_comparison = has_comparison_kw

        recommendation_type = "none"
        if requires_provider_comparison:
            if any(kw in query_lower for kw in ["architect", "design", "pattern", "infrastructure"]):
                recommendation_type = "architecture"
            elif any(kw in query_lower for kw in ["migrat", "move", "lift and shift"]):
                recommendation_type = "migration"
            elif any(kw in query_lower for kw in ["cost", "price", "pricing", "budget", "cheap", "$", "/mo", "/month"]):
                recommendation_type = "cost_comparison"
            else:
                recommendation_type = "service_selection"

        # Risk level detection
        settings = get_settings()
        risk_keywords = getattr(settings, "risk_gate_keywords", [])
        risk_level = "low"
        if any(kw in query_lower for kw in risk_keywords) or any(kw in query_lower for kw in ["$", "/mo", "/month"]):
            risk_level = "medium"
        if any(kw in query_lower for kw in ["security breach", "data loss", "outage", "breach", "incident"]):
            risk_level = "high"

        # Recommendation bias: if comparison query, upgrade routes to multi-hop
        if requires_provider_comparison and "RAG" in routes and getattr(settings, "recommendation_comparison_bias", True):
            if not any(p in query_lower for p in ["troubleshoot", "error", "fix"]):
                if not providers:
                    providers = ["aws", "gcp", "azure"]

        needs_internet = ("INTERNET" in routes or "WEB" in routes)
        return QueryClassification(
            intent=intent,
            routes=list(set(routes)),
            providers=providers,
            services=services,
            categories=list(categories),
            confidence=0.7,
            reasoning="Rule-based fallback matching",
            needs_internet=needs_internet,
            requires_provider_comparison=requires_provider_comparison,
            recommendation_type=recommendation_type,
            risk_level=risk_level,
        )
