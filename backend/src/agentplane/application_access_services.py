from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.application_services import get_calling_application
from agentplane.db import utc_now
from agentplane.errors import ApiError
from agentplane.identity import (
    ApplicationIdentityContext,
    IdentityContext,
    RepresentedUserContext,
)
from agentplane.models import (
    AgentDefinition,
    ApplicationAgentGrant,
    ApplicationToolGrant,
    AppUser,
    CallingApplication,
    ExternalUserMapping,
    UserStatus,
)
from agentplane.schemas import (
    ApplicationPermissionsRead,
    ApplicationPermissionsReplace,
    ExternalUserMappingCreate,
    ExternalUserMappingPatch,
)
from agentplane.tools import validate_tool_keys


def _mapping_not_found() -> ApiError:
    return ApiError(404, "EXTERNAL_USER_MAPPING_NOT_FOUND", "外部用户映射不存在")


async def _ensure_tenant_user(db: AsyncSession, tenant_id: UUID, user_id: UUID) -> AppUser:
    user = await db.scalar(
        select(AppUser).where(
            AppUser.tenant_id == tenant_id,
            AppUser.id == user_id,
        )
    )
    if user is None:
        raise ApiError(400, "INVALID_EXTERNAL_USER_MAPPING", "映射用户不属于当前租户")
    return user


async def list_external_user_mappings(
    db: AsyncSession,
    identity: IdentityContext,
    application_id: UUID,
) -> Sequence[ExternalUserMapping]:
    await get_calling_application(db, identity, application_id)
    mappings = await db.scalars(
        select(ExternalUserMapping)
        .where(
            ExternalUserMapping.tenant_id == identity.tenant_id,
            ExternalUserMapping.application_id == application_id,
        )
        .order_by(ExternalUserMapping.created_at.asc())
    )
    return mappings.all()


async def create_external_user_mapping(
    db: AsyncSession,
    identity: IdentityContext,
    application_id: UUID,
    payload: ExternalUserMappingCreate,
) -> ExternalUserMapping:
    await get_calling_application(db, identity, application_id)
    await _ensure_tenant_user(db, identity.tenant_id, payload.user_id)
    duplicate = await db.scalar(
        select(ExternalUserMapping.id).where(
            ExternalUserMapping.tenant_id == identity.tenant_id,
            ExternalUserMapping.application_id == application_id,
            ExternalUserMapping.external_user_id == payload.external_user_id,
        )
    )
    if duplicate is not None:
        raise ApiError(409, "EXTERNAL_USER_MAPPING_EXISTS", "当前应用已存在该外部用户映射")
    mapping = ExternalUserMapping(
        tenant_id=identity.tenant_id,
        application_id=application_id,
        external_user_id=payload.external_user_id,
        user_id=payload.user_id,
        created_by=identity.user_id,
        updated_by=identity.user_id,
    )
    db.add(mapping)
    await db.flush()
    return mapping


async def patch_external_user_mapping(
    db: AsyncSession,
    identity: IdentityContext,
    application_id: UUID,
    mapping_id: UUID,
    payload: ExternalUserMappingPatch,
) -> ExternalUserMapping:
    await get_calling_application(db, identity, application_id)
    mapping = await db.scalar(
        select(ExternalUserMapping)
        .where(
            ExternalUserMapping.tenant_id == identity.tenant_id,
            ExternalUserMapping.application_id == application_id,
            ExternalUserMapping.id == mapping_id,
        )
        .with_for_update()
    )
    if mapping is None:
        raise _mapping_not_found()
    changes = payload.model_dump(exclude_unset=True)
    user_id = changes.get("user_id")
    if user_id is not None:
        await _ensure_tenant_user(db, identity.tenant_id, user_id)
    for field, value in changes.items():
        setattr(mapping, field, value)
    mapping.updated_by = identity.user_id
    mapping.updated_at = utc_now()
    await db.flush()
    return mapping


async def resolve_external_user(
    db: AsyncSession,
    application_identity: ApplicationIdentityContext,
    external_user_id: str,
) -> RepresentedUserContext:
    normalized_external_user_id = external_user_id.strip()
    application_active = await db.scalar(
        select(CallingApplication.active).where(
            CallingApplication.tenant_id == application_identity.tenant_id,
            CallingApplication.id == application_identity.application_id,
        )
    )
    if application_active is not True:
        raise ApiError(403, "APPLICATION_DISABLED", "调用应用已停用")
    row = (
        await db.execute(
            select(ExternalUserMapping, AppUser)
            .join(
                AppUser,
                (AppUser.tenant_id == ExternalUserMapping.tenant_id)
                & (AppUser.id == ExternalUserMapping.user_id),
            )
            .where(
                ExternalUserMapping.tenant_id == application_identity.tenant_id,
                ExternalUserMapping.application_id == application_identity.application_id,
                ExternalUserMapping.external_user_id == normalized_external_user_id,
                ExternalUserMapping.active.is_(True),
            )
        )
    ).one_or_none()
    if row is None:
        raise ApiError(403, "EXTERNAL_USER_NOT_MAPPED", "外部用户未建立有效映射")
    mapping, user = row
    if user.status != UserStatus.ACTIVE:
        raise ApiError(403, "REPRESENTED_USER_DISABLED", "被代表用户未启用")
    return RepresentedUserContext(
        tenant_id=mapping.tenant_id,
        application_id=mapping.application_id,
        user_id=user.id,
        external_user_id=mapping.external_user_id,
    )


async def get_application_permissions(
    db: AsyncSession,
    identity: IdentityContext,
    application_id: UUID,
) -> ApplicationPermissionsRead:
    await get_calling_application(db, identity, application_id)
    agent_ids = (
        await db.scalars(
            select(ApplicationAgentGrant.agent_definition_id)
            .where(
                ApplicationAgentGrant.tenant_id == identity.tenant_id,
                ApplicationAgentGrant.application_id == application_id,
            )
            .order_by(ApplicationAgentGrant.agent_definition_id.asc())
        )
    ).all()
    tool_keys = (
        await db.scalars(
            select(ApplicationToolGrant.tool_key)
            .where(
                ApplicationToolGrant.tenant_id == identity.tenant_id,
                ApplicationToolGrant.application_id == application_id,
            )
            .order_by(ApplicationToolGrant.tool_key.asc())
        )
    ).all()
    return ApplicationPermissionsRead(
        application_id=application_id,
        agent_ids=list(agent_ids),
        tool_keys=list(tool_keys),
    )


async def replace_application_permissions(
    db: AsyncSession,
    identity: IdentityContext,
    application_id: UUID,
    payload: ApplicationPermissionsReplace,
) -> ApplicationPermissionsRead:
    await get_calling_application(db, identity, application_id, for_update=True)
    if payload.agent_ids:
        existing_agent_ids = set(
            (
                await db.scalars(
                    select(AgentDefinition.id).where(
                        AgentDefinition.tenant_id == identity.tenant_id,
                        AgentDefinition.id.in_(payload.agent_ids),
                    )
                )
            ).all()
        )
        if existing_agent_ids != set(payload.agent_ids):
            raise ApiError(
                400,
                "INVALID_APPLICATION_AGENT_GRANT",
                "应用授权包含不存在的 Agent",
            )
    validate_tool_keys(payload.tool_keys)

    await db.execute(
        delete(ApplicationAgentGrant).where(
            ApplicationAgentGrant.tenant_id == identity.tenant_id,
            ApplicationAgentGrant.application_id == application_id,
        )
    )
    await db.execute(
        delete(ApplicationToolGrant).where(
            ApplicationToolGrant.tenant_id == identity.tenant_id,
            ApplicationToolGrant.application_id == application_id,
        )
    )
    db.add_all(
        [
            ApplicationAgentGrant(
                tenant_id=identity.tenant_id,
                application_id=application_id,
                agent_definition_id=agent_id,
                granted_by=identity.user_id,
            )
            for agent_id in payload.agent_ids
        ]
    )
    db.add_all(
        [
            ApplicationToolGrant(
                tenant_id=identity.tenant_id,
                application_id=application_id,
                tool_key=tool_key,
                granted_by=identity.user_id,
            )
            for tool_key in payload.tool_keys
        ]
    )
    await db.flush()
    return await get_application_permissions(db, identity, application_id)
