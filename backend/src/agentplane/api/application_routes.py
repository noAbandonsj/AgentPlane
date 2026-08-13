from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.api.deps import get_db, require_admin
from agentplane.application_services import (
    IssuedApplicationCredential,
    create_calling_application,
    get_calling_application,
    list_calling_applications,
    patch_calling_application,
    rotate_application_credential,
)
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import CallingApplication
from agentplane.schemas import (
    ApplicationCredentialIssued,
    ApplicationCredentialRotate,
    CallingApplicationCreate,
    CallingApplicationCreated,
    CallingApplicationPatch,
    CallingApplicationRead,
)

router = APIRouter(prefix="/api/v1/admin/applications", tags=["applications"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminIdentityDep = Annotated[IdentityContext, Depends(require_admin)]


def _issued_response(issued: IssuedApplicationCredential) -> ApplicationCredentialIssued:
    return ApplicationCredentialIssued(
        id=issued.credential.id,
        token=issued.token,
        token_prefix=issued.credential.token_prefix,
        expires_at=issued.credential.expires_at,
        created_at=issued.credential.created_at,
    )


@router.get("", response_model=list[CallingApplicationRead])
async def applications_list(db: DbDep, identity: AdminIdentityDep) -> Sequence[CallingApplication]:
    return await list_calling_applications(db, identity)


@router.post("", response_model=CallingApplicationCreated, status_code=status.HTTP_201_CREATED)
async def applications_create(
    payload: CallingApplicationCreate,
    db: DbDep,
    identity: AdminIdentityDep,
) -> CallingApplicationCreated:
    application, issued = await create_calling_application(db, identity, payload)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "APPLICATION_CODE_EXISTS", "当前租户已存在同编码调用应用") from exc
    await db.refresh(application)
    await db.refresh(issued.credential)
    return CallingApplicationCreated(
        application=CallingApplicationRead.model_validate(application),
        credential=_issued_response(issued),
    )


@router.get("/{application_id}", response_model=CallingApplicationRead)
async def applications_get(
    application_id: UUID,
    db: DbDep,
    identity: AdminIdentityDep,
) -> CallingApplication:
    return await get_calling_application(db, identity, application_id)


@router.patch("/{application_id}", response_model=CallingApplicationRead)
async def applications_patch(
    application_id: UUID,
    payload: CallingApplicationPatch,
    db: DbDep,
    identity: AdminIdentityDep,
) -> CallingApplication:
    application = await patch_calling_application(db, identity, application_id, payload)
    await db.commit()
    await db.refresh(application)
    return application


@router.post(
    "/{application_id}/credentials/rotate",
    response_model=ApplicationCredentialIssued,
    status_code=status.HTTP_201_CREATED,
)
async def application_credentials_rotate(
    application_id: UUID,
    payload: ApplicationCredentialRotate,
    db: DbDep,
    identity: AdminIdentityDep,
) -> ApplicationCredentialIssued:
    issued = await rotate_application_credential(
        db,
        identity,
        application_id,
        payload.expires_at,
    )
    await db.commit()
    await db.refresh(issued.credential)
    return _issued_response(issued)
