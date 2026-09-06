"""GCP pricing tool with two clearly separated data sources.

LIVE — Cloud Billing Catalog API (public SKU list) via Application Default
Credentials. When ADC is available, Compute and Storage prices are composed
from the real regional core/RAM/storage rates, exactly how GCP bills standard
machine shapes.

FALLBACK — a static US-Central1 baseline catalog. Any result served from it is
flagged ``estimated=True`` / ``source="static_catalog"`` so downstream
consumers (dispatcher → context builder → LLM) never present it as verified.

Unknown SKUs are never given an invented default price: they return
``success=False``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Any

import httpx

from .base import PricingResult
from config import get_settings

logger = logging.getLogger(__name__)

# Static US-Central1 baseline rates (estimated fallback catalog only).
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

# Public Cloud Billing Catalog service IDs (https://cloud.google.com/billing/docs).
_BILLING_API_BASE = "https://cloudbilling.googleapis.com/v1"
_SERVICE_IDS = {
    "compute": "6F81-5844-456A",  # Compute Engine
    "storage": "95FF-2EF5-5DA1",  # Cloud Storage
}

# Standard machine shapes are billed per vCPU-hour + GiB-hour. Shared-core
# types (e2-micro/small/medium) are billed by their own SKUs and therefore stay
# on the estimated catalog instead of being composed.
_MACHINE_RE = re.compile(r"^(e2|n1|n2|n2d|c2|c3|c3d|t2d)-(standard|highmem|highcpu)-(\d+)$")
_RAM_GIB_PER_VCPU: dict[tuple[str, str], float] = {
    ("e2", "standard"): 4.0, ("e2", "highmem"): 8.0, ("e2", "highcpu"): 1.0,
    ("n1", "standard"): 3.75, ("n1", "highmem"): 6.5, ("n1", "highcpu"): 0.9,
    ("n2", "standard"): 4.0, ("n2", "highmem"): 8.0, ("n2", "highcpu"): 1.0,
    ("n2d", "standard"): 4.0, ("n2d", "highmem"): 8.0, ("n2d", "highcpu"): 1.0,
    ("c2", "standard"): 4.0, ("c2", "highmem"): 8.0, ("c2", "highcpu"): 1.0,
    ("c3", "standard"): 4.0, ("c3", "highmem"): 8.0, ("c3", "highcpu"): 1.0,
    ("c3d", "standard"): 4.0, ("c3d", "highmem"): 8.0, ("c3d", "highcpu"): 1.0,
    ("t2d", "standard"): 4.0,
}
_FAMILY_SKU_LABEL = {
    "e2": "E2", "n1": "N1", "n2": "N2", "n2d": "N2D",
    "c2": "C2", "c3": "C3", "c3d": "C3D", "t2d": "T2D",
}

# In-process catalog cache: real SKU data is stable enough to hold for a day.
_SKU_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SKU_TTL_SECONDS = 24 * 3600.0
_SKU_FAILURE_BACKOFF_SECONDS = 300.0
_last_fetch_failure: dict[str, float] = {}


def _money_to_float(money: dict[str, Any]) -> float:
    """Parse a Cloud Billing Money object ({units, nanos}) into a float."""
    try:
        return int(money.get("units", 0)) + int(money.get("nanos", 0)) / 1e9
    except (TypeError, ValueError):
        return 0.0


def _sku_rate(sku: dict[str, Any]) -> float:
    """Extract the OnDemand unit price from a SKU's pricing expression."""
    try:
        for info in sku.get("pricingInfo", []):
            expr = info.get("pricingExpression", {})
            tiers = expr.get("tieredRates") or []
            if tiers:
                return _money_to_float(tiers[-1].get("unitPrice", {}))
    except (TypeError, ValueError, KeyError):
        pass
    return 0.0


def _sku_matches_region(sku: dict[str, Any], region: str) -> bool:
    regions = sku.get("serviceRegions") or []
    if region in regions:
        return True
    # Regional SKUs sometimes roll up under the broader multi-region prefix.
    prefix = region.split("-")[0] if region else ""
    return bool(prefix) and prefix in regions


class GCPPricingTool:
    """Fetch GCP pricing from the live Cloud Billing Catalog API, falling back
    to a clearly-labelled static baseline catalog when ADC is unavailable."""

    def __init__(self):
        self.settings = get_settings()

    # ── Live catalog access ────────────────────────────────────────────────

    async def _get_catalog(self, service_type: str, region: str) -> dict[str, Any] | None:
        """Return {'core': rate, 'ram': rate} for compute or {'standard': rate, ...}
        for storage, fetched from the live API; None when unavailable."""
        service_id = _SERVICE_IDS.get(service_type)
        if not service_id:
            return None
        cache_key = f"{service_type}:{region}"
        now = time.monotonic()

        cached = _SKU_CACHE.get(cache_key)
        if cached and now - cached[0] < _SKU_TTL_SECONDS:
            return cached[1]
        if now - _last_fetch_failure.get(service_type, 0.0) < _SKU_FAILURE_BACKOFF_SECONDS:
            return None

        try:
            token = await asyncio.to_thread(self._access_token)
            if not token:
                _last_fetch_failure[service_type] = now
                return None
            async with httpx.AsyncClient(timeout=10.0) as client:
                rates = await self._fetch_rates(client, token, service_id, service_type, region)
        except Exception as exc:
            logger.warning("GCP billing catalog fetch failed (%s): %s", service_type, exc)
            _last_fetch_failure[service_type] = now
            return None

        if not rates:
            _last_fetch_failure[service_type] = now
            return None
        _SKU_CACHE[cache_key] = (now, rates)
        return rates

    @staticmethod
    def _access_token() -> str | None:
        try:
            import google.auth
            from google.auth.transport.requests import Request as AuthRequest

            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            if not creds.valid:
                creds.refresh(AuthRequest())
            token = getattr(creds, "token", None)
            return token if isinstance(token, str) and token else None
        except Exception as exc:
            logger.info("GCP ADC unavailable, pricing falls back to static catalog: %s", exc)
            return None

    async def _fetch_rates(
        self, client: httpx.AsyncClient, token: str, service_id: str, service_type: str, region: str
    ) -> dict[str, Any]:
        rates: dict[str, float] = {}
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"pageSize": 5000, "currencyCode": "USD"}
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(
                f"{_BILLING_API_BASE}/services/{service_id}/skus",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()
            for sku in payload.get("skus", []):
                if (sku.get("category") or {}).get("usageType") != "OnDemand":
                    continue
                if not _sku_matches_region(sku, region):
                    continue
                description = sku.get("description", "")
                rate = _sku_rate(sku)
                if rate <= 0:
                    continue
                if service_type == "compute":
                    for family, label in _FAMILY_SKU_LABEL.items():
                        if f"{label} Instance Core" in description:
                            rates.setdefault("core", rate)
                        elif f"{label} Instance Ram" in description:
                            rates.setdefault("ram", rate)
                else:  # storage
                    for cls in ("Standard", "Nearline", "Coldline", "Archive"):
                        if description.startswith(f"{cls} Storage"):
                            rates.setdefault(cls.lower(), rate)
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return rates

    # ── Catalog fallback helpers ────────────────────────────────────────────

    @staticmethod
    def _catalog_rate(catalog: dict[str, tuple[float, str]], key: str) -> tuple[float, str] | None:
        """Exact then bounded substring match against the static catalog.

        Only matches catalog keys that appear inside the requested SKU string
        (e.g. 'e2-standard-2-custom-4-8192' → 'e2-standard-2'); it never
        invents a default for a genuinely unknown SKU."""
        clean = (key or "").strip().lower()
        if clean in catalog:
            return catalog[clean]
        for prefix, rate in catalog.items():
            if prefix in clean:
                return rate
        return None

    def _estimated_result(self, provider_service: str, config: dict[str, Any], region: str, catalog: dict[str, tuple[float, str]], key: str) -> PricingResult:
        matched = self._catalog_rate(catalog, key)
        if matched is None:
            return PricingResult(
                success=False,
                error_message=(
                    f"Unknown {provider_service} SKU '{key}' and no live GCP billing "
                    "catalog access (set Application Default Credentials for real rates)."
                ),
                provider="GCP",
                service=provider_service,
                config=config,
                region=region,
            )
        price, unit = matched
        return PricingResult(
            success=True,
            provider="GCP",
            service=provider_service,
            config=config,
            unit_price=price,
            unit=unit,
            region=region,
            estimated=True,
            source="static_catalog",
            timestamp=datetime.utcnow().isoformat(),
        )

    # ── Public API (signatures unchanged) ───────────────────────────────────

    async def get_compute_price(self, machine_type: str, region: str = "us-central1") -> PricingResult:
        """Get Compute Engine pricing (live core+RAM composition when possible)."""
        config = {"machine_type": machine_type}
        try:
            match = _MACHINE_RE.match((machine_type or "").strip().lower())
            if match:
                family, shape, count = match.groups()
                vcpu = int(count)
                ram = vcpu * _RAM_GIB_PER_VCPU[(family, shape)]
                live = await self._get_catalog("compute", region)
                if live and "core" in live and "ram" in live:
                    price = vcpu * live["core"] + ram * live["ram"]
                    return PricingResult(
                        success=True,
                        provider="GCP",
                        service="Compute Engine",
                        config=config,
                        unit_price=round(price, 6),
                        unit="Hour",
                        region=region,
                        estimated=False,
                        source="cloud_billing_catalog_api",
                        timestamp=datetime.utcnow().isoformat(),
                    )
            return self._estimated_result("Compute Engine", config, region, GCP_COMPUTE_CATALOG, machine_type)
        except Exception as exc:
            logger.warning("GCP Compute Engine pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="GCP",
                service="Compute Engine",
                config=config,
                region=region,
            )

    async def get_cloud_storage_price(self, storage_class: str = "Standard", region: str = "us-central1") -> PricingResult:
        """Get Cloud Storage pricing."""
        config = {"storage_class": storage_class}
        try:
            clean = (storage_class or "").strip().lower()
            live = await self._get_catalog("storage", region)
            if live and clean in live:
                return PricingResult(
                    success=True,
                    provider="GCP",
                    service="Cloud Storage",
                    config=config,
                    unit_price=live[clean],
                    unit="GiBy.mo",
                    region=region,
                    estimated=False,
                    source="cloud_billing_catalog_api",
                    timestamp=datetime.utcnow().isoformat(),
                )
            return self._estimated_result("Cloud Storage", config, region, GCP_STORAGE_CATALOG, storage_class)
        except Exception as exc:
            logger.warning("GCP Cloud Storage pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="GCP",
                service="Cloud Storage",
                config=config,
                region=region,
            )

    async def get_cloud_sql_price(self, tier: str, region: str = "us-central1") -> PricingResult:
        """Get Cloud SQL pricing.

        Cloud SQL has no single per-tier SKU in the public catalog (vCPU/RAM
        composition varies by edition and region), so this lookup is served
        from the static baseline catalog and is always flagged estimated."""
        config = {"tier": tier}
        try:
            return self._estimated_result("Cloud SQL", config, region, GCP_SQL_CATALOG, tier)
        except Exception as exc:
            logger.warning("GCP Cloud SQL pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="GCP",
                service="Cloud SQL",
                config=config,
                region=region,
            )
