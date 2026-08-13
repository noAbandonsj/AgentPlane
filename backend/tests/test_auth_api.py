from __future__ import annotations

from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agentplane.api.app import create_app
from agentplane.config import Settings
from agentplane.db import Base
from agentplane.models import LocalCredential, Tenant

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def test_local_auth_bootstrap_registration_and_admin_approval() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add(Tenant(id=TENANT_ID, name="本地认证测试租户"))
        await db.commit()

    settings = Settings(app_env="test", auth_mode="local", dev_tenant_id=TENANT_ID)
    app = create_app(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            bootstrap = await client.get("/api/v1/auth/bootstrap-status")
            assert bootstrap.status_code == 200
            assert bootstrap.json() == {"required": True}

            registered = await client.post(
                "/api/v1/auth/register",
                json={
                    "login_name": "normal-user",
                    "display_name": "普通用户",
                    "password": "secret1",
                },
            )
            assert registered.status_code == 201
            normal_user = registered.json()
            assert normal_user["status"] == "PENDING"

            pending_login = await client.post(
                "/api/v1/auth/login",
                json={"login_name": "normal-user", "password": "secret1"},
            )
            assert pending_login.status_code == 403
            assert pending_login.json()["error"]["code"] == "ACCOUNT_PENDING"

            admin_created = await client.post(
                "/api/v1/auth/bootstrap-admin",
                json={
                    "login_name": "admin-user",
                    "display_name": "管理员",
                    "password": "secret2",
                },
            )
            assert admin_created.status_code == 201
            assert admin_created.json()["role"] == "ADMIN"
            assert settings.auth_cookie_name in client.cookies

            current = await client.get("/api/v1/auth/me")
            assert current.status_code == 200
            assert current.json()["login_name"] == "admin-user"

            activated = await client.patch(
                f"/api/v1/admin/users/{normal_user['id']}/status",
                json={"status": "ACTIVE"},
            )
            assert activated.status_code == 200
            assert activated.json()["status"] == "ACTIVE"

            logged_in = await client.post(
                "/api/v1/auth/login",
                json={"login_name": "normal-user", "password": "secret1"},
            )
            assert logged_in.status_code == 200
            assert logged_in.json()["role"] == "USER"

            forbidden = await client.get("/api/v1/admin/users")
            assert forbidden.status_code == 403
            assert forbidden.json()["error"]["code"] == "ADMIN_REQUIRED"

            applications_forbidden = await client.get("/api/v1/admin/applications")
            assert applications_forbidden.status_code == 403
            assert applications_forbidden.json()["error"]["code"] == "ADMIN_REQUIRED"

            application_access_forbidden = await client.get(
                "/api/v1/admin/applications/00000000-0000-0000-0000-000000000001/permissions"
            )
            assert application_access_forbidden.status_code == 403
            assert application_access_forbidden.json()["error"]["code"] == "ADMIN_REQUIRED"

            invocations_forbidden = await client.get("/api/v1/admin/invocations")
            assert invocations_forbidden.status_code == 403
            assert invocations_forbidden.json()["error"]["code"] == "ADMIN_REQUIRED"

        async with session_factory() as db:
            password_hash = await db.scalar(
                select(LocalCredential.password_hash).where(
                    LocalCredential.user_id == UUID(normal_user["id"])
                )
            )
            assert password_hash is not None
            assert password_hash != "secret1"
            assert password_hash.startswith("pbkdf2_sha256$")
    finally:
        await engine.dispose()
