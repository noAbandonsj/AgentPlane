from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentplane.api.deps import (
    get_app_settings,
    get_db,
    get_identity,
    get_redis,
    get_session_factory,
    require_admin,
)
from agentplane.api.event_stream import run_event_stream
from agentplane.config import Settings
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import AgentDefinition, AgentVersion, ChatSession, SessionMessage, TaskRun
from agentplane.queue import notify_run_event
from agentplane.schemas import (
    AgentCreate,
    AgentPatch,
    AgentRead,
    AgentVersionRead,
    CapabilityResponse,
    HealthResponse,
    MessageRead,
    RunCreate,
    RunRead,
    SessionCreate,
    SessionRead,
    ToolMetadata,
)
from agentplane.services import (
    cancel_run,
    create_agent,
    create_chat_session,
    create_run,
    ensure_agent_access,
    get_agent,
    get_chat_session,
    get_run,
    list_agent_versions,
    list_agents,
    list_messages,
    list_sessions,
    list_tenant_agents,
    patch_agent,
    publish_agent,
)
from agentplane.tools import list_tool_metadata

router = APIRouter(prefix="/api/v1")

DbDep = Annotated[AsyncSession, Depends(get_db)]
IdentityDep = Annotated[IdentityContext, Depends(get_identity)]
AdminIdentityDep = Annotated[IdentityContext, Depends(require_admin)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


@router.get("/agents", response_model=list[AgentRead])
async def agents_list(db: DbDep, identity: IdentityDep) -> Sequence[AgentDefinition]:
    return await list_agents(db, identity)


@router.get("/admin/agents", response_model=list[AgentRead])
async def admin_agents_list(db: DbDep, identity: AdminIdentityDep) -> Sequence[AgentDefinition]:
    return await list_tenant_agents(db, identity)


@router.post("/agents", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def agents_create(
    payload: AgentCreate, db: DbDep, identity: AdminIdentityDep
) -> AgentDefinition:
    agent = await create_agent(db, identity, payload)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "AGENT_NAME_EXISTS", "当前租户已存在同名 Agent") from exc
    await db.refresh(agent)
    return agent


@router.get("/agents/{agent_id}", response_model=AgentRead)
async def agents_get(agent_id: UUID, db: DbDep, identity: IdentityDep) -> AgentDefinition:
    agent = await get_agent(db, identity, agent_id)
    await ensure_agent_access(db, identity, agent.id)
    return agent


@router.patch("/agents/{agent_id}", response_model=AgentRead)
async def agents_patch(
    agent_id: UUID, payload: AgentPatch, db: DbDep, identity: AdminIdentityDep
) -> AgentDefinition:
    agent = await patch_agent(db, identity, agent_id, payload)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "AGENT_NAME_EXISTS", "当前租户已存在同名 Agent") from exc
    await db.refresh(agent)
    return agent


@router.post(
    "/agents/{agent_id}/publish",
    response_model=AgentVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def agents_publish(agent_id: UUID, db: DbDep, identity: AdminIdentityDep) -> AgentVersion:
    version = await publish_agent(db, identity, agent_id)
    await db.commit()
    await db.refresh(version)
    return version


@router.get("/agents/{agent_id}/versions", response_model=list[AgentVersionRead])
async def agents_versions(
    agent_id: UUID, db: DbDep, identity: AdminIdentityDep
) -> Sequence[AgentVersion]:
    return await list_agent_versions(db, identity, agent_id)


@router.get("/tools", response_model=list[ToolMetadata])
async def tools_list(_identity: IdentityDep) -> list[ToolMetadata]:
    return list_tool_metadata()


@router.get("/sessions", response_model=list[SessionRead])
async def sessions_list(db: DbDep, identity: IdentityDep) -> Sequence[ChatSession]:
    return await list_sessions(db, identity)


@router.post("/sessions", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def sessions_create(payload: SessionCreate, db: DbDep, identity: IdentityDep) -> ChatSession:
    chat_session = await create_chat_session(db, identity, payload)
    await db.commit()
    await db.refresh(chat_session)
    return chat_session


@router.get("/sessions/{session_id}", response_model=SessionRead)
async def sessions_get(session_id: UUID, db: DbDep, identity: IdentityDep) -> ChatSession:
    return await get_chat_session(db, identity, session_id)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageRead])
async def sessions_messages(
    session_id: UUID, db: DbDep, identity: IdentityDep
) -> Sequence[SessionMessage]:
    return await list_messages(db, identity, session_id)


@router.post(
    "/sessions/{session_id}/runs", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED
)
async def runs_create(
    session_id: UUID,
    payload: RunCreate,
    db: DbDep,
    identity: IdentityDep,
    settings: SettingsDep,
) -> TaskRun:
    run = await create_run(db, identity, session_id, payload, settings)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "ACTIVE_RUN_EXISTS", "当前会话已有运行中的 Run") from exc
    await db.refresh(run)
    return run


@router.get("/runs/{run_id}", response_model=RunRead)
async def runs_get(run_id: UUID, db: DbDep, identity: IdentityDep) -> TaskRun:
    return await get_run(db, identity, run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunRead)
async def runs_cancel(
    run_id: UUID,
    db: DbDep,
    identity: IdentityDep,
    redis: RedisDep,
) -> TaskRun:
    run, event = await cancel_run(db, identity, run_id)
    await db.commit()
    await db.refresh(run)
    if event is not None:
        await notify_run_event(redis, run.id, event.sequence)
    return run


@router.get("/runs/{run_id}/events", response_class=StreamingResponse)
async def runs_events(
    run_id: UUID,
    request: Request,
    db: DbDep,
    identity: IdentityDep,
    settings: SettingsDep,
    redis: RedisDep,
    session_factory: SessionFactoryDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    await get_run(db, identity, run_id)
    try:
        start_sequence = max(int(last_event_id or "0"), 0)
    except ValueError as exc:
        raise ApiError(400, "INVALID_LAST_EVENT_ID", "Last-Event-ID 必须是整数") from exc
    return StreamingResponse(
        run_event_stream(
            request,
            session_factory,
            redis,
            identity,
            run_id,
            start_sequence,
            settings,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/health/live", response_model=HealthResponse)
async def health_live() -> HealthResponse:
    return HealthResponse(status="ok", checks={"process": "ok"})


@router.get("/health/ready", response_model=HealthResponse)
async def health_ready(db: DbDep, redis: RedisDep) -> HealthResponse:
    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["postgresql"] = "ok"
    except Exception:
        checks["postgresql"] = "unavailable"
    try:
        checks["redis"] = "ok" if await redis.ping() else "unavailable"
    except Exception:
        checks["redis"] = "unavailable"
    if all(value == "ok" for value in checks.values()):
        return HealthResponse(status="ok", checks=checks)
    return HealthResponse(status="degraded", checks=checks)


@router.get("/capabilities", response_model=CapabilityResponse)
async def capabilities(settings: SettingsDep, _identity: IdentityDep) -> CapabilityResponse:
    return CapabilityResponse(
        runtime="langgraph",
        model_configured=settings.model_configured,
        model_aliases=["default"] if settings.model_configured else [],
        tools=list_tool_metadata(),
        approval_resume_supported=False,
    )


@router.get("/openapi-version")
async def openapi_version(response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"version": "v1"}
