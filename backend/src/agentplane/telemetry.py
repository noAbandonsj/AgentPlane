from __future__ import annotations

from agentplane.config import Settings
from agentplane.logging import get_logger

logger = get_logger(__name__)


def initialize_telemetry(settings: Settings) -> None:
    """Reserved initialization boundary for a later OpenTelemetry provider."""
    logger.info(
        "telemetry_initialization_reserved",
        service="agentplane",
        environment=settings.app_env,
        enabled=False,
    )
