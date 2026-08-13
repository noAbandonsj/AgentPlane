from __future__ import annotations

from typing import Any
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from agentplane.api.app import create_app
from agentplane.config import Settings
from agentplane.db import Base
from agentplane.models import AppUser, Invocation, TaskRun, Tenant, UserRole, UserStatus
from agentplane.services import mark_run_started, mark_run_succeeded

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
ADMIN_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000002")


class _FakePubSub:
    async def subscribe(self, _channel: str) -> None:
        return None

    async def unsubscribe(self, _channel: str) -> None:
        return None

    async def get_message(self, **_kwargs: Any) -> None:
        return None

    async def aclose(self) -> None:
        return None


class _FakeRedis:
    def pubsub(self) -> _FakePubSub:
        return _FakePubSub()


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
                Tenant(id=TENANT_ID, name="企业调用测试租户"),
                AppUser(
                    id=ADMIN_ID,
                    tenant_id=TENANT_ID,
                    login_name="test-admin",
                    display_name="测试管理员",
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                ),
                AppUser(
                    id=USER_ID,
                    tenant_id=TENANT_ID,
                    login_name="represented-user",
                    display_name="被代表用户",
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                ),
            ]
        )
        await db.commit()

    settings = Settings(
        app_env="test",
        auth_mode="dev",
        dev_tenant_id=TENANT_ID,
        dev_user_id=ADMIN_ID,
        model_api_key="test-only-key",
        model_name="fake-model",
        sse_poll_seconds=0.1,
        sse_keepalive_seconds=1,
    )
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.redis = _FakeRedis()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return engine, session_factory, client


async def _prepare_access(client: AsyncClient, *, application_code: str = "crm") -> dict[str, str]:
    agent_response = await client.post(
        "/api/v1/agents",
        json={
            "name": f"{application_code.upper()} 接入助手",
            "instructions": "按权限响应企业系统请求",
            "tool_keys": ["calculator.add"],
        },
    )
    assert agent_response.status_code == 201
    agent_id = agent_response.json()["id"]
    published = await client.post(f"/api/v1/agents/{agent_id}/publish")
    assert published.status_code == 201

    user_permissions = await client.put(
        f"/api/v1/admin/users/{USER_ID}/permissions",
        json={"agent_ids": [agent_id], "tool_keys": ["calculator.add"]},
    )
    assert user_permissions.status_code == 200

    application_response = await client.post(
        "/api/v1/admin/applications",
        json={"code": application_code, "name": f"{application_code.upper()} 调用端"},
    )
    assert application_response.status_code == 201
    application_body = application_response.json()
    application_id = application_body["application"]["id"]
    token = application_body["credential"]["token"]

    mapping = await client.post(
        f"/api/v1/admin/applications/{application_id}/user-mappings",
        json={"external_user_id": "CRM-USER-001", "user_id": str(USER_ID)},
    )
    assert mapping.status_code == 201
    application_permissions = await client.put(
        f"/api/v1/admin/applications/{application_id}/permissions",
        json={"agent_ids": [agent_id], "tool_keys": ["calculator.add"]},
    )
    assert application_permissions.status_code == 200
    return {
        "agent_id": agent_id,
        "application_id": application_id,
        "token": token,
    }


def _invocation_payload(agent_id: str, request_id: str = "crm-request-001") -> dict[str, str]:
    return {
        "external_request_id": request_id,
        "external_user_id": "CRM-USER-001",
        "conversation_key": "customer-10001",
        "agent_id": agent_id,
        "input": "计算 1 加 2",
    }


async def test_invocation_creation_idempotency_permissions_and_events() -> None:
    engine, session_factory, client = await _create_environment()
    try:
        access = await _prepare_access(client)
        headers = {"Authorization": f"Bearer {access['token']}"}
        payload = _invocation_payload(access["agent_id"])

        created = await client.post("/api/v1/invocations", json=payload, headers=headers)
        assert created.status_code == 202
        invocation = created.json()
        assert invocation["decision"] == "ALLOWED"
        assert invocation["status"] == "QUEUED"
        assert invocation["effective_tool_keys"] == ["calculator.add"]
        assert invocation["run_id"] is not None

        repeated = await client.post("/api/v1/invocations", json=payload, headers=headers)
        assert repeated.status_code == 202
        assert repeated.json()["id"] == invocation["id"]
        assert repeated.json()["run_id"] == invocation["run_id"]

        conflicting = await client.post(
            "/api/v1/invocations",
            json={**payload, "input": "不同内容"},
            headers=headers,
        )
        assert conflicting.status_code == 409
        assert conflicting.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

        async with session_factory() as db:
            run = await db.get(TaskRun, UUID(invocation["run_id"]))
            assert run is not None
            await mark_run_started(db, run)
            await mark_run_succeeded(db, run, "结果为 3")
            await db.commit()

        details = await client.get(
            f"/api/v1/invocations/{invocation['id']}",
            headers=headers,
        )
        assert details.status_code == 200
        assert details.json()["status"] == "SUCCEEDED"
        assert details.json()["output"] == "结果为 3"

        events = await client.get(
            f"/api/v1/invocations/{invocation['id']}/events",
            headers=headers,
        )
        assert events.status_code == 200
        assert "event: run.started" in events.text
        assert "event: run.completed" in events.text

        application_permissions = await client.put(
            f"/api/v1/admin/applications/{access['application_id']}/permissions",
            json={"agent_ids": [access["agent_id"]], "tool_keys": []},
        )
        assert application_permissions.status_code == 200
        continued = await client.post(
            "/api/v1/invocations",
            json=_invocation_payload(access["agent_id"], "crm-request-002"),
            headers=headers,
        )
        assert continued.status_code == 202
        assert continued.json()["session_id"] == invocation["session_id"]
        assert continued.json()["effective_tool_keys"] == []

        original = await client.get(
            f"/api/v1/invocations/{invocation['id']}",
            headers=headers,
        )
        assert original.json()["effective_tool_keys"] == ["calculator.add"]
    finally:
        await client.aclose()
        await engine.dispose()


async def test_invocation_denials_are_audited_and_application_scoped() -> None:
    engine, session_factory, client = await _create_environment()
    try:
        access = await _prepare_access(client)
        headers = {"Authorization": f"Bearer {access['token']}"}
        permissions_url = f"/api/v1/admin/applications/{access['application_id']}/permissions"
        removed = await client.put(
            permissions_url,
            json={"agent_ids": [], "tool_keys": ["calculator.add"]},
        )
        assert removed.status_code == 200

        denied = await client.post(
            "/api/v1/invocations",
            json=_invocation_payload(access["agent_id"], "denied-request-001"),
            headers=headers,
        )
        assert denied.status_code == 403
        denied_body = denied.json()["error"]
        assert denied_body["code"] == "APPLICATION_AGENT_NOT_GRANTED"
        invocation_id = denied_body["details"]["invocation_id"]

        audited = await client.get(f"/api/v1/invocations/{invocation_id}", headers=headers)
        assert audited.status_code == 200
        assert audited.json()["decision"] == "DENIED"
        assert audited.json()["status"] == "REJECTED"
        assert audited.json()["run_id"] is None

        async with session_factory() as db:
            stored = await db.get(Invocation, UUID(invocation_id))
            assert stored is not None
            assert stored.decision_code == "APPLICATION_AGENT_NOT_GRANTED"

        other = await client.post(
            "/api/v1/admin/applications",
            json={"code": "erp", "name": "ERP 调用端"},
        )
        assert other.status_code == 201
        other_token = other.json()["credential"]["token"]
        hidden = await client.get(
            f"/api/v1/invocations/{invocation_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert hidden.status_code == 404
        assert hidden.json()["error"]["code"] == "INVOCATION_NOT_FOUND"

        rejected_events = await client.get(
            f"/api/v1/invocations/{invocation_id}/events",
            headers=headers,
        )
        assert rejected_events.status_code == 409
        assert rejected_events.json()["error"]["code"] == "INVOCATION_REJECTED"

        unexpected_capability = await client.post(
            "/api/v1/invocations",
            json={
                **_invocation_payload(access["agent_id"], "invalid-request-001"),
                "tool_keys": ["calculator.add"],
            },
            headers=headers,
        )
        assert unexpected_capability.status_code == 422
        assert unexpected_capability.json()["error"]["code"] == "VALIDATION_ERROR"
    finally:
        await client.aclose()
        await engine.dispose()
