from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.api.deps import get_db, require_admin
from agentplane.application_access_services import (
    create_external_user_mapping,
    get_application_permissions,
    list_external_user_mappings,
    patch_external_user_mapping,
    replace_application_permissions,
)
from agentplane.application_services import (
    IssuedApplicationCredential,
    create_calling_application,
    get_calling_application,
    list_application_credentials,
    list_calling_applications,
    patch_calling_application,
    rotate_application_credential,
)
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import ApplicationCredential, CallingApplication, ExternalUserMapping
from agentplane.schemas import (
    ApplicationCredentialIssued,
    ApplicationCredentialRead,
    ApplicationCredentialRotate,
    ApplicationPermissionsRead,
    ApplicationPermissionsReplace,
    CallingApplicationCreate,
    CallingApplicationCreated,
    CallingApplicationPatch,
    CallingApplicationRead,
    ExternalUserMappingCreate,
    ExternalUserMappingPatch,
    ExternalUserMappingRead,
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


@router.get(
    "/{application_id}/credentials",
    response_model=list[ApplicationCredentialRead],
)
async def application_credentials_list(
    application_id: UUID,
    db: DbDep,
    identity: AdminIdentityDep,
) -> Sequence[ApplicationCredential]:
    return await list_application_credentials(db, identity, application_id)


@router.get(
    "/{application_id}/user-mappings",
    response_model=list[ExternalUserMappingRead],
)
async def application_user_mappings_list(
    application_id: UUID,
    db: DbDep,
    identity: AdminIdentityDep,
) -> Sequence[ExternalUserMapping]:
    return await list_external_user_mappings(db, identity, application_id)


@router.post(
    "/{application_id}/user-mappings",
    response_model=ExternalUserMappingRead,
    status_code=status.HTTP_201_CREATED,
)
async def application_user_mappings_create(
    application_id: UUID,
    payload: ExternalUserMappingCreate,
    db: DbDep,
    identity: AdminIdentityDep,
) -> ExternalUserMapping:
    mapping = await create_external_user_mapping(db, identity, application_id, payload)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(
            409,
            "EXTERNAL_USER_MAPPING_EXISTS",
            "当前应用已存在该外部用户映射",
        ) from exc
    await db.refresh(mapping)
    return mapping


@router.patch(
    "/{application_id}/user-mappings/{mapping_id}",
    response_model=ExternalUserMappingRead,
)
async def application_user_mappings_patch(
    application_id: UUID,
    mapping_id: UUID,
    payload: ExternalUserMappingPatch,
    db: DbDep,
    identity: AdminIdentityDep,
) -> ExternalUserMapping:
    mapping = await patch_external_user_mapping(
        db,
        identity,
        application_id,
        mapping_id,
        payload,
    )
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.get(
    "/{application_id}/permissions",
    response_model=ApplicationPermissionsRead,
)
async def application_permissions_get(
    application_id: UUID,
    db: DbDep,
    identity: AdminIdentityDep,
) -> ApplicationPermissionsRead:
    return await get_application_permissions(db, identity, application_id)


@router.put(
    "/{application_id}/permissions",
    response_model=ApplicationPermissionsRead,
)
async def application_permissions_replace(
    application_id: UUID,
    payload: ApplicationPermissionsReplace,
    db: DbDep,
    identity: AdminIdentityDep,
) -> ApplicationPermissionsRead:
    permissions = await replace_application_permissions(
        db,
        identity,
        application_id,
        payload,
    )
    await db.commit()
    return permissions
