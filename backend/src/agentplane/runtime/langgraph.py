from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
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
        self._checkpoint_context: AbstractAsyncContextManager[AsyncPostgresSaver] | None = None
        self._checkpointer: AsyncPostgresSaver | None = None
        self._model_semaphore = asyncio.Semaphore(settings.model_max_concurrency)

    async def open(self) -> None:
        if self._checkpointer is not None:
            return
        context = AsyncPostgresSaver.from_conn_string(self.settings.checkpoint_database_url)
        self._checkpoint_context = context
        self._checkpointer = await context.__aenter__()
        await self._checkpointer.setup()

    async def close(self) -> None:
        if self._checkpoint_context is not None:
            await self._checkpoint_context.__aexit__(None, None, None)
        self._checkpoint_context = None
        self._checkpointer = None

    def validate_definition(self, definition: RuntimeDefinition) -> None:
        if definition.model_alias != "default":
            raise ValueError(f"首版不支持模型别名: {definition.model_alias}")
        validate_tool_keys(definition.tool_keys)

    def _build_graph(self, definition: RuntimeDefinition) -> Any:
        if self._checkpointer is None:
            raise RuntimeError("LangGraphRuntimeAdapter 尚未打开")
        tools = build_langchain_tools(definition.tool_keys)
        model = ChatOpenAI(
            model=self.settings.model_name,
            api_key=SecretStr(self.settings.model_api_key),
            base_url=self.settings.model_base_url or None,
            timeout=self.settings.model_timeout_seconds,
            max_retries=2,
        )
        runnable_model = model.bind_tools(tools) if tools else model

        async def call_model(state: MessagesState) -> dict[str, list[AIMessage]]:
            messages = [SystemMessage(content=definition.instructions), *state["messages"]]
            async with self._model_semaphore:
                response = await runnable_model.ainvoke(messages)
            return {"messages": [response]}

        builder = StateGraph(MessagesState)
        builder.add_node("agent", call_model)
        builder.add_edge(START, "agent")
        if tools:
            builder.add_node("tools", ToolNode(tools))
            builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
            builder.add_edge("tools", "agent")
        else:
            builder.add_edge("agent", END)
        return builder.compile(checkpointer=self._checkpointer)

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
        run_id = str(request.run_id)
        config = {
            "configurable": {"thread_id": str(request.runtime_thread_id)},
            "metadata": {"agentplane_run_id": run_id},
        }
        prior_snapshot = await graph.aget_state(config)
        same_run = prior_snapshot.metadata.get("agentplane_run_id") == run_id
        if same_run and not prior_snapshot.next:
            return self._result_from_messages(prior_snapshot.values.get("messages", []))
        graph_input = (
            None if same_run else {"messages": [{"role": "user", "content": request.input_text}]}
        )
        async for event in graph.astream_events(
            graph_input,
            config=config,
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

        snapshot = await graph.aget_state(config)
        return self._result_from_messages(snapshot.values.get("messages", []))

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
