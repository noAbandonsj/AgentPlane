from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agentplane.config import Settings
from agentplane.db import Base
from agentplane.identity import IdentityContext
from agentplane.models import AppUser, Tenant, UserRole, UserStatus

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                Tenant(id=TENANT_ID, name="测试租户"),
                AppUser(
                    id=USER_ID,
                    tenant_id=TENANT_ID,
                    login_name="test-admin",
                    display_name="测试用户",
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                ),
            ]
        )
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture
def identity() -> IdentityContext:
    return IdentityContext(tenant_id=TENANT_ID, user_id=USER_ID, role=UserRole.ADMIN)


@pytest.fixture
def configured_settings() -> Settings:
    return Settings(
        app_env="test",
        model_api_key="test-only-key",
        model_name="fake-model",
    )
