from __future__ import annotations

from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from agentplane.runtime import RuntimeDefinition, RuntimeEvent, RuntimeMessage, RuntimeRunRequest
from agentplane.runtime.fake import FakeRuntimeAdapter
from agentplane.runtime.langgraph import LangGraphRuntimeAdapter


async def test_fake_runtime_emits_tool_and_model_events() -> None:
    runtime = FakeRuntimeAdapter()
    events: list[RuntimeEvent] = []

    async def emit(event: RuntimeEvent) -> None:
        events.append(event)

    async def is_cancelled() -> bool:
        return False

    request = RuntimeRunRequest(
        run_id=uuid4(),
        session_id=uuid4(),
        trace_id=uuid4(),
        input_text="add: 20, 22",
        definition=RuntimeDefinition(
            agent_definition_id=uuid4(),
            agent_version_id=uuid4(),
            instructions="使用安全工具",
            model_alias="default",
            tool_keys=["calculator.add"],
        ),
    )

    result = await runtime.start_run(request, emit, is_cancelled)

    assert result.output_text == "计算结果是 42"
    assert [event.event_type for event in events] == [
        "tool.started",
        "tool.completed",
        "model.delta",
        "model.delta",
    ]


def test_langgraph_input_uses_stored_history_before_current_message() -> None:
    request = RuntimeRunRequest(
        run_id=uuid4(),
        session_id=uuid4(),
        trace_id=uuid4(),
        input_text="继续说明",
        definition=RuntimeDefinition(
            agent_definition_id=uuid4(),
            agent_version_id=uuid4(),
            instructions="回答测试问题",
            model_alias="default",
            tool_keys=[],
        ),
        history=(
            RuntimeMessage(role="user", content="第一问"),
            RuntimeMessage(role="assistant", content="第一答"),
        ),
    )

    messages = LangGraphRuntimeAdapter._input_messages(  # pyright: ignore[reportPrivateUsage]
        request
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage, HumanMessage]
    assert [message.content for message in messages] == ["第一问", "第一答", "继续说明"]
