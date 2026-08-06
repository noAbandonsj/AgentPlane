from __future__ import annotations

from uuid import uuid4

from agentplane.runtime import RuntimeDefinition, RuntimeEvent, RuntimeRunRequest
from agentplane.runtime.fake import FakeRuntimeAdapter


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
        runtime_thread_id=uuid4(),
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
