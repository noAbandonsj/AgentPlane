from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.config import Settings
from agentplane.db import utc_now
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import (
    AgentDefinition,
    AppUser,
    AuthSession,
    LocalCredential,
    Tenant,
    UserAgentGrant,
    UserRole,
    UserStatus,
    UserToolGrant,
)
from agentplane.schemas import AuthRegister, UserPermissionsRead
from agentplane.security import (
    create_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from agentplane.tools import validate_tool_keys


async def bootstrap_required(db: AsyncSession, tenant_id: UUID) -> bool:
    admin_id = await db.scalar(
        select(AppUser.id).where(
            AppUser.tenant_id == tenant_id,
            AppUser.role == UserRole.ADMIN,
        )
    )
    return admin_id is None


async def _login_exists(db: AsyncSession, tenant_id: UUID, login_name: str) -> bool:
    user_id = await db.scalar(
        select(AppUser.id).where(
            AppUser.tenant_id == tenant_id,
            AppUser.login_name == login_name,
        )
    )
    return user_id is not None


async def create_local_user(
    db: AsyncSession,
    tenant_id: UUID,
    payload: AuthRegister,
    settings: Settings,
    *,
    role: UserRole = UserRole.USER,
    status: UserStatus = UserStatus.PENDING,
) -> AppUser:
    if await _login_exists(db, tenant_id, payload.login_name):
        raise ApiError(409, "LOGIN_NAME_EXISTS", "登录名已被使用")
    user = AppUser(
        id=uuid4(),
        tenant_id=tenant_id,
        login_name=payload.login_name,
        display_name=payload.display_name,
        role=role,
        status=status,
    )
    credential = LocalCredential(
        tenant_id=tenant_id,
        user_id=user.id,
        password_hash=hash_password(payload.password, settings.password_pbkdf2_iterations),
    )
    db.add_all([user, credential])
    await db.flush()
    return user


async def create_bootstrap_admin(
    db: AsyncSession, tenant_id: UUID, payload: AuthRegister, settings: Settings
) -> AppUser:
    await db.execute(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
    if not await bootstrap_required(db, tenant_id):
        raise ApiError(409, "ADMIN_ALREADY_INITIALIZED", "管理员已经初始化")
    return await create_local_user(
        db,
        tenant_id,
        payload,
        settings,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )


async def authenticate_local_user(
    db: AsyncSession, tenant_id: UUID, login_name: str, password: str
) -> AppUser:
    row = (
        await db.execute(
            select(AppUser, LocalCredential)
            .join(
                LocalCredential,
                (LocalCredential.tenant_id == AppUser.tenant_id)
                & (LocalCredential.user_id == AppUser.id),
            )
            .where(
                AppUser.tenant_id == tenant_id,
                AppUser.login_name == login_name,
            )
        )
    ).one_or_none()
    if row is None or not verify_password(password, row.LocalCredential.password_hash):
        raise ApiError(401, "INVALID_CREDENTIALS", "登录名或密码错误")
    user = row.AppUser
    if user.status == UserStatus.PENDING:
        raise ApiError(403, "ACCOUNT_PENDING", "账号正在等待管理员审核")
    if user.status == UserStatus.DISABLED:
        raise ApiError(403, "ACCOUNT_DISABLED", "账号已被停用")
    return user


async def create_login_session(db: AsyncSession, user: AppUser, settings: Settings) -> str:
    token = create_session_token()
    db.add(
        AuthSession(
            tenant_id=user.tenant_id,
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=utc_now() + timedelta(hours=settings.auth_session_hours),
        )
    )
    await db.flush()
    return token


async def revoke_login_session(db: AsyncSession, token: str) -> None:
    auth_session = await db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_session_token(token))
    )
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = utc_now()
        await db.flush()


async def get_user_for_identity(db: AsyncSession, identity: IdentityContext) -> AppUser:
    user = await db.scalar(
        select(AppUser).where(
            AppUser.tenant_id == identity.tenant_id,
            AppUser.id == identity.user_id,
        )
    )
    if user is None:
        raise ApiError(401, "AUTHENTICATION_REQUIRED", "请先登录")
    return user


async def list_tenant_users(db: AsyncSession, identity: IdentityContext) -> Sequence[AppUser]:
    users = await db.scalars(
        select(AppUser)
        .where(AppUser.tenant_id == identity.tenant_id)
        .order_by(AppUser.created_at.asc())
    )
    return users.all()


async def get_tenant_user(db: AsyncSession, identity: IdentityContext, user_id: UUID) -> AppUser:
    user = await db.scalar(
        select(AppUser).where(
            AppUser.tenant_id == identity.tenant_id,
            AppUser.id == user_id,
        )
    )
    if user is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "用户不存在")
    return user


async def update_user_status(
    db: AsyncSession,
    identity: IdentityContext,
    user_id: UUID,
    status: UserStatus,
) -> AppUser:
    user = await get_tenant_user(db, identity, user_id)
    if user.id == identity.user_id and status != UserStatus.ACTIVE:
        raise ApiError(409, "CANNOT_DISABLE_SELF", "不能停用当前管理员账号")
    user.status = status
    if status != UserStatus.ACTIVE:
        await db.execute(
            delete(AuthSession).where(
                AuthSession.tenant_id == identity.tenant_id,
                AuthSession.user_id == user.id,
            )
        )
    await db.flush()
    return user


async def get_user_permissions(
    db: AsyncSession, identity: IdentityContext, user_id: UUID
) -> UserPermissionsRead:
    await get_tenant_user(db, identity, user_id)
    agent_ids = (
        await db.scalars(
            select(UserAgentGrant.agent_definition_id)
            .where(
                UserAgentGrant.tenant_id == identity.tenant_id,
                UserAgentGrant.user_id == user_id,
            )
            .order_by(UserAgentGrant.agent_definition_id.asc())
        )
    ).all()
    tool_keys = (
        await db.scalars(
            select(UserToolGrant.tool_key)
            .where(
                UserToolGrant.tenant_id == identity.tenant_id,
                UserToolGrant.user_id == user_id,
            )
            .order_by(UserToolGrant.tool_key.asc())
        )
    ).all()
    return UserPermissionsRead(
        user_id=user_id,
        agent_ids=list(agent_ids),
        tool_keys=list(tool_keys),
    )


async def replace_user_agent_grants(
    db: AsyncSession,
    identity: IdentityContext,
    user_id: UUID,
    agent_ids: list[UUID],
) -> UserPermissionsRead:
    await get_tenant_user(db, identity, user_id)
    if agent_ids:
        existing = set(
            (
                await db.scalars(
                    select(AgentDefinition.id).where(
                        AgentDefinition.tenant_id == identity.tenant_id,
                        AgentDefinition.id.in_(agent_ids),
                    )
                )
            ).all()
        )
        if existing != set(agent_ids):
            raise ApiError(400, "INVALID_AGENT_GRANT", "包含不存在的 Agent")
    await db.execute(
        delete(UserAgentGrant).where(
            UserAgentGrant.tenant_id == identity.tenant_id,
            UserAgentGrant.user_id == user_id,
        )
    )
    db.add_all(
        [
            UserAgentGrant(
                tenant_id=identity.tenant_id,
                user_id=user_id,
                agent_definition_id=agent_id,
                granted_by=identity.user_id,
            )
            for agent_id in agent_ids
        ]
    )
    await db.flush()
    return await get_user_permissions(db, identity, user_id)


async def replace_user_tool_grants(
    db: AsyncSession,
    identity: IdentityContext,
    user_id: UUID,
    tool_keys: list[str],
) -> UserPermissionsRead:
    await get_tenant_user(db, identity, user_id)
    validate_tool_keys(tool_keys)
    await db.execute(
        delete(UserToolGrant).where(
            UserToolGrant.tenant_id == identity.tenant_id,
            UserToolGrant.user_id == user_id,
        )
    )
    db.add_all(
        [
            UserToolGrant(
                tenant_id=identity.tenant_id,
                user_id=user_id,
                tool_key=tool_key,
                granted_by=identity.user_id,
            )
            for tool_key in tool_keys
        ]
    )
    await db.flush()
    return await get_user_permissions(db, identity, user_id)
