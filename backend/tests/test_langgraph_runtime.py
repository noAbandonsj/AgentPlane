from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.base import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import Field

import agentplane.runtime.langgraph as langgraph_runtime
from agentplane.config import Settings
from agentplane.runtime import RuntimeDefinition, RuntimeEvent, RuntimeRunRequest
from agentplane.runtime.langgraph import LangGraphRuntimeAdapter
from agentplane.tool_contracts import ToolExecutionError
from agentplane.tools import snapshot_tool_bindings


class StreamingToolFakeModel(BaseChatModel):
    calls: int = 0
    tool_name: str = "calculator_add"
    tool_arguments: str = '{"a": 1, "b": 2}'
    received_messages: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "streaming-tool-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="unused"))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        self.calls += 1
        self.received_messages.append(messages)
        if self.calls == 1:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": self.tool_name,
                            "index": 0,
                            "args": self.tool_arguments,
                            "id": "call_1",
                        }
                    ],
                )
            )
            return
        for text in ("算", "好", "了"):
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


class ConcurrentStreamingFakeModel(BaseChatModel):
    active_calls: int = 0
    max_active_calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "concurrent-streaming-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="完成"))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0.05)
            yield ChatGenerationChunk(message=AIMessageChunk(content="完成"))
        finally:
            self.active_calls -= 1


def _request(*, instructions: str = "使用安全工具", tool_keys: list[str]) -> RuntimeRunRequest:
    return RuntimeRunRequest(
        run_id=uuid4(),
        session_id=uuid4(),
        trace_id=uuid4(),
        input_text="请计算",
        definition=RuntimeDefinition(
            agent_definition_id=uuid4(),
            agent_version_id=uuid4(),
            instructions=instructions,
            model_alias="default",
            tool_keys=tool_keys,
            tool_bindings=snapshot_tool_bindings(tool_keys),
        ),
    )


async def _not_cancelled() -> bool:
    return False


async def test_langgraph_runtime_preserves_stream_event_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = StreamingToolFakeModel()

    def build_fake_model(**kwargs: Any) -> BaseChatModel:
        return model

    monkeypatch.setattr(langgraph_runtime, "ChatOpenAI", build_fake_model)
    runtime = LangGraphRuntimeAdapter(
        Settings(app_env="test", model_api_key="test-key", model_name="fake-model")
    )
    events: list[RuntimeEvent] = []

    async def emit(event: RuntimeEvent) -> None:
        events.append(event)

    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def execute(key: str, version: str, arguments: dict[str, Any]) -> str:
        calls.append((key, version, arguments))
        await emit(RuntimeEvent("tool.started", {"tool": key, "version": version}))
        await emit(RuntimeEvent("tool.completed", {"tool": key, "status": "SUCCEEDED"}))
        return "3"

    request = replace(_request(tool_keys=["calculator.add"]), execute_tool=execute)
    result = await runtime.start_run(request, emit, _not_cancelled)

    assert result.output_text == "算好了"
    assert [event.event_type for event in events] == [
        "tool.started",
        "tool.completed",
        "model.delta",
        "model.delta",
        "model.delta",
    ]
    assert events[0].payload == {
        "tool": "calculator.add",
        "version": "1.0.0",
    }
    assert events[1].payload["tool"] == "calculator.add"
    assert calls == [("calculator.add", "1.0.0", {"a": 1, "b": 2})]
    assert all("input" not in event.payload and "output" not in event.payload for event in events)
    assert [event.payload["delta"] for event in events[2:]] == ["算", "好", "了"]
    assert isinstance(model.received_messages[0][0], SystemMessage)
    assert model.received_messages[0][0].content == "使用安全工具"


async def test_langgraph_runtime_limits_concurrent_model_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ConcurrentStreamingFakeModel()

    def build_fake_model(**kwargs: Any) -> BaseChatModel:
        return model

    monkeypatch.setattr(langgraph_runtime, "ChatOpenAI", build_fake_model)
    runtime = LangGraphRuntimeAdapter(
        Settings(
            app_env="test",
            model_api_key="test-key",
            model_name="fake-model",
            model_max_concurrency=1,
        )
    )

    async def emit(event: RuntimeEvent) -> None:
        return None

    results = await asyncio.gather(
        runtime.start_run(_request(tool_keys=[]), emit, _not_cancelled),
        runtime.start_run(_request(tool_keys=[]), emit, _not_cancelled),
    )

    assert [result.output_text for result in results] == ["完成", "完成"]
    assert model.max_active_calls == 1


async def test_unknown_model_tool_is_rejected_through_audit_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = StreamingToolFakeModel(tool_name="arbitrary_sql")

    def build_fake_model(**_kwargs: Any) -> BaseChatModel:
        return model

    monkeypatch.setattr(langgraph_runtime, "ChatOpenAI", build_fake_model)
    calls: list[str] = []

    async def execute(key: str, _version: str, _arguments: dict[str, Any]) -> str:
        calls.append(key)
        raise ToolExecutionError("TOOL_NOT_GRANTED")

    async def emit(_event: RuntimeEvent) -> None:
        pass

    runtime = LangGraphRuntimeAdapter(Settings(model_api_key="test", model_name="test"))
    request = replace(_request(tool_keys=["calculator.add"]), execute_tool=execute)
    with pytest.raises(ToolExecutionError, match="TOOL_NOT_GRANTED"):
        await runtime.start_run(request, emit, _not_cancelled)
    assert calls == ["unregistered:arbitrary_sql"]
