from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Worker-created identity; never populated from model arguments."""

    tenant_id: UUID
    user_id: UUID
    run_id: UUID
    session_id: UUID
    trace_id: UUID
    dispatch_message_id: str
    application_id: UUID | None = None
    external_user_id: str | None = None


ToolInvoker = Callable[[str, str, dict[str, Any]], Awaitable[str]]


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
