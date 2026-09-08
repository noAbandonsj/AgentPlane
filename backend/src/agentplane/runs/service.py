from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.agents.service import ensure_agent_access
from agentplane.config import Settings
from agentplane.db import utc_now
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import (
    AgentVersion,
    ChatSession,
    MessageRole,
    OutboxEvent,
    RunEvent,
    RunStatus,
    SessionMessage,
    SessionStatus,
    TaskRun,
    UserToolGrant,
)
from agentplane.schemas import RunCreate
from agentplane.sessions.service import get_chat_session, next_message_sequence

ACTIVE_RUN_STATUSES = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_APPROVAL,
)


def _run_not_found() -> ApiError:
    return ApiError(404, "RESOURCE_NOT_FOUND", "Run不存在")


async def create_run(
    db: AsyncSession,
    identity: IdentityContext,
    session_id: UUID,
    payload: RunCreate,
    settings: Settings,
) -> TaskRun:
    chat_session = await get_chat_session(db, identity, session_id, for_update=True)
    await ensure_agent_access(db, identity, chat_session.agent_definition_id)
    granted_tool_keys = set(
        (
            await db.scalars(
                select(UserToolGrant.tool_key).where(
                    UserToolGrant.tenant_id == identity.tenant_id,
                    UserToolGrant.user_id == identity.user_id,
                )
            )
        ).all()
    )
    return await _create_run_for_session(
        db,
        chat_session,
        identity.user_id,
        payload.input,
        settings,
        granted_tool_keys,
    )


async def create_authorized_run(
    db: AsyncSession,
    identity: IdentityContext,
    session_id: UUID,
    payload: RunCreate,
    settings: Settings,
    effective_tool_keys: list[str],
) -> TaskRun:
    chat_session = await get_chat_session(db, identity, session_id, for_update=True)
    return await _create_run_for_session(
        db,
        chat_session,
        identity.user_id,
        payload.input,
        settings,
        set(effective_tool_keys),
    )


async def _create_run_for_session(
    db: AsyncSession,
    chat_session: ChatSession,
    user_id: UUID,
    input_text: str,
    settings: Settings,
    granted_tool_keys: set[str],
) -> TaskRun:
    if chat_session.status != SessionStatus.ACTIVE:
        raise ApiError(409, "SESSION_ARCHIVED", "已归档会话不能创建 Run")
    active_run = await db.scalar(
        select(TaskRun.id).where(
            TaskRun.tenant_id == chat_session.tenant_id,
            TaskRun.session_id == chat_session.id,
            TaskRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    )
    if active_run is not None:
        raise ApiError(409, "ACTIVE_RUN_EXISTS", "当前会话已有运行中的 Run")
    if not settings.model_configured:
        raise ApiError(503, "MODEL_NOT_CONFIGURED", "模型接口尚未配置")
    version = await db.scalar(
        select(AgentVersion).where(
            AgentVersion.id == chat_session.agent_version_id,
            AgentVersion.tenant_id == chat_session.tenant_id,
        )
    )
    if version is None:
        raise ApiError(409, "AGENT_VERSION_MISSING", "Agent 发布版本不存在")
    effective_tool_keys = [key for key in version.tool_keys if key in granted_tool_keys]
    run = TaskRun(
        id=uuid4(),
        tenant_id=chat_session.tenant_id,
        user_id=user_id,
        session_id=chat_session.id,
        agent_definition_id=chat_session.agent_definition_id,
        agent_version_id=chat_session.agent_version_id,
        input_text=input_text,
        effective_tool_keys=effective_tool_keys,
        tool_bindings={
            key: version.tool_bindings[key]
            for key in effective_tool_keys
            if key in version.tool_bindings
        },
        model_name=settings.model_name,
    )
    message = SessionMessage(
        tenant_id=chat_session.tenant_id,
        session_id=chat_session.id,
        run_id=run.id,
        sequence=await next_message_sequence(db, chat_session.id),
        role=MessageRole.USER,
        content=input_text,
    )
    outbox = OutboxEvent(
        tenant_id=chat_session.tenant_id,
        aggregate_type="TaskRun",
        aggregate_id=run.id,
        event_type="run.queued",
        payload={"run_id": str(run.id), "tenant_id": str(chat_session.tenant_id)},
    )
    db.add_all([run, message, outbox])
    chat_session.updated_at = utc_now()
    await db.flush()
    return run


async def get_run(
    db: AsyncSession,
    identity: IdentityContext,
    run_id: UUID,
    *,
    for_update: bool = False,
) -> TaskRun:
    statement: Select[tuple[TaskRun]] = select(TaskRun).where(
        TaskRun.id == run_id,
        TaskRun.tenant_id == identity.tenant_id,
        TaskRun.user_id == identity.user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    run = await db.scalar(statement)
    if run is None:
        raise _run_not_found()
    return run


async def get_run_for_worker(
    db: AsyncSession, run_id: UUID, *, for_update: bool = False
) -> TaskRun | None:
    statement: Select[tuple[TaskRun]] = select(TaskRun).where(TaskRun.id == run_id)
    if for_update:
        statement = statement.with_for_update()
    return await db.scalar(statement)


async def append_run_event(
    db: AsyncSession,
    run: TaskRun,
    event_type: str,
    payload: dict[str, object] | None = None,
) -> RunEvent:
    locked_run = await db.scalar(select(TaskRun).where(TaskRun.id == run.id).with_for_update())
    if locked_run is None:
        raise _run_not_found()
    maximum = await db.scalar(select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id))
    event = RunEvent(
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=(maximum or 0) + 1,
        event_type=event_type,
        payload=payload or {},
        trace_id=run.trace_id,
    )
    db.add(event)
    await db.flush()
    return event


async def list_run_events_after(
    db: AsyncSession,
    identity: IdentityContext,
    run_id: UUID,
    after_sequence: int,
) -> Sequence[RunEvent]:
    await get_run(db, identity, run_id)
    events = await db.scalars(
        select(RunEvent)
        .where(
            RunEvent.tenant_id == identity.tenant_id,
            RunEvent.run_id == run_id,
            RunEvent.sequence > after_sequence,
        )
        .order_by(RunEvent.sequence.asc())
    )
    return events.all()


async def cancel_run(
    db: AsyncSession, identity: IdentityContext, run_id: UUID
) -> tuple[TaskRun, RunEvent | None]:
    run = await get_run(db, identity, run_id, for_update=True)
    if run.status.is_terminal:
        return run, None
    now = utc_now()
    run.cancel_requested_at = now
    if run.status in {RunStatus.QUEUED, RunStatus.WAITING_APPROVAL}:
        run.status = RunStatus.CANCELLED
        run.completed_at = now
        event = await append_run_event(db, run, "run.cancelled", {"reason": "user_requested"})
        return run, event
    return run, None


async def mark_run_started(db: AsyncSession, run: TaskRun) -> RunEvent | None:
    if run.status == RunStatus.QUEUED:
        run.status = RunStatus.RUNNING
        run.started_at = run.started_at or utc_now()
        run.attempt_count += 1
        return await append_run_event(db, run, "run.started", {"attempt": run.attempt_count})
    return None


async def mark_run_succeeded(
    db: AsyncSession,
    run: TaskRun,
    output: str,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> RunEvent:
    run.status = RunStatus.SUCCEEDED
    run.output_text = output
    run.input_tokens = input_tokens
    run.output_tokens = output_tokens
    run.completed_at = utc_now()
    message = SessionMessage(
        tenant_id=run.tenant_id,
        session_id=run.session_id,
        run_id=run.id,
        sequence=await next_message_sequence(db, run.session_id),
        role=MessageRole.ASSISTANT,
        content=output,
        message_metadata={
            "model": run.model_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
    db.add(message)
    return await append_run_event(
        db,
        run,
        "run.completed",
        {"output": output, "input_tokens": input_tokens, "output_tokens": output_tokens},
    )


async def mark_run_failed(db: AsyncSession, run: TaskRun, code: str, message: str) -> RunEvent:
    run.status = RunStatus.FAILED
    run.error_code = code
    run.error_message = message[:4000]
    run.completed_at = utc_now()
    return await append_run_event(
        db, run, "run.failed", {"code": code, "message": run.error_message}
    )


async def mark_run_cancelled(db: AsyncSession, run: TaskRun) -> RunEvent:
    run.status = RunStatus.CANCELLED
    run.completed_at = utc_now()
    return await append_run_event(db, run, "run.cancelled", {"reason": "user_requested"})


def run_is_cancel_requested(run: TaskRun) -> bool:
    return run.cancel_requested_at is not None or run.status == RunStatus.CANCELLED


def terminal_at(status: RunStatus) -> datetime | None:
    return utc_now() if status.is_terminal else None
