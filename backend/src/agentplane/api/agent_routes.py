from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.agents.service import (
    create_agent,
    ensure_agent_access,
    get_agent,
    list_agent_versions,
    list_agents,
    list_tenant_agents,
    patch_agent,
    publish_agent,
)
from agentplane.api.deps import get_db, get_identity, require_admin
from agentplane.identity import IdentityContext
from agentplane.models import AgentDefinition, AgentVersion
from agentplane.schemas import AgentCreate, AgentPatch, AgentRead, AgentVersionRead

router = APIRouter(prefix="/api/v1", tags=["agents"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
IdentityDep = Annotated[IdentityContext, Depends(get_identity)]
AdminIdentityDep = Annotated[IdentityContext, Depends(require_admin)]


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
    await db.commit()
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
    await db.commit()
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
