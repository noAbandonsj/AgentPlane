"""Probe v3: confirm on_chat_model_stream + tool events surface from create_agent
with a model that genuinely streams tokens and emits tool_calls."""

from __future__ import annotations

import asyncio

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool


@tool
def calculator_add(a: float, b: float) -> str:
    """Add two numbers."""
    return f"{a + b}"


class StreamingFakeModel(BaseChatModel):
    """Emits a tool call first, then streams a final answer token-by-token."""

    @property
    def _llm_type(self) -> str:
        return "streaming-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def __init__(self) -> None:
        super().__init__()
        self._calls = 0

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls += 1
        if self._calls == 1:
            # First call: request the tool.
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "calculator_add",
                            "index": 0,
                            "args": '{"a": 1, "b": 2}',
                            "id": "call_1",
                        }
                    ],
                )
            )
            return
        # Later calls: stream the final text.
        for ch in ["算", "好", "了"]:
            yield ChatGenerationChunk(message=AIMessageChunk(content=ch))

    def bind_tools(self, tools, **kwargs):
        return self


async def stream(label: str, graph, graph_input: dict) -> None:
    print(f"\n[{label}] event names:")
    seen: dict[str, int] = {}
    deltas: list[str] = []
    tool_events: list[str] = []
    final_messages = None
    async for event in graph.astream_events(graph_input, config={}, version="v2"):
        name = event.get("event")
        seen[name] = seen.get(name, 0) + 1
        data = event.get("data", {})
        if name == "on_chat_model_stream":
            chunk = data.get("chunk")
            if isinstance(chunk, AIMessageChunk):
                deltas.append(str(chunk.content))
        elif name == "on_tool_start":
            tool_events.append(f"start:{event.get('name')}")
        elif name == "on_tool_end":
            tool_events.append(f"end:{event.get('name')}")
        elif name == "on_chain_end" and not event.get("parent_ids"):
            out = data.get("output")
            if isinstance(out, dict) and isinstance(out.get("messages"), list):
                final_messages = out["messages"]
    print("   seen:", dict(seen))
    print("   deltas:", deltas)
    print("   tool_events:", tool_events)
    top = final_messages[-1] if final_messages else None
    print(
        f"   final_messages: {final_messages is not None} "
        f"last={type(top).__name__ if top else None}"
    )


async def main() -> None:
    agent = create_agent(
        model=StreamingFakeModel(),
        tools=[calculator_add],
        system_prompt="你是测试助手。",
    )
    print("nodes:", list(agent.get_graph().nodes.keys()))
    await stream("create_agent", agent, {"messages": [HumanMessage(content="hi")]})


asyncio.run(main())
