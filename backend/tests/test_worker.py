from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agentplane.agents.service import create_agent, publish_agent
from agentplane.config import Settings
from agentplane.identity import IdentityContext
from agentplane.models import RunEvent, RunStatus, TaskRun, UserAgentGrant
from agentplane.runs.service import cancel_run, create_run, mark_run_failed
from agentplane.runtime import RuntimeCancelled, RuntimeEvent, RuntimeResult, RuntimeRunRequest
from agentplane.runtime.fake import FakeRuntimeAdapter
from agentplane.schemas import AgentCreate, RunCreate, SessionCreate
from agentplane.sessions.service import create_chat_session
from agentplane.worker.main import AgentWorker


@dataclass
class WorkerEnvironment:
    worker: AgentWorker
    redis: AsyncMock
    start: AsyncMock
    factory: async_sessionmaker[AsyncSession]
    identity: IdentityContext
    agent_id: UUID

    async def new_run(self) -> UUID:
        async with self.factory() as db:
            session = await create_chat_session(
                db, self.identity, SessionCreate(agent_id=self.agent_id, title="worker test")
            )
            run = await create_run(
                db, self.identity, session.id, RunCreate(input="hello"), self.worker.settings
            )
            await db.commit()
            return run.id

    async def state(self, run_id: UUID) -> tuple[RunStatus, str | None, list[str]]:
        async with self.factory() as db:
            run = await db.get(TaskRun, run_id)
            assert run is not None
            events = await db.scalars(
                select(RunEvent.event_type)
                .where(RunEvent.run_id == run_id)
                .order_by(RunEvent.sequence)
            )
            return run.status, run.error_code, list(events)


@pytest.fixture
async def env(
    db: AsyncSession, identity: IdentityContext, monkeypatch: pytest.MonkeyPatch
) -> WorkerEnvironment:
    agent = await create_agent(db, identity, AgentCreate(name="worker", instructions="test"))
    await publish_agent(db, identity, agent.id)
    db.add(
        UserAgentGrant(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_definition_id=agent.id,
            granted_by=identity.user_id,
        )
    )
    await db.commit()
    assert isinstance(db.bind, AsyncEngine)
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        model_api_key="test",
        model_name="fake",
        worker_retry_initial_seconds=0.01,
        worker_retry_max_seconds=0.02,
        worker_shutdown_grace_seconds=0.01,
        worker_cleanup_timeout_seconds=0.5,
    )
    redis = AsyncMock(spec=Redis)
    for method in ("xautoclaim", "xreadgroup", "xgroup_create", "xack", "xclaim", "publish"):
        setattr(redis, method, AsyncMock())
    redis.xautoclaim.return_value = ["0-0", [], []]
    runtime = FakeRuntimeAdapter()
    start = AsyncMock(wraps=runtime.start_run)
    monkeypatch.setattr(runtime, "start_run", start)
    worker = AgentWorker(settings, factory, redis, runtime)
    return WorkerEnvironment(worker, redis, start, factory, identity, agent.id)


async def test_poison_message_does_not_stop_consumer(
    env: WorkerEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad, good = await env.new_run(), await env.new_run()
    original = env.worker._load_request  # pyright: ignore[reportPrivateUsage]

    async def load(db: AsyncSession, run: TaskRun) -> RuntimeRunRequest:
        if run.id == bad:
            raise RuntimeError("RUN_CONFIGURATION_MISSING")
        return await original(db, run)

    monkeypatch.setattr(env.worker, "_load_request", load)
    deliveries = iter([(bad, "1-0"), (good, "2-0")])

    async def read(*_args: object, **_kwargs: object) -> list[object]:
        item = next(deliveries, None)
        if item is None:
            env.worker.request_stop()
            return []
        run_id, message_id = item
        return [("runs", [(message_id, {"aggregate_id": str(run_id)})])]

    env.redis.xreadgroup.side_effect = read
    await asyncio.wait_for(env.worker.run_forever(), timeout=2)
    assert await env.state(bad) == (RunStatus.FAILED, "RUN_CONFIGURATION_MISSING", ["run.failed"])
    assert (await env.state(good))[0] == RunStatus.SUCCEEDED
    assert env.start.await_count == 1
    assert env.redis.xack.await_count == 2


@pytest.mark.parametrize("after_commit", [False, True])
async def test_uncertain_commit_keeps_pending_and_never_reexecutes(
    env: WorkerEnvironment, monkeypatch: pytest.MonkeyPatch, after_commit: bool
) -> None:
    run_id = await env.new_run()
    env.start.return_value = RuntimeResult("done")
    commit = AsyncSession.commit
    calls = 0

    async def failing_commit(db: AsyncSession) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            if after_commit:
                await commit(db)
            raise OperationalError("commit", {}, Exception("connection lost"))
        await commit(db)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", failing_commit)
        with pytest.raises(OperationalError):
            await env.worker.process_message("1-0", {"aggregate_id": str(run_id)})
    env.redis.xack.assert_not_awaited()
    assert (await env.state(run_id))[0] == (
        RunStatus.SUCCEEDED if after_commit else RunStatus.RUNNING
    )
    await env.worker.process_message("1-0", {"aggregate_id": str(run_id)})
    assert env.start.await_count == 1
    state = await env.state(run_id)
    assert state[0] == (RunStatus.SUCCEEDED if after_commit else RunStatus.FAILED)
    assert state[2].count("run.completed") == int(after_commit)
    env.redis.xack.assert_awaited_once()


async def test_ack_failure_only_repeats_ack(env: WorkerEnvironment) -> None:
    run_id = await env.new_run()
    env.redis.xack.side_effect = [RedisConnectionError("offline"), 1]
    with pytest.raises(RedisConnectionError):
        await env.worker.process_message("1-0", {"aggregate_id": str(run_id)})
    await env.worker.process_message("1-0", {"aggregate_id": str(run_id)})
    assert env.start.await_count == 1
    assert (await env.state(run_id))[2].count("run.completed") == 1
    assert env.redis.xack.await_count == 2


async def test_failure_persistence_failure_does_not_ack(
    env: WorkerEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = await env.new_run()
    env.start.side_effect = ValueError("bad runtime")
    commit = AsyncSession.commit
    calls = 0

    async def failing_commit(db: AsyncSession) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OperationalError("commit", {}, Exception("offline"))
        await commit(db)

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    with pytest.raises(OperationalError):
        await env.worker.process_message("1-0", {"aggregate_id": str(run_id)})
    env.redis.xack.assert_not_awaited()
    assert (await env.state(run_id))[0] == RunStatus.RUNNING


@pytest.mark.parametrize(
    "database_available,user_cancel",
    [
        (True, False),
        (False, False),
        (True, True),
    ],
)
async def test_cancellation_preserves_result_or_pending(
    env: WorkerEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    database_available: bool,
    user_cancel: bool,
) -> None:
    run_id = await env.new_run()
    entered = asyncio.Event()

    async def block(*_args: object) -> RuntimeResult:
        entered.set()
        await asyncio.Event().wait()
        return RuntimeResult("unreachable")

    env.start.side_effect = block
    task = asyncio.create_task(env.worker.process_message("1-0", {"aggregate_id": str(run_id)}))
    await asyncio.wait_for(entered.wait(), timeout=2)
    if user_cancel:
        async with env.factory() as db:
            await cancel_run(db, env.identity, run_id)
            await db.commit()
    if not database_available:
        monkeypatch.setattr(
            AsyncSession,
            "commit",
            AsyncMock(side_effect=OperationalError("commit", {}, Exception("offline"))),
        )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = await env.state(run_id)
    if not database_available:
        assert state[0] == RunStatus.RUNNING
        env.redis.xack.assert_not_awaited()
    else:
        assert state[0] == (RunStatus.CANCELLED if user_cancel else RunStatus.FAILED)
        assert state[1] == (None if user_cancel else "WORKER_INTERRUPTED")
        env.redis.xack.assert_awaited_once()


async def test_late_result_and_event_cannot_overwrite_terminal(env: WorkerEnvironment) -> None:
    run_id = await env.new_run()

    async def late_result(*_args: object) -> RuntimeResult:
        async with env.factory() as db:
            run = await db.get(TaskRun, run_id)
            assert run is not None
            await mark_run_failed(db, run, "WORKER_INTERRUPTED", "interrupted")
            await db.commit()
        return RuntimeResult("late success")

    env.start.side_effect = late_result
    await env.worker.process_message("1-0", {"aggregate_id": str(run_id)})
    assert await env.state(run_id) == (
        RunStatus.FAILED,
        "WORKER_INTERRUPTED",
        ["run.started", "run.failed"],
    )
    with pytest.raises(RuntimeCancelled):
        await env.worker._emit_runtime_event(  # pyright: ignore[reportPrivateUsage]
            run_id, "1-0", RuntimeEvent("model.delta", {"delta": "late"})
        )
    assert (await env.state(run_id))[2] == ["run.started", "run.failed"]


async def test_execution_timeout_terminates_run(env: WorkerEnvironment) -> None:
    run_id = await env.new_run()
    env.worker.settings.worker_run_timeout_seconds = 0.01

    async def block(*_args: object) -> RuntimeResult:
        await asyncio.Event().wait()
        return RuntimeResult("unreachable")

    env.start.side_effect = block
    await env.worker.process_message("1-0", {"aggregate_id": str(run_id)})
    assert (await env.state(run_id))[:2] == (RunStatus.FAILED, "RUN_EXECUTION_TIMEOUT")
    env.redis.xack.assert_awaited_once()


@pytest.mark.parametrize("busy", [False, True])
async def test_shutdown_drains_and_closes_runtime(
    env: WorkerEnvironment, monkeypatch: pytest.MonkeyPatch, busy: bool
) -> None:
    run_id = await env.new_run()
    entered = asyncio.Event()
    closed = AsyncMock()
    monkeypatch.setattr(env.worker.runtime, "close", closed)

    async def block(*_args: object) -> RuntimeResult:
        entered.set()
        await asyncio.Event().wait()
        return RuntimeResult("unreachable")

    async def idle_read(*_args: object, **_kwargs: object) -> list[object]:
        entered.set()
        await asyncio.Event().wait()
        return []

    if busy:
        env.start.side_effect = block
        env.redis.xreadgroup.return_value = [("runs", [("1-0", {"aggregate_id": str(run_id)})])]
    else:
        env.redis.xreadgroup.side_effect = idle_read
    task = asyncio.create_task(env.worker.run_forever())
    await asyncio.wait_for(entered.wait(), timeout=2)
    env.worker.request_stop()
    await asyncio.wait_for(task, timeout=2)
    closed.assert_awaited_once()
    if busy:
        assert (await env.state(run_id))[1] == "WORKER_INTERRUPTED"
        assert env.start.await_count == 1
    else:
        env.redis.xack.assert_not_awaited()


async def test_redis_failure_recovers_without_busy_loop(env: WorkerEnvironment) -> None:
    attempts = 0
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    async def read(*_args: object, **_kwargs: object) -> list[object]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RedisConnectionError("offline")
        env.worker.request_stop()
        return []

    env.redis.xreadgroup.side_effect = read
    await asyncio.wait_for(env.worker.run_forever(), timeout=2)
    assert attempts == 3
    assert loop.time() - started_at >= 0.02


async def test_reclaim_cursor_capacity_and_unique_identity(env: WorkerEnvironment) -> None:
    second = AgentWorker(env.worker.settings, env.factory, env.redis, FakeRuntimeAdapter())
    assert second.consumer_name != env.worker.consumer_name
    env.redis.xautoclaim.side_effect = [["99-0", [], []], ["0-0", [], []]]
    await env.worker._reclaim_pending()  # pyright: ignore[reportPrivateUsage]
    await env.worker._reclaim_pending()  # pyright: ignore[reportPrivateUsage]
    calls = env.redis.xautoclaim.await_args_list
    assert calls[0].kwargs["start_id"] == "0-0"
    assert calls[1].kwargs["start_id"] == "99-0"
    assert all(call.kwargs["count"] == 1 for call in calls)


async def test_reclaimed_work_does_not_starve_new_messages(env: WorkerEnvironment) -> None:
    reclaimed, new = await env.new_run(), await env.new_run()
    env.redis.xautoclaim.return_value = ["0-0", [("1-0", {"aggregate_id": str(reclaimed)})], []]
    reads = 0

    async def read(*_args: object, **kwargs: object) -> list[object]:
        nonlocal reads
        reads += 1
        assert kwargs["block"] is None
        if reads == 1:
            return [("runs", [("2-0", {"aggregate_id": str(new)})])]
        env.worker.request_stop()
        return []

    env.redis.xreadgroup.side_effect = read
    await asyncio.wait_for(env.worker.run_forever(), timeout=2)
    assert (await env.state(new))[0] == RunStatus.SUCCEEDED
    assert env.start.await_count == 2


@pytest.mark.parametrize("stage", ["load", "ack"])
async def test_cancel_during_preflight_or_ack(
    env: WorkerEnvironment, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    run_id = await env.new_run()
    entered = asyncio.Event()

    async def block(*_args: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    if stage == "load":
        monkeypatch.setattr(env.worker, "_load_request", block)
    else:
        # The first ACK is interrupted; cleanup must re-read the committed terminal state.
        async def ack(*_args: object) -> int:
            if not entered.is_set():
                await block()
            return 1

        env.redis.xack.side_effect = ack
    task = asyncio.create_task(env.worker.process_message("1-0", {"aggregate_id": str(run_id)}))
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = await env.state(run_id)
    if stage == "load":
        assert state == (RunStatus.FAILED, "WORKER_INTERRUPTED", ["run.failed"])
        env.start.assert_not_awaited()
    else:
        assert state[0] == RunStatus.SUCCEEDED
        assert state[2].count("run.completed") == 1


async def test_stop_allows_inflight_success_without_reading_next(env: WorkerEnvironment) -> None:
    run_id = await env.new_run()

    async def complete(*_args: object) -> RuntimeResult:
        env.worker.request_stop()
        await asyncio.sleep(0)
        return RuntimeResult("completed during drain")

    env.start.side_effect = complete
    env.worker.settings.worker_shutdown_grace_seconds = 1
    env.redis.xreadgroup.return_value = [("runs", [("1-0", {"aggregate_id": str(run_id)})])]
    await asyncio.wait_for(env.worker.run_forever(), timeout=2)
    assert (await env.state(run_id))[0] == RunStatus.SUCCEEDED
    env.redis.xreadgroup.assert_awaited_once()
    env.redis.xack.assert_awaited_once()


async def test_stop_during_backoff_is_immediate(env: WorkerEnvironment) -> None:
    failed = asyncio.Event()
    env.worker.settings.worker_retry_initial_seconds = 30
    env.worker.settings.worker_retry_max_seconds = 30

    async def unavailable(*_args: object, **_kwargs: object) -> None:
        failed.set()
        raise RedisConnectionError("offline")

    env.redis.xgroup_create.side_effect = unavailable
    task = asyncio.create_task(env.worker.run_forever())
    await asyncio.wait_for(failed.wait(), timeout=2)
    env.worker.request_stop()
    await asyncio.wait_for(task, timeout=1)
    env.redis.xgroup_create.assert_awaited_once()


async def test_missing_consumer_group_is_recreated(env: WorkerEnvironment) -> None:
    env.redis.xautoclaim.side_effect = [ResponseError("NOGROUP missing group"), ["0-0", [], []]]

    async def read(*_args: object, **_kwargs: object) -> list[object]:
        env.worker.request_stop()
        return []

    env.redis.xreadgroup.side_effect = read
    await asyncio.wait_for(env.worker.run_forever(), timeout=2)
    assert env.redis.xgroup_create.await_count == 2


async def test_preflight_database_failure_keeps_queued_for_recovery(
    env: WorkerEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = await env.new_run()
    with monkeypatch.context() as patch:
        patch.setattr(
            env.worker,
            "_load_request",
            AsyncMock(side_effect=OperationalError("select", {}, Exception("offline"))),
        )
        with pytest.raises(OperationalError):
            await env.worker.process_message("1-0", {"aggregate_id": str(run_id)})
    env.redis.xack.assert_not_awaited()
    assert await env.state(run_id) == (RunStatus.QUEUED, None, [])
    await env.worker.process_message("1-0", {"aggregate_id": str(run_id)})
    assert (await env.state(run_id))[0] == RunStatus.SUCCEEDED


@pytest.mark.parametrize("second_cancel", [False, True])
async def test_cleanup_timeout_or_repeated_cancel_keeps_pending(
    env: WorkerEnvironment, monkeypatch: pytest.MonkeyPatch, second_cancel: bool
) -> None:
    run_id = await env.new_run()
    running, cleaning = asyncio.Event(), asyncio.Event()
    env.worker.settings.worker_cleanup_timeout_seconds = 0.02

    async def block(*_args: object) -> RuntimeResult:
        running.set()
        await asyncio.Event().wait()
        return RuntimeResult("unreachable")

    async def blocked_finish(*_args: object, **_kwargs: object) -> bool:
        cleaning.set()
        await asyncio.Event().wait()
        return False

    env.start.side_effect = block
    monkeypatch.setattr(env.worker, "_finish", blocked_finish)
    task = asyncio.create_task(env.worker.process_message("1-0", {"aggregate_id": str(run_id)}))
    await asyncio.wait_for(running.wait(), timeout=2)
    task.cancel()
    await asyncio.wait_for(cleaning.wait(), timeout=2)
    if second_cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    env.redis.xack.assert_not_awaited()
    assert (await env.state(run_id))[0] == RunStatus.RUNNING
