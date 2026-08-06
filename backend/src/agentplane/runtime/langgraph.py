from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
)
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agentplane.config import Settings
from agentplane.runtime.base import (
    AgentRuntimeAdapter,
    CancellationCheck,
    RuntimeCancelled,
    RuntimeDefinition,
    RuntimeEvent,
    RuntimeEventEmitter,
    RuntimeResult,
    RuntimeRunRequest,
)
from agentplane.tools import build_langchain_tools, validate_tool_keys


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


class LangGraphRuntimeAdapter(AgentRuntimeAdapter):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model_semaphore = asyncio.Semaphore(settings.model_max_concurrency)

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def validate_definition(self, definition: RuntimeDefinition) -> None:
        if definition.model_alias != "default":
            raise ValueError(f"首版不支持模型别名: {definition.model_alias}")
        validate_tool_keys(definition.tool_keys)

    def _build_graph(self, definition: RuntimeDefinition) -> Any:
        tools = build_langchain_tools(definition.tool_keys)
        model = ChatOpenAI(
            model=self.settings.model_name,
            api_key=SecretStr(self.settings.model_api_key),
            base_url=self.settings.model_base_url or None,
            timeout=self.settings.model_timeout_seconds,
            max_retries=2,
        )

        @wrap_model_call
        async def limit_model_concurrency(
            request: ModelRequest[Any],
            handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
        ) -> ModelResponse[Any]:
            async with self._model_semaphore:
                return await handler(request)

        return create_agent(
            model=model,
            tools=tools,
            system_prompt=definition.instructions,
            middleware=[limit_model_concurrency],
        )

    @staticmethod
    def _input_messages(request: RuntimeRunRequest) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        for message in request.history:
            if message.role == "user":
                messages.append(HumanMessage(content=message.content))
            else:
                messages.append(AIMessage(content=message.content))
        messages.append(HumanMessage(content=request.input_text))
        return messages

    @staticmethod
    def _result_from_messages(messages: list[Any]) -> RuntimeResult:
        final_message = next(
            (message for message in reversed(messages) if isinstance(message, AIMessage)), None
        )
        if final_message is None:
            raise RuntimeError("模型未返回最终消息")
        usage = final_message.usage_metadata or {}
        return RuntimeResult(
            output_text=_content_to_text(final_message.content),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )

    async def start_run(
        self,
        request: RuntimeRunRequest,
        emit: RuntimeEventEmitter,
        is_cancelled: CancellationCheck,
    ) -> RuntimeResult:
        self.validate_definition(request.definition)
        if not self.settings.model_configured:
            raise RuntimeError("MODEL_NOT_CONFIGURED")
        if await is_cancelled():
            raise RuntimeCancelled
        graph = self._build_graph(request.definition)
        graph_input = {"messages": self._input_messages(request)}
        final_messages: list[Any] | None = None
        async for event in graph.astream_events(
            graph_input,
            config={"metadata": {"agentplane_run_id": str(request.run_id)}},
            version="v2",
        ):
            if await is_cancelled():
                raise RuntimeCancelled
            event_name = event.get("event")
            data = event.get("data", {})
            if event_name == "on_chat_model_stream":
                chunk = data.get("chunk")
                if isinstance(chunk, AIMessageChunk):
                    text = _content_to_text(chunk.content)
                    if text:
                        await emit(RuntimeEvent("model.delta", {"delta": text}))
            elif event_name == "on_tool_start":
                await emit(
                    RuntimeEvent(
                        "tool.started",
                        {"tool": event.get("name"), "input": data.get("input")},
                    )
                )
            elif event_name == "on_tool_end":
                await emit(
                    RuntimeEvent(
                        "tool.completed",
                        {"tool": event.get("name"), "output": str(data.get("output"))},
                    )
                )
            elif event_name == "on_chain_end" and not event.get("parent_ids"):
                output = data.get("output")
                if isinstance(output, dict) and isinstance(output.get("messages"), list):
                    final_messages = output["messages"]

        if final_messages is None:
            raise RuntimeError("LangGraph 未返回最终状态")
        return self._result_from_messages(final_messages)

    async def resume_run(
        self,
        request: RuntimeRunRequest,
        approval_payload: dict[str, Any],
        emit: RuntimeEventEmitter,
        is_cancelled: CancellationCheck,
    ) -> RuntimeResult:
        raise NotImplementedError("第一版不支持审批恢复")

    async def cancel_run(self, run_id: UUID) -> None:
        return None
