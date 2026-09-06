"""
Enterprise Tool Output Normalizer (llm/tool_normalizer.py).

Normalizes raw outputs from pricing engines, live cloud SDK APIs, and financial
calculators into high-signal, compact structures prior to LLM context injection.
Eliminates redundant HTTP envelopes, SDK metadata, and noisy fields to maximize
information density and token efficiency.
"""

from __future__ import annotations

import json
from typing import Any


class ToolOutputNormalizer:
    """Normalizes and canonicalizes tool outputs before prompt context injection."""

    # SDK noise fields to strip from live cloud API responses
    NOISY_SDK_KEYS = {
        "ResponseMetadata",
        "HTTPStatusCode",
        "HTTPHeaders",
        "RetryAttempts",
        "RequestId",
        "request_id",
        "x-amzn-requestid",
        "x-ms-request-id",
        "server",
        "date",
    }

    @classmethod
    def normalize_pricing(cls, pricing_data: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """
        Transforms heterogeneous pricing data into canonical pricing records:
        - provider: str
        - sku: str
        - hourly_cost: float
        - monthly_cost: float
        - currency: str
        - formatted: str
        """
        if not pricing_data:
            return []

        normalized: list[dict[str, Any]] = []
        for item in pricing_data:
            if not isinstance(item, dict):
                continue

            provider = str(item.get("provider", "cloud")).strip().lower()
            sku = str(item.get("sku") or item.get("service") or item.get("instance_type") or "resource").strip()
            hourly = item.get("hourly_cost") or item.get("hourly") or item.get("cost_per_hour")
            monthly = item.get("monthly_cost") or item.get("monthly") or item.get("cost_per_month")
            currency = str(item.get("currency", "USD")).strip().upper()

            try:
                hourly_float = float(hourly) if hourly is not None else 0.0
            except (ValueError, TypeError):
                hourly_float = 0.0

            try:
                monthly_float = float(monthly) if monthly is not None else 0.0
            except (ValueError, TypeError):
                monthly_float = 0.0

            if monthly_float == 0.0 and hourly_float > 0:
                monthly_float = round(hourly_float * 730, 2)

            curr_sym = "$" if currency == "USD" else f"{currency} "
            formatted_str = f"{provider.upper()} {sku}: {curr_sym}{hourly_float:.4f}/hr | {curr_sym}{monthly_float:.2f}/mo ({currency})"

            rec = {
                "provider": provider,
                "sku": sku,
                "hourly_cost": hourly_float,
                "monthly_cost": monthly_float,
                "currency": currency,
                "formatted": formatted_str,
            }
            # Preserve additional high-signal fields if present (e.g. region, tier)
            for extra_key in ("region", "tier", "unit", "description"):
                if extra_key in item:
                    rec[extra_key] = item[extra_key]

            normalized.append(rec)

        return normalized

    @classmethod
    def normalize_calculator(cls, calc_data: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
        """Canonicalizes financial calculator expression and evaluation result."""
        if not calc_data:
            return {}

        if isinstance(calc_data, list):
            item = calc_data[0] if calc_data and isinstance(calc_data[0], dict) else {}
        elif isinstance(calc_data, dict):
            item = calc_data
        else:
            return {}

        expression = str(item.get("expression") or item.get("formula") or item.get("query") or "").strip()
        result = str(item.get("result") or item.get("value") or item.get("total") or "").strip()
        unit = str(item.get("unit") or item.get("currency") or "").strip()

        return {
            "expression": expression,
            "result": result,
            "unit": unit,
            "formatted": f"{expression} = {result}" if expression and result else (result or expression),
        }

    @classmethod
    def normalize_cloud_api(cls, api_data: list[dict[str, Any]] | dict[str, Any] | None) -> list[dict[str, Any]]:
        """
        Strips noisy headers, HTTP status envelopes, and internal SDK metadata
        from live cloud API tool executions (AWS Boto3, GCP client, Azure SDK).
        """
        if not api_data:
            return []

        raw_list = api_data if isinstance(api_data, list) else [api_data]
        normalized: list[dict[str, Any]] = []

        for item in raw_list:
            if not isinstance(item, dict):
                continue
            cleaned = cls._prune_sdk_noise(item)
            if cleaned:
                normalized.append(cleaned)

        return normalized

    @classmethod
    def _prune_sdk_noise(cls, data: Any) -> Any:
        """Recursively removes SDK headers and metadata noise."""
        if isinstance(data, dict):
            pruned: dict[str, Any] = {}
            for k, v in data.items():
                if k in cls.NOISY_SDK_KEYS:
                    continue
                pruned[k] = cls._prune_sdk_noise(v)
            return pruned
        elif isinstance(data, list):
            return [cls._prune_sdk_noise(x) for x in data]
        return data

    @classmethod
    def format_verified_cost_block(cls, normalized_pricing: list[dict[str, Any]]) -> str:
        """Renders Lite-tier style <verified_cost_data> block."""
        if not normalized_pricing:
            return ""
        lines = ["\n<verified_cost_data>"]
        for p in normalized_pricing:
            lines.append(f"- {p['formatted']}")
        lines.append("</verified_cost_data>")
        return "\n".join(lines)

    @classmethod
    def format_tool_executions_block(
        cls,
        pricing_data: list[dict[str, Any]] | None = None,
        calc_results: dict[str, Any] | list[dict[str, Any]] | None = None,
        api_data: list[dict[str, Any]] | dict[str, Any] | None = None,
    ) -> str:
        """
        Renders Core/Agentic-tier style <cloud_tool_executions> block with
        <tool_result name="..."> tags.
        """
        norm_pricing = cls.normalize_pricing(pricing_data)
        norm_calc = cls.normalize_calculator(calc_results)
        norm_api = cls.normalize_cloud_api(api_data)

        if not norm_pricing and not norm_calc and not norm_api:
            return ""

        parts = ["\n<cloud_tool_executions>"]

        if norm_pricing:
            parts.append('  <tool_result name="pricing">')
            parts.append(f"    {json.dumps(norm_pricing, separators=(',', ':'))}")
            parts.append("  </tool_result>")

        if norm_calc:
            parts.append('  <tool_result name="calculator">')
            parts.append(f"    {json.dumps(norm_calc, separators=(',', ':'))}")
            parts.append("  </tool_result>")

        if norm_api:
            parts.append('  <tool_result name="cloud_api">')
            parts.append(f"    {json.dumps(norm_api, separators=(',', ':'))}")
            parts.append("  </tool_result>")

        parts.append("</cloud_tool_executions>")
        return "\n".join(parts)

    @classmethod
    def compact_json(cls, data: Any) -> str:
        """Converts data to minimal JSON without extraneous whitespace."""
        return json.dumps(data, separators=(",", ":"))

    normalize_calculation = normalize_calculator
