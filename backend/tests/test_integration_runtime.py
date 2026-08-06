from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, text

from agentplane.api.app import create_app
from agentplane.config import Settings
from agentplane.db import create_engine, create_session_factory
from agentplane.identity import IdentityContext
from agentplane.models import (
    AppUser,
    RunEvent,
    RunStatus,
    Tenant,
    UserAgentGrant,
    UserRole,
    UserStatus,
    UserToolGrant,
)
from agentplane.queue import ensure_run_consumer_group, publish_outbox_batch
from agentplane.runtime.fake import FakeRuntimeAdapter
from agentplane.schemas import AgentCreate, RunCreate, SessionCreate
from agentplane.services import (
    create_agent,
    create_chat_session,
    create_run,
    get_run_for_worker,
    list_run_events_after,
    mark_run_started,
    publish_agent,
)
from agentplane.worker.main import AgentWorker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1",
        reason="set RUN_INTEGRATION=1 after starting the Compose services",
    ),
]


async def test_postgres_redis_worker_interruption_and_sse_replay() -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    stream_name = f"agentplane:test:runs:{uuid4()}"
    group_name = f"test-workers-{uuid4()}"
    settings = Settings(
        app_env="test",
        dev_tenant_id=tenant_id,
        dev_user_id=user_id,
        model_api_key="test-only-key",
        model_name="fake-model",
        redis_run_stream=stream_name,
        redis_run_group=group_name,
        worker_consumer_name="integration-worker",
        worker_reclaim_idle_ms=1000,
        sse_poll_seconds=0.1,
    )
    identity = IdentityContext(tenant_id=tenant_id, user_id=user_id, role=UserRole.ADMIN)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)

    try:
        async with session_factory() as db:
            migration = await db.scalar(text("SELECT version_num FROM alembic_version"))
            assert migration == "20260806_0003"
            db.add(Tenant(id=tenant_id, name="集成测试租户"))
            await db.flush()
            db.add(
                AppUser(
                    id=user_id,
                    tenant_id=tenant_id,
                    login_name="integration-admin",
                    display_name="集成测试用户",
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                )
            )
            await db.commit()

            agent = await create_agent(
                db,
                identity,
                AgentCreate(
                    name="集成测试助手",
                    instructions="使用安全工具完成任务",
                    tool_keys=["calculator.add"],
                ),
            )
            await publish_agent(db, identity, agent.id)
            db.add_all(
                [
                    UserAgentGrant(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        agent_definition_id=agent.id,
                        granted_by=user_id,
                    ),
                    UserToolGrant(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        tool_key="calculator.add",
                        granted_by=user_id,
                    ),
                ]
            )
            chat_session = await create_chat_session(
                db,
                identity,
                SessionCreate(agent_id=agent.id, title="集成闭环"),
            )
            await db.commit()
            first_run = await create_run(
                db,
                identity,
                chat_session.id,
                RunCreate(input="add: 20, 22"),
                settings,
            )
            await db.commit()

        await ensure_run_consumer_group(redis, settings)
        assert await publish_outbox_batch(session_factory, redis, settings) == 1
        messages = await redis.xreadgroup(
            group_name,
            settings.worker_consumer_name,
            {stream_name: ">"},
            count=1,
        )
        message_id, fields = messages[0][1][0]
        worker = AgentWorker(
            settings,
            session_factory,
            redis,
            FakeRuntimeAdapter(),
        )
        await worker.process_message(message_id, fields)

        async with session_factory() as db:
            completed = await get_run_for_worker(db, first_run.id)
            assert completed is not None
            assert completed.status == RunStatus.SUCCEEDED
            assert completed.output_text == "计算结果是 42"
            events = await list_run_events_after(db, identity, first_run.id, 0)
            assert [event.event_type for event in events] == [
                "run.started",
                "tool.started",
                "tool.completed",
                "model.delta",
                "model.delta",
                "run.completed",
            ]
            event_count = len(events)

        duplicate_id = await redis.xadd(
            stream_name,
            {
                "outbox_id": str(uuid4()),
                "event_type": "run.queued",
                "aggregate_id": str(first_run.id),
                "tenant_id": str(tenant_id),
            },
        )
        duplicate_messages = await redis.xreadgroup(
            group_name,
            settings.worker_consumer_name,
            {stream_name: ">"},
            count=1,
        )
        assert duplicate_messages[0][1][0][0] == duplicate_id
        await worker.process_message(*duplicate_messages[0][1][0])
        async with session_factory() as db:
            assert (
                await db.scalar(
                    select(func.count(RunEvent.id)).where(RunEvent.run_id == first_run.id)
                )
            ) == event_count

            interrupted_run = await create_run(
                db,
                identity,
                chat_session.id,
                RunCreate(input="中断执行"),
                settings,
            )
            await db.commit()

        assert await publish_outbox_batch(session_factory, redis, settings) == 1
        claimed_by_dead_worker = await redis.xreadgroup(
            group_name,
            "dead-worker",
            {stream_name: ">"},
            count=1,
        )
        interruption_message_id, _interruption_fields = claimed_by_dead_worker[0][1][0]
        async with session_factory() as db:
            running = await get_run_for_worker(db, interrupted_run.id, for_update=True)
            assert running is not None
            running.dispatch_message_id = interruption_message_id
            await mark_run_started(db, running)
            await db.commit()

        await asyncio.sleep(1.1)
        reclaimed = await worker._reclaim_pending()  # pyright: ignore[reportPrivateUsage]
        assert reclaimed and reclaimed[0][0] == interruption_message_id
        await worker.process_message(*reclaimed[0])
        async with session_factory() as db:
            interrupted = await get_run_for_worker(db, interrupted_run.id)
            assert interrupted is not None
            assert interrupted.status == RunStatus.FAILED
            assert interrupted.error_code == "WORKER_INTERRUPTED"
            assert interrupted.attempt_count == 1

        app = create_app(settings)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    f"/api/v1/runs/{first_run.id}/events",
                    headers={"Last-Event-ID": "3"},
                )
        assert response.status_code == 200
        assert "id: 3\n" not in response.text
        assert "id: 4\n" in response.text
        assert "event: run.completed" in response.text
    finally:
        async with session_factory() as db:
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()
        await redis.delete(stream_name)
        await redis.aclose()
        await engine.dispose()
