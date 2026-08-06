from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentplane.asyncio_compat import run_async
from agentplane.config import Settings, get_settings
from agentplane.db import create_engine, create_session_factory
from agentplane.logging import bind_log_context, clear_log_context, configure_logging, get_logger
from agentplane.models import AgentVersion, MessageRole, RunStatus, TaskRun
from agentplane.queue import ensure_run_consumer_group, notify_run_event
from agentplane.runtime import (
    AgentRuntimeAdapter,
    RuntimeCancelled,
    RuntimeDefinition,
    RuntimeEvent,
    RuntimeMessage,
    RuntimeRunRequest,
)
from agentplane.runtime.langgraph import LangGraphRuntimeAdapter
from agentplane.services import (
    append_run_event,
    get_run_for_worker,
    list_runtime_history,
    mark_run_cancelled,
    mark_run_failed,
    mark_run_started,
    mark_run_succeeded,
)
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

    async def _notify(self, run_id: UUID, sequence: int) -> None:
        try:
            await notify_run_event(self.redis, run_id, sequence)
        except Exception:
            logger.exception("run_event_notification_failed", run_id=str(run_id), sequence=sequence)

    async def _emit_runtime_event(self, run_id: UUID, runtime_event: RuntimeEvent) -> None:
        async with self.session_factory() as db:
            run = await get_run_for_worker(db, run_id)
            if run is None or run.status.is_terminal:
                return
            event = await append_run_event(db, run, runtime_event.event_type, runtime_event.payload)
            await db.commit()
            await self._notify(run_id, event.sequence)

    async def _is_cancelled(self, run_id: UUID) -> bool:
        async with self.session_factory() as db:
            run = await get_run_for_worker(db, run_id)
            return (
                run is None
                or run.status == RunStatus.CANCELLED
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
                        self.settings.worker_consumer_name,
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

    async def process_message(self, message_id: str, fields: dict[str, Any]) -> None:
        raw_run_id = fields.get("aggregate_id")
        if not isinstance(raw_run_id, str):
            logger.error("invalid_run_message", message_id=message_id, fields=fields)
            await self.redis.xack(
                self.settings.redis_run_stream, self.settings.redis_run_group, message_id
            )
            return
        try:
            run_id = UUID(raw_run_id)
        except ValueError:
            logger.error("invalid_run_id", message_id=message_id, run_id=raw_run_id)
            await self.redis.xack(
                self.settings.redis_run_stream, self.settings.redis_run_group, message_id
            )
            return

        started_event_sequence: int | None = None
        async with self.session_factory() as db:
            run = await get_run_for_worker(db, run_id, for_update=True)
            if run is None or run.status.is_terminal:
                await db.rollback()
                await self.redis.xack(
                    self.settings.redis_run_stream, self.settings.redis_run_group, message_id
                )
                return
            if run.status == RunStatus.RUNNING:
                if run.dispatch_message_id != message_id:
                    await db.rollback()
                    await self.redis.xack(
                        self.settings.redis_run_stream,
                        self.settings.redis_run_group,
                        message_id,
                    )
                    return
                event = await mark_run_failed(
                    db,
                    run,
                    "WORKER_INTERRUPTED",
                    "Worker 在执行期间中断，请重新提交任务",
                )
                await db.commit()
                await self._notify(run_id, event.sequence)
                await self.redis.xack(
                    self.settings.redis_run_stream,
                    self.settings.redis_run_group,
                    message_id,
                )
                return
            elif run.status == RunStatus.QUEUED:
                run.dispatch_message_id = message_id
                event = await mark_run_started(db, run)
            else:
                await db.rollback()
                await self.redis.xack(
                    self.settings.redis_run_stream,
                    self.settings.redis_run_group,
                    message_id,
                )
                return
            request = await self._load_request(db, run)
            run_context = {
                "tenant_id": run.tenant_id,
                "user_id": run.user_id,
                "agent_id": run.agent_definition_id,
                "agent_version_id": run.agent_version_id,
                "session_id": run.session_id,
                "run_id": run.id,
                "trace_id": run.trace_id,
            }
            if event is not None:
                started_event_sequence = event.sequence
            await db.commit()
        if started_event_sequence is not None:
            await self._notify(run_id, started_event_sequence)

        clear_log_context()
        bind_log_context(**run_context)
        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_pending(message_id, heartbeat_stop),
            name=f"pending-heartbeat-{message_id}",
        )
        try:
            result = await self.runtime.start_run(
                request,
                lambda event: self._emit_runtime_event(run_id, event),
                lambda: self._is_cancelled(run_id),
            )
            async with self.session_factory() as db:
                run = await get_run_for_worker(db, run_id, for_update=True)
                if run is None:
                    raise RuntimeError("RUN_NOT_FOUND")
                if run.cancel_requested_at is not None:
                    terminal_event = await mark_run_cancelled(db, run)
                else:
                    terminal_event = await mark_run_succeeded(
                        db,
                        run,
                        result.output_text,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                    )
                await db.commit()
            await self._notify(run_id, terminal_event.sequence)
        except RuntimeCancelled:
            async with self.session_factory() as db:
                run = await get_run_for_worker(db, run_id, for_update=True)
                if run is not None and not run.status.is_terminal:
                    terminal_event = await mark_run_cancelled(db, run)
                    await db.commit()
                    await self._notify(run_id, terminal_event.sequence)
        except Exception as exc:
            logger.exception("run_execution_failed", run_id=str(run_id))
            async with self.session_factory() as db:
                run = await get_run_for_worker(db, run_id, for_update=True)
                if run is not None and not run.status.is_terminal:
                    code = (
                        "MODEL_NOT_CONFIGURED"
                        if str(exc) == "MODEL_NOT_CONFIGURED"
                        else "RUN_EXECUTION_FAILED"
                    )
                    terminal_event = await mark_run_failed(db, run, code, str(exc))
                    await db.commit()
                    await self._notify(run_id, terminal_event.sequence)
        finally:
            heartbeat_stop.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            try:
                await self.redis.xack(
                    self.settings.redis_run_stream,
                    self.settings.redis_run_group,
                    message_id,
                )
            finally:
                clear_log_context()

    async def _reclaim_pending(self) -> list[tuple[str, dict[str, Any]]]:
        result = await self.redis.xautoclaim(
            self.settings.redis_run_stream,
            self.settings.redis_run_group,
            self.settings.worker_consumer_name,
            min_idle_time=self.settings.worker_reclaim_idle_ms,
            start_id="0-0",
            count=10,
        )
        return list(result[1]) if len(result) > 1 else []

    async def run_forever(self) -> None:
        await ensure_run_consumer_group(self.redis, self.settings)
        await self.runtime.open()
        logger.info("worker_started", consumer=self.settings.worker_consumer_name)
        try:
            while True:
                reclaimed = await self._reclaim_pending()
                if reclaimed:
                    for message_id, fields in reclaimed:
                        await self.process_message(message_id, fields)
                    continue
                messages = await self.redis.xreadgroup(
                    self.settings.redis_run_group,
                    self.settings.worker_consumer_name,
                    {self.settings.redis_run_stream: ">"},
                    count=1,
                    block=self.settings.worker_block_ms,
                )
                for _stream, entries in messages:
                    for message_id, fields in entries:
                        await self.process_message(message_id, fields)
        finally:
            await self.runtime.close()
            logger.info("worker_stopped")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    initialize_telemetry(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    runtime = LangGraphRuntimeAdapter(settings)
    worker = AgentWorker(settings, session_factory, redis, runtime)
    try:
        await worker.run_forever()
    finally:
        with suppress(Exception):
            await redis.aclose()
        await engine.dispose()


def run() -> None:
    run_async(main())


if __name__ == "__main__":
    run()
