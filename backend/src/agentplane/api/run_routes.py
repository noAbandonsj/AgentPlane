from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentplane.api.deps import (
    get_app_settings,
    get_db,
    get_identity,
    get_redis,
    get_session_factory,
)
from agentplane.api.event_stream import run_event_stream
from agentplane.config import Settings
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import TaskRun
from agentplane.queue import notify_run_event
from agentplane.runs.service import cancel_run, create_run, get_run
from agentplane.schemas import RunCreate, RunRead

router = APIRouter(prefix="/api/v1", tags=["runs"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
IdentityDep = Annotated[IdentityContext, Depends(get_identity)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


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
    await db.commit()
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
