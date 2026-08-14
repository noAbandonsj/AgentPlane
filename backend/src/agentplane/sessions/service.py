from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.agents.service import ensure_agent_access, get_agent
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import (
    AgentLifecycle,
    AgentVersion,
    ChatSession,
    MessageRole,
    RunStatus,
    SessionMessage,
    TaskRun,
)
from agentplane.schemas import SessionCreate


def _session_not_found() -> ApiError:
    return ApiError(404, "RESOURCE_NOT_FOUND", "Session不存在")


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
        raise _session_not_found()
    return chat_session


async def create_chat_session(
    db: AsyncSession, identity: IdentityContext, payload: SessionCreate
) -> ChatSession:
    agent = await get_agent(db, identity, payload.agent_id)
    await ensure_agent_access(db, identity, agent.id)
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
