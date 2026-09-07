from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.agents.service import create_agent, publish_agent
from agentplane.config import Settings
from agentplane.db import create_engine, create_session_factory
from agentplane.identity import IdentityContext
from agentplane.models import (
    AppUser,
    RunEvent,
    RunStatus,
    TaskRun,
    Tenant,
    UserAgentGrant,
    UserRole,
    UserStatus,
)
from agentplane.queue import ensure_run_consumer_group
from agentplane.runs.service import create_authorized_run, mark_run_failed, mark_run_started
from agentplane.runtime import RuntimeCancelled, RuntimeEvent, RuntimeResult
from agentplane.runtime.fake import FakeRuntimeAdapter
from agentplane.schemas import AgentCreate, RunCreate, SessionCreate
from agentplane.sessions.service import create_chat_session
from agentplane.worker.main import AgentWorker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="requires PostgreSQL and Redis"),
]


@pytest.fixture
async def worker_run() -> AsyncIterator[tuple[AgentWorker, UUID]]:
    tenant_id, user_id = uuid4(), uuid4()
    settings = Settings(
        app_env="test",
        model_api_key="test-only",
        model_name="fake-model",
        redis_run_stream=f"agentplane:test:runs:{uuid4()}",
        redis_run_group=f"worker-test-{uuid4()}",
        worker_reclaim_idle_ms=1000,
    )
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    worker = AgentWorker(settings, factory, redis, FakeRuntimeAdapter())
    identity = IdentityContext(tenant_id=tenant_id, user_id=user_id, role=UserRole.ADMIN)
    try:
        async with factory() as db:
            db.add(Tenant(id=tenant_id, name="worker integration"))
            await db.flush()
            db.add(
                AppUser(
                    id=user_id,
                    tenant_id=tenant_id,
                    login_name="worker-test",
                    display_name="worker-test",
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                )
            )
            await db.flush()
            agent = await create_agent(
                db, identity, AgentCreate(name="worker", instructions="test")
            )
            await publish_agent(db, identity, agent.id)
            db.add(
                UserAgentGrant(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    agent_definition_id=agent.id,
                    granted_by=user_id,
                )
            )
            await db.flush()
            session = await create_chat_session(db, identity, SessionCreate(agent_id=agent.id))
            run = await create_authorized_run(
                db, identity, session.id, RunCreate(input="test"), settings, []
            )
            await db.commit()
            run_id = run.id
        await ensure_run_consumer_group(redis, settings)
        await redis.xadd(settings.redis_run_stream, {"aggregate_id": str(run_id)})
        yield worker, run_id
    finally:
        async with factory() as db:
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()
        await redis.delete(settings.redis_run_stream)
        await redis.aclose()
        await engine.dispose()


async def _delivery(worker: AgentWorker) -> tuple[str, dict[str, str]]:
    messages = await worker.redis.xreadgroup(
        worker.settings.redis_run_group,
        worker.consumer_name,
        {worker.settings.redis_run_stream: ">"},
        count=1,
    )
    return messages[0][1][0]


async def _assert_state(worker: AgentWorker, run_id: UUID, status: RunStatus, pending: int) -> None:
    async with worker.session_factory() as db:
        run = await db.get(TaskRun, run_id)
        assert run is not None and run.status == status
    summary = await worker.redis.xpending(
        worker.settings.redis_run_stream, worker.settings.redis_run_group
    )
    assert summary["pending"] == pending


async def test_postgres_preflight_error_is_terminal_and_acknowledged(
    worker_run: tuple[AgentWorker, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, run_id = worker_run
    monkeypatch.setattr(
        worker, "_load_request", AsyncMock(side_effect=RuntimeError("RUN_CONFIGURATION_MISSING"))
    )
    await worker.process_message(*await _delivery(worker))
    await _assert_state(worker, run_id, RunStatus.FAILED, 0)


@pytest.mark.parametrize("failure", ["ack", "commit_before", "commit_after"])
async def test_postgres_redis_redelivery_does_not_execute_twice(
    worker_run: tuple[AgentWorker, UUID], monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    worker, run_id = worker_run
    start = AsyncMock(return_value=RuntimeResult("done"))
    monkeypatch.setattr(worker.runtime, "start_run", start)
    delivery = await _delivery(worker)
    commit, commits = AsyncSession.commit, 0

    async def fail_commit(db: AsyncSession) -> None:
        nonlocal commits
        commits += 1
        if commits == 2:
            if failure == "commit_after":
                await commit(db)
            raise OperationalError("commit", {}, Exception("injected lost connection"))
        await commit(db)

    with monkeypatch.context() as patch:
        if failure == "ack":
            patch.setattr(
                worker.redis, "xack", AsyncMock(side_effect=RedisConnectionError("offline"))
            )
        else:
            patch.setattr(AsyncSession, "commit", fail_commit)
        with pytest.raises((RedisConnectionError, OperationalError)):
            await worker.process_message(*delivery)
    await _assert_state(
        worker, run_id, RunStatus.RUNNING if failure == "commit_before" else RunStatus.SUCCEEDED, 1
    )
    # Advance just this test delivery's idle time, then exercise real XAUTOCLAIM.
    await worker.redis.xclaim(
        worker.settings.redis_run_stream,
        worker.settings.redis_run_group,
        worker.consumer_name,
        min_idle_time=0,
        message_ids=[delivery[0]],
        idle=2000,
    )
    recovered = AgentWorker(worker.settings, worker.session_factory, worker.redis, worker.runtime)
    reclaimed = await recovered._reclaim_pending()  # pyright: ignore[reportPrivateUsage]
    assert len(reclaimed) == 1 and reclaimed[0][0] == delivery[0]
    await recovered.process_message(*reclaimed[0])
    await _assert_state(
        worker, run_id, RunStatus.FAILED if failure == "commit_before" else RunStatus.SUCCEEDED, 0
    )
    start.assert_awaited_once()


async def test_postgres_cancellation_saves_interruption_before_ack(
    worker_run: tuple[AgentWorker, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, run_id = worker_run
    entered = asyncio.Event()

    async def block(*_args: object) -> RuntimeResult:
        entered.set()
        await asyncio.Event().wait()
        return RuntimeResult("unreachable")

    monkeypatch.setattr(worker.runtime, "start_run", block)
    task = asyncio.create_task(worker.process_message(*await _delivery(worker)))
    await asyncio.wait_for(entered.wait(), timeout=3)
    await _assert_state(worker, run_id, RunStatus.RUNNING, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_state(worker, run_id, RunStatus.FAILED, 0)


async def test_postgres_late_event_checks_terminal_after_acquiring_lock(
    worker_run: tuple[AgentWorker, UUID],
) -> None:
    worker, run_id = worker_run
    message_id, _fields = await _delivery(worker)
    async with worker.session_factory() as db:
        run = await db.get(TaskRun, run_id, with_for_update=True)
        assert run is not None
        run.dispatch_message_id = message_id
        await mark_run_started(db, run)
        await db.commit()
    async with worker.session_factory() as db:
        run = await db.get(TaskRun, run_id, with_for_update=True)
        assert run is not None
        await mark_run_failed(db, run, "WORKER_INTERRUPTED", "test")
        late = asyncio.create_task(
            worker._emit_runtime_event(  # pyright: ignore[reportPrivateUsage]
                run_id, message_id, RuntimeEvent("model.delta", {"delta": "late"})
            )
        )
        try:
            done, _ = await asyncio.wait({late}, timeout=0.1)
            assert not done  # The other transaction is holding the Run lock.
            await db.commit()
            with pytest.raises(RuntimeCancelled):
                await asyncio.wait_for(late, timeout=3)
        finally:
            late.cancel()
            await asyncio.gather(late, return_exceptions=True)
    async with worker.session_factory() as db:
        events = list(
            await db.scalars(
                select(RunEvent.event_type)
                .where(RunEvent.run_id == run_id)
                .order_by(RunEvent.sequence)
            )
        )
        assert events == ["run.started", "run.failed"]
