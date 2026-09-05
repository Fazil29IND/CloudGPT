import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, Sequence, Type

from .aws_tools import AWSTools
from .azure_tools import AzureTools
from .gcp_tools import GCPTools

logger = logging.getLogger(__name__)


async def retry_with_backoff(
    fn: Callable[[], Awaitable[Any]],
    max_retries: int = 3,
    base_delay: float = 0.5,
    retry_exceptions: Sequence[Type[BaseException]] = (Exception,),
) -> Any:
    """Retry an async callable with exponential backoff and jitter."""
    for attempt in range(max_retries):
        try:
            return await fn()
        except tuple(retry_exceptions) as exc:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.2)
            logger.warning(
                "cloud_api_retry",
                extra={"attempt": attempt + 1, "delay": round(delay, 2), "error": str(exc)},
            )
            await asyncio.sleep(delay)


__all__ = ["AWSTools", "GCPTools", "AzureTools", "retry_with_backoff"]

