from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.agents.service import (
    create_agent,
    get_agent,
    list_agent_versions,
    patch_agent,
    publish_agent,
)
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import AppUser, Tenant, UserAgentGrant, UserStatus
from agentplane.schemas import AgentCreate, AgentPatch, SessionCreate
from agentplane.sessions.service import create_chat_session


async def test_published_version_is_immutable_and_session_pins_version(
    db: AsyncSession,
    identity: IdentityContext,
) -> None:
    agent = await create_agent(
        db,
        identity,
        AgentCreate(
            name="企业助手",
            description="第一版测试",
            instructions="你是第一版助手",
            tool_keys=["calculator.add"],
        ),
    )
    version_one = await publish_agent(db, identity, agent.id)
    db.add(
        UserAgentGrant(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_definition_id=agent.id,
            granted_by=identity.user_id,
        )
    )
    session = await create_chat_session(
        db,
        identity,
        SessionCreate(agent_id=agent.id, title="版本固定测试"),
    )

    await patch_agent(
        db,
        identity,
        agent.id,
        AgentPatch(instructions="你是第二版助手", tool_keys=[]),
    )
    version_two = await publish_agent(db, identity, agent.id)
    await db.commit()

    assert version_one.version_number == 1
    assert version_one.instructions == "你是第一版助手"
    assert version_one.tool_keys == ["calculator.add"]
    assert version_two.version_number == 2
    assert version_two.instructions == "你是第二版助手"
    assert session.agent_version_id == version_one.id

    versions = await list_agent_versions(db, identity, agent.id)
    assert [version.version_number for version in versions] == [2, 1]


async def test_tenant_filter_hides_other_tenant_agent(
    db: AsyncSession,
    identity: IdentityContext,
) -> None:
    agent = await create_agent(
        db,
        identity,
        AgentCreate(name="租户隔离助手", instructions="只属于租户一"),
    )
    other_tenant_id = uuid4()
    other_user_id = uuid4()
    db.add_all(
        [
            Tenant(id=other_tenant_id, name="其他租户"),
            AppUser(
                id=other_user_id,
                tenant_id=other_tenant_id,
                login_name="other-user",
                display_name="其他用户",
                status=UserStatus.ACTIVE,
            ),
        ]
    )
    await db.commit()

    with pytest.raises(ApiError) as caught:
        await get_agent(
            db,
            IdentityContext(tenant_id=other_tenant_id, user_id=other_user_id),
            agent.id,
        )

    assert caught.value.status_code == 404
    assert caught.value.code == "RESOURCE_NOT_FOUND"
