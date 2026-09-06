import asyncio
import json
import logging
from datetime import datetime

from pydantic import BaseModel

from config import get_settings

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import NoCredentialsError, ClientError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


from .base import PricingResult


class AWSPricingTool:
    """Tool for fetching AWS pricing using the Price List API."""

    def __init__(self):
        self.settings = get_settings()
        self.client = None

        if not BOTO3_AVAILABLE:
            logger.warning("boto3 not installed. AWS Pricing unavailable.")
            return

        try:
            if self.settings.has_aws:
                self.client = boto3.client(
                    "pricing",
                    region_name="us-east-1",
                    aws_access_key_id=self.settings.aws_access_key_id,
                    aws_secret_access_key=self.settings.aws_secret_access_key,
                )
            else:
                logger.warning("AWS credentials not fully configured.")
        except Exception as e:
            logger.error(f"Failed to initialize AWS Pricing client: {e}")

    def _extract_price(self, price_list: list) -> tuple[float, str]:
        """Extract the unit price and unit from the raw price list JSON."""
        if not price_list:
            raise ValueError("No price items found.")

        price_item = json.loads(price_list[0])
        terms = price_item.get("terms", {})
        on_demand = terms.get("OnDemand", {})

        if not on_demand:
            raise ValueError("No OnDemand pricing found.")

        first_term = list(on_demand.values())[0]
        price_dimensions = first_term.get("priceDimensions", {})

        if not price_dimensions:
            raise ValueError("No price dimensions found.")

        first_dim = list(price_dimensions.values())[0]
        price = float(first_dim.get("pricePerUnit", {}).get("USD", 0.0))
        unit = first_dim.get("unit", "Unknown")

        return price, unit

    async def get_ec2_price(self, instance_type: str, region: str = 'us-east-1', os: str = 'Linux') -> PricingResult:
        if not self.client:
            return PricingResult(
                success=False,
                error_message="AWS Pricing client not initialized.",
                provider="AWS",
                service="EC2",
                config={"instance_type": instance_type, "os": os},
                region=region,
            )

        try:
            filters = [
                {"Type": "TERM_MATCH", "Field": "ServiceCode", "Value": "AmazonEC2"},
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": os},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            ]

            response = await asyncio.to_thread(
                self.client.get_products, ServiceCode="AmazonEC2", Filters=filters, MaxResults=1
            )
            price, unit = self._extract_price(response.get("PriceList", []))

            return PricingResult(
                success=True,
                provider="AWS",
                service="EC2",
                config={"instance_type": instance_type, "os": os},
                unit_price=price,
                unit=unit,
                region=region,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning("AWS EC2 pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="AWS",
                service="EC2",
                config={"instance_type": instance_type, "os": os},
                region=region,
            )

    async def get_rds_price(self, instance_type: str, engine: str, region: str = 'us-east-1') -> PricingResult:
        if not self.client:
            return PricingResult(
                success=False,
                error_message="AWS Pricing client not initialized.",
                provider="AWS",
                service="RDS",
                config={"instance_type": instance_type, "engine": engine},
                region=region,
            )

        try:
            filters = [
                {"Type": "TERM_MATCH", "Field": "ServiceCode", "Value": "AmazonRDS"},
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                {"Type": "TERM_MATCH", "Field": "databaseEngine", "Value": engine},
            ]

            response = await asyncio.to_thread(
                self.client.get_products, ServiceCode="AmazonRDS", Filters=filters, MaxResults=1
            )
            price, unit = self._extract_price(response.get("PriceList", []))

            return PricingResult(
                success=True,
                provider="AWS",
                service="RDS",
                config={"instance_type": instance_type, "engine": engine},
                unit_price=price,
                unit=unit,
                region=region,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning("AWS RDS pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="AWS",
                service="RDS",
                config={"instance_type": instance_type, "engine": engine},
                region=region,
            )

    async def get_s3_price(self, storage_class: str = "General Purpose", region: str = 'us-east-1') -> PricingResult:
        if not self.client:
            return PricingResult(
                success=False,
                error_message="AWS Pricing client not initialized.",
                provider="AWS",
                service="S3",
                config={"storage_class": storage_class},
                region=region,
            )

        try:
            filters = [
                {"Type": "TERM_MATCH", "Field": "ServiceCode", "Value": "AmazonS3"},
                {"Type": "TERM_MATCH", "Field": "storageClass", "Value": storage_class},
            ]

            response = await asyncio.to_thread(
                self.client.get_products, ServiceCode="AmazonS3", Filters=filters, MaxResults=1
            )
            price, unit = self._extract_price(response.get("PriceList", []))

            return PricingResult(
                success=True,
                provider="AWS",
                service="S3",
                config={"storage_class": storage_class},
                unit_price=price,
                unit=unit,
                region=region,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning("AWS S3 pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="AWS",
                service="S3",
                config={"storage_class": storage_class},
                region=region,
            )

    async def get_lambda_price(self, region: str = 'us-east-1') -> PricingResult:
        if not self.client:
            return PricingResult(
                success=False,
                error_message="AWS Pricing client not initialized.",
                provider="AWS",
                service="Lambda",
                config={"type": "Duration"},
                region=region,
            )

        try:
            filters = [
                {"Type": "TERM_MATCH", "Field": "ServiceCode", "Value": "AWSLambda"},
                {"Type": "TERM_MATCH", "Field": "group", "Value": "AWS-Lambda-Duration"},
            ]

            response = await asyncio.to_thread(
                self.client.get_products, ServiceCode="AWSLambda", Filters=filters, MaxResults=1
            )
            price, unit = self._extract_price(response.get("PriceList", []))

            return PricingResult(
                success=True,
                provider="AWS",
                service="Lambda",
                config={"type": "Duration"},
                unit_price=price,
                unit=unit,
                region=region,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning("AWS Lambda pricing query failed: %s", exc)
            return PricingResult(
                success=False,
                error_message=str(exc),
                provider="AWS",
                service="Lambda",
                config={"type": "Duration"},
                region=region,
            )
