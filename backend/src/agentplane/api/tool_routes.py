from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.api.deps import get_db, require_admin
from agentplane.identity import IdentityContext
from agentplane.models import ToolCallStatus
from agentplane.schemas import ToolCallRead, ToolMetadata, ToolPolicyPatch
from agentplane.tool_management import (
    list_tenant_tools,
    list_tool_calls,
    list_tool_versions,
    set_tool_enabled,
)

router = APIRouter(prefix="/api/v1/admin", tags=["tool-management"])
DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminDep = Annotated[IdentityContext, Depends(require_admin)]


@router.get("/tools", response_model=list[ToolMetadata])
async def tools_list(db: DbDep, identity: AdminDep) -> list[ToolMetadata]:
    return await list_tenant_tools(db, identity.tenant_id)


@router.get("/tools/{tool_key}/versions", response_model=list[ToolMetadata])
async def versions_list(tool_key: str, db: DbDep, identity: AdminDep) -> list[ToolMetadata]:
    return await list_tool_versions(db, identity.tenant_id, tool_key)


@router.patch("/tools/{tool_key}", response_model=ToolMetadata)
async def tool_patch(
    tool_key: str, payload: ToolPolicyPatch, db: DbDep, identity: AdminDep
) -> ToolMetadata:
    result = await set_tool_enabled(db, identity, tool_key, payload.enabled)
    await db.commit()
    return result


@router.get("/tool-calls", response_model=list[ToolCallRead])
async def calls_list(
    db: DbDep,
    identity: AdminDep,
    tool_key: str | None = None,
    run_id: UUID | None = None,
    user_id: UUID | None = None,
    application_id: UUID | None = None,
    status: ToolCallStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
) -> list[ToolCallRead]:
    return [
        ToolCallRead.model_validate(row)
        for row in await list_tool_calls(
            db,
            identity.tenant_id,
            tool_key=tool_key,
            run_id=run_id,
            user_id=user_id,
            application_id=application_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    ]
