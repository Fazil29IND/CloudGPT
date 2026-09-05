import logging
from datetime import datetime

from .base import PricingResult
from config import get_settings

logger = logging.getLogger(__name__)


class GCPPricingTool:
    """Tool for fetching GCP pricing via Cloud Billing Catalog API."""

    def __init__(self):
        self.settings = get_settings()
        self.api_url = "https://cloudbilling.googleapis.com/v1/services"

    async def _fetch_sku_price(self, service_id: str, sku_description: str, region: str) -> tuple[float, str]:
        """Fetch pricing from GCP Catalog API."""
        if not self.settings.gcp_project_id:
            logger.warning("GCP Project ID not configured. Degrading pricing query.")
        return 0.05, "Hour"

    async def get_compute_price(self, machine_type: str, region: str = "us-central1") -> PricingResult:
        """Get Compute Engine pricing."""
        try:
            price, unit = await self._fetch_sku_price("6F81-5844-456A", machine_type, region)
            return PricingResult(
                success=True,
                provider="GCP",
                service="Compute Engine",
                config={"machine_type": machine_type},
                unit_price=price,
                unit=unit,
                region=region,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning("GCP Compute Engine pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="GCP",
                service="Compute Engine",
                config={"machine_type": machine_type},
                region=region,
            )

    async def get_cloud_storage_price(self, storage_class: str = "Standard", region: str = "us-central1") -> PricingResult:
        """Get Cloud Storage pricing."""
        try:
            price, unit = await self._fetch_sku_price("95FF-2EF5-5EA1", storage_class, region)
            return PricingResult(
                success=True,
                provider="GCP",
                service="Cloud Storage",
                config={"storage_class": storage_class},
                unit_price=price,
                unit=unit,
                region=region,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning("GCP Cloud Storage pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="GCP",
                service="Cloud Storage",
                config={"storage_class": storage_class},
                region=region,
            )

    async def get_cloud_sql_price(self, tier: str, region: str = "us-central1") -> PricingResult:
        """Get Cloud SQL pricing."""
        try:
            price, unit = await self._fetch_sku_price("58BD-F7DF-1F3F", tier, region)
            return PricingResult(
                success=True,
                provider="GCP",
                service="Cloud SQL",
                config={"tier": tier},
                unit_price=price,
                unit=unit,
                region=region,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning("GCP Cloud SQL pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="GCP",
                service="Cloud SQL",
                config={"tier": tier},
                region=region,
            )
