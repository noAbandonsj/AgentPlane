from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agentplane.tools import InvalidToolKeysError


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def error_response(status_code: int, code: str, message: str, **details: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )


async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数校验失败",
                "details": {"errors": jsonable_encoder(exc.errors())},
            }
        },
    )


async def handle_invalid_tool_keys(_request: Request, exc: InvalidToolKeysError) -> JSONResponse:
    return error_response(
        400,
        "INVALID_TOOL_KEYS",
        "请求包含未注册的工具",
        unknown_tool_keys=exc.unknown_tool_keys,
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, handle_api_error)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(
        InvalidToolKeysError,
        handle_invalid_tool_keys,  # pyright: ignore[reportArgumentType]
    )
    app.add_exception_handler(
        RequestValidationError,
        handle_validation_error,  # pyright: ignore[reportArgumentType]
    )
