from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import structlog

SENSITIVE_KEYS = {
    "authorization",
    "application_token",
    "cookie",
    "database_url",
    "model_api_key",
    "password",
    "redis_url",
    "token",
}


def _redact_sensitive(
    _logger: Any, _method_name: str, event_dict: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else value
        for key, value in event_dict.items()
    }


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_sensitive,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "agentplane") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_log_context(**values: UUID | str | None) -> None:
    structlog.contextvars.bind_contextvars(
        **{key: str(value) for key, value in values.items() if value is not None}
    )


def clear_log_context() -> None:
    structlog.contextvars.clear_contextvars()
