import logging

from config import get_settings
from metrics import CLOUD_API_ERRORS_TOTAL

logger = logging.getLogger(__name__)

try:
    from azure.core.exceptions import AzureError
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.compute import ComputeManagementClient
    from azure.mgmt.storage import StorageManagementClient
    from azure.mgmt.web import WebSiteManagementClient
    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False
    AzureError = Exception  # type: ignore[assignment,misc]


class AzureTools:
    """Tools for querying live Azure resources."""

    def __init__(self):
        self.settings = get_settings()
        self.has_credentials = self.settings.has_azure

        if not AZURE_AVAILABLE:
            logger.warning("azure-mgmt SDKs not installed. Azure Tools unavailable.")
        elif not self.has_credentials:
            logger.warning("Azure credentials not fully configured.")

    def _get_credential(self):
        if not AZURE_AVAILABLE:
            raise ImportError("azure-mgmt SDKs are not installed")
        if not self.has_credentials:
            raise RuntimeError("Azure tools unavailable. Ensure credentials are set.")
        return DefaultAzureCredential()

    async def list_virtual_machines(self, subscription: str | None = None) -> list[dict]:
        """List Azure Virtual Machines."""
        try:
            sub_id = subscription or self.settings.azure_subscription_id
            if not sub_id:
                raise ValueError("Azure Subscription ID must be provided.")

            client = ComputeManagementClient(self._get_credential(), sub_id)
            vms = []
            for vm in client.virtual_machines.list_all():
                vms.append({
                    'id': vm.id,
                    'name': vm.name,
                    'location': vm.location,
                    'vm_size': vm.hardware_profile.vm_size if vm.hardware_profile else 'Unknown',
                })
            return vms
        except ImportError as exc:
            logger.warning("cloud_sdk_missing", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="import").inc()
            return []
        except ValueError as exc:
            logger.warning("cloud_config_missing", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="config").inc()
            return []
        except RuntimeError as exc:
            logger.error("cloud_sdk_init_failed", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="init").inc()
            return []
        except AzureError as exc:
            logger.error("cloud_api_error", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="api").inc()
            return []
        except Exception as exc:
            logger.error("cloud_api_unexpected_error", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="unexpected").inc()
            return []

    async def list_storage_accounts(self, subscription: str | None = None) -> list[dict]:
        """List Azure Storage Accounts."""
        try:
            sub_id = subscription or self.settings.azure_subscription_id
            if not sub_id:
                raise ValueError("Azure Subscription ID must be provided.")

            client = StorageManagementClient(self._get_credential(), sub_id)
            accounts = []
            for acc in client.storage_accounts.list():
                accounts.append({
                    'id': acc.id,
                    'name': acc.name,
                    'location': acc.location,
                    'kind': acc.kind,
                })
            return accounts
        except ImportError as exc:
            logger.warning("cloud_sdk_missing", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="import").inc()
            return []
        except ValueError as exc:
            logger.warning("cloud_config_missing", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="config").inc()
            return []
        except RuntimeError as exc:
            logger.error("cloud_sdk_init_failed", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="init").inc()
            return []
        except AzureError as exc:
            logger.error("cloud_api_error", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="api").inc()
            return []
        except Exception as exc:
            logger.error("cloud_api_unexpected_error", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="unexpected").inc()
            return []

    async def list_function_apps(self, subscription: str | None = None) -> list[dict]:
        """List Azure Function Apps (Web Apps)."""
        try:
            sub_id = subscription or self.settings.azure_subscription_id
            if not sub_id:
                raise ValueError("Azure Subscription ID must be provided.")

            client = WebSiteManagementClient(self._get_credential(), sub_id)
            apps = []
            for app in client.web_apps.list():
                if app.kind and 'functionapp' in app.kind.lower():
                    apps.append({
                        'id': app.id,
                        'name': app.name,
                        'location': app.location,
                        'state': app.state,
                    })
            return apps
        except ImportError as exc:
            logger.warning("cloud_sdk_missing", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="import").inc()
            return []
        except ValueError as exc:
            logger.warning("cloud_config_missing", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="config").inc()
            return []
        except RuntimeError as exc:
            logger.error("cloud_sdk_init_failed", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="init").inc()
            return []
        except AzureError as exc:
            logger.error("cloud_api_error", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="api").inc()
            return []
        except Exception as exc:
            logger.error("cloud_api_unexpected_error", extra={"provider": "azure", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="azure", type="unexpected").inc()
            return []
