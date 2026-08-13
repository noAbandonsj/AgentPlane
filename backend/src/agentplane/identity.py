from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from starlette.types import ASGIApp, Receive, Scope, Send

from agentplane.config import Settings
from agentplane.logging import bind_log_context, clear_log_context
from agentplane.models import UserRole


@dataclass(frozen=True, slots=True)
class IdentityContext:
    tenant_id: UUID
    user_id: UUID
    role: UserRole = UserRole.USER


@dataclass(frozen=True, slots=True)
class ApplicationIdentityContext:
    tenant_id: UUID
    application_id: UUID
    credential_id: UUID
    application_code: str


class DevIdentityMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            if self.settings.auth_mode == "dev":
                identity = IdentityContext(
                    tenant_id=self.settings.dev_tenant_id,
                    user_id=self.settings.dev_user_id,
                    role=UserRole.ADMIN,
                )
                scope.setdefault("state", {})["identity"] = identity
                clear_log_context()
                bind_log_context(tenant_id=identity.tenant_id, user_id=identity.user_id)
                try:
                    await self.app(scope, receive, send)
                finally:
                    clear_log_context()
                return
            if self.settings.auth_mode != "local":
                raise RuntimeError("仅支持 AUTH_MODE=dev 或 AUTH_MODE=local")
            clear_log_context()
            try:
                await self.app(scope, receive, send)
            finally:
                clear_log_context()
            return
        await self.app(scope, receive, send)
