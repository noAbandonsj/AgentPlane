from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.access.service import (
    get_user_permissions,
    list_tenant_users,
    replace_user_agent_grants,
    replace_user_tool_grants,
    update_user_status,
)
from agentplane.api.deps import get_db, require_admin
from agentplane.identity import IdentityContext
from agentplane.models import AppUser
from agentplane.schemas import (
    AgentGrantReplace,
    ToolGrantReplace,
    UserPermissionsRead,
    UserPermissionsReplace,
    UserRead,
    UserStatusPatch,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminIdentityDep = Annotated[IdentityContext, Depends(require_admin)]


@router.get("/users", response_model=list[UserRead])
async def admin_users_list(db: DbDep, identity: AdminIdentityDep) -> Sequence[AppUser]:
    return await list_tenant_users(db, identity)


@router.patch("/users/{user_id}/status", response_model=UserRead)
async def admin_user_status(
    user_id: UUID,
    payload: UserStatusPatch,
    db: DbDep,
    identity: AdminIdentityDep,
) -> AppUser:
    user = await update_user_status(db, identity, user_id, payload.status)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/users/{user_id}/permissions", response_model=UserPermissionsRead)
async def admin_user_permissions(
    user_id: UUID, db: DbDep, identity: AdminIdentityDep
) -> UserPermissionsRead:
    return await get_user_permissions(db, identity, user_id)


@router.put("/users/{user_id}/agent-grants", response_model=UserPermissionsRead)
async def admin_user_agent_grants(
    user_id: UUID,
    payload: AgentGrantReplace,
    db: DbDep,
    identity: AdminIdentityDep,
) -> UserPermissionsRead:
    permissions = await replace_user_agent_grants(db, identity, user_id, payload.agent_ids)
    await db.commit()
    return permissions


@router.put("/users/{user_id}/tool-grants", response_model=UserPermissionsRead)
async def admin_user_tool_grants(
    user_id: UUID,
    payload: ToolGrantReplace,
    db: DbDep,
    identity: AdminIdentityDep,
) -> UserPermissionsRead:
    permissions = await replace_user_tool_grants(db, identity, user_id, payload.tool_keys)
    await db.commit()
    return permissions


@router.put("/users/{user_id}/permissions", response_model=UserPermissionsRead)
async def admin_user_permissions_replace(
    user_id: UUID,
    payload: UserPermissionsReplace,
    db: DbDep,
    identity: AdminIdentityDep,
) -> UserPermissionsRead:
    await replace_user_agent_grants(db, identity, user_id, payload.agent_ids)
    permissions = await replace_user_tool_grants(db, identity, user_id, payload.tool_keys)
    await db.commit()
    return permissions
