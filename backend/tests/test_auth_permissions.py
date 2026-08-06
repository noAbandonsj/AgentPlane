from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.access_services import (
    authenticate_local_user,
    create_local_user,
    replace_user_agent_grants,
    replace_user_tool_grants,
    update_user_status,
)
from agentplane.config import Settings
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import AppUser, RunStatus, Tenant, UserRole, UserStatus
from agentplane.schemas import AgentCreate, AuthRegister, RunCreate, SessionCreate
from agentplane.services import (
    create_agent,
    create_chat_session,
    create_run,
    list_agents,
    mark_run_failed,
    mark_run_started,
    publish_agent,
)


async def _normal_user(
    db: AsyncSession, identity: IdentityContext, settings: Settings
) -> tuple[AppUser, IdentityContext]:
    user = await create_local_user(
        db,
        identity.tenant_id,
        AuthRegister(login_name="normal-user", display_name="普通用户", password="secret1"),
        settings,
    )
    await update_user_status(db, identity, user.id, UserStatus.ACTIVE)
    await db.commit()
    return user, IdentityContext(
        tenant_id=identity.tenant_id,
        user_id=user.id,
        role=UserRole.USER,
    )


async def test_pending_user_cannot_login_until_admin_activates(
    db: AsyncSession,
    identity: IdentityContext,
    configured_settings: Settings,
) -> None:
    user = await create_local_user(
        db,
        identity.tenant_id,
        AuthRegister(login_name="waiting-user", display_name="待审核用户", password="secret1"),
        configured_settings,
    )
    await db.commit()

    with pytest.raises(ApiError) as pending:
        await authenticate_local_user(db, identity.tenant_id, "waiting-user", "secret1")
    assert pending.value.code == "ACCOUNT_PENDING"

    await update_user_status(db, identity, user.id, UserStatus.ACTIVE)
    await db.commit()
    authenticated = await authenticate_local_user(db, identity.tenant_id, "waiting-user", "secret1")
    assert authenticated.id == user.id


async def test_agent_and_tool_grants_are_enforced_and_snapshotted(
    db: AsyncSession,
    identity: IdentityContext,
    configured_settings: Settings,
) -> None:
    user, user_identity = await _normal_user(db, identity, configured_settings)
    agent = await create_agent(
        db,
        identity,
        AgentCreate(
            name="授权测试助手",
            instructions="按授权使用工具",
            tool_keys=["calculator.add"],
        ),
    )
    await publish_agent(db, identity, agent.id)
    await db.commit()

    assert await list_agents(db, user_identity) == []
    with pytest.raises(ApiError) as denied:
        await create_chat_session(
            db,
            user_identity,
            SessionCreate(agent_id=agent.id, title="无权限会话"),
        )
    assert denied.value.code == "AGENT_NOT_GRANTED"

    await replace_user_agent_grants(db, identity, user.id, [agent.id])
    await replace_user_tool_grants(db, identity, user.id, ["calculator.add"])
    await db.commit()

    assert [item.id for item in await list_agents(db, user_identity)] == [agent.id]
    chat_session = await create_chat_session(
        db,
        user_identity,
        SessionCreate(agent_id=agent.id, title="授权会话"),
    )
    await db.commit()
    run = await create_run(
        db,
        user_identity,
        chat_session.id,
        RunCreate(input="计算"),
        configured_settings,
    )
    await db.commit()
    assert run.effective_tool_keys == ["calculator.add"]

    await replace_user_tool_grants(db, identity, user.id, [])
    await db.commit()
    await db.refresh(run)
    assert run.effective_tool_keys == ["calculator.add"]

    await mark_run_started(db, run)
    await mark_run_failed(db, run, "TEST_DONE", "结束测试")
    await replace_user_agent_grants(db, identity, user.id, [])
    await db.commit()
    assert run.status == RunStatus.FAILED
    with pytest.raises(ApiError) as revoked:
        await create_run(
            db,
            user_identity,
            chat_session.id,
            RunCreate(input="再次执行"),
            configured_settings,
        )
    assert revoked.value.code == "AGENT_NOT_GRANTED"


async def test_grant_management_rejects_cross_tenant_resources(
    db: AsyncSession,
    identity: IdentityContext,
    configured_settings: Settings,
) -> None:
    user, _user_identity = await _normal_user(db, identity, configured_settings)
    other_tenant_id = uuid4()
    other_user_id = uuid4()
    db.add_all(
        [
            Tenant(id=other_tenant_id, name="其他租户"),
            AppUser(
                id=other_user_id,
                tenant_id=other_tenant_id,
                login_name="other-admin",
                display_name="其他租户管理员",
                role=UserRole.ADMIN,
                status=UserStatus.ACTIVE,
            ),
        ]
    )
    await db.flush()
    other_identity = IdentityContext(
        tenant_id=other_tenant_id,
        user_id=other_user_id,
        role=UserRole.ADMIN,
    )
    other_agent = await create_agent(
        db,
        other_identity,
        AgentCreate(name="其他租户助手", instructions="隔离测试"),
    )
    await db.commit()

    with pytest.raises(ApiError) as invalid_agent:
        await replace_user_agent_grants(db, identity, user.id, [other_agent.id])
    assert invalid_agent.value.code == "INVALID_AGENT_GRANT"

    with pytest.raises(ApiError) as hidden_user:
        await replace_user_tool_grants(db, identity, other_user_id, ["calculator.add"])
    assert hidden_user.value.code == "RESOURCE_NOT_FOUND"
