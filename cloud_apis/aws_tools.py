import logging
from typing import Any

from config import get_settings
from metrics import CLOUD_API_ERRORS_TOTAL

logger = logging.getLogger(__name__)

try:
    import boto3
    import botocore.exceptions
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    BotoCoreError = Exception  # type: ignore[assignment,misc]
    ClientError = Exception  # type: ignore[assignment,misc]
    NoCredentialsError = Exception  # type: ignore[assignment,misc]


class AWSTools:
    """Tools for querying live AWS resources."""

    def __init__(self):
        self.settings = get_settings()
        self.has_credentials = self.settings.has_aws

        if not BOTO3_AVAILABLE:
            logger.warning("boto3 not installed. AWS Tools unavailable.")
        elif not self.has_credentials:
            logger.warning("AWS credentials not fully configured.")

    def _get_client(self, service: str, region: str | None = None) -> Any:
        if not BOTO3_AVAILABLE:
            raise ImportError("boto3 is not installed")
        if not self.has_credentials:
            raise RuntimeError("AWS tools unavailable. Ensure credentials are set.")

        region_name = region or self.settings.aws_default_region
        return boto3.client(
            service,
            region_name=region_name,
            aws_access_key_id=self.settings.aws_access_key_id,
            aws_secret_access_key=self.settings.aws_secret_access_key,
        )

    async def list_ec2_instances(self, region: str | None = None) -> list[dict]:
        """List running EC2 instances."""
        try:
            client = self._get_client('ec2', region)
            instances = []
            paginator = client.get_paginator('describe_instances')
            for page in paginator.paginate():
                for reservation in page.get('Reservations', []):
                    for inst in reservation.get('Instances', []):
                        name = next((tag['Value'] for tag in inst.get('Tags', []) if tag['Key'] == 'Name'), 'Unknown')
                        instances.append({
                            'id': inst['InstanceId'],
                            'name': name,
                            'state': inst['State']['Name'],
                            'type': inst['InstanceType'],
                            'region': client.meta.region_name
                        })
            return instances
        except ImportError as exc:
            logger.warning("cloud_sdk_missing", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="import").inc()
            return []
        except RuntimeError as exc:
            logger.error("cloud_sdk_init_failed", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="init").inc()
            return []
        except (BotoCoreError, ClientError, NoCredentialsError) as exc:
            logger.error("cloud_api_error", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="api").inc()
            return []
        except Exception as exc:
            logger.error("cloud_api_unexpected_error", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="unexpected").inc()
            return []

    async def list_s3_buckets(self) -> list[dict]:
        """List S3 buckets."""
        try:
            client = self._get_client('s3')
            buckets = []
            response = client.list_buckets()
            for b in response.get('Buckets', []):
                buckets.append({
                    'name': b['Name'],
                    'creation_date': b['CreationDate'].isoformat() if hasattr(b['CreationDate'], 'isoformat') else str(b['CreationDate']),
                    'region': 'global'
                })
            return buckets
        except ImportError as exc:
            logger.warning("cloud_sdk_missing", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="import").inc()
            return []
        except RuntimeError as exc:
            logger.error("cloud_sdk_init_failed", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="init").inc()
            return []
        except (BotoCoreError, ClientError, NoCredentialsError) as exc:
            logger.error("cloud_api_error", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="api").inc()
            return []
        except Exception as exc:
            logger.error("cloud_api_unexpected_error", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="unexpected").inc()
            return []

    async def list_lambda_functions(self, region: str | None = None) -> list[dict]:
        """List Lambda functions."""
        try:
            client = self._get_client('lambda', region)
            functions = []
            paginator = client.get_paginator('list_functions')
            for page in paginator.paginate():
                for func in page.get('Functions', []):
                    functions.append({
                        'name': func['FunctionName'],
                        'runtime': func.get('Runtime', 'Unknown'),
                        'state': func.get('State', 'Active'),
                        'region': client.meta.region_name
                    })
            return functions
        except ImportError as exc:
            logger.warning("cloud_sdk_missing", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="import").inc()
            return []
        except RuntimeError as exc:
            logger.error("cloud_sdk_init_failed", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="init").inc()
            return []
        except (BotoCoreError, ClientError, NoCredentialsError) as exc:
            logger.error("cloud_api_error", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="api").inc()
            return []
        except Exception as exc:
            logger.error("cloud_api_unexpected_error", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="unexpected").inc()
            return []

    async def list_rds_instances(self, region: str | None = None) -> list[dict]:
        """List RDS database instances."""
        try:
            client = self._get_client('rds', region)
            instances = []
            paginator = client.get_paginator('describe_db_instances')
            for page in paginator.paginate():
                for db in page.get('DBInstances', []):
                    instances.append({
                        'id': db['DBInstanceIdentifier'],
                        'engine': db['Engine'],
                        'status': db['DBInstanceStatus'],
                        'class': db['DBInstanceClass'],
                        'region': client.meta.region_name
                    })
            return instances
        except ImportError as exc:
            logger.warning("cloud_sdk_missing", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="import").inc()
            return []
        except RuntimeError as exc:
            logger.error("cloud_sdk_init_failed", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="init").inc()
            return []
        except (BotoCoreError, ClientError, NoCredentialsError) as exc:
            logger.error("cloud_api_error", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="api").inc()
            return []
        except Exception as exc:
            logger.error("cloud_api_unexpected_error", extra={"provider": "aws", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="aws", type="unexpected").inc()
            return []
