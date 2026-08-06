from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.config import Settings
from agentplane.db import utc_now
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import (
    AgentDefinition,
    AgentLifecycle,
    AgentVersion,
    ChatSession,
    MessageRole,
    OutboxEvent,
    RunEvent,
    RunStatus,
    SessionMessage,
    SessionStatus,
    TaskRun,
)
from agentplane.schemas import AgentCreate, AgentPatch, RunCreate, SessionCreate
from agentplane.tools import validate_tool_keys

ACTIVE_RUN_STATUSES = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_APPROVAL,
)


def _not_found(resource: str) -> ApiError:
    return ApiError(404, "RESOURCE_NOT_FOUND", f"{resource}不存在")


async def list_agents(db: AsyncSession, identity: IdentityContext) -> Sequence[AgentDefinition]:
    result = await db.scalars(
        select(AgentDefinition)
        .where(AgentDefinition.tenant_id == identity.tenant_id)
        .order_by(AgentDefinition.updated_at.desc())
    )
    return result.all()


async def get_agent(
    db: AsyncSession,
    identity: IdentityContext,
    agent_id: UUID,
    *,
    for_update: bool = False,
) -> AgentDefinition:
    statement: Select[tuple[AgentDefinition]] = select(AgentDefinition).where(
        AgentDefinition.id == agent_id,
        AgentDefinition.tenant_id == identity.tenant_id,
    )
    if for_update:
        statement = statement.with_for_update()
    agent = await db.scalar(statement)
    if agent is None:
        raise _not_found("Agent")
    return agent


async def create_agent(
    db: AsyncSession, identity: IdentityContext, payload: AgentCreate
) -> AgentDefinition:
    validate_tool_keys(payload.tool_keys)
    duplicate = await db.scalar(
        select(AgentDefinition.id).where(
            AgentDefinition.tenant_id == identity.tenant_id,
            AgentDefinition.name == payload.name,
        )
    )
    if duplicate is not None:
        raise ApiError(409, "AGENT_NAME_EXISTS", "当前租户已存在同名 Agent")
    agent = AgentDefinition(
        tenant_id=identity.tenant_id,
        name=payload.name,
        description=payload.description,
        draft_instructions=payload.instructions,
        draft_model_alias=payload.model_alias,
        draft_tool_keys=payload.tool_keys,
        created_by=identity.user_id,
        updated_by=identity.user_id,
    )
    db.add(agent)
    await db.flush()
    return agent


async def patch_agent(
    db: AsyncSession,
    identity: IdentityContext,
    agent_id: UUID,
    payload: AgentPatch,
) -> AgentDefinition:
    agent = await get_agent(db, identity, agent_id, for_update=True)
    changes = payload.model_dump(exclude_unset=True)
    if "tool_keys" in changes:
        validate_tool_keys(changes["tool_keys"])
    mapping = {
        "instructions": "draft_instructions",
        "model_alias": "draft_model_alias",
        "tool_keys": "draft_tool_keys",
    }
    if "name" in changes and changes["name"] != agent.name:
        duplicate = await db.scalar(
            select(AgentDefinition.id).where(
                AgentDefinition.tenant_id == identity.tenant_id,
                AgentDefinition.name == changes["name"],
                AgentDefinition.id != agent.id,
            )
        )
        if duplicate is not None:
            raise ApiError(409, "AGENT_NAME_EXISTS", "当前租户已存在同名 Agent")
    for field, value in changes.items():
        setattr(agent, mapping.get(field, field), value)
    agent.updated_by = identity.user_id
    agent.updated_at = utc_now()
    await db.flush()
    return agent


async def publish_agent(
    db: AsyncSession, identity: IdentityContext, agent_id: UUID
) -> AgentVersion:
    agent = await get_agent(db, identity, agent_id, for_update=True)
    if agent.lifecycle != AgentLifecycle.ACTIVE:
        raise ApiError(409, "AGENT_ARCHIVED", "已归档 Agent 不能发布")
    validate_tool_keys(agent.draft_tool_keys)
    max_version = await db.scalar(
        select(func.max(AgentVersion.version_number)).where(
            AgentVersion.agent_definition_id == agent.id
        )
    )
    version = AgentVersion(
        tenant_id=identity.tenant_id,
        agent_definition_id=agent.id,
        version_number=(max_version or 0) + 1,
        name=agent.name,
        description=agent.description,
        instructions=agent.draft_instructions,
        model_alias=agent.draft_model_alias,
        tool_keys=list(agent.draft_tool_keys),
        published_by=identity.user_id,
    )
    db.add(version)
    await db.flush()
    agent.latest_published_version_id = version.id
    agent.updated_by = identity.user_id
    agent.updated_at = utc_now()
    return version


async def list_agent_versions(
    db: AsyncSession, identity: IdentityContext, agent_id: UUID
) -> Sequence[AgentVersion]:
    await get_agent(db, identity, agent_id)
    versions = await db.scalars(
        select(AgentVersion)
        .where(
            AgentVersion.tenant_id == identity.tenant_id,
            AgentVersion.agent_definition_id == agent_id,
        )
        .order_by(AgentVersion.version_number.desc())
    )
    return versions.all()


async def list_sessions(db: AsyncSession, identity: IdentityContext) -> Sequence[ChatSession]:
    sessions = await db.scalars(
        select(ChatSession)
        .where(
            ChatSession.tenant_id == identity.tenant_id,
            ChatSession.user_id == identity.user_id,
        )
        .order_by(ChatSession.updated_at.desc())
    )
    return sessions.all()


async def get_chat_session(
    db: AsyncSession,
    identity: IdentityContext,
    session_id: UUID,
    *,
    for_update: bool = False,
) -> ChatSession:
    statement: Select[tuple[ChatSession]] = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.tenant_id == identity.tenant_id,
        ChatSession.user_id == identity.user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    chat_session = await db.scalar(statement)
    if chat_session is None:
        raise _not_found("Session")
    return chat_session


async def create_chat_session(
    db: AsyncSession, identity: IdentityContext, payload: SessionCreate
) -> ChatSession:
    agent = await get_agent(db, identity, payload.agent_id)
    if agent.lifecycle != AgentLifecycle.ACTIVE or agent.latest_published_version_id is None:
        raise ApiError(409, "AGENT_NOT_PUBLISHED", "Agent 尚未发布可用版本")
    version = await db.scalar(
        select(AgentVersion).where(
            AgentVersion.id == agent.latest_published_version_id,
            AgentVersion.tenant_id == identity.tenant_id,
        )
    )
    if version is None:
        raise ApiError(409, "AGENT_VERSION_MISSING", "Agent 发布版本不存在")
    chat_session = ChatSession(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        agent_definition_id=agent.id,
        agent_version_id=version.id,
        title=payload.title.strip(),
    )
    db.add(chat_session)
    await db.flush()
    return chat_session


async def list_messages(
    db: AsyncSession, identity: IdentityContext, session_id: UUID
) -> Sequence[SessionMessage]:
    await get_chat_session(db, identity, session_id)
    messages = await db.scalars(
        select(SessionMessage)
        .where(
            SessionMessage.tenant_id == identity.tenant_id,
            SessionMessage.session_id == session_id,
        )
        .order_by(SessionMessage.sequence.asc())
    )
    return messages.all()


async def list_runtime_history(db: AsyncSession, run: TaskRun) -> Sequence[SessionMessage]:
    messages = await db.scalars(
        select(SessionMessage)
        .join(TaskRun, SessionMessage.run_id == TaskRun.id)
        .where(
            SessionMessage.tenant_id == run.tenant_id,
            SessionMessage.session_id == run.session_id,
            SessionMessage.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
            TaskRun.status == RunStatus.SUCCEEDED,
        )
        .order_by(SessionMessage.sequence.asc())
    )
    return messages.all()


async def next_message_sequence(db: AsyncSession, session_id: UUID) -> int:
    maximum = await db.scalar(
        select(func.max(SessionMessage.sequence)).where(SessionMessage.session_id == session_id)
    )
    return (maximum or 0) + 1


async def create_run(
    db: AsyncSession,
    identity: IdentityContext,
    session_id: UUID,
    payload: RunCreate,
    settings: Settings,
) -> TaskRun:
    if not settings.model_configured:
        raise ApiError(503, "MODEL_NOT_CONFIGURED", "模型接口尚未配置")
    chat_session = await get_chat_session(db, identity, session_id, for_update=True)
    if chat_session.status != SessionStatus.ACTIVE:
        raise ApiError(409, "SESSION_ARCHIVED", "已归档会话不能创建 Run")
    active_run = await db.scalar(
        select(TaskRun.id).where(
            TaskRun.tenant_id == identity.tenant_id,
            TaskRun.session_id == session_id,
            TaskRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    )
    if active_run is not None:
        raise ApiError(409, "ACTIVE_RUN_EXISTS", "当前会话已有运行中的 Run")

    run = TaskRun(
        id=uuid4(),
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        session_id=chat_session.id,
        agent_definition_id=chat_session.agent_definition_id,
        agent_version_id=chat_session.agent_version_id,
        input_text=payload.input,
        model_name=settings.model_name,
    )
    message = SessionMessage(
        tenant_id=identity.tenant_id,
        session_id=chat_session.id,
        run_id=run.id,
        sequence=await next_message_sequence(db, chat_session.id),
        role=MessageRole.USER,
        content=payload.input,
    )
    outbox = OutboxEvent(
        tenant_id=identity.tenant_id,
        aggregate_type="TaskRun",
        aggregate_id=run.id,
        event_type="run.queued",
        payload={"run_id": str(run.id), "tenant_id": str(identity.tenant_id)},
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
        raise _not_found("Run")
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
        raise _not_found("Run")
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
