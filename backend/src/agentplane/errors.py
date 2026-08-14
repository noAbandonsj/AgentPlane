from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from agentplane.logging import get_logger
from agentplane.tools import InvalidToolKeysError

logger = get_logger(__name__)


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


@dataclass(frozen=True, slots=True)
class IntegrityErrorSpec:
    constraint: str
    code: str
    message: str


_INTEGRITY_ERROR_SPECS = (
    IntegrityErrorSpec(
        "uq_app_users_tenant_login_name",
        "LOGIN_NAME_EXISTS",
        "登录名已被使用",
    ),
    IntegrityErrorSpec(
        "uq_calling_applications_tenant_code",
        "APPLICATION_CODE_EXISTS",
        "当前租户已存在同编码调用应用",
    ),
    IntegrityErrorSpec(
        "uq_external_user_mappings_application_external_user",
        "EXTERNAL_USER_MAPPING_EXISTS",
        "当前应用已存在该外部用户映射",
    ),
    IntegrityErrorSpec(
        "uq_agent_definitions_tenant_name",
        "AGENT_NAME_EXISTS",
        "当前租户已存在同名 Agent",
    ),
    IntegrityErrorSpec(
        "uq_task_runs_active_session",
        "ACTIVE_RUN_EXISTS",
        "当前会话已有运行中的 Run",
    ),
    IntegrityErrorSpec(
        "uq_invocations_application_external_request",
        "IDEMPOTENCY_KEY_REUSED",
        "外部请求号已被使用",
    ),
)

_SQLITE_CONSTRAINT_ALIASES = {
    "app_users.tenant_id, app_users.login_name": "uq_app_users_tenant_login_name",
    "calling_applications.tenant_id, calling_applications.code": (
        "uq_calling_applications_tenant_code"
    ),
    (
        "external_user_mappings.tenant_id, external_user_mappings.application_id, "
        "external_user_mappings.external_user_id"
    ): "uq_external_user_mappings_application_external_user",
    "agent_definitions.tenant_id, agent_definitions.name": "uq_agent_definitions_tenant_name",
    "task_runs.session_id": "uq_task_runs_active_session",
    "invocations.tenant_id, invocations.application_id, invocations.external_request_id": (
        "uq_invocations_application_external_request"
    ),
}


def integrity_constraint_name(exc: IntegrityError) -> str | None:
    original = exc.orig
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if isinstance(constraint_name, str) and constraint_name:
        return constraint_name
    message = str(original).casefold()
    for signature, alias in _SQLITE_CONSTRAINT_ALIASES.items():
        if signature in message:
            return alias
    for spec in _INTEGRITY_ERROR_SPECS:
        if spec.constraint in message:
            return spec.constraint
    return None


def integrity_error_spec(exc: IntegrityError) -> IntegrityErrorSpec | None:
    constraint = integrity_constraint_name(exc)
    return next(
        (spec for spec in _INTEGRITY_ERROR_SPECS if spec.constraint == constraint),
        None,
    )


def is_integrity_constraint(exc: IntegrityError, constraint: str) -> bool:
    return integrity_constraint_name(exc) == constraint


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


async def handle_integrity_error(_request: Request, exc: IntegrityError) -> JSONResponse:
    spec = integrity_error_spec(exc)
    logger.error(
        "database_integrity_error",
        constraint=integrity_constraint_name(exc),
        database_error_type=type(exc.orig).__name__,
    )
    if spec is not None:
        return error_response(409, spec.code, spec.message)
    return error_response(500, "DATABASE_CONSTRAINT_ERROR", "数据约束校验失败")


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
    app.add_exception_handler(
        IntegrityError,
        handle_integrity_error,  # pyright: ignore[reportArgumentType]
    )
