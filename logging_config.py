"""Structured logging configuration for CloudGPT.

Uses structlog to emit JSON logs in production and colored console logs in
development. Every log line is enriched with:
    - timestamp (ISO-8601)
    - level
    - logger name
    - request_id / user_id when bound via the context vars below

Both structlog loggers and the standard library logging used by third-party
modules are routed through the same renderer, so output is uniform.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)


def _inject_request_context(
    logger, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Merge request_id / user_id context vars into every log event."""
    request_id = request_id_var.get()
    if request_id:
        event_dict.setdefault("request_id", request_id)
    user_id = user_id_var.get()
    if user_id is not None:
        event_dict.setdefault("user_id", user_id)
    return event_dict


def bind_request_context(request_id: str | None = None, user_id: int | None = None) -> None:
    """Bind the current request context for log correlation."""
    if request_id:
        request_id_var.set(request_id)
    if user_id is not None:
        user_id_var.set(user_id)


def unbind_request_context() -> None:
    """Clear request context vars (call when a request completes)."""
    request_id_var.set(None)
    user_id_var.set(None)


def configure_logging(log_level: str = "INFO", log_format: str = "console") -> None:
    """Configure root + structlog logging once at application startup.

    Args:
        log_level: Standard level name (DEBUG, INFO, ...).
        log_format: "json" for production JSON lines, "console" for
            human-friendly colored development output.
    """
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass

    level = getattr(logging, str(log_level).upper(), logging.INFO)
    use_json = log_format.lower() == "json"

    timestamper = structlog.processors.TimeStamper(fmt="iso")
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        _inject_request_context,
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if use_json
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    # Standard library records (uvicorn, psycopg2, ...) get the same rendering.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Quiet the noisiest third-party loggers while keeping warnings/errors.
    for noisy in ("httpx", "httpcore", "duckduckgo_search", "asyncio"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to the given module name."""
    return structlog.get_logger(name)
