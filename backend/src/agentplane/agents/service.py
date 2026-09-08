from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.db import utc_now
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import (
    AgentDefinition,
    AgentLifecycle,
    AgentVersion,
    UserAgentGrant,
)
from agentplane.schemas import AgentCreate, AgentPatch
from agentplane.tools import snapshot_tool_bindings, validate_tool_keys


def _agent_not_found() -> ApiError:
    return ApiError(404, "RESOURCE_NOT_FOUND", "Agent不存在")


async def list_agents(db: AsyncSession, identity: IdentityContext) -> Sequence[AgentDefinition]:
    result = await db.scalars(
        select(AgentDefinition)
        .join(
            UserAgentGrant,
            (UserAgentGrant.tenant_id == AgentDefinition.tenant_id)
            & (UserAgentGrant.agent_definition_id == AgentDefinition.id),
        )
        .where(
            AgentDefinition.tenant_id == identity.tenant_id,
            UserAgentGrant.user_id == identity.user_id,
        )
        .order_by(AgentDefinition.updated_at.desc())
    )
    return result.all()


async def list_tenant_agents(
    db: AsyncSession, identity: IdentityContext
) -> Sequence[AgentDefinition]:
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
        raise _agent_not_found()
    return agent


async def ensure_agent_access(db: AsyncSession, identity: IdentityContext, agent_id: UUID) -> None:
    grant = await db.scalar(
        select(UserAgentGrant.agent_definition_id).where(
            UserAgentGrant.tenant_id == identity.tenant_id,
            UserAgentGrant.user_id == identity.user_id,
            UserAgentGrant.agent_definition_id == agent_id,
        )
    )
    if grant is None:
        raise ApiError(403, "AGENT_NOT_GRANTED", "当前用户未获得该 Agent 的使用权限")


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
        tool_bindings=snapshot_tool_bindings(agent.draft_tool_keys),
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
