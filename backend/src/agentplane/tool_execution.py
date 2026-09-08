from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentplane.db import utc_now
from agentplane.logging import get_logger
from agentplane.models import (
    AgentDefinition,
    AgentLifecycle,
    AgentVersion,
    ApplicationAgentGrant,
    ApplicationToolGrant,
    AppUser,
    CallingApplication,
    ExternalUserMapping,
    Invocation,
    InvocationDecision,
    RunStatus,
    TaskRun,
    Tenant,
    TenantToolPolicy,
    ToolCall,
    ToolCallStatus,
    UserAgentGrant,
    UserStatus,
    UserToolGrant,
)
from agentplane.runtime.base import RuntimeCancelled, RuntimeEvent, RuntimeEventEmitter
from agentplane.tool_contracts import ToolExecutionContext, ToolExecutionError
from agentplane.tools import TOOL_REGISTRY, ToolDefinition

logger = get_logger(__name__)


async def interrupt_tool_calls(db: AsyncSession, run: TaskRun) -> None:
    """Close orphaned admissions in the same transaction as the Run terminal state."""
    await db.execute(
        update(ToolCall)
        .where(
            ToolCall.tenant_id == run.tenant_id,
            ToolCall.run_id == run.id,
            ToolCall.status == ToolCallStatus.STARTED,
        )
        .values(
            status=ToolCallStatus.INTERRUPTED,
            error_code="TOOL_EXECUTION_INTERRUPTED",
            completed_at=utc_now(),
        )
    )


class ToolExecutor:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        context: ToolExecutionContext,
        emit: RuntimeEventEmitter,
    ) -> None:
        self.factory = factory
        self.context = context
        self.emit = emit

    async def _authorize(self, db: AsyncSession, run: TaskRun, key: str, version: str) -> None:
        context = self.context
        if (
            run.user_id != context.user_id
            or run.session_id != context.session_id
            or run.trace_id != context.trace_id
        ):
            raise ToolExecutionError("TOOL_CONTEXT_INVALID")
        if (
            run.status != RunStatus.RUNNING
            or run.dispatch_message_id != context.dispatch_message_id
            or run.cancel_requested_at is not None
        ):
            raise ToolExecutionError("TOOL_RUN_INACTIVE")
        tenant = await db.scalar(
            select(Tenant).where(Tenant.id == context.tenant_id).with_for_update(read=True)
        )
        if tenant is None or not tenant.active:
            raise ToolExecutionError("TOOL_ACCESS_DENIED")
        policy = await db.get(TenantToolPolicy, (context.tenant_id, key))
        if policy is not None and not policy.enabled:
            raise ToolExecutionError("TOOL_DISABLED")
        agent = await db.scalar(
            select(AgentDefinition).where(
                AgentDefinition.tenant_id == context.tenant_id,
                AgentDefinition.id == run.agent_definition_id,
            )
        )
        agent_version = await db.scalar(
            select(AgentVersion).where(
                AgentVersion.tenant_id == context.tenant_id,
                AgentVersion.id == run.agent_version_id,
                AgentVersion.agent_definition_id == run.agent_definition_id,
            )
        )
        if (
            agent is None
            or agent.lifecycle != AgentLifecycle.ACTIVE
            or agent_version is None
            or key not in run.effective_tool_keys
            or key not in agent_version.tool_keys
            or run.tool_bindings.get(key) != version
            or agent_version.tool_bindings.get(key) != version
        ):
            raise ToolExecutionError("TOOL_NOT_GRANTED")
        user = await db.scalar(
            select(AppUser).where(
                AppUser.tenant_id == context.tenant_id,
                AppUser.id == context.user_id,
                AppUser.status == UserStatus.ACTIVE,
            )
        )
        if (
            user is None
            or await db.get(
                UserAgentGrant, (context.tenant_id, context.user_id, run.agent_definition_id)
            )
            is None
            or await db.get(UserToolGrant, (context.tenant_id, context.user_id, key)) is None
        ):
            raise ToolExecutionError("TOOL_NOT_GRANTED")
        invocation = await db.scalar(
            select(Invocation).where(
                Invocation.tenant_id == context.tenant_id, Invocation.run_id == run.id
            )
        )
        if invocation is None:
            if context.application_id is not None or context.external_user_id is not None:
                raise ToolExecutionError("TOOL_CONTEXT_INVALID")
            return
        if (
            invocation.application_id != context.application_id
            or invocation.user_id != context.user_id
            or invocation.external_user_id != context.external_user_id
            or invocation.decision != InvocationDecision.ALLOWED
            or key not in invocation.effective_tool_keys
        ):
            raise ToolExecutionError("TOOL_CONTEXT_INVALID")
        application = await db.scalar(
            select(CallingApplication).where(
                CallingApplication.tenant_id == context.tenant_id,
                CallingApplication.id == context.application_id,
                CallingApplication.active.is_(True),
            )
        )
        mapping = await db.scalar(
            select(ExternalUserMapping.id).where(
                ExternalUserMapping.tenant_id == context.tenant_id,
                ExternalUserMapping.application_id == context.application_id,
                ExternalUserMapping.external_user_id == context.external_user_id,
                ExternalUserMapping.user_id == context.user_id,
                ExternalUserMapping.active.is_(True),
            )
        )
        if (
            application is None
            or mapping is None
            or await db.get(
                ApplicationAgentGrant,
                (context.tenant_id, context.application_id, run.agent_definition_id),
            )
            is None
            or await db.get(ApplicationToolGrant, (context.tenant_id, context.application_id, key))
            is None
        ):
            raise ToolExecutionError("TOOL_NOT_GRANTED")

    async def _admit(
        self, key: str, version: str, arguments: dict[str, Any]
    ) -> tuple[UUID, ToolDefinition, BaseModel]:
        error: ToolExecutionError | None = None
        definition = TOOL_REGISTRY.get((key, version))
        parsed: BaseModel | None = None
        async with self.factory() as db:
            run = await db.scalar(
                select(TaskRun)
                .where(
                    TaskRun.id == self.context.run_id, TaskRun.tenant_id == self.context.tenant_id
                )
                .with_for_update()
            )
            if run is None:
                raise ToolExecutionError("TOOL_CONTEXT_INVALID")
            invocation = await db.scalar(
                select(Invocation).where(
                    Invocation.tenant_id == run.tenant_id, Invocation.run_id == run.id
                )
            )
            call = ToolCall(
                id=uuid4(),
                tenant_id=run.tenant_id,
                run_id=run.id,
                user_id=run.user_id,
                application_id=invocation.application_id if invocation else None,
                trace_id=run.trace_id,
                tool_key=key[:200],
                tool_version=version[:50],
                status=ToolCallStatus.STARTED,
                input_summary={"field_count": len(arguments), "values": "REDACTED"},
            )
            try:
                await self._authorize(db, run, key, version)
                if definition is None:
                    raise ToolExecutionError("TOOL_VERSION_UNAVAILABLE")
                if not definition.read_only or definition.requires_approval:
                    raise ToolExecutionError("TOOL_APPROVAL_REQUIRED")
                try:
                    parsed = definition.input_model.model_validate(arguments)
                except ValidationError:
                    raise ToolExecutionError("TOOL_INPUT_INVALID") from None
            except ToolExecutionError as exc:
                error = exc
                call.status = ToolCallStatus.DENIED
                call.error_code = exc.code
                call.duration_ms = 0
                call.completed_at = utc_now()
            db.add(call)
            await db.commit()  # No handler can run unless the admission is durable.
        if error is not None:
            await self.emit(
                RuntimeEvent(
                    "tool.failed",
                    {
                        "tool": key,
                        "version": version,
                        "call_id": str(call.id),
                        "error_code": error.code,
                    },
                )
            )
            raise error
        assert definition is not None and parsed is not None
        return call.id, definition, parsed

    async def _complete(
        self,
        call_id: UUID,
        status: ToolCallStatus,
        started: float,
        code: str | None = None,
        output: str | None = None,
    ) -> ToolCallStatus:
        async with self.factory() as db:
            run = await db.scalar(
                select(TaskRun)
                .where(
                    TaskRun.id == self.context.run_id, TaskRun.tenant_id == self.context.tenant_id
                )
                .with_for_update()
            )
            call = await db.scalar(
                select(ToolCall)
                .where(ToolCall.id == call_id, ToolCall.tenant_id == self.context.tenant_id)
                .with_for_update()
            )
            if call is None:
                raise ToolExecutionError("TOOL_AUDIT_MISSING")
            if call.status != ToolCallStatus.STARTED:
                return call.status
            if status == ToolCallStatus.SUCCEEDED and (
                run is None
                or run.status != RunStatus.RUNNING
                or run.dispatch_message_id != self.context.dispatch_message_id
                or run.cancel_requested_at is not None
            ):
                status, code, output = (
                    ToolCallStatus.INTERRUPTED,
                    "TOOL_EXECUTION_INTERRUPTED",
                    None,
                )
            call.status, call.error_code = status, code
            call.duration_ms = min(int((monotonic() - started) * 1000), 2147483647)
            call.completed_at = utc_now()
            if output is not None:
                call.output_summary = {"characters": len(output), "values": "REDACTED"}
            await db.commit()
            return status

    async def execute(self, key: str, version: str, arguments: dict[str, Any]) -> str:
        started = monotonic()
        call_id, definition, parsed = await self._admit(key, version, arguments)
        payload: dict[str, Any] = {"tool": key, "version": version, "call_id": str(call_id)}
        try:
            await self.emit(RuntimeEvent("tool.started", payload))
            async with asyncio.timeout(definition.timeout_seconds):
                output = await definition.handler(self.context, parsed)
            try:
                definition.output_model.model_validate(output)
            except ValidationError:
                raise ToolExecutionError("TOOL_OUTPUT_INVALID") from None
        except (asyncio.CancelledError, RuntimeCancelled):

            async def cleanup() -> None:
                async with asyncio.timeout(5):
                    await self._complete(
                        call_id, ToolCallStatus.CANCELLED, started, "TOOL_CANCELLED"
                    )

            task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(task)
            except Exception:
                logger.warning("tool_cancel_cleanup_failed", call_id=str(call_id))
            finally:
                # Retrieve exceptions even when shutdown issues a second cancellation.
                task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise
        except Exception as exc:
            status = (
                ToolCallStatus.TIMED_OUT if isinstance(exc, TimeoutError) else ToolCallStatus.FAILED
            )
            code = (
                "TOOL_TIMEOUT"
                if isinstance(exc, TimeoutError)
                else exc.code
                if isinstance(exc, ToolExecutionError)
                else "TOOL_EXECUTION_FAILED"
            )
            await self._complete(call_id, status, started, code)
            await self.emit(RuntimeEvent("tool.failed", {**payload, "error_code": code}))
            raise ToolExecutionError(code) from None
        status = await self._complete(call_id, ToolCallStatus.SUCCEEDED, started, output=output)
        if status != ToolCallStatus.SUCCEEDED:
            raise ToolExecutionError("TOOL_EXECUTION_INTERRUPTED")
        await self.emit(RuntimeEvent("tool.completed", {**payload, "status": status.value}))
        return output
