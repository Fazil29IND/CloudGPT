import logging
from datetime import datetime

import httpx

from .base import PricingResult

logger = logging.getLogger(__name__)


class AzurePricingTool:
    """Tool for fetching Azure pricing using the Azure Retail Prices API."""

    def __init__(self):
        self.api_url = "https://prices.azure.com/api/retail/prices"

    async def _fetch_price(self, filter_query: str) -> tuple[float, str]:
        """Fetch price using OData filter query."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.api_url,
                params={"$filter": filter_query},
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()

            items = data.get("Items", [])
            if not items:
                raise ValueError("No pricing items found matching the filter.")

            item = items[0]
            return float(item.get("retailPrice", 0.0)), item.get("unitOfMeasure", "Unknown")

    async def get_vm_price(self, sku: str, region: str = 'eastus') -> PricingResult:
        """Get Azure Virtual Machine pricing."""
        filter_q = f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}' and armSkuName eq '{sku}' and priceType eq 'Consumption'"
        try:
            price, unit = await self._fetch_price(filter_q)
            return PricingResult(
                success=True,
                provider="Azure",
                service="Virtual Machines",
                config={"sku": sku},
                unit_price=price,
                unit=unit,
                region=region,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning("Azure VM pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="Azure",
                service="Virtual Machines",
                config={"sku": sku},
                region=region,
            )

    async def get_blob_storage_price(self, tier: str, region: str = 'eastus') -> PricingResult:
        """Get Azure Blob Storage pricing."""
        filter_q = f"serviceName eq 'Storage' and armRegionName eq '{region}' and skuName eq '{tier}'"
        try:
            price, unit = await self._fetch_price(filter_q)
            return PricingResult(
                success=True,
                provider="Azure",
                service="Blob Storage",
                config={"tier": tier},
                unit_price=price,
                unit=unit,
                region=region,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning("Azure Blob Storage pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="Azure",
                service="Blob Storage",
                config={"tier": tier},
                region=region,
            )

    async def get_sql_price(self, tier: str, region: str = 'eastus') -> PricingResult:
        """Get Azure SQL Database pricing."""
        filter_q = f"serviceName eq 'SQL Database' and armRegionName eq '{region}' and skuName eq '{tier}'"
        try:
            price, unit = await self._fetch_price(filter_q)
            return PricingResult(
                success=True,
                provider="Azure",
                service="SQL Database",
                config={"tier": tier},
                unit_price=price,
                unit=unit,
                region=region,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning("Azure SQL pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="Azure",
                service="SQL Database",
                config={"tier": tier},
                region=region,
            )
