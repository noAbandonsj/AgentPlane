from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.api.deps import get_app_settings, get_db, get_identity, get_redis
from agentplane.config import Settings
from agentplane.identity import IdentityContext
from agentplane.schemas import CapabilityResponse, HealthResponse, ToolMetadata
from agentplane.tool_management import list_tenant_tools

router = APIRouter(prefix="/api/v1", tags=["system"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
IdentityDep = Annotated[IdentityContext, Depends(get_identity)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]


@router.get("/tools", response_model=list[ToolMetadata])
async def tools_list(db: DbDep, identity: IdentityDep) -> list[ToolMetadata]:
    return await list_tenant_tools(db, identity.tenant_id)


@router.get("/health/live", response_model=HealthResponse)
async def health_live() -> HealthResponse:
    return HealthResponse(status="ok", checks={"process": "ok"})


@router.get("/health/ready", response_model=HealthResponse)
async def health_ready(db: DbDep, redis: RedisDep) -> HealthResponse:
    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["postgresql"] = "ok"
    except Exception:
        checks["postgresql"] = "unavailable"
    try:
        checks["redis"] = "ok" if await redis.ping() else "unavailable"
    except Exception:
        checks["redis"] = "unavailable"
    if all(value == "ok" for value in checks.values()):
        return HealthResponse(status="ok", checks=checks)
    return HealthResponse(status="degraded", checks=checks)


@router.get("/capabilities", response_model=CapabilityResponse)
async def capabilities(
    settings: SettingsDep, db: DbDep, identity: IdentityDep
) -> CapabilityResponse:
    return CapabilityResponse(
        runtime="langgraph",
        model_configured=settings.model_configured,
        model_aliases=["default"] if settings.model_configured else [],
        tools=await list_tenant_tools(db, identity.tenant_id),
        approval_resume_supported=False,
    )


@router.get("/openapi-version")
async def openapi_version(response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"version": "v1"}
