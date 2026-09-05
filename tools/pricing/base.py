from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PricingResult:
    """Standardized result container for cloud pricing lookups."""
    success: bool = True
    error_message: str | None = None
    data: dict[str, Any] | None = None
    provider: str = ""
    service: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    unit_price: float = 0.0
    unit: str = "Unknown"
    currency: str = "USD"
    region: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    source_url: str | None = None

    @property
    def retail_price(self) -> float:
        return self.unit_price

    @property
    def sku_name(self) -> str:
        return str(self.config.get("sku") or self.config.get("instance_type") or self.service)

    @property
    def unit_of_measure(self) -> str:
        return self.unit

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "error_message": self.error_message,
            "provider": self.provider,
            "service": self.service,
            "sku": self.sku_name,
            "unit_price": self.unit_price,
            "hourly_cost": self.unit_price,
            "unit": self.unit,
            "currency": self.currency,
            "region": self.region,
            "config": self.config,
            "timestamp": self.timestamp,
            "data": self.data or {},
        }
