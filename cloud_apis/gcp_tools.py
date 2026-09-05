import logging

from config import get_settings
from metrics import CLOUD_API_ERRORS_TOTAL

logger = logging.getLogger(__name__)

try:
    from google.api_core.exceptions import GoogleAPICallError
    from google.cloud import compute_v1, run_v2, storage
    GCP_AVAILABLE = True
except ImportError:
    GCP_AVAILABLE = False
    GoogleAPICallError = Exception  # type: ignore[assignment,misc]


class GCPTools:
    """Tools for querying live GCP resources."""

    def __init__(self):
        self.settings = get_settings()
        self.has_credentials = self.settings.has_gcp

        if not GCP_AVAILABLE:
            logger.warning("google-cloud SDK not installed. GCP Tools unavailable.")
        elif not self.has_credentials:
            logger.warning("GCP credentials not fully configured.")

    def _check_availability(self):
        if not GCP_AVAILABLE:
            raise ImportError("google-cloud SDKs are not installed")
        if not self.has_credentials:
            raise RuntimeError("GCP tools unavailable. Ensure credentials are set.")

    async def list_compute_instances(self, project: str | None = None, zone: str | None = None) -> list[dict]:
        """List Compute Engine instances."""
        try:
            self._check_availability()
            project_id = project or self.settings.gcp_project_id
            if not project_id:
                raise ValueError("GCP Project ID must be provided.")

            instances = []
            client = compute_v1.InstancesClient()
            if zone:
                request = compute_v1.ListInstancesRequest(project=project_id, zone=zone)
                for inst in client.list(request=request):
                    instances.append({
                        'id': inst.id,
                        'name': inst.name,
                        'status': inst.status,
                        'machine_type': inst.machine_type.split('/')[-1] if inst.machine_type else 'Unknown',
                        'zone': zone
                    })
            else:
                request = compute_v1.AggregatedListInstancesRequest(project=project_id)
                for zone_name, zone_instances in client.aggregated_list(request=request):
                    if zone_instances.instances:
                        for inst in zone_instances.instances:
                            z = zone_name.split('/')[-1]
                            instances.append({
                                'id': inst.id,
                                'name': inst.name,
                                'status': inst.status,
                                'machine_type': inst.machine_type.split('/')[-1] if inst.machine_type else 'Unknown',
                                'zone': z
                            })
            return instances
        except ImportError as exc:
            logger.warning("cloud_sdk_missing", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="import").inc()
            return []
        except ValueError as exc:
            logger.warning("cloud_config_missing", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="config").inc()
            return []
        except RuntimeError as exc:
            logger.error("cloud_sdk_init_failed", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="init").inc()
            return []
        except GoogleAPICallError as exc:
            logger.error("cloud_api_error", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="api").inc()
            return []
        except Exception as exc:
            logger.error("cloud_api_unexpected_error", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="unexpected").inc()
            return []

    async def list_storage_buckets(self, project: str | None = None) -> list[dict]:
        """List Cloud Storage buckets."""
        try:
            self._check_availability()
            project_id = project or self.settings.gcp_project_id
            if not project_id:
                raise ValueError("GCP Project ID must be provided.")

            buckets = []
            client = storage.Client(project=project_id)
            for b in client.list_buckets():
                buckets.append({
                    'name': b.name,
                    'location': b.location,
                    'storage_class': b.storage_class,
                })
            return buckets
        except ImportError as exc:
            logger.warning("cloud_sdk_missing", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="import").inc()
            return []
        except ValueError as exc:
            logger.warning("cloud_config_missing", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="config").inc()
            return []
        except RuntimeError as exc:
            logger.error("cloud_sdk_init_failed", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="init").inc()
            return []
        except GoogleAPICallError as exc:
            logger.error("cloud_api_error", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="api").inc()
            return []
        except Exception as exc:
            logger.error("cloud_api_unexpected_error", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="unexpected").inc()
            return []

    async def list_cloud_run_services(self, project: str | None = None, region: str | None = None) -> list[dict]:
        """List Cloud Run services."""
        try:
            self._check_availability()
            project_id = project or self.settings.gcp_project_id
            if not project_id:
                raise ValueError("GCP Project ID must be provided.")
            loc = region or "-"  # "-" means all regions

            services = []
            client = run_v2.ServicesClient()
            parent = f"projects/{project_id}/locations/{loc}"
            request = run_v2.ListServicesRequest(parent=parent)

            for svc in client.list_services(request=request):
                services.append({
                    'name': svc.name.split('/')[-1],
                    'uri': svc.uri,
                    'region': svc.name.split('/')[3],
                })
            return services
        except ImportError as exc:
            logger.warning("cloud_sdk_missing", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="import").inc()
            return []
        except ValueError as exc:
            logger.warning("cloud_config_missing", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="config").inc()
            return []
        except RuntimeError as exc:
            logger.error("cloud_sdk_init_failed", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="init").inc()
            return []
        except GoogleAPICallError as exc:
            logger.error("cloud_api_error", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="api").inc()
            return []
        except Exception as exc:
            logger.error("cloud_api_unexpected_error", extra={"provider": "gcp", "error": str(exc)})
            CLOUD_API_ERRORS_TOTAL.labels(provider="gcp", type="unexpected").inc()
            return []
