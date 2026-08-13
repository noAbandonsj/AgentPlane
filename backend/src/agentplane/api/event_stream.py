from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import orjson
from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentplane.config import Settings
from agentplane.identity import IdentityContext
from agentplane.queue import run_event_channel
from agentplane.schemas import RunEventRead
from agentplane.services import get_run, list_run_events_after


def sse_event(event: RunEventRead) -> str:
    data = orjson.dumps(event.model_dump(mode="json")).decode("utf-8")
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


async def run_event_stream(
    request: Request,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    identity: IdentityContext,
    run_id: UUID,
    start_sequence: int,
    settings: Settings,
) -> AsyncIterator[str]:
    sequence = start_sequence
    last_keepalive = asyncio.get_running_loop().time()
    pubsub = redis.pubsub()
    await pubsub.subscribe(run_event_channel(run_id))
    try:
        while not await request.is_disconnected():
            async with session_factory() as db:
                events = await list_run_events_after(db, identity, run_id, sequence)
                run = await get_run(db, identity, run_id)
            for event in events:
                event_model = RunEventRead.model_validate(event)
                sequence = event.sequence
                yield sse_event(event_model)
            if run.status.is_terminal and not events:
                break
            try:
                await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=settings.sse_poll_seconds,
                )
            except TimeoutError:
                pass
            now = asyncio.get_running_loop().time()
            if now - last_keepalive >= settings.sse_keepalive_seconds:
                last_keepalive = now
                yield ": keepalive\n\n"
    finally:
        await pubsub.unsubscribe(run_event_channel(run_id))
        await pubsub.aclose()
