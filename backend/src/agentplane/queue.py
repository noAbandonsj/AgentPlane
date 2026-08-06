from __future__ import annotations

import asyncio
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentplane.config import Settings
from agentplane.db import utc_now
from agentplane.logging import get_logger
from agentplane.models import OutboxEvent

logger = get_logger(__name__)


def run_event_channel(run_id: UUID) -> str:
    return f"agentplane:run-events:{run_id}"


async def ensure_run_consumer_group(redis: Redis, settings: Settings) -> None:
    try:
        await redis.xgroup_create(
            settings.redis_run_stream,
            settings.redis_run_group,
            id="0-0",
            mkstream=True,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def publish_outbox_batch(
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    settings: Settings,
    batch_size: int = 50,
) -> int:
    published = 0
    for _ in range(batch_size):
        async with session_factory() as db:
            event = await db.scalar(
                select(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if event is None:
                break
            try:
                await redis.xadd(
                    settings.redis_run_stream,
                    {
                        "outbox_id": str(event.id),
                        "event_type": event.event_type,
                        "aggregate_id": str(event.aggregate_id),
                        "tenant_id": str(event.tenant_id),
                    },
                )
                event.published_at = utc_now()
                event.publish_attempts += 1
                event.last_error = None
                await db.commit()
                published += 1
            except Exception as exc:
                await db.rollback()
                failed_event = await db.get(OutboxEvent, event.id, with_for_update=True)
                if failed_event is not None:
                    failed_event.publish_attempts += 1
                    failed_event.last_error = str(exc)[:2000]
                    await db.commit()
                raise
    return published


async def outbox_publisher_loop(
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    settings: Settings,
    stop_event: asyncio.Event,
) -> None:
    logger.info("outbox_publisher_started")
    while not stop_event.is_set():
        try:
            published = await publish_outbox_batch(session_factory, redis, settings)
            if published == 0:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.outbox_poll_seconds)
        except TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("outbox_publish_failed")
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=min(settings.outbox_poll_seconds * 4, 5)
                )
            except TimeoutError:
                continue
    logger.info("outbox_publisher_stopped")


async def notify_run_event(redis: Redis, run_id: UUID, sequence: int) -> None:
    await redis.publish(run_event_channel(run_id), str(sequence))
