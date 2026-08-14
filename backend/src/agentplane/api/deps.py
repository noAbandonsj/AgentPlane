from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentplane.applications.service import authenticate_calling_application
from agentplane.config import Settings
from agentplane.db import utc_now
from agentplane.errors import ApiError
from agentplane.identity import ApplicationIdentityContext, IdentityContext
from agentplane.logging import bind_log_context
from agentplane.models import AppUser, AuthSession, UserRole, UserStatus
from agentplane.security import hash_session_token

application_bearer = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def get_identity(request: Request) -> IdentityContext:
    identity = getattr(request.state, "identity", None)
    if isinstance(identity, IdentityContext):
        return identity
    settings = get_app_settings(request)
    if settings.auth_mode != "local":
        raise ApiError(401, "AUTHENTICATION_REQUIRED", "请先登录")
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        raise ApiError(401, "AUTHENTICATION_REQUIRED", "请先登录")
    session_factory = get_session_factory(request)
    async with session_factory() as db:
        row = (
            await db.execute(
                select(AuthSession, AppUser)
                .join(
                    AppUser,
                    (AppUser.tenant_id == AuthSession.tenant_id)
                    & (AppUser.id == AuthSession.user_id),
                )
                .where(
                    AuthSession.token_hash == hash_session_token(token),
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > utc_now(),
                    AppUser.status == UserStatus.ACTIVE,
                )
            )
        ).one_or_none()
    if row is None:
        raise ApiError(401, "AUTHENTICATION_REQUIRED", "登录已失效，请重新登录")
    _auth_session, user = row
    identity = IdentityContext(tenant_id=user.tenant_id, user_id=user.id, role=user.role)
    bind_log_context(tenant_id=identity.tenant_id, user_id=identity.user_id)
    return identity


async def require_admin(request: Request) -> IdentityContext:
    identity = await get_identity(request)
    if identity.role != UserRole.ADMIN:
        raise ApiError(403, "ADMIN_REQUIRED", "需要管理员权限")
    return identity


async def get_application_identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(application_bearer),
) -> ApplicationIdentityContext:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise ApiError(401, "APPLICATION_AUTHENTICATION_REQUIRED", "请提供调用应用凭据")
    session_factory = get_session_factory(request)
    async with session_factory() as db:
        identity = await authenticate_calling_application(db, credentials.credentials)
    bind_log_context(
        tenant_id=identity.tenant_id,
        application_id=identity.application_id,
        credential_id=identity.credential_id,
    )
    return identity


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory(request)
    async with session_factory() as session:
        yield session
