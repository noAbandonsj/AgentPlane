from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.config import Settings
from agentplane.errors import ApiError
from agentplane.identity import IdentityContext
from agentplane.models import ChatSession, MessageRole, RunStatus, UserAgentGrant
from agentplane.schemas import AgentCreate, RunCreate, SessionCreate
from agentplane.services import (
    cancel_run,
    create_agent,
    create_chat_session,
    create_run,
    list_run_events_after,
    list_runtime_history,
    mark_run_failed,
    mark_run_started,
    mark_run_succeeded,
    publish_agent,
)


async def _published_session(db: AsyncSession, identity: IdentityContext) -> ChatSession:
    agent = await create_agent(
        db,
        identity,
        AgentCreate(name="运行测试助手", instructions="回答测试问题"),
    )
    await publish_agent(db, identity, agent.id)
    db.add(
        UserAgentGrant(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_definition_id=agent.id,
            granted_by=identity.user_id,
        )
    )
    chat_session = await create_chat_session(
        db,
        identity,
        SessionCreate(agent_id=agent.id, title="运行测试"),
    )
    await db.commit()
    return chat_session


async def test_model_must_be_configured_before_run_creation(
    db: AsyncSession,
    identity: IdentityContext,
) -> None:
    chat_session = await _published_session(db, identity)

    with pytest.raises(ApiError) as caught:
        await create_run(
            db,
            identity,
            chat_session.id,
            RunCreate(input="你好"),
            Settings(app_env="test", model_api_key="", model_name=""),
        )

    assert caught.value.status_code == 503
    assert caught.value.code == "MODEL_NOT_CONFIGURED"


async def test_only_one_active_run_per_session_and_event_order(
    db: AsyncSession,
    identity: IdentityContext,
    configured_settings: Settings,
) -> None:
    chat_session = await _published_session(db, identity)
    run = await create_run(
        db,
        identity,
        chat_session.id,
        RunCreate(input="生成一条回复"),
        configured_settings,
    )
    await db.commit()

    with pytest.raises(ApiError) as caught:
        await create_run(
            db,
            identity,
            chat_session.id,
            RunCreate(input="不能并发的第二条消息"),
            configured_settings,
        )
    assert caught.value.code == "ACTIVE_RUN_EXISTS"

    started = await mark_run_started(db, run)
    completed = await mark_run_succeeded(
        db,
        run,
        "执行完成",
        input_tokens=4,
        output_tokens=2,
    )
    await db.commit()

    assert started is not None
    assert run.status == RunStatus.SUCCEEDED
    assert completed.sequence == 2
    events = await list_run_events_after(db, identity, run.id, 0)
    assert [event.event_type for event in events] == ["run.started", "run.completed"]


async def test_queued_run_cancels_immediately(
    db: AsyncSession,
    identity: IdentityContext,
    configured_settings: Settings,
) -> None:
    chat_session = await _published_session(db, identity)
    run = await create_run(
        db,
        identity,
        chat_session.id,
        RunCreate(input="取消我"),
        configured_settings,
    )
    await db.commit()

    cancelled_run, event = await cancel_run(db, identity, run.id)
    await db.commit()

    assert cancelled_run.status == RunStatus.CANCELLED
    assert cancelled_run.cancel_requested_at is not None
    assert event is not None
    assert event.sequence == 1
    assert event.event_type == "run.cancelled"


async def test_runtime_history_uses_only_successful_run_messages(
    db: AsyncSession,
    identity: IdentityContext,
    configured_settings: Settings,
) -> None:
    chat_session = await _published_session(db, identity)
    successful_run = await create_run(
        db,
        identity,
        chat_session.id,
        RunCreate(input="成功的问题"),
        configured_settings,
    )
    await mark_run_started(db, successful_run)
    await mark_run_succeeded(db, successful_run, "成功的回答")
    await db.commit()

    failed_run = await create_run(
        db,
        identity,
        chat_session.id,
        RunCreate(input="失败的问题"),
        configured_settings,
    )
    await mark_run_started(db, failed_run)
    await mark_run_failed(db, failed_run, "TEST_FAILURE", "测试失败")
    await db.commit()

    current_run = await create_run(
        db,
        identity,
        chat_session.id,
        RunCreate(input="当前问题"),
        configured_settings,
    )

    history = await list_runtime_history(db, current_run)

    assert [(message.role, message.content) for message in history] == [
        (MessageRole.USER, "成功的问题"),
        (MessageRole.ASSISTANT, "成功的回答"),
    ]
