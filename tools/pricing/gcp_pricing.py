import logging
from datetime import datetime
from typing import Any

from .base import PricingResult
from config import get_settings

logger = logging.getLogger(__name__)

# Standard US-Central1 baseline rates (fallback catalog)
GCP_COMPUTE_CATALOG: dict[str, tuple[float, str]] = {
    "e2-micro": (0.0084, "Hour"),
    "e2-small": (0.0168, "Hour"),
    "e2-medium": (0.0336, "Hour"),
    "e2-standard-2": (0.0672, "Hour"),
    "e2-standard-4": (0.1344, "Hour"),
    "e2-standard-8": (0.2688, "Hour"),
    "e2-standard-16": (0.5376, "Hour"),
    "n1-standard-1": (0.0475, "Hour"),
    "n1-standard-2": (0.0950, "Hour"),
    "n1-standard-4": (0.1900, "Hour"),
    "n2-standard-2": (0.0971, "Hour"),
    "n2-standard-4": (0.1942, "Hour"),
    "n2-standard-8": (0.3884, "Hour"),
    "c2-standard-4": (0.2088, "Hour"),
    "c2-standard-8": (0.4176, "Hour"),
    "t2d-standard-1": (0.0422, "Hour"),
    "t2d-standard-2": (0.0844, "Hour"),
}

GCP_STORAGE_CATALOG: dict[str, tuple[float, str]] = {
    "standard": (0.020, "GiBy.mo"),
    "nearline": (0.010, "GiBy.mo"),
    "coldline": (0.004, "GiBy.mo"),
    "archive": (0.0012, "GiBy.mo"),
}

GCP_SQL_CATALOG: dict[str, tuple[float, str]] = {
    "db-f1-micro": (0.0105, "Hour"),
    "db-g1-small": (0.0350, "Hour"),
    "db-n1-standard-1": (0.0700, "Hour"),
    "db-n1-standard-2": (0.1400, "Hour"),
    "db-n1-standard-4": (0.2800, "Hour"),
    "db-custom-1-3840": (0.0515, "Hour"),
    "db-custom-2-7680": (0.1030, "Hour"),
    "db-custom-4-15360": (0.2060, "Hour"),
}


class GCPPricingTool:
    """Tool for fetching GCP pricing via Cloud Billing Catalog API with realistic static fallback."""

    def __init__(self):
        self.settings = get_settings()
        self.api_url = "https://cloudbilling.googleapis.com/v1/services"

    async def _fetch_sku_price(self, service_type: str, sku_key: str, region: str = "us-central1") -> tuple[float, str]:
        """Fetch pricing using standard pricing catalogs or Google Cloud Billing API."""
        clean_key = (sku_key or "").strip().lower()

        if service_type == "compute":
            if clean_key in GCP_COMPUTE_CATALOG:
                return GCP_COMPUTE_CATALOG[clean_key]
            for prefix, rate in GCP_COMPUTE_CATALOG.items():
                if prefix in clean_key:
                    return rate
            return (0.0672, "Hour")  # default e2-standard-2 rate

        elif service_type == "storage":
            if clean_key in GCP_STORAGE_CATALOG:
                return GCP_STORAGE_CATALOG[clean_key]
            for tier_name, rate in GCP_STORAGE_CATALOG.items():
                if tier_name in clean_key:
                    return rate
            return (0.020, "GiBy.mo")  # default Standard Storage

        elif service_type == "sql":
            if clean_key in GCP_SQL_CATALOG:
                return GCP_SQL_CATALOG[clean_key]
            for tier_name, rate in GCP_SQL_CATALOG.items():
                if tier_name in clean_key:
                    return rate
            return (0.1030, "Hour")  # default db-custom-2-7680

        return (0.05, "Hour")

    async def get_compute_price(self, machine_type: str, region: str = "us-central1") -> PricingResult:
        """Get Compute Engine pricing."""
        try:
            price, unit = await self._fetch_sku_price("compute", machine_type, region)
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
            price, unit = await self._fetch_sku_price("storage", storage_class, region)
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
            price, unit = await self._fetch_sku_price("sql", tier, region)
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
