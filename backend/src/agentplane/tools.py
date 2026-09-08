from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field, RootModel

from agentplane.schemas import ToolMetadata
from agentplane.tool_contracts import ToolExecutionContext, ToolExecutionError, ToolInvoker


class InvalidToolKeysError(ValueError):
    def __init__(self, unknown_tool_keys: list[str]) -> None:
        self.unknown_tool_keys = unknown_tool_keys
        super().__init__(f"未知工具: {', '.join(unknown_tool_keys)}")


class CalculatorAddInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a: Decimal = Field(description="第一个加数", max_digits=28, decimal_places=10)
    b: Decimal = Field(description="第二个加数", max_digits=28, decimal_places=10)


class TextOutput(RootModel[str]):
    pass


def calculator_add(a: Decimal, b: Decimal) -> str:
    """Add two decimal values without evaluating arbitrary code."""
    with localcontext() as context:
        context.prec = 40
        return format(a + b, "f")


async def _add(_context: ToolExecutionContext, arguments: BaseModel) -> str:
    parsed = CalculatorAddInput.model_validate(arguments)
    return calculator_add(parsed.a, parsed.b)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    key: str
    version: str
    model_name: str
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[ToolExecutionContext, BaseModel], Awaitable[str]]
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    read_only: bool = True
    requires_approval: bool = False
    timeout_seconds: float = 10

    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            key=self.key,
            version=self.version,
            name=self.name,
            description=self.description,
            risk_level=self.risk_level,
            read_only=self.read_only,
            requires_approval=self.requires_approval,
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
            timeout_seconds=self.timeout_seconds,
        )


# A published (key, version) is immutable. Retain older handlers while pinned Runs exist.
TOOL_REGISTRY: dict[tuple[str, str], ToolDefinition] = {
    ("calculator.add", "1.0.0"): ToolDefinition(
        key="calculator.add",
        version="1.0.0",
        model_name="calculator_add",
        name="加法计算器",
        description="对两个十进制数字执行安全加法。",
        input_model=CalculatorAddInput,
        output_model=TextOutput,
        handler=_add,
    )
}
CURRENT_TOOL_VERSIONS = {"calculator.add": "1.0.0"}


def list_tool_metadata() -> list[ToolMetadata]:
    return [
        TOOL_REGISTRY[key, version].metadata() for key, version in CURRENT_TOOL_VERSIONS.items()
    ]


def validate_tool_keys(tool_keys: list[str]) -> None:
    unknown = sorted(set(tool_keys) - CURRENT_TOOL_VERSIONS.keys())
    if unknown:
        raise InvalidToolKeysError(unknown)


def snapshot_tool_bindings(tool_keys: list[str]) -> dict[str, str]:
    validate_tool_keys(tool_keys)
    return {key: CURRENT_TOOL_VERSIONS[key] for key in tool_keys}


def build_langchain_tools(bindings: dict[str, str], invoke: ToolInvoker | None) -> list[BaseTool]:
    def build(definition: ToolDefinition) -> BaseTool:
        async def execute(**arguments: Any) -> str:
            if invoke is None:
                raise ToolExecutionError("TOOL_CONTEXT_MISSING")
            return await invoke(definition.key, definition.version, arguments)

        # JSON schema is presented to the model; validation runs INSIDE the audited gateway.
        return StructuredTool.from_function(
            coroutine=execute,
            name=definition.model_name,
            description=definition.description,
            args_schema=definition.input_model.model_json_schema(),
        )

    result: list[BaseTool] = []
    for key, version in bindings.items():
        definition = TOOL_REGISTRY.get((key, version))
        if definition is None:
            raise ToolExecutionError("TOOL_VERSION_UNAVAILABLE")
        result.append(build(definition))
    return result
