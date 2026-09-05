"""ARQ worker entry point.

Run with: `python worker.py` (or `arq tasks.WorkerSettings`).
Deployed as the `worker` service in docker-compose.
"""

from __future__ import annotations

import logging

from arq import run_worker

from logging_config import configure_logging
from tasks import WorkerSettings


def main() -> None:
    configure_logging(log_level="INFO", log_format="json")
    logging.getLogger("arq").setLevel(logging.INFO)
    run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
