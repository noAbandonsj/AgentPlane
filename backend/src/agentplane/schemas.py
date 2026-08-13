from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentplane.models import (
    AgentLifecycle,
    MessageRole,
    RunStatus,
    SessionStatus,
    UserRole,
    UserStatus,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class AuthRegister(BaseModel):
    login_name: str = Field(min_length=3, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=6, max_length=200)

    @field_validator("login_name")
    @classmethod
    def normalize_login_name(cls, value: str) -> str:
        return value.strip().casefold()

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        return value.strip()


class AuthLogin(BaseModel):
    login_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("login_name")
    @classmethod
    def normalize_login_name(cls, value: str) -> str:
        return value.strip().casefold()


class UserRead(ApiModel):
    id: UUID
    login_name: str
    display_name: str
    role: UserRole
    status: UserStatus
    created_at: datetime
    updated_at: datetime


class BootstrapStatus(BaseModel):
    required: bool


class UserStatusPatch(BaseModel):
    status: UserStatus


class AgentGrantReplace(BaseModel):
    agent_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    @field_validator("agent_ids")
    @classmethod
    def unique_agent_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class ToolGrantReplace(BaseModel):
    tool_keys: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("tool_keys")
    @classmethod
    def unique_tool_keys(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class UserPermissionsRead(BaseModel):
    user_id: UUID
    agent_ids: list[UUID]
    tool_keys: list[str]


class UserPermissionsReplace(BaseModel):
    agent_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    tool_keys: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("agent_ids")
    @classmethod
    def unique_permission_agent_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))

    @field_validator("tool_keys")
    @classmethod
    def unique_permission_tool_keys(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class CallingApplicationCreate(BaseModel):
    code: str = Field(min_length=2, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    credential_expires_at: datetime | None = None

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().casefold()

    @field_validator("name")
    @classmethod
    def strip_application_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("应用名称不能为空")
        return stripped

    @field_validator("description")
    @classmethod
    def strip_application_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("credential_expires_at")
    @classmethod
    def validate_credential_expiration(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("应用凭据过期时间必须包含时区")
            if value <= datetime.now(UTC):
                raise ValueError("应用凭据过期时间必须晚于当前时间")
        return value


class CallingApplicationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    active: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> CallingApplicationPatch:
        null_fields = [field for field in self.model_fields_set if getattr(self, field) is None]
        if null_fields:
            raise ValueError(f"字段不能为 null: {', '.join(sorted(null_fields))}")
        return self

    @field_validator("name")
    @classmethod
    def strip_optional_application_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("应用名称不能为空")
        return stripped

    @field_validator("description")
    @classmethod
    def strip_optional_application_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class CallingApplicationRead(ApiModel):
    id: UUID
    code: str
    name: str
    description: str
    active: bool
    created_at: datetime
    updated_at: datetime


class ApplicationCredentialRotate(BaseModel):
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def validate_expiration(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("应用凭据过期时间必须包含时区")
            if value <= datetime.now(UTC):
                raise ValueError("应用凭据过期时间必须晚于当前时间")
        return value


class ApplicationCredentialIssued(BaseModel):
    id: UUID
    token: str
    token_prefix: str
    expires_at: datetime | None
    created_at: datetime


class CallingApplicationCreated(BaseModel):
    application: CallingApplicationRead
    credential: ApplicationCredentialIssued


class ExternalUserMappingCreate(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=200)
    user_id: UUID

    @field_validator("external_user_id")
    @classmethod
    def normalize_external_user_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("外部用户标识不能为空")
        return stripped


class ExternalUserMappingPatch(BaseModel):
    user_id: UUID | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> ExternalUserMappingPatch:
        null_fields = [field for field in self.model_fields_set if getattr(self, field) is None]
        if null_fields:
            raise ValueError(f"字段不能为 null: {', '.join(sorted(null_fields))}")
        return self


class ExternalUserMappingRead(ApiModel):
    id: UUID
    external_user_id: str
    user_id: UUID
    active: bool
    created_at: datetime
    updated_at: datetime


class ApplicationPermissionsRead(BaseModel):
    application_id: UUID
    agent_ids: list[UUID]
    tool_keys: list[str]


class ApplicationPermissionsReplace(BaseModel):
    agent_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    tool_keys: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("agent_ids")
    @classmethod
    def unique_agent_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))

    @field_validator("tool_keys")
    @classmethod
    def unique_tool_keys(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    instructions: str = Field(min_length=1, max_length=50000)
    model_alias: str = Field(default="default", min_length=1, max_length=100)
    tool_keys: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("name", "instructions", "model_alias")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("tool_keys")
    @classmethod
    def unique_tool_keys(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    instructions: str | None = Field(default=None, min_length=1, max_length=50000)
    model_alias: str | None = Field(default=None, min_length=1, max_length=100)
    tool_keys: list[str] | None = Field(default=None, max_length=100)
    lifecycle: AgentLifecycle | None = None

    @field_validator("name", "instructions", "model_alias")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("tool_keys")
    @classmethod
    def unique_optional_tool_keys(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else list(dict.fromkeys(value))


class AgentRead(ApiModel):
    id: UUID
    name: str
    description: str
    instructions: str = Field(validation_alias="draft_instructions")
    model_alias: str = Field(validation_alias="draft_model_alias")
    tool_keys: list[str] = Field(validation_alias="draft_tool_keys")
    lifecycle: AgentLifecycle
    latest_published_version_id: UUID | None
    created_at: datetime
    updated_at: datetime


class AgentVersionRead(ApiModel):
    id: UUID
    agent_definition_id: UUID
    version_number: int
    name: str
    description: str
    instructions: str
    model_alias: str
    tool_keys: list[str]
    published_at: datetime
    published_by: UUID


class ToolMetadata(BaseModel):
    key: str
    name: str
    description: str
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    requires_approval: bool


class SessionCreate(BaseModel):
    agent_id: UUID
    title: str = Field(default="新会话", min_length=1, max_length=300)


class SessionRead(ApiModel):
    id: UUID
    user_id: UUID
    agent_definition_id: UUID
    agent_version_id: UUID
    title: str
    status: SessionStatus
    created_at: datetime
    updated_at: datetime


class MessageRead(ApiModel):
    id: UUID
    session_id: UUID
    run_id: UUID | None
    sequence: int
    role: MessageRole
    content: str
    message_metadata: dict[str, Any]
    created_at: datetime


class RunCreate(BaseModel):
    input: str = Field(min_length=1, max_length=100000)

    @field_validator("input")
    @classmethod
    def strip_input(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("消息不能为空")
        return value


class RunRead(ApiModel):
    id: UUID
    user_id: UUID
    session_id: UUID
    agent_definition_id: UUID
    agent_version_id: UUID
    trace_id: UUID
    status: RunStatus
    input_text: str
    effective_tool_keys: list[str]
    output_text: str | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    model_name: str | None
    input_tokens: int | None
    output_tokens: int | None
    cancel_requested_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class RunEventRead(ApiModel):
    id: UUID
    run_id: UUID
    sequence: int
    event_type: str
    payload: dict[str, Any]
    trace_id: UUID
    created_at: datetime


class ApprovalRequest(BaseModel):
    approval_id: UUID
    run_id: UUID
    request_type: str
    request_payload: dict[str, Any]
    created_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, str] = Field(default_factory=dict)


class CapabilityResponse(BaseModel):
    runtime: str
    model_configured: bool
    model_aliases: list[str]
    tools: list[ToolMetadata]
    approval_resume_supported: bool
