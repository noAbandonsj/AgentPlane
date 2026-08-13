from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from agentplane.api.admin_invocation_routes import router as admin_invocation_router
from agentplane.api.admin_routes import router as admin_router
from agentplane.api.application_routes import router as application_router
from agentplane.api.auth_routes import router as auth_router
from agentplane.api.invocation_routes import router as invocation_router
from agentplane.api.routes import router
from agentplane.asyncio_compat import run_async
from agentplane.config import Settings, get_settings
from agentplane.db import create_engine, create_session_factory
from agentplane.errors import install_error_handlers
from agentplane.identity import DevIdentityMiddleware
from agentplane.logging import configure_logging
from agentplane.queue import ensure_run_consumer_group, outbox_publisher_loop
from agentplane.telemetry import initialize_telemetry


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        initialize_telemetry(app_settings)
        engine = create_engine(app_settings)
        session_factory = create_session_factory(engine)
        redis: Redis = Redis.from_url(app_settings.redis_url, decode_responses=True)
        app.state.settings = app_settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.redis = redis
        await ensure_run_consumer_group(redis, app_settings)
        stop_event = asyncio.Event()
        publisher = asyncio.create_task(
            outbox_publisher_loop(session_factory, redis, app_settings, stop_event),
            name="outbox-publisher",
        )
        try:
            yield
        finally:
            stop_event.set()
            publisher.cancel()
            await asyncio.gather(publisher, return_exceptions=True)
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="AgentPlane API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[app_settings.web_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(DevIdentityMiddleware, settings=app_settings)
    install_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(admin_invocation_router)
    app.include_router(application_router)
    app.include_router(invocation_router)
    app.include_router(router)
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    server = uvicorn.Server(
        uvicorn.Config(
            "agentplane.api.app:app",
            host=settings.app_host,
            port=settings.app_port,
        )
    )
    run_async(server.serve())
