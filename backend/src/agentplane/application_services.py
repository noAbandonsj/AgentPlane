from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.db import utc_now
from agentplane.errors import ApiError
from agentplane.identity import ApplicationIdentityContext, IdentityContext
from agentplane.models import ApplicationCredential, CallingApplication
from agentplane.schemas import CallingApplicationCreate, CallingApplicationPatch
from agentplane.security import (
    application_token_matches,
    create_application_token,
    hash_application_token,
    parse_application_token,
)


@dataclass(frozen=True, slots=True)
class IssuedApplicationCredential:
    credential: ApplicationCredential
    token: str


def _not_found() -> ApiError:
    return ApiError(404, "APPLICATION_NOT_FOUND", "调用应用不存在")


def _credential_is_expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False
    comparison_now = now if expires_at.tzinfo is not None else now.replace(tzinfo=None)
    return expires_at <= comparison_now


async def list_calling_applications(
    db: AsyncSession, identity: IdentityContext
) -> Sequence[CallingApplication]:
    applications = await db.scalars(
        select(CallingApplication)
        .where(CallingApplication.tenant_id == identity.tenant_id)
        .order_by(CallingApplication.created_at.desc())
    )
    return applications.all()


async def get_calling_application(
    db: AsyncSession,
    identity: IdentityContext,
    application_id: UUID,
    *,
    for_update: bool = False,
) -> CallingApplication:
    statement = select(CallingApplication).where(
        CallingApplication.id == application_id,
        CallingApplication.tenant_id == identity.tenant_id,
    )
    if for_update:
        statement = statement.with_for_update()
    application = await db.scalar(statement)
    if application is None:
        raise _not_found()
    return application


async def _issue_application_credential(
    db: AsyncSession,
    application: CallingApplication,
    created_by: UUID,
    expires_at: datetime | None,
) -> IssuedApplicationCredential:
    credential_id = uuid4()
    token = create_application_token(credential_id)
    credential = ApplicationCredential(
        id=credential_id,
        tenant_id=application.tenant_id,
        application_id=application.id,
        token_hash=hash_application_token(token),
        token_prefix=token[:16],
        expires_at=expires_at,
        created_by=created_by,
    )
    db.add(credential)
    await db.flush()
    return IssuedApplicationCredential(credential=credential, token=token)


async def create_calling_application(
    db: AsyncSession,
    identity: IdentityContext,
    payload: CallingApplicationCreate,
) -> tuple[CallingApplication, IssuedApplicationCredential]:
    duplicate = await db.scalar(
        select(CallingApplication.id).where(
            CallingApplication.tenant_id == identity.tenant_id,
            CallingApplication.code == payload.code,
        )
    )
    if duplicate is not None:
        raise ApiError(409, "APPLICATION_CODE_EXISTS", "当前租户已存在同编码调用应用")
    application = CallingApplication(
        tenant_id=identity.tenant_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        created_by=identity.user_id,
        updated_by=identity.user_id,
    )
    db.add(application)
    await db.flush()
    issued = await _issue_application_credential(
        db,
        application,
        identity.user_id,
        payload.credential_expires_at,
    )
    return application, issued


async def patch_calling_application(
    db: AsyncSession,
    identity: IdentityContext,
    application_id: UUID,
    payload: CallingApplicationPatch,
) -> CallingApplication:
    application = await get_calling_application(db, identity, application_id, for_update=True)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(application, field, value)
    application.updated_by = identity.user_id
    application.updated_at = utc_now()
    await db.flush()
    return application


async def rotate_application_credential(
    db: AsyncSession,
    identity: IdentityContext,
    application_id: UUID,
    expires_at: datetime | None,
) -> IssuedApplicationCredential:
    application = await get_calling_application(db, identity, application_id, for_update=True)
    now = utc_now()
    await db.execute(
        update(ApplicationCredential)
        .where(
            ApplicationCredential.tenant_id == identity.tenant_id,
            ApplicationCredential.application_id == application.id,
            ApplicationCredential.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    return await _issue_application_credential(
        db,
        application,
        identity.user_id,
        expires_at,
    )


async def authenticate_calling_application(
    db: AsyncSession, token: str
) -> ApplicationIdentityContext:
    credential_id = parse_application_token(token)
    if credential_id is None:
        raise ApiError(401, "INVALID_APPLICATION_CREDENTIAL", "调用应用凭据无效")
    row = (
        await db.execute(
            select(ApplicationCredential, CallingApplication)
            .join(
                CallingApplication,
                (CallingApplication.tenant_id == ApplicationCredential.tenant_id)
                & (CallingApplication.id == ApplicationCredential.application_id),
            )
            .where(ApplicationCredential.id == credential_id)
        )
    ).one_or_none()
    if row is None:
        raise ApiError(401, "INVALID_APPLICATION_CREDENTIAL", "调用应用凭据无效")
    credential, application = row
    now = utc_now()
    if (
        not application_token_matches(token, credential.token_hash)
        or credential.revoked_at is not None
        or _credential_is_expired(credential.expires_at, now)
    ):
        raise ApiError(401, "INVALID_APPLICATION_CREDENTIAL", "调用应用凭据无效")
    if not application.active:
        raise ApiError(403, "APPLICATION_DISABLED", "调用应用已停用")
    return ApplicationIdentityContext(
        tenant_id=application.tenant_id,
        application_id=application.id,
        credential_id=credential.id,
        application_code=application.code,
    )
