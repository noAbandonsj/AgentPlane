from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.identity import ApplicationIdentityContext, RepresentedUserContext
from agentplane.models import AppUser, CallingApplication, ExternalUserMapping, UserStatus


@dataclass(frozen=True, slots=True)
class ExternalUserResolution:
    external_user_id: str
    represented_user: RepresentedUserContext | None = None
    mapped_user_id: UUID | None = None
    denial_code: str | None = None
    denial_message: str | None = None

    @property
    def allowed(self) -> bool:
        return self.represented_user is not None


async def resolve_external_user_result(
    db: AsyncSession,
    application_identity: ApplicationIdentityContext,
    external_user_id: str,
    *,
    lock_application: bool = False,
) -> ExternalUserResolution:
    normalized_external_user_id = external_user_id.strip()
    application_statement: Select[tuple[CallingApplication]] = select(CallingApplication).where(
        CallingApplication.tenant_id == application_identity.tenant_id,
        CallingApplication.id == application_identity.application_id,
    )
    if lock_application:
        application_statement = application_statement.with_for_update()
    application = await db.scalar(application_statement)
    if application is None or not application.active:
        return ExternalUserResolution(
            external_user_id=normalized_external_user_id,
            denial_code="APPLICATION_DISABLED",
            denial_message="调用应用已停用",
        )

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
        return ExternalUserResolution(
            external_user_id=normalized_external_user_id,
            denial_code="EXTERNAL_USER_NOT_MAPPED",
            denial_message="外部用户未建立有效映射",
        )
    mapping, user = row
    if user.status != UserStatus.ACTIVE:
        return ExternalUserResolution(
            external_user_id=normalized_external_user_id,
            mapped_user_id=user.id,
            denial_code="REPRESENTED_USER_DISABLED",
            denial_message="被代表用户未启用",
        )
    return ExternalUserResolution(
        external_user_id=normalized_external_user_id,
        mapped_user_id=user.id,
        represented_user=RepresentedUserContext(
            tenant_id=mapping.tenant_id,
            application_id=mapping.application_id,
            user_id=user.id,
            external_user_id=mapping.external_user_id,
        ),
    )
