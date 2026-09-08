from __future__ import annotations

from uuid import UUID

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
from agentplane.tools import validate_tool_keys


class FakeRuntimeAdapter(AgentRuntimeAdapter):
    """Deterministic runtime available only to tests."""

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def validate_definition(self, definition: RuntimeDefinition) -> None:
        validate_tool_keys(definition.tool_keys)

    async def start_run(
        self,
        request: RuntimeRunRequest,
        emit: RuntimeEventEmitter,
        is_cancelled: CancellationCheck,
    ) -> RuntimeResult:
        self.validate_definition(request.definition)
        if await is_cancelled():
            raise RuntimeCancelled
        if (
            request.input_text.startswith("add:")
            and "calculator.add" in request.definition.tool_keys
        ):
            values = request.input_text.removeprefix("add:").split(",", maxsplit=1)
            if request.execute_tool is not None:
                result = await request.execute_tool(
                    "calculator.add",
                    request.definition.tool_bindings["calculator.add"],
                    {"a": values[0].strip(), "b": values[1].strip()},
                )
            else:
                # Standalone adapter tests have no database or production executor.
                await emit(RuntimeEvent("tool.started", {"tool": "calculator_add"}))
                result = str(int(values[0].strip()) + int(values[1].strip()))
                await emit(
                    RuntimeEvent("tool.completed", {"tool": "calculator_add", "output": result})
                )
            output = f"计算结果是 {result}"
        else:
            output = f"测试回复：{request.input_text}"
        for chunk in (output[: max(1, len(output) // 2)], output[max(1, len(output) // 2) :]):
            if await is_cancelled():
                raise RuntimeCancelled
            if chunk:
                await emit(RuntimeEvent("model.delta", {"delta": chunk}))
        return RuntimeResult(output_text=output, input_tokens=5, output_tokens=5)

    async def resume_run(
        self,
        request: RuntimeRunRequest,
        approval_payload: dict[str, object],
        emit: RuntimeEventEmitter,
        is_cancelled: CancellationCheck,
    ) -> RuntimeResult:
        raise NotImplementedError("第一版不支持审批恢复")

    async def cancel_run(self, run_id: UUID) -> None:
        return None
