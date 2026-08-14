from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentplane.api.deps import (
    get_app_settings,
    get_application_identity,
    get_db,
    get_redis,
    get_session_factory,
)
from agentplane.api.event_stream import run_event_stream
from agentplane.config import Settings
from agentplane.errors import ApiError, is_integrity_constraint
from agentplane.identity import ApplicationIdentityContext, IdentityContext
from agentplane.invocations.service import (
    create_invocation,
    get_invocation,
    invocation_denial_error,
    invocation_response,
    recover_idempotent_invocation,
)
from agentplane.logging import bind_log_context
from agentplane.models import InvocationDecision
from agentplane.schemas import InvocationCreate, InvocationRead

router = APIRouter(prefix="/api/v1/invocations", tags=["invocations"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
ApplicationIdentityDep = Annotated[
    ApplicationIdentityContext,
    Depends(get_application_identity),
]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


@router.post("", response_model=InvocationRead, status_code=status.HTTP_202_ACCEPTED)
async def invocations_create(
    payload: InvocationCreate,
    db: DbDep,
    identity: ApplicationIdentityDep,
    settings: SettingsDep,
) -> InvocationRead:
    try:
        result = await create_invocation(db, identity, payload, settings)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if not is_integrity_constraint(exc, "uq_invocations_application_external_request"):
            raise
        recovered = await recover_idempotent_invocation(db, identity, payload)
        if recovered is None:
            raise
        result = recovered
    bind_log_context(
        invocation_id=result.invocation.id,
        external_request_id=result.invocation.external_request_id,
        user_id=result.invocation.user_id,
        run_id=result.invocation.run_id,
    )
    await db.refresh(result.invocation)
    if result.run is not None:
        await db.refresh(result.run)
    if result.invocation.decision == InvocationDecision.DENIED:
        raise invocation_denial_error(result.invocation)
    return invocation_response(result)


@router.get("/{invocation_id}", response_model=InvocationRead)
async def invocations_get(
    invocation_id: UUID,
    db: DbDep,
    identity: ApplicationIdentityDep,
) -> InvocationRead:
    return invocation_response(await get_invocation(db, identity, invocation_id))


@router.get("/{invocation_id}/events", response_class=StreamingResponse)
async def invocation_events(
    invocation_id: UUID,
    request: Request,
    db: DbDep,
    identity: ApplicationIdentityDep,
    settings: SettingsDep,
    redis: RedisDep,
    session_factory: SessionFactoryDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    result = await get_invocation(db, identity, invocation_id)
    invocation = result.invocation
    if invocation.decision == InvocationDecision.DENIED or result.run is None:
        raise ApiError(
            409,
            "INVOCATION_REJECTED",
            "被拒绝的 Invocation 没有运行事件",
            {"invocation_id": str(invocation.id)},
        )
    try:
        start_sequence = max(int(last_event_id or "0"), 0)
    except ValueError as exc:
        raise ApiError(400, "INVALID_LAST_EVENT_ID", "Last-Event-ID 必须是整数") from exc
    if invocation.user_id is None:
        raise ApiError(409, "INVOCATION_USER_MISSING", "Invocation 缺少被代表用户")
    represented_identity = IdentityContext(
        tenant_id=identity.tenant_id,
        user_id=invocation.user_id,
    )
    return StreamingResponse(
        run_event_stream(
            request,
            session_factory,
            redis,
            represented_identity,
            result.run.id,
            start_sequence,
            settings,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
