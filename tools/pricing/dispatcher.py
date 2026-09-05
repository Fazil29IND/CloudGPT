"""Multi-cloud pricing dispatcher for AWS, Azure, and GCP."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from tools.pricing.aws_pricing import AWSPricingTool
from tools.pricing.azure_pricing import AzurePricingTool
from tools.pricing.gcp_pricing import GCPPricingTool

logger = logging.getLogger(__name__)

AWS_EC2_RE = re.compile(r"\b([a-z][0-9][a-z]?\.[a-z0-9]+)\b", re.IGNORECASE)
AZURE_VM_RE = re.compile(r"\b(Standard_[A-Za-z0-9_]+)\b", re.IGNORECASE)
GCP_VM_RE = re.compile(r"\b([cenf][0-9][a-z]?-(?:standard|highmem|highcpu)-[0-9]+|e2-medium|e2-micro|e2-small)\b", re.IGNORECASE)


async def fetch_cloud_pricing(
    query: str,
    providers: list[str] | None = None,
    aws_tool: AWSPricingTool | None = None,
    azure_tool: AzurePricingTool | None = None,
    gcp_tool: GCPPricingTool | None = None,
    timeout: float = 3.0,
) -> list[dict[str, Any]]:
    """Fetch structured pricing records from AWS, Azure, and GCP based on query context."""
    q_lower = query.lower()
    norm_providers = [p.lower() for p in (providers or [])]

    check_all = not norm_providers or "all" in norm_providers or "multi-cloud" in norm_providers or "cross-cloud" in norm_providers
    want_aws = check_all or "aws" in norm_providers or any(k in q_lower for k in ("aws", "ec2", "s3", "rds", "lambda", "amazon"))
    want_azure = check_all or "azure" in norm_providers or any(k in q_lower for k in ("azure", "microsoft", "d2s", "blob"))
    want_gcp = check_all or "gcp" in norm_providers or any(k in q_lower for k in ("gcp", "google", "cloud storage", "compute engine", "bigquery", "cloud sql"))

    tasks = []

    # AWS Pricing queries
    if want_aws and aws_tool:
        ec2_match = AWS_EC2_RE.search(query)
        instance_type = ec2_match.group(1).lower() if ec2_match else ("t3.medium" if ("ec2" in q_lower or "vm" in q_lower or "instance" in q_lower or check_all) else None)
        if instance_type:
            tasks.append(aws_tool.get_ec2_price(instance_type=instance_type))

        if any(k in q_lower for k in ("s3", "bucket", "object storage")):
            tasks.append(aws_tool.get_s3_price())

        if any(k in q_lower for k in ("rds", "database", "postgres", "mysql")):
            tasks.append(aws_tool.get_rds_price(instance_type="db.t3.medium", engine="PostgreSQL"))

        if any(k in q_lower for k in ("lambda", "serverless", "function")):
            tasks.append(aws_tool.get_lambda_price())

    # Azure Pricing queries
    if want_azure and azure_tool:
        azure_match = AZURE_VM_RE.search(query)
        sku = azure_match.group(1) if azure_match else ("Standard_D2s_v5" if ("azure" in q_lower or "vm" in q_lower or check_all) else None)
        if sku:
            tasks.append(azure_tool.get_vm_price(sku=sku))

        if any(k in q_lower for k in ("blob", "storage")):
            tasks.append(azure_tool.get_blob_storage_price(tier="Hot"))

        if any(k in q_lower for k in ("sql", "database")):
            tasks.append(azure_tool.get_sql_price(tier="General Purpose"))

    # GCP Pricing queries
    if want_gcp and gcp_tool:
        gcp_match = GCP_VM_RE.search(query)
        machine_type = gcp_match.group(1).lower() if gcp_match else ("e2-standard-2" if ("gcp" in q_lower or "compute" in q_lower or "vm" in q_lower or check_all) else None)
        if machine_type:
            tasks.append(gcp_tool.get_compute_price(machine_type=machine_type))

        if any(k in q_lower for k in ("storage", "gcs", "bucket")):
            tasks.append(gcp_tool.get_cloud_storage_price(storage_class="Standard"))

        if any(k in q_lower for k in ("cloud sql", "database")):
            tasks.append(gcp_tool.get_cloud_sql_price(tier="db-custom-2-7680"))

    if not tasks:
        if azure_tool:
            tasks.append(azure_tool.get_vm_price(sku="Standard_D2s_v5"))
        if aws_tool:
            tasks.append(aws_tool.get_ec2_price(instance_type="t3.medium"))
        if gcp_tool:
            tasks.append(gcp_tool.get_compute_price(machine_type="e2-standard-2"))

    results: list[dict[str, Any]] = []
    try:
        raw_results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )
        for r in raw_results:
            if isinstance(r, Exception) or not r:
                continue
            if getattr(r, "success", False):
                hourly = getattr(r, "retail_price", 0.0) or getattr(r, "unit_price", 0.0)
                if hourly and hourly > 0:
                    results.append({
                        "provider": getattr(r, "provider", "cloud").lower(),
                        "service": getattr(r, "service", "Compute"),
                        "sku": getattr(r, "sku_name", None) or getattr(r, "config", {}).get("instance_type") or getattr(r, "config", {}).get("machine_type") or "Standard",
                        "hourly_cost": hourly,
                        "unit": getattr(r, "unit_of_measure", "1 Hour") or getattr(r, "unit", "1 Hour"),
                        "currency": getattr(r, "currency", "USD"),
                        "region": getattr(r, "region", "us-east-1"),
                    })
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning("pricing_dispatcher.timeout", query=query)
    except Exception as e:
        logger.warning("pricing_dispatcher.error", error=str(e))

    return results
