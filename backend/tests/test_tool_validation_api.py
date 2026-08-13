from __future__ import annotations

from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agentplane.api.app import create_app
from agentplane.config import Settings
from agentplane.db import Base
from agentplane.models import AppUser, Tenant, UserRole, UserStatus

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def test_unknown_agent_tool_returns_stable_client_error() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add_all(
            [
                Tenant(id=TENANT_ID, name="工具校验测试租户"),
                AppUser(
                    id=USER_ID,
                    tenant_id=TENANT_ID,
                    login_name="test-admin",
                    display_name="测试管理员",
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                ),
            ]
        )
        await db.commit()

    settings = Settings(
        app_env="test",
        auth_mode="dev",
        dev_tenant_id=TENANT_ID,
        dev_user_id=USER_ID,
    )
    app = create_app(settings)
    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/agents",
                json={
                    "name": "非法工具测试助手",
                    "instructions": "不应创建成功",
                    "tool_keys": ["missing.tool"],
                },
            )
    finally:
        await engine.dispose()

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_TOOL_KEYS",
            "message": "请求包含未注册的工具",
            "details": {"unknown_tool_keys": ["missing.tool"]},
        }
    }
