from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.access_services import (
    authenticate_local_user,
    bootstrap_required,
    create_bootstrap_admin,
    create_local_user,
    create_login_session,
    get_user_for_identity,
    revoke_login_session,
)
from agentplane.api.deps import get_app_settings, get_db, get_identity
from agentplane.config import Settings
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import AppUser
from agentplane.schemas import AuthLogin, AuthRegister, BootstrapStatus, UserRead

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
IdentityDep = Annotated[IdentityContext, Depends(get_identity)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def _require_local_auth(settings: Settings) -> None:
    if settings.auth_mode != "local":
        raise ApiError(409, "LOCAL_AUTH_DISABLED", "当前未启用本地登录模式")


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_session_hours * 3600,
        httponly=True,
        secure=settings.app_env.lower() == "production",
        samesite="lax",
        path="/",
    )


@router.get("/bootstrap-status", response_model=BootstrapStatus)
async def auth_bootstrap_status(db: DbDep, settings: SettingsDep) -> BootstrapStatus:
    if settings.auth_mode != "local":
        return BootstrapStatus(required=False)
    return BootstrapStatus(required=await bootstrap_required(db, settings.dev_tenant_id))


@router.post(
    "/bootstrap-admin",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def auth_bootstrap_admin(
    payload: AuthRegister,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> AppUser:
    _require_local_auth(settings)
    try:
        user = await create_bootstrap_admin(db, settings.dev_tenant_id, payload, settings)
        token = await create_login_session(db, user, settings)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "LOGIN_NAME_EXISTS", "登录名已被使用") from exc
    await db.refresh(user)
    _set_session_cookie(response, settings, token)
    return user


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def auth_register(payload: AuthRegister, db: DbDep, settings: SettingsDep) -> AppUser:
    _require_local_auth(settings)
    try:
        user = await create_local_user(db, settings.dev_tenant_id, payload, settings)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "LOGIN_NAME_EXISTS", "登录名已被使用") from exc
    await db.refresh(user)
    return user


@router.post("/login", response_model=UserRead)
async def auth_login(
    payload: AuthLogin,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> AppUser:
    _require_local_auth(settings)
    user = await authenticate_local_user(
        db, settings.dev_tenant_id, payload.login_name, payload.password
    )
    token = await create_login_session(db, user, settings)
    await db.commit()
    _set_session_cookie(response, settings, token)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def auth_logout(request: Request, db: DbDep, settings: SettingsDep) -> Response:
    token = request.cookies.get(settings.auth_cookie_name)
    if token:
        await revoke_login_session(db, token)
        await db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(settings.auth_cookie_name, path="/")
    return response


@router.get("/me", response_model=UserRead)
async def auth_me(db: DbDep, identity: IdentityDep, settings: SettingsDep) -> UserRead:
    user = UserRead.model_validate(await get_user_for_identity(db, identity))
    if settings.auth_mode == "dev":
        return user.model_copy(update={"role": identity.role})
    return user
