from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentplane.config import Settings
from agentplane.identity import IdentityContext


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_identity(request: Request) -> IdentityContext:
    return cast(IdentityContext, request.state.identity)


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory(request)
    async with session_factory() as session:
        yield session
