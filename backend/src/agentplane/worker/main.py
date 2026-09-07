from __future__ import annotations

import asyncio
import random
import signal
from contextlib import suppress
from types import FrameType
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentplane.asyncio_compat import run_async
from agentplane.config import Settings, get_settings
from agentplane.db import create_engine, create_session_factory
from agentplane.logging import bind_log_context, clear_log_context, configure_logging, get_logger
from agentplane.models import AgentVersion, MessageRole, RunStatus, TaskRun
from agentplane.queue import ensure_run_consumer_group, notify_run_event
from agentplane.runs.service import (
    append_run_event,
    get_run_for_worker,
    mark_run_cancelled,
    mark_run_failed,
    mark_run_started,
    mark_run_succeeded,
)
from agentplane.runtime import (
    AgentRuntimeAdapter,
    RuntimeCancelled,
    RuntimeDefinition,
    RuntimeEvent,
    RuntimeMessage,
    RuntimeResult,
    RuntimeRunRequest,
)
from agentplane.runtime.langgraph import LangGraphRuntimeAdapter
from agentplane.sessions.service import list_runtime_history
from agentplane.telemetry import initialize_telemetry

logger = get_logger(__name__)


class AgentWorker:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        runtime: AgentRuntimeAdapter,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.redis = redis
        self.runtime = runtime
        self.consumer_name = f"{settings.worker_consumer_name}-{uuid4().hex}"
        self.stop_event = asyncio.Event()
        self._reclaim_cursor = "0-0"

    def request_stop(self) -> None:
        self.stop_event.set()

    async def _ack(self, message_id: str) -> None:
        await self.redis.xack(
            self.settings.redis_run_stream, self.settings.redis_run_group, message_id
        )

    async def _notify(self, run_id: UUID, sequence: int) -> None:
        try:
            await notify_run_event(self.redis, run_id, sequence)
        except Exception:
            logger.exception("run_event_notification_failed", run_id=str(run_id), sequence=sequence)

    async def _emit_runtime_event(
        self, run_id: UUID, message_id: str, runtime_event: RuntimeEvent
    ) -> None:
        async with self.session_factory() as db:
            run = await get_run_for_worker(db, run_id, for_update=True)
            if (
                run is None
                or run.status != RunStatus.RUNNING
                or run.dispatch_message_id != message_id
                or run.cancel_requested_at is not None
            ):
                raise RuntimeCancelled
            event = await append_run_event(db, run, runtime_event.event_type, runtime_event.payload)
            await db.commit()
            await self._notify(run_id, event.sequence)

    async def _is_cancelled(self, run_id: UUID, message_id: str) -> bool:
        async with self.session_factory() as db:
            run = await get_run_for_worker(db, run_id)
            return (
                run is None
                or run.status != RunStatus.RUNNING
                or run.dispatch_message_id != message_id
                or run.cancel_requested_at is not None
            )

    async def _heartbeat_pending(self, message_id: str, stop_event: asyncio.Event) -> None:
        interval = max(self.settings.worker_reclaim_idle_ms / 3000, 1)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                try:
                    await self.redis.xclaim(
                        self.settings.redis_run_stream,
                        self.settings.redis_run_group,
                        self.consumer_name,
                        min_idle_time=0,
                        message_ids=[message_id],
                        justid=True,
                    )
                except Exception:
                    logger.exception("pending_message_heartbeat_failed", message_id=message_id)

    async def _load_request(self, db: AsyncSession, run: TaskRun) -> RuntimeRunRequest:
        version = await db.scalar(
            select(AgentVersion).where(
                AgentVersion.id == run.agent_version_id,
                AgentVersion.tenant_id == run.tenant_id,
            )
        )
        if version is None:
            raise RuntimeError("RUN_CONFIGURATION_MISSING")
        snapshot_tool_keys = set(run.effective_tool_keys)
        effective_tool_keys = [key for key in version.tool_keys if key in snapshot_tool_keys]
        stored_history = await list_runtime_history(db, run)
        history = tuple(
            RuntimeMessage(
                role="user" if message.role == MessageRole.USER else "assistant",
                content=message.content,
            )
            for message in stored_history
        )
        return RuntimeRunRequest(
            run_id=run.id,
            session_id=run.session_id,
            trace_id=run.trace_id,
            input_text=run.input_text,
            definition=RuntimeDefinition(
                agent_definition_id=version.agent_definition_id,
                agent_version_id=version.id,
                instructions=version.instructions,
                model_alias=version.model_alias,
                tool_keys=effective_tool_keys,
            ),
            history=history,
        )

    async def _finish(
        self,
        run_id: UUID,
        message_id: str,
        *,
        result: RuntimeResult | None = None,
        code: str = "RUN_EXECUTION_FAILED",
        message: str = "任务执行失败，请查看服务端日志",
        cancelled: bool = False,
    ) -> bool:
        """Persist a result before permitting ACK; re-read after uncertain commits."""
        sequence: int | None = None
        async with self.session_factory() as db:
            run = await get_run_for_worker(db, run_id, for_update=True)
            if run is None or run.status.is_terminal:
                return True
            if run.dispatch_message_id not in (None, message_id):
                # This delivery is superseded, and must not change the active run.
                return True
            if run.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
                return False
            if run.cancel_requested_at is not None or cancelled:
                event = await mark_run_cancelled(db, run)
            elif result is not None:
                if run.status != RunStatus.RUNNING or run.dispatch_message_id != message_id:
                    return False
                event = await mark_run_succeeded(
                    db,
                    run,
                    result.output_text,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            else:
                event = await mark_run_failed(db, run, code, message)
            sequence = event.sequence
            await db.commit()
        await self._notify(run_id, sequence)
        return True

    async def _execute_message(self, run_id: UUID, message_id: str) -> bool:
        async with self.session_factory() as db:
            run = await get_run_for_worker(db, run_id, for_update=True)
            if run is None or run.status.is_terminal:
                return True
            if run.status == RunStatus.RUNNING:
                # Recovery ends interrupted work; it never replays external side effects.
                await db.rollback()
                return await self._finish(
                    run_id,
                    message_id,
                    code="WORKER_INTERRUPTED",
                    message="Worker 在执行期间中断，请重新提交任务",
                )
            if run.status != RunStatus.QUEUED:
                return False
            run.dispatch_message_id = message_id
            request = await self._load_request(db, run)
            event = await mark_run_started(db, run)
            sequence = event.sequence if event is not None else None
            bind_log_context(
                tenant_id=run.tenant_id,
                user_id=run.user_id,
                agent_id=run.agent_definition_id,
                agent_version_id=run.agent_version_id,
                session_id=run.session_id,
                run_id=run.id,
                trace_id=run.trace_id,
            )
            await db.commit()
        if sequence is not None:
            await self._notify(run_id, sequence)
        try:
            async with asyncio.timeout(self.settings.worker_run_timeout_seconds):
                result = await self.runtime.start_run(
                    request,
                    lambda event: self._emit_runtime_event(run_id, message_id, event),
                    lambda: self._is_cancelled(run_id, message_id),
                )
        except TimeoutError:
            return await self._finish(
                run_id,
                message_id,
                code="RUN_EXECUTION_TIMEOUT",
                message="任务执行超时",
            )
        return await self._finish(run_id, message_id, result=result)

    @staticmethod
    def _is_infrastructure_error(exc: Exception) -> bool:
        return isinstance(
            exc, RedisError | OperationalError | InterfaceError | PoolTimeoutError
        ) or (isinstance(exc, DBAPIError) and exc.connection_invalidated)

    async def _interrupt_message(self, run_id: UUID, message_id: str) -> None:
        try:
            async with asyncio.timeout(self.settings.worker_cleanup_timeout_seconds):
                if await self._finish(
                    run_id,
                    message_id,
                    code="WORKER_INTERRUPTED",
                    message="Worker 停机或执行被中断，请重新提交任务",
                ):
                    await self._ack(message_id)
        except Exception:
            logger.exception("run_interruption_cleanup_failed", run_id=str(run_id))

    async def process_message(self, message_id: str, fields: dict[str, Any]) -> None:
        raw_run_id = fields.get("aggregate_id")
        try:
            if not isinstance(raw_run_id, str):
                raise ValueError("missing aggregate_id")
            run_id = UUID(raw_run_id)
        except ValueError:
            logger.error("invalid_run_message", message_id=message_id)
            await self._ack(message_id)
            return

        clear_log_context()
        bind_log_context(run_id=run_id, message_id=message_id, consumer=self.consumer_name)
        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_pending(message_id, heartbeat_stop),
            name=f"pending-heartbeat-{message_id}",
        )
        try:
            try:
                should_ack = await self._execute_message(run_id, message_id)
            except RuntimeCancelled:
                should_ack = await self._finish(run_id, message_id, cancelled=True)
            except Exception as exc:
                if self._is_infrastructure_error(exc):
                    raise
                logger.exception("run_execution_failed", run_id=str(run_id))
                errors = {
                    "RUN_CONFIGURATION_MISSING": "任务配置不存在",
                    "MODEL_NOT_CONFIGURED": "模型接口尚未配置",
                }
                code = str(exc) if str(exc) in errors else "RUN_EXECUTION_FAILED"
                should_ack = await self._finish(
                    run_id,
                    message_id,
                    code=code,
                    message=errors.get(code, "任务执行失败，请查看服务端日志"),
                )
            if should_ack:
                await self._ack(message_id)
        except asyncio.CancelledError:
            # Keep cancellation distinct from user cancellation. A second cancellation
            # may abort cleanup, but can never reach an unconditional ACK.
            cleanup = asyncio.create_task(self._interrupt_message(run_id, message_id))
            try:
                await asyncio.shield(cleanup)
            finally:
                if not cleanup.done():
                    cleanup.cancel()
                await asyncio.gather(cleanup, return_exceptions=True)
            raise
        finally:
            heartbeat_stop.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            clear_log_context()

    async def _reclaim_pending(self) -> list[tuple[str, dict[str, Any]]]:
        result = await self.redis.xautoclaim(
            self.settings.redis_run_stream,
            self.settings.redis_run_group,
            self.consumer_name,
            min_idle_time=self.settings.worker_reclaim_idle_ms,
            start_id=self._reclaim_cursor,
            count=1,
        )
        self._reclaim_cursor = str(result[0])
        return list(result[1]) if len(result) > 1 else []

    async def _consume(self) -> None:
        group_ready = False
        runtime_ready = False
        reclaim_next = True
        delay = min(
            self.settings.worker_retry_initial_seconds, self.settings.worker_retry_max_seconds
        )
        try:
            while not self.stop_event.is_set():
                try:
                    if not group_ready:
                        await ensure_run_consumer_group(self.redis, self.settings)
                        group_ready = True
                    if not runtime_ready:
                        await self.runtime.open()
                        runtime_ready = True
                    if self.stop_event.is_set():
                        break
                    reclaimed = await self._reclaim_pending() if reclaim_next else []
                    if reclaimed:
                        entries = reclaimed
                        reclaim_next = False
                    else:
                        # Give new deliveries a turn after each reclaimed message.
                        # Continue scanning pending pages without a blocking read.
                        block = (
                            self.settings.worker_block_ms
                            if reclaim_next and self._reclaim_cursor == "0-0"
                            else None
                        )
                        messages = await self.redis.xreadgroup(
                            self.settings.redis_run_group,
                            self.consumer_name,
                            {self.settings.redis_run_stream: ">"},
                            count=1,
                            block=block,
                        )
                        entries = [entry for _stream, batch in messages for entry in batch]
                        reclaim_next = True
                    for message_id, fields in entries:
                        if self.stop_event.is_set():
                            break  # A read racing with stop leaves the delivery pending.
                        await self.process_message(message_id, fields)
                    delay = min(
                        self.settings.worker_retry_initial_seconds,
                        self.settings.worker_retry_max_seconds,
                    )
                except Exception as exc:
                    logger.exception("worker_consume_failed", consumer=self.consumer_name)
                    if isinstance(exc, ResponseError) and "NOGROUP" in str(exc):
                        group_ready = False
                        self._reclaim_cursor = "0-0"
                    try:
                        await asyncio.wait_for(
                            self.stop_event.wait(), timeout=delay * random.uniform(0.8, 1.0)
                        )
                    except TimeoutError:
                        pass
                    delay = min(delay * 2, self.settings.worker_retry_max_seconds)
        finally:
            try:
                async with asyncio.timeout(self.settings.worker_cleanup_timeout_seconds):
                    await self.runtime.close()
            except Exception:
                logger.exception("worker_runtime_close_failed")

    async def _drain(self, consumer: asyncio.Task[None]) -> None:
        done, _ = await asyncio.wait(
            {consumer}, timeout=self.settings.worker_shutdown_grace_seconds
        )
        if not done:
            consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer

    async def run_forever(self) -> None:
        consumer = asyncio.create_task(self._consume(), name="worker-consumer")
        stopping = asyncio.create_task(self.stop_event.wait(), name="worker-stop")
        logger.info("worker_started", consumer=self.consumer_name)
        try:
            await asyncio.wait({consumer, stopping}, return_when=asyncio.FIRST_COMPLETED)
            await self._drain(consumer)
        except asyncio.CancelledError:
            self.request_stop()
            await self._drain(consumer)
            raise
        finally:
            stopping.cancel()
            if not consumer.done():
                consumer.cancel()
            await asyncio.gather(consumer, stopping, return_exceptions=True)
            logger.info("worker_stopped", consumer=self.consumer_name)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    initialize_telemetry(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis: Redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.worker_redis_timeout_seconds,
        socket_timeout=max(
            settings.worker_redis_timeout_seconds, settings.worker_block_ms / 1000 + 1
        ),
    )
    runtime = LangGraphRuntimeAdapter(settings)
    worker = AgentWorker(settings, session_factory, redis, runtime)
    loop = asyncio.get_running_loop()

    def stop_worker(_signum: int, _frame: FrameType | None) -> None:
        loop.call_soon_threadsafe(worker.request_stop)

    # signal.signal also works with the Windows selector loop, unlike add_signal_handler.
    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in previous_handlers:
            signal.signal(sig, stop_worker)
        await worker.run_forever()
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        try:
            async with asyncio.timeout(settings.worker_cleanup_timeout_seconds):
                with suppress(Exception):
                    await redis.aclose()
                await engine.dispose()
        except TimeoutError:
            logger.exception("worker_resource_close_timeout")


def run() -> None:
    run_async(main())


if __name__ == "__main__":
    run()
