from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from agentplane.api.app import create_app
from agentplane.api.deps import get_application_identity
from agentplane.applications.service import authenticate_calling_application
from agentplane.config import Settings
from agentplane.db import Base, utc_now
from agentplane.errors import ApiError
from agentplane.identity import ApplicationIdentityContext
from agentplane.models import (
    ApplicationCredential,
    AppUser,
    CallingApplication,
    Tenant,
    UserRole,
    UserStatus,
)
from agentplane.security import hash_application_token

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _create_environment() -> tuple[
    AsyncEngine, async_sessionmaker[AsyncSession], AsyncClient
]:
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
                Tenant(id=TENANT_ID, name="调用应用测试租户"),
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

    async def application_identity_probe(
        identity: Annotated[ApplicationIdentityContext, Depends(get_application_identity)],
    ) -> dict[str, str]:
        return {
            "tenant_id": str(identity.tenant_id),
            "application_id": str(identity.application_id),
            "credential_id": str(identity.credential_id),
            "application_code": identity.application_code,
        }

    app.add_api_route("/test/application-identity", application_identity_probe, methods=["GET"])
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return engine, session_factory, client


async def test_application_secret_lifecycle_and_authentication() -> None:
    engine, session_factory, client = await _create_environment()
    try:
        created = await client.post(
            "/api/v1/admin/applications",
            json={"code": "CRM", "name": "客户关系管理", "description": "CRM 调用端"},
        )
        assert created.status_code == 201
        created_body = created.json()
        application = created_body["application"]
        first_credential = created_body["credential"]
        first_token = first_credential["token"]
        assert application["code"] == "crm"
        assert application["active"] is True
        assert first_token.startswith("ap_")

        missing_credential = await client.get("/test/application-identity")
        authenticated_request = await client.get(
            "/test/application-identity",
            headers={"Authorization": f"Bearer {first_token}"},
        )
        assert missing_credential.status_code == 401
        assert missing_credential.json()["error"]["code"] == "APPLICATION_AUTHENTICATION_REQUIRED"
        assert authenticated_request.status_code == 200
        assert authenticated_request.json()["application_code"] == "crm"

        async with session_factory() as db:
            stored_credential = await db.scalar(
                select(ApplicationCredential).where(
                    ApplicationCredential.id == UUID(first_credential["id"])
                )
            )
            assert stored_credential is not None
            assert stored_credential.token_hash == hash_application_token(first_token)
            assert first_token not in stored_credential.token_hash

            identity = await authenticate_calling_application(db, first_token)
            assert identity.tenant_id == TENANT_ID
            assert identity.application_id == UUID(application["id"])
            assert identity.credential_id == UUID(first_credential["id"])
            assert identity.application_code == "crm"

        listed = await client.get("/api/v1/admin/applications")
        details = await client.get(f"/api/v1/admin/applications/{application['id']}")
        assert listed.status_code == 200
        assert details.status_code == 200
        assert "token" not in listed.text
        assert "token" not in details.text

        duplicate = await client.post(
            "/api/v1/admin/applications",
            json={"code": "crm", "name": "重复 CRM"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "APPLICATION_CODE_EXISTS"

        invalid_patch = await client.patch(
            f"/api/v1/admin/applications/{application['id']}",
            json={"active": None},
        )
        assert invalid_patch.status_code == 422
        assert invalid_patch.json()["error"]["code"] == "VALIDATION_ERROR"

        rotated = await client.post(
            f"/api/v1/admin/applications/{application['id']}/credentials/rotate",
            json={},
        )
        assert rotated.status_code == 201
        second_credential = rotated.json()
        second_token = second_credential["token"]
        assert second_token != first_token

        credential_list = await client.get(
            f"/api/v1/admin/applications/{application['id']}/credentials"
        )
        assert credential_list.status_code == 200
        credential_rows = credential_list.json()
        assert len(credential_rows) == 2
        assert all("token" not in row for row in credential_rows)
        assert credential_rows[0]["id"] == second_credential["id"]
        assert credential_rows[0]["revoked_at"] is None
        assert credential_rows[1]["id"] == first_credential["id"]
        assert credential_rows[1]["revoked_at"] is not None

        async with session_factory() as db:
            with pytest.raises(ApiError) as revoked:
                await authenticate_calling_application(db, first_token)
            assert revoked.value.code == "INVALID_APPLICATION_CREDENTIAL"
            await authenticate_calling_application(db, second_token)

            stored_second = await db.get(ApplicationCredential, UUID(second_credential["id"]))
            assert stored_second is not None
            stored_second.expires_at = utc_now() - timedelta(seconds=1)
            await db.commit()

        expired_request = await client.get(
            "/test/application-identity",
            headers={"Authorization": f"Bearer {second_token}"},
        )
        assert expired_request.status_code == 401
        assert expired_request.json()["error"]["code"] == "INVALID_APPLICATION_CREDENTIAL"

        rotated_again = await client.post(
            f"/api/v1/admin/applications/{application['id']}/credentials/rotate",
            json={},
        )
        assert rotated_again.status_code == 201
        active_token = rotated_again.json()["token"]

        disabled = await client.patch(
            f"/api/v1/admin/applications/{application['id']}",
            json={"active": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["active"] is False

        async with session_factory() as db:
            with pytest.raises(ApiError) as inactive:
                await authenticate_calling_application(db, active_token)
            assert inactive.value.code == "APPLICATION_DISABLED"
    finally:
        await client.aclose()
        await engine.dispose()


async def test_application_admin_routes_are_tenant_scoped() -> None:
    engine, session_factory, client = await _create_environment()
    other_tenant_id = uuid4()
    other_user_id = uuid4()
    other_application_id = uuid4()
    try:
        async with session_factory() as db:
            db.add_all(
                [
                    Tenant(id=other_tenant_id, name="其他租户"),
                    AppUser(
                        id=other_user_id,
                        tenant_id=other_tenant_id,
                        login_name="other-admin",
                        display_name="其他管理员",
                        role=UserRole.ADMIN,
                        status=UserStatus.ACTIVE,
                    ),
                    CallingApplication(
                        id=other_application_id,
                        tenant_id=other_tenant_id,
                        code="other",
                        name="其他应用",
                        created_by=other_user_id,
                        updated_by=other_user_id,
                    ),
                ]
            )
            await db.commit()

        details = await client.get(f"/api/v1/admin/applications/{other_application_id}")
        update = await client.patch(
            f"/api/v1/admin/applications/{other_application_id}",
            json={"active": False},
        )
        rotate = await client.post(
            f"/api/v1/admin/applications/{other_application_id}/credentials/rotate",
            json={},
        )
        credentials = await client.get(
            f"/api/v1/admin/applications/{other_application_id}/credentials"
        )

        assert details.status_code == 404
        assert update.status_code == 404
        assert rotate.status_code == 404
        assert credentials.status_code == 404
        assert details.json()["error"]["code"] == "APPLICATION_NOT_FOUND"
    finally:
        await client.aclose()
        await engine.dispose()
