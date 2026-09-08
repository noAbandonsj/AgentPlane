from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import delete

from agentplane.api.app import create_app
from agentplane.config import Settings
from agentplane.db import create_engine, create_session_factory
from agentplane.models import AppUser, Tenant, UserRole, UserStatus
from agentplane.queue import ensure_run_consumer_group, publish_outbox_batch
from agentplane.runtime.fake import FakeRuntimeAdapter
from agentplane.worker.main import AgentWorker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1",
        reason="set RUN_INTEGRATION=1 after starting the Compose services",
    ),
]


async def test_postgres_redis_application_invocation_contract() -> None:
    tenant_id = uuid4()
    admin_id = uuid4()
    represented_user_id = uuid4()
    stream_name = f"agentplane:test:invocations:{uuid4()}"
    group_name = f"test-invocation-workers-{uuid4()}"
    settings = Settings(
        app_env="test",
        auth_mode="dev",
        dev_tenant_id=tenant_id,
        dev_user_id=admin_id,
        model_api_key="test-only-key",
        model_name="fake-model",
        redis_run_stream=stream_name,
        redis_run_group=group_name,
        worker_consumer_name="integration-invocation-worker",
        sse_poll_seconds=0.1,
        sse_keepalive_seconds=1,
    )
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)

    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.redis = redis
    transport = httpx.ASGITransport(app=app)

    try:
        async with session_factory() as db:
            db.add(Tenant(id=tenant_id, name="企业调用集成测试租户"))
            await db.flush()
            db.add_all(
                [
                    AppUser(
                        id=admin_id,
                        tenant_id=tenant_id,
                        login_name=f"integration-admin-{admin_id.hex[:8]}",
                        display_name="企业调用集成测试管理员",
                        role=UserRole.ADMIN,
                        status=UserStatus.ACTIVE,
                    ),
                    AppUser(
                        id=represented_user_id,
                        tenant_id=tenant_id,
                        login_name=f"crm-user-{represented_user_id.hex[:8]}",
                        display_name="CRM 被代表用户",
                        role=UserRole.USER,
                        status=UserStatus.ACTIVE,
                    ),
                ]
            )
            await db.commit()

        await ensure_run_consumer_group(redis, settings)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            agent_response = await client.post(
                "/api/v1/agents",
                json={
                    "name": f"CRM 集成助手 {tenant_id.hex[:8]}",
                    "instructions": "使用授权工具响应 CRM 请求",
                    "tool_keys": ["calculator.add"],
                },
            )
            assert agent_response.status_code == 201
            agent_id = agent_response.json()["id"]
            published = await client.post(f"/api/v1/agents/{agent_id}/publish")
            assert published.status_code == 201

            user_permissions = await client.put(
                f"/api/v1/admin/users/{represented_user_id}/permissions",
                json={"agent_ids": [agent_id], "tool_keys": ["calculator.add"]},
            )
            assert user_permissions.status_code == 200

            application_response = await client.post(
                "/api/v1/admin/applications",
                json={
                    "code": f"crm-{tenant_id.hex[:8]}",
                    "name": "CRM 集成测试调用端",
                },
            )
            assert application_response.status_code == 201
            application = application_response.json()
            application_id = application["application"]["id"]
            token = application["credential"]["token"]
            headers = {"Authorization": f"Bearer {token}"}

            mapping = await client.post(
                f"/api/v1/admin/applications/{application_id}/user-mappings",
                json={
                    "external_user_id": "CRM-USER-INTEGRATION",
                    "user_id": str(represented_user_id),
                },
            )
            assert mapping.status_code == 201
            application_permissions = await client.put(
                f"/api/v1/admin/applications/{application_id}/permissions",
                json={"agent_ids": [agent_id], "tool_keys": ["calculator.add"]},
            )
            assert application_permissions.status_code == 200

            payload = {
                "external_request_id": f"crm-request-{uuid4()}",
                "external_user_id": "CRM-USER-INTEGRATION",
                "conversation_key": "customer-10001",
                "agent_id": agent_id,
                "input": "add: 20, 22",
            }
            first, duplicate = await asyncio.gather(
                client.post("/api/v1/invocations", json=payload, headers=headers),
                client.post("/api/v1/invocations", json=payload, headers=headers),
            )
            assert first.status_code == duplicate.status_code == 202
            invocation = first.json()
            assert duplicate.json()["id"] == invocation["id"]
            assert duplicate.json()["run_id"] == invocation["run_id"]
            assert invocation["decision"] == "ALLOWED"
            assert invocation["status"] == "QUEUED"
            assert invocation["effective_tool_keys"] == ["calculator.add"]

            conflicting = await client.post(
                "/api/v1/invocations",
                json={**payload, "input": "add: 1, 2"},
                headers=headers,
            )
            assert conflicting.status_code == 409
            assert conflicting.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

            assert await publish_outbox_batch(session_factory, redis, settings) == 1
            messages = await redis.xreadgroup(
                group_name,
                settings.worker_consumer_name,
                {stream_name: ">"},
                count=1,
            )
            message_id, fields = messages[0][1][0]
            worker = AgentWorker(settings, session_factory, redis, FakeRuntimeAdapter())
            await worker.process_message(message_id, fields)

            completed = await client.get(
                f"/api/v1/invocations/{invocation['id']}",
                headers=headers,
            )
            assert completed.status_code == 200
            assert completed.json()["status"] == "SUCCEEDED"
            assert completed.json()["output"] == "计算结果是 42"
            tool_calls = await client.get(
                "/api/v1/admin/tool-calls", params={"run_id": invocation["run_id"]}
            )
            assert tool_calls.status_code == 200
            (tool_call,) = tool_calls.json()
            assert tool_call["status"] == "SUCCEEDED"
            assert tool_call["tool_version"] == "1.0.0"
            assert tool_call["application_id"] == application_id
            assert tool_call["user_id"] == str(represented_user_id)
            assert tool_call["input_summary"] == {"field_count": 2, "values": "REDACTED"}
            await worker.process_message(message_id, fields)
            assert (
                len(
                    (
                        await client.get(
                            "/api/v1/admin/tool-calls", params={"run_id": invocation["run_id"]}
                        )
                    ).json()
                )
                == 1
            )

            events = await client.get(
                f"/api/v1/invocations/{invocation['id']}/events",
                headers=headers,
            )
            assert events.status_code == 200
            assert "event: tool.started" in events.text
            assert "event: run.completed" in events.text

            continued = await client.post(
                "/api/v1/invocations",
                json={
                    **payload,
                    "external_request_id": f"crm-request-{uuid4()}",
                    "input": "add: 1, 2",
                },
                headers=headers,
            )
            assert continued.status_code == 202
            assert continued.json()["session_id"] == invocation["session_id"]
            assert continued.json()["effective_tool_keys"] == ["calculator.add"]
            disabled = await client.patch(
                "/api/v1/admin/tools/calculator.add", json={"enabled": False}
            )
            assert disabled.status_code == 200
            assert await publish_outbox_batch(session_factory, redis, settings) == 1
            pending = await redis.xreadgroup(
                group_name, worker.consumer_name, {stream_name: ">"}, count=1
            )
            await worker.process_message(*pending[0][1][0])
            failed = await client.get(
                f"/api/v1/invocations/{continued.json()['id']}", headers=headers
            )
            assert failed.json()["status"] == "FAILED"
            assert failed.json()["error_code"] == "TOOL_DISABLED"
            denied_calls = await client.get(
                "/api/v1/admin/tool-calls", params={"run_id": continued.json()["run_id"]}
            )
            assert denied_calls.json()[0]["status"] == "DENIED"
            assert (
                await client.patch("/api/v1/admin/tools/calculator.add", json={"enabled": True})
            ).status_code == 200

            removed_permissions = await client.put(
                f"/api/v1/admin/applications/{application_id}/permissions",
                json={"agent_ids": [], "tool_keys": ["calculator.add"]},
            )
            assert removed_permissions.status_code == 200
            denied = await client.post(
                "/api/v1/invocations",
                json={
                    **payload,
                    "external_request_id": f"crm-denied-{uuid4()}",
                },
                headers=headers,
            )
            assert denied.status_code == 403
            denial = denied.json()["error"]
            assert denial["code"] == "APPLICATION_AGENT_NOT_GRANTED"
            denied_audit = await client.get(
                f"/api/v1/invocations/{denial['details']['invocation_id']}",
                headers=headers,
            )
            assert denied_audit.status_code == 200
            assert denied_audit.json()["decision"] == "DENIED"
            assert denied_audit.json()["status"] == "REJECTED"

            rotated = await client.post(
                f"/api/v1/admin/applications/{application_id}/credentials/rotate",
                json={},
            )
            assert rotated.status_code == 201
            old_credential = await client.get(
                f"/api/v1/invocations/{invocation['id']}",
                headers=headers,
            )
            assert old_credential.status_code == 401
            new_headers = {"Authorization": f"Bearer {rotated.json()['token']}"}
            new_credential = await client.get(
                f"/api/v1/invocations/{invocation['id']}",
                headers=new_headers,
            )
            assert new_credential.status_code == 200
    finally:
        async with session_factory() as db:
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()
        await redis.delete(stream_name)
        await redis.aclose()
        await engine.dispose()
