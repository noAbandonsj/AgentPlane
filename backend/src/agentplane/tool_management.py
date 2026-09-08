from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import Tenant, TenantToolPolicy, ToolCall, ToolCallStatus
from agentplane.schemas import ToolMetadata
from agentplane.tools import TOOL_REGISTRY, list_tool_metadata, validate_tool_keys


async def list_tenant_tools(db: AsyncSession, tenant_id: UUID) -> list[ToolMetadata]:
    policies = {
        row.tool_key: row.enabled
        for row in await db.scalars(
            select(TenantToolPolicy).where(TenantToolPolicy.tenant_id == tenant_id)
        )
    }
    return [
        item.model_copy(update={"enabled": policies.get(item.key, True)})
        for item in list_tool_metadata()
    ]


async def list_tool_versions(db: AsyncSession, tenant_id: UUID, key: str) -> list[ToolMetadata]:
    validate_tool_keys([key])
    policy = await db.get(TenantToolPolicy, (tenant_id, key))
    return [
        definition.metadata().model_copy(update={"enabled": policy.enabled if policy else True})
        for (tool_key, _version), definition in TOOL_REGISTRY.items()
        if tool_key == key
    ]


async def set_tool_enabled(
    db: AsyncSession, identity: IdentityContext, key: str, enabled: bool
) -> ToolMetadata:
    validate_tool_keys([key])
    # Serialize first policy creation, including against concurrent execution admission.
    tenant = await db.scalar(
        select(Tenant).where(Tenant.id == identity.tenant_id).with_for_update()
    )
    if tenant is None or not tenant.active:
        raise ApiError(403, "TENANT_DISABLED", "租户不可用")
    policy = await db.get(TenantToolPolicy, (identity.tenant_id, key))
    if policy is None:
        policy = TenantToolPolicy(
            tenant_id=identity.tenant_id, tool_key=key, enabled=enabled, updated_by=identity.user_id
        )
        db.add(policy)
    else:
        policy.enabled = enabled
        policy.updated_by = identity.user_id
    await db.flush()
    return next(item for item in await list_tenant_tools(db, identity.tenant_id) if item.key == key)


async def list_tool_calls(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    tool_key: str | None = None,
    run_id: UUID | None = None,
    user_id: UUID | None = None,
    application_id: UUID | None = None,
    status: ToolCallStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[ToolCall]:
    statement = select(ToolCall).where(ToolCall.tenant_id == tenant_id)
    for column, value in (
        (ToolCall.tool_key, tool_key),
        (ToolCall.run_id, run_id),
        (ToolCall.user_id, user_id),
        (ToolCall.application_id, application_id),
        (ToolCall.status, status),
    ):
        if value is not None:
            statement = statement.where(column == value)
    return (
        await db.scalars(
            statement.order_by(ToolCall.created_at.desc(), ToolCall.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
