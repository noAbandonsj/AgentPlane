from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.api.deps import get_db, require_admin
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.invocations.service import (
    get_admin_invocation,
    invocation_response,
    list_admin_invocations,
)
from agentplane.models import InvocationDecision
from agentplane.schemas import InvocationAdminStatus, InvocationRead

router = APIRouter(prefix="/api/v1/admin/invocations", tags=["admin-invocations"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminIdentityDep = Annotated[IdentityContext, Depends(require_admin)]


@router.get("", response_model=list[InvocationRead])
async def admin_invocations_list(
    db: DbDep,
    identity: AdminIdentityDep,
    application_id: UUID | None = None,
    external_request_id: Annotated[str | None, Query(max_length=200)] = None,
    external_user_id: Annotated[str | None, Query(max_length=200)] = None,
    agent_id: UUID | None = None,
    decision: InvocationDecision | None = None,
    status: InvocationAdminStatus | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[InvocationRead]:
    if created_from is not None and created_to is not None and created_from > created_to:
        raise ApiError(400, "INVALID_INVOCATION_TIME_RANGE", "开始时间不能晚于结束时间")
    results = await list_admin_invocations(
        db,
        identity,
        application_id=application_id,
        external_request_id=external_request_id,
        external_user_id=external_user_id,
        agent_id=agent_id,
        decision=decision,
        status=status,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )
    return [invocation_response(result) for result in results]


@router.get("/{invocation_id}", response_model=InvocationRead)
async def admin_invocations_get(
    invocation_id: UUID,
    db: DbDep,
    identity: AdminIdentityDep,
) -> InvocationRead:
    return invocation_response(await get_admin_invocation(db, identity, invocation_id))
