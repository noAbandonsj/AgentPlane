from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import agentplane.runtime.langgraph as langgraph_runtime
from agentplane.agents.service import create_agent, publish_agent
from agentplane.api.app import create_app
from agentplane.api.deps import get_identity, require_admin
from agentplane.config import Settings
from agentplane.identity import IdentityContext
from agentplane.models import (
    ApplicationAgentGrant,
    ApplicationCredential,
    ApplicationToolGrant,
    CallingApplication,
    ExternalUserMapping,
    Invocation,
    InvocationDecision,
    TaskRun,
    Tenant,
    ToolCall,
    ToolCallStatus,
    UserAgentGrant,
    UserRole,
    UserToolGrant,
)
from agentplane.runs.service import create_run, mark_run_started
from agentplane.runtime import RuntimeCancelled, RuntimeEvent, RuntimeRunRequest
from agentplane.runtime.fake import FakeRuntimeAdapter
from agentplane.runtime.langgraph import LangGraphRuntimeAdapter
from agentplane.schemas import AgentCreate, RunCreate, SessionCreate
from agentplane.sessions.service import create_chat_session
from agentplane.tool_contracts import ToolExecutionContext, ToolExecutionError
from agentplane.tool_execution import ToolExecutor
from agentplane.tool_management import list_tool_calls, set_tool_enabled
from agentplane.tools import CURRENT_TOOL_VERSIONS, TOOL_REGISTRY, build_langchain_tools
from agentplane.worker.main import AgentWorker
from test_langgraph_runtime import StreamingToolFakeModel


@dataclass
class ToolEnvironment:
    factory: async_sessionmaker[AsyncSession]
    worker: AgentWorker
    request: RuntimeRunRequest
    executor: ToolExecutor
    events: list[RuntimeEvent]
    identity: IdentityContext

    async def calls(self) -> list[ToolCall]:
        async with self.factory() as db:
            return list(
                await list_tool_calls(db, self.identity.tenant_id, run_id=self.request.run_id)
            )


@pytest.fixture
async def env(
    db: AsyncSession, identity: IdentityContext, configured_settings: Settings
) -> ToolEnvironment:
    agent = await create_agent(
        db, identity, AgentCreate(name="tools", instructions="test", tool_keys=["calculator.add"])
    )
    await publish_agent(db, identity, agent.id)
    db.add_all(
        [
            UserAgentGrant(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                agent_definition_id=agent.id,
                granted_by=identity.user_id,
            ),
            UserToolGrant(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                tool_key="calculator.add",
                granted_by=identity.user_id,
            ),
        ]
    )
    await db.flush()
    session = await create_chat_session(db, identity, SessionCreate(agent_id=agent.id))
    run = await create_run(
        db, identity, session.id, RunCreate(input="add:1,2"), configured_settings
    )
    run.dispatch_message_id = "1-0"
    await mark_run_started(db, run)
    await db.commit()
    assert isinstance(db.bind, AsyncEngine)
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    redis = AsyncMock(spec=Redis)
    redis.publish = AsyncMock()
    redis.xack = AsyncMock()
    worker = AgentWorker(configured_settings, factory, redis, FakeRuntimeAdapter())
    request = await worker._load_request(db, run)  # pyright: ignore[reportPrivateUsage]
    await db.rollback()
    assert request.tool_context is not None
    events: list[RuntimeEvent] = []

    async def emit(event: RuntimeEvent) -> None:
        events.append(event)

    executor = ToolExecutor(factory, request.tool_context, emit)
    return ToolEnvironment(factory, worker, request, executor, events, identity)


async def test_success_is_audited_without_raw_values_and_uses_worker_context(
    env: ToolEnvironment,
) -> None:
    assert env.request.execute_tool is not None
    result = await env.request.execute_tool(
        "calculator.add", "1.0.0", {"a": "987654321.1", "b": "0.2"}
    )
    assert result == "987654321.3"
    (call,) = await env.calls()
    assert call.status == ToolCallStatus.SUCCEEDED
    assert call.user_id == env.identity.user_id and call.application_id is None
    assert call.duration_ms is not None and call.completed_at is not None
    assert "987654321" not in str(call.input_summary) + str(call.output_summary)
    assert env.request.definition.tool_bindings == {"calculator.add": "1.0.0"}


@pytest.mark.parametrize(
    "arguments",
    [
        {"a": 1, "b": 2, "tenant_id": "attacker"},
        {"a": "secret-password", "b": 2},
        {"a": "NaN", "b": 1},
        {"a": 1},
        {"a": "1e999999", "b": 2},
    ],
)
async def test_validation_inside_langchain_tool_is_audited(
    env: ToolEnvironment, arguments: dict[str, Any]
) -> None:
    (tool,) = build_langchain_tools(env.request.definition.tool_bindings, env.executor.execute)
    with pytest.raises(ToolExecutionError, match="TOOL_INPUT_INVALID"):
        await tool.ainvoke(arguments)
    (call,) = await env.calls()
    assert call.status == ToolCallStatus.DENIED
    assert call.error_code == "TOOL_INPUT_INVALID"
    assert "secret-password" not in str(call.input_summary)
    assert [event.event_type for event in env.events] == ["tool.failed"]


@pytest.mark.parametrize("revoke", ["tool", "agent", "disabled"])
async def test_live_revocation_denies_pinned_run(env: ToolEnvironment, revoke: str) -> None:
    async with env.factory() as db:
        if revoke == "disabled":
            await set_tool_enabled(db, env.identity, "calculator.add", False)
        elif revoke == "tool":
            await db.execute(
                delete(UserToolGrant).where(UserToolGrant.user_id == env.identity.user_id)
            )
        else:
            await db.execute(
                delete(UserAgentGrant).where(UserAgentGrant.user_id == env.identity.user_id)
            )
        await db.commit()
    with pytest.raises(ToolExecutionError, match=r"TOOL_DISABLED|TOOL_NOT_GRANTED"):
        await env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2})
    assert (await env.calls())[0].status == ToolCallStatus.DENIED


async def test_foreign_tenant_policy_and_audit_are_isolated(env: ToolEnvironment) -> None:
    tenant_id = uuid4()
    async with env.factory() as db:
        db.add(Tenant(id=tenant_id, name="other"))
        await db.commit()
        identity = replace(env.identity, tenant_id=tenant_id)
        await set_tool_enabled(db, identity, "calculator.add", False)
        await db.commit()
        assert not await list_tool_calls(db, tenant_id)
    assert await env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2}) == "3"
    context = replace(env.executor.context, tenant_id=tenant_id)
    with pytest.raises(ToolExecutionError, match="TOOL_CONTEXT_INVALID"):
        await ToolExecutor(env.factory, context, env.executor.emit).execute(
            "calculator.add", "1.0.0", {"a": 1, "b": 2}
        )


@pytest.mark.parametrize("change", ["user", "trace", "dispatch"])
async def test_forged_or_stale_context_cannot_execute(env: ToolEnvironment, change: str) -> None:
    changes = {
        "user": {"user_id": uuid4()},
        "trace": {"trace_id": uuid4()},
        "dispatch": {"dispatch_message_id": "old"},
    }
    context = replace(env.executor.context, **changes[change])
    with pytest.raises(ToolExecutionError, match=r"TOOL_CONTEXT_INVALID|TOOL_RUN_INACTIVE"):
        await ToolExecutor(env.factory, context, env.executor.emit).execute(
            "calculator.add", "1.0.0", {"a": 1, "b": 2}
        )
    (call,) = await env.calls()
    assert call.user_id == env.identity.user_id  # Audit records canonical identity.


async def test_pinned_version_is_not_replaced_by_current(
    env: ToolEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(CURRENT_TOOL_VERSIONS, "calculator.add", "2.0.0")
    assert await env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2}) == "3"
    with pytest.raises(ToolExecutionError, match="TOOL_NOT_GRANTED"):
        await env.executor.execute("calculator.add", "2.0.0", {"a": 1, "b": 2})


@pytest.mark.parametrize("mode", ["write", "approval", "timeout", "error", "output"])
async def test_execution_boundaries(
    env: ToolEnvironment, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    async def handler(_context: ToolExecutionContext, _arguments: BaseModel) -> str:
        if mode == "timeout":
            await asyncio.sleep(1)
        if mode == "error":
            raise ValueError("credential=secret-value")
        if mode == "output":
            return 123  # type: ignore[return-value]
        return "3"

    definition = replace(
        TOOL_REGISTRY["calculator.add", "1.0.0"],
        handler=handler,
        read_only=mode != "write",
        requires_approval=mode == "approval",
        timeout_seconds=0.01,
    )
    monkeypatch.setitem(TOOL_REGISTRY, ("calculator.add", "1.0.0"), definition)
    with pytest.raises(ToolExecutionError) as error:
        await env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2})
    assert "secret-value" not in str(error.value)
    (call,) = await env.calls()
    assert call.status == {
        "write": ToolCallStatus.DENIED,
        "approval": ToolCallStatus.DENIED,
        "timeout": ToolCallStatus.TIMED_OUT,
    }.get(mode, ToolCallStatus.FAILED)


async def test_audit_commit_failure_prevents_handler(
    env: ToolEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = AsyncMock(return_value="3")
    monkeypatch.setitem(
        TOOL_REGISTRY,
        ("calculator.add", "1.0.0"),
        replace(TOOL_REGISTRY["calculator.add", "1.0.0"], handler=handler),
    )
    monkeypatch.setattr(
        AsyncSession, "commit", AsyncMock(side_effect=OperationalError("test", {}, Exception()))
    )
    with pytest.raises(OperationalError):
        await env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2})
    handler.assert_not_awaited()


async def test_cancellation_closes_audit(
    env: ToolEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()

    async def handler(_context: ToolExecutionContext, _arguments: BaseModel) -> str:
        entered.set()
        await asyncio.Event().wait()
        return "unreachable"

    monkeypatch.setitem(
        TOOL_REGISTRY,
        ("calculator.add", "1.0.0"),
        replace(TOOL_REGISTRY["calculator.add", "1.0.0"], handler=handler),
    )
    task = asyncio.create_task(env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2}))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await env.calls())[0].status == ToolCallStatus.CANCELLED


async def test_registered_tool_runs_through_real_graph_and_gateway(
    env: ToolEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = StreamingToolFakeModel()

    def build_model(**_kwargs: Any) -> StreamingToolFakeModel:
        return model

    monkeypatch.setattr(langgraph_runtime, "ChatOpenAI", build_model)
    runtime = LangGraphRuntimeAdapter(env.worker.settings)
    request = replace(env.request, execute_tool=env.executor.execute)

    async def not_cancelled() -> bool:
        return False

    result = await runtime.start_run(request, env.executor.emit, not_cancelled)
    assert result.output_text == "算好了"
    (call,) = await env.calls()
    assert call.status == ToolCallStatus.SUCCEEDED
    assert [event.event_type for event in env.events] == [
        "tool.started",
        "tool.completed",
        "model.delta",
        "model.delta",
        "model.delta",
    ]
    assert env.events[0].payload["call_id"] == str(call.id)
    assert all(
        "input" not in event.payload and "output" not in event.payload for event in env.events
    )


async def test_disabling_after_admission_allows_current_call_but_denies_next(
    env: ToolEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(_context: ToolExecutionContext, _arguments: BaseModel) -> str:
        async with env.factory() as db:
            await set_tool_enabled(db, env.identity, "calculator.add", False)
            await db.commit()
        return "3"

    monkeypatch.setitem(
        TOOL_REGISTRY,
        ("calculator.add", "1.0.0"),
        replace(TOOL_REGISTRY["calculator.add", "1.0.0"], handler=handler),
    )
    assert await env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2}) == "3"
    with pytest.raises(ToolExecutionError, match="TOOL_DISABLED"):
        await env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2})
    assert {call.status for call in await env.calls()} == {
        ToolCallStatus.SUCCEEDED,
        ToolCallStatus.DENIED,
    }


async def test_cooperative_cancellation_before_handler_is_preserved(
    env: ToolEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = AsyncMock(return_value="3")
    monkeypatch.setitem(
        TOOL_REGISTRY,
        ("calculator.add", "1.0.0"),
        replace(TOOL_REGISTRY["calculator.add", "1.0.0"], handler=handler),
    )

    async def cancelled_emit(_event: RuntimeEvent) -> None:
        raise RuntimeCancelled

    executor = ToolExecutor(env.factory, env.executor.context, cancelled_emit)
    with pytest.raises(RuntimeCancelled):
        await executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2})
    handler.assert_not_awaited()
    assert (await env.calls())[0].status == ToolCallStatus.CANCELLED


async def test_framework_reserved_arguments_are_validated_and_audited(
    env: ToolEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = StreamingToolFakeModel(tool_arguments='{"a": 1, "b": 2, "run_manager": "forged"}')

    def build_model(**_kwargs: Any) -> StreamingToolFakeModel:
        return model

    monkeypatch.setattr(langgraph_runtime, "ChatOpenAI", build_model)

    async def not_cancelled() -> bool:
        return False

    runtime = LangGraphRuntimeAdapter(env.worker.settings)
    with pytest.raises(ToolExecutionError, match="TOOL_INPUT_INVALID"):
        await runtime.start_run(
            replace(env.request, execute_tool=env.executor.execute),
            env.executor.emit,
            not_cancelled,
        )
    (call,) = await env.calls()
    assert call.status == ToolCallStatus.DENIED and call.input_summary["field_count"] == 3


async def test_worker_recovery_closes_started_audit_and_rejects_late_result(
    env: ToolEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def handler(_context: ToolExecutionContext, _arguments: BaseModel) -> str:
        await env.worker._finish(env.request.run_id, "1-0", code="WORKER_INTERRUPTED")  # pyright: ignore[reportPrivateUsage]
        return "late-result"

    monkeypatch.setitem(
        TOOL_REGISTRY,
        ("calculator.add", "1.0.0"),
        replace(TOOL_REGISTRY["calculator.add", "1.0.0"], handler=handler),
    )
    with pytest.raises(ToolExecutionError, match="TOOL_EXECUTION_INTERRUPTED"):
        await env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2})
    assert (await env.calls())[0].status == ToolCallStatus.INTERRUPTED


async def test_application_context_and_live_grants(env: ToolEnvironment) -> None:
    context = env.executor.context
    async with env.factory() as db:
        application = CallingApplication(
            tenant_id=context.tenant_id,
            code="test",
            name="test",
            created_by=context.user_id,
            updated_by=context.user_id,
        )
        db.add(application)
        await db.flush()
        credential = ApplicationCredential(
            tenant_id=context.tenant_id,
            application_id=application.id,
            token_hash="test-hash",
            token_prefix="test",
            created_by=context.user_id,
        )
        db.add(credential)
        await db.flush()
        db.add_all(
            [
                ApplicationAgentGrant(
                    tenant_id=context.tenant_id,
                    application_id=application.id,
                    agent_definition_id=env.request.definition.agent_definition_id,
                    granted_by=context.user_id,
                ),
                ApplicationToolGrant(
                    tenant_id=context.tenant_id,
                    application_id=application.id,
                    tool_key="calculator.add",
                    granted_by=context.user_id,
                ),
                ExternalUserMapping(
                    tenant_id=context.tenant_id,
                    application_id=application.id,
                    external_user_id="external",
                    user_id=context.user_id,
                    created_by=context.user_id,
                    updated_by=context.user_id,
                ),
                Invocation(
                    tenant_id=context.tenant_id,
                    application_id=application.id,
                    credential_id=credential.id,
                    external_request_id="request",
                    request_fingerprint="hash",
                    external_user_id="external",
                    user_id=context.user_id,
                    conversation_key="key",
                    requested_agent_id=env.request.definition.agent_definition_id,
                    decision=InvocationDecision.ALLOWED,
                    decision_code="ALLOWED",
                    decision_message="ok",
                    effective_tool_keys=["calculator.add"],
                    run_id=context.run_id,
                ),
            ]
        )
        await db.commit()
        run = await db.get(TaskRun, context.run_id)
        assert run is not None
        request = await env.worker._load_request(db, run)  # pyright: ignore[reportPrivateUsage]
        assert (
            request.tool_context is not None
            and request.tool_context.application_id == application.id
        )
        assert request.tool_context.external_user_id == "external"
    with pytest.raises(ToolExecutionError, match="TOOL_CONTEXT_INVALID"):
        await env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2})
    executor = ToolExecutor(env.factory, request.tool_context, env.executor.emit)
    assert await executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2}) == "3"
    async with env.factory() as db:
        await db.execute(
            delete(ApplicationToolGrant).where(
                ApplicationToolGrant.application_id == application.id
            )
        )
        await db.commit()
    with pytest.raises(ToolExecutionError, match="TOOL_NOT_GRANTED"):
        await executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2})
    assert all(call.application_id == application.id for call in await env.calls())


async def test_management_api_access_filtering_and_policy(env: ToolEnvironment) -> None:
    settings = Settings(
        auth_mode="dev", dev_tenant_id=env.identity.tenant_id, dev_user_id=env.identity.user_id
    )
    app = create_app(settings)
    app.state.session_factory = env.factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        metadata = await client.get("/api/v1/admin/tools")
        assert metadata.status_code == 200
        assert metadata.json()[0]["input_schema"]["additionalProperties"] is False
        versions = await client.get("/api/v1/admin/tools/calculator.add/versions")
        assert versions.json()[0]["version"] == "1.0.0"
        assert (
            await client.patch("/api/v1/admin/tools/calculator.add", json={"enabled": False})
        ).json()["enabled"] is False
        assert (await client.get("/api/v1/tools")).json()[0]["enabled"] is False
        with pytest.raises(ToolExecutionError):
            await env.executor.execute("calculator.add", "1.0.0", {"a": 1, "b": 2})
        calls = await client.get(
            "/api/v1/admin/tool-calls",
            params={"run_id": str(env.request.run_id), "status": "DENIED"},
        )
        assert len(calls.json()) == 1
        assert (
            await client.get("/api/v1/admin/tool-calls", params={"run_id": str(uuid4())})
        ).json() == []
        assert (await client.get("/api/v1/admin/tool-calls?limit=1000")).status_code == 422
        other = replace(env.identity, tenant_id=uuid4())
        app.dependency_overrides[require_admin] = lambda: other
        assert (await client.get("/api/v1/admin/tool-calls")).json() == []
        app.dependency_overrides.clear()

        # require_admin resolves the request identity, not a user-supplied role parameter.
        async def normal_identity() -> IdentityContext:
            return replace(env.identity, role=UserRole.USER)

        # Override middleware identity through a separate route dependency wrapper.
        async def as_user(request: Request) -> IdentityContext:
            request.state.identity = await normal_identity()
            return await require_admin(request)

        app.dependency_overrides[require_admin] = as_user
        app.dependency_overrides[get_identity] = normal_identity
        assert (await client.get("/api/v1/admin/tools")).status_code == 403
        assert (
            await client.patch("/api/v1/admin/tools/calculator.add", json={"enabled": True})
        ).status_code == 403
