from __future__ import annotations

from decimal import Decimal
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from agentplane.schemas import ToolMetadata


class InvalidToolKeysError(ValueError):
    def __init__(self, unknown_tool_keys: list[str]) -> None:
        self.unknown_tool_keys = unknown_tool_keys
        super().__init__(f"未知工具: {', '.join(unknown_tool_keys)}")


class CalculatorAddInput(BaseModel):
    a: Decimal = Field(description="第一个加数")
    b: Decimal = Field(description="第二个加数")


def calculator_add(a: Decimal, b: Decimal) -> str:
    """Add two decimal values without evaluating arbitrary code."""
    return format(a + b, "f")


TOOL_METADATA: dict[str, ToolMetadata] = {
    "calculator.add": ToolMetadata(
        key="calculator.add",
        name="加法计算器",
        description="对两个十进制数字执行安全加法。",
        risk_level="LOW",
        requires_approval=False,
    )
}


def list_tool_metadata() -> list[ToolMetadata]:
    return list(TOOL_METADATA.values())


def validate_tool_keys(tool_keys: list[str]) -> None:
    unknown = sorted(set(tool_keys) - TOOL_METADATA.keys())
    if unknown:
        raise InvalidToolKeysError(unknown)


def build_langchain_tools(tool_keys: list[str]) -> list[BaseTool]:
    validate_tool_keys(tool_keys)
    tools: list[BaseTool] = []
    if "calculator.add" in tool_keys:
        tools.append(
            StructuredTool.from_function(
                func=calculator_add,
                name="calculator_add",
                description="对两个十进制数字执行加法，参数为 a 和 b。",
                args_schema=CalculatorAddInput,
            )
        )
    return tools


def tool_event_payload(tool_name: str, data: Any) -> dict[str, Any]:
    return {"tool": tool_name, "data": str(data) if data is not None else None}
