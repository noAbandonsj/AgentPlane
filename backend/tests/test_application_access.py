from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from agentplane.api.app import create_app
from agentplane.application_access_services import resolve_external_user
from agentplane.config import Settings
from agentplane.db import Base
from agentplane.errors import ApiError
from agentplane.identity import ApplicationIdentityContext
from agentplane.models import (
    AgentDefinition,
    AppUser,
    CallingApplication,
    Tenant,
    UserRole,
    UserStatus,
)

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
ADMIN_ID = UUID("00000000-0000-0000-0000-000000000001")
ACTIVE_USER_ID = UUID("00000000-0000-0000-0000-000000000002")
DISABLED_USER_ID = UUID("00000000-0000-0000-0000-000000000003")
AGENT_ID = UUID("00000000-0000-0000-0000-000000000010")


async def _create_environment() -> tuple[
    AsyncEngine, async_sessionmaker[AsyncSession], AsyncClient
]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add_all(
            [
                Tenant(id=TENANT_ID, name="应用访问测试租户"),
                AppUser(
                    id=ADMIN_ID,
                    tenant_id=TENANT_ID,
                    login_name="test-admin",
                    display_name="测试管理员",
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                ),
                AppUser(
                    id=ACTIVE_USER_ID,
                    tenant_id=TENANT_ID,
                    login_name="active-user",
                    display_name="有效用户",
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                ),
                AppUser(
                    id=DISABLED_USER_ID,
                    tenant_id=TENANT_ID,
                    login_name="disabled-user",
                    display_name="停用用户",
                    role=UserRole.USER,
                    status=UserStatus.DISABLED,
                ),
                AgentDefinition(
                    id=AGENT_ID,
                    tenant_id=TENANT_ID,
                    name="应用授权测试 Agent",
                    draft_instructions="用于测试应用授权",
                    created_by=ADMIN_ID,
                    updated_by=ADMIN_ID,
                ),
            ]
        )
        await db.commit()

    settings = Settings(
        app_env="test",
        auth_mode="dev",
        dev_tenant_id=TENANT_ID,
        dev_user_id=ADMIN_ID,
    )
    app = create_app(settings)
    app.state.session_factory = session_factory
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return engine, session_factory, client


async def _create_application(client: AsyncClient, code: str = "crm") -> dict[str, object]:
    response = await client.post(
        "/api/v1/admin/applications",
        json={"code": code, "name": f"{code.upper()} 调用端"},
    )
    assert response.status_code == 201
    return response.json()["application"]


async def test_external_user_mapping_lifecycle_and_resolution() -> None:
    engine, session_factory, client = await _create_environment()
    try:
        application = await _create_application(client)
        application_id = str(application["id"])
        collection_url = f"/api/v1/admin/applications/{application_id}/user-mappings"

        created = await client.post(
            collection_url,
            json={"external_user_id": "  CRM-User-001  ", "user_id": str(ACTIVE_USER_ID)},
        )
        assert created.status_code == 201
        mapping = created.json()
        assert mapping["external_user_id"] == "CRM-User-001"
        assert mapping["user_id"] == str(ACTIVE_USER_ID)
        assert mapping["active"] is True

        listed = await client.get(collection_url)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [mapping["id"]]

        duplicate = await client.post(
            collection_url,
            json={"external_user_id": "CRM-User-001", "user_id": str(ACTIVE_USER_ID)},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "EXTERNAL_USER_MAPPING_EXISTS"

        identity = ApplicationIdentityContext(
            tenant_id=TENANT_ID,
            application_id=UUID(application_id),
            credential_id=uuid4(),
            application_code="crm",
        )
        async with session_factory() as db:
            represented = await resolve_external_user(db, identity, "  CRM-User-001  ")
            assert represented.tenant_id == TENANT_ID
            assert represented.application_id == UUID(application_id)
            assert represented.user_id == ACTIVE_USER_ID
            assert represented.external_user_id == "CRM-User-001"

        disabled = await client.patch(
            f"{collection_url}/{mapping['id']}",
            json={"active": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["active"] is False

        async with session_factory() as db:
            with pytest.raises(ApiError) as inactive_mapping:
                await resolve_external_user(db, identity, "CRM-User-001")
            assert inactive_mapping.value.code == "EXTERNAL_USER_NOT_MAPPED"

        remapped = await client.patch(
            f"{collection_url}/{mapping['id']}",
            json={"user_id": str(DISABLED_USER_ID), "active": True},
        )
        assert remapped.status_code == 200
        assert remapped.json()["user_id"] == str(DISABLED_USER_ID)

        async with session_factory() as db:
            with pytest.raises(ApiError) as disabled_user:
                await resolve_external_user(db, identity, "CRM-User-001")
            assert disabled_user.value.code == "REPRESENTED_USER_DISABLED"

        await client.patch(
            f"/api/v1/admin/applications/{application_id}",
            json={"active": False},
        )
        async with session_factory() as db:
            with pytest.raises(ApiError) as disabled_application:
                await resolve_external_user(db, identity, "CRM-User-001")
            assert disabled_application.value.code == "APPLICATION_DISABLED"
    finally:
        await client.aclose()
        await engine.dispose()


async def test_application_permissions_replace_and_validation_are_atomic() -> None:
    engine, _session_factory, client = await _create_environment()
    try:
        application = await _create_application(client)
        permissions_url = f"/api/v1/admin/applications/{application['id']}/permissions"

        empty = await client.get(permissions_url)
        assert empty.status_code == 200
        assert empty.json() == {
            "application_id": application["id"],
            "agent_ids": [],
            "tool_keys": [],
        }

        replaced = await client.put(
            permissions_url,
            json={
                "agent_ids": [str(AGENT_ID), str(AGENT_ID)],
                "tool_keys": ["calculator.add", "calculator.add"],
            },
        )
        assert replaced.status_code == 200
        assert replaced.json() == {
            "application_id": application["id"],
            "agent_ids": [str(AGENT_ID)],
            "tool_keys": ["calculator.add"],
        }

        invalid_agent = await client.put(
            permissions_url,
            json={"agent_ids": [str(uuid4())], "tool_keys": []},
        )
        assert invalid_agent.status_code == 400
        assert invalid_agent.json()["error"]["code"] == "INVALID_APPLICATION_AGENT_GRANT"

        invalid_tool = await client.put(
            permissions_url,
            json={"agent_ids": [], "tool_keys": ["missing.tool"]},
        )
        assert invalid_tool.status_code == 400
        assert invalid_tool.json()["error"]["code"] == "INVALID_TOOL_KEYS"

        unchanged = await client.get(permissions_url)
        assert unchanged.json() == replaced.json()
    finally:
        await client.aclose()
        await engine.dispose()


async def test_application_access_is_tenant_scoped() -> None:
    engine, session_factory, client = await _create_environment()
    other_tenant_id = uuid4()
    other_admin_id = uuid4()
    other_user_id = uuid4()
    other_application_id = uuid4()
    other_agent_id = uuid4()
    try:
        async with session_factory() as db:
            db.add_all(
                [
                    Tenant(id=other_tenant_id, name="其他租户"),
                    AppUser(
                        id=other_admin_id,
                        tenant_id=other_tenant_id,
                        login_name="other-admin",
                        display_name="其他管理员",
                        role=UserRole.ADMIN,
                        status=UserStatus.ACTIVE,
                    ),
                    AppUser(
                        id=other_user_id,
                        tenant_id=other_tenant_id,
                        login_name="other-user",
                        display_name="其他用户",
                        role=UserRole.USER,
                        status=UserStatus.ACTIVE,
                    ),
                    CallingApplication(
                        id=other_application_id,
                        tenant_id=other_tenant_id,
                        code="other",
                        name="其他应用",
                        created_by=other_admin_id,
                        updated_by=other_admin_id,
                    ),
                    AgentDefinition(
                        id=other_agent_id,
                        tenant_id=other_tenant_id,
                        name="其他 Agent",
                        draft_instructions="其他租户 Agent",
                        created_by=other_admin_id,
                        updated_by=other_admin_id,
                    ),
                ]
            )
            await db.commit()

        application = await _create_application(client)
        mapping_url = f"/api/v1/admin/applications/{application['id']}/user-mappings"
        permissions_url = f"/api/v1/admin/applications/{application['id']}/permissions"

        foreign_user = await client.post(
            mapping_url,
            json={"external_user_id": "foreign-user", "user_id": str(other_user_id)},
        )
        foreign_agent = await client.put(
            permissions_url,
            json={"agent_ids": [str(other_agent_id)], "tool_keys": []},
        )
        foreign_application_mapping = await client.get(
            f"/api/v1/admin/applications/{other_application_id}/user-mappings"
        )
        foreign_application_permissions = await client.get(
            f"/api/v1/admin/applications/{other_application_id}/permissions"
        )

        assert foreign_user.status_code == 400
        assert foreign_user.json()["error"]["code"] == "INVALID_EXTERNAL_USER_MAPPING"
        assert foreign_agent.status_code == 400
        assert foreign_agent.json()["error"]["code"] == "INVALID_APPLICATION_AGENT_GRANT"
        assert foreign_application_mapping.status_code == 404
        assert foreign_application_permissions.status_code == 404
        assert foreign_application_mapping.json()["error"]["code"] == "APPLICATION_NOT_FOUND"
    finally:
        await client.aclose()
        await engine.dispose()
