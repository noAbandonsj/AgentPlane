from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RuntimeDefinition:
    agent_definition_id: UUID
    agent_version_id: UUID
    instructions: str
    model_alias: str
    tool_keys: list[str]


@dataclass(frozen=True, slots=True)
class RuntimeMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class RuntimeRunRequest:
    run_id: UUID
    session_id: UUID
    trace_id: UUID
    input_text: str
    definition: RuntimeDefinition
    history: tuple[RuntimeMessage, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    output_text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


RuntimeEventEmitter = Callable[[RuntimeEvent], Awaitable[None]]
CancellationCheck = Callable[[], Awaitable[bool]]


class RuntimeCancelled(Exception):
    """Raised when a cooperative cancellation is observed."""


class AgentRuntimeAdapter(ABC):
    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    def validate_definition(self, definition: RuntimeDefinition) -> None: ...

    @abstractmethod
    async def start_run(
        self,
        request: RuntimeRunRequest,
        emit: RuntimeEventEmitter,
        is_cancelled: CancellationCheck,
    ) -> RuntimeResult: ...

    @abstractmethod
    async def resume_run(
        self,
        request: RuntimeRunRequest,
        approval_payload: dict[str, Any],
        emit: RuntimeEventEmitter,
        is_cancelled: CancellationCheck,
    ) -> RuntimeResult: ...

    @abstractmethod
    async def cancel_run(self, run_id: UUID) -> None: ...
