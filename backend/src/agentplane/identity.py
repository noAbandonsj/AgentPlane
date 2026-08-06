from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from starlette.types import ASGIApp, Receive, Scope, Send

from agentplane.config import Settings
from agentplane.logging import bind_log_context, clear_log_context


@dataclass(frozen=True, slots=True)
class IdentityContext:
    tenant_id: UUID
    user_id: UUID


class DevIdentityMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            if self.settings.auth_mode != "dev":
                raise RuntimeError("第一版仅支持 AUTH_MODE=dev")
            identity = IdentityContext(
                tenant_id=self.settings.dev_tenant_id,
                user_id=self.settings.dev_user_id,
            )
            scope.setdefault("state", {})["identity"] = identity
            clear_log_context()
            bind_log_context(tenant_id=identity.tenant_id, user_id=identity.user_id)
            try:
                await self.app(scope, receive, send)
            finally:
                clear_log_context()
            return
        await self.app(scope, receive, send)
