from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentplane.db import Base, utc_now


class AgentLifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    USER = "USER"


class UserStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    TOOL = "TOOL"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class InvocationDecision(StrEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"


class ToolCallStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    DENIED = "DENIED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AppUser(Base, TimestampMixin):
    __tablename__ = "app_users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_app_users_tenant_id_id"),
        UniqueConstraint("tenant_id", "login_name", name="uq_app_users_tenant_login_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    login_name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, native_enum=False, length=20), nullable=False, default=UserRole.USER
    )
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, native_enum=False, length=20),
        nullable=False,
        default=UserStatus.PENDING,
    )


class LocalCredential(Base, TimestampMixin):
    __tablename__ = "local_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            ondelete="CASCADE",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CallingApplication(Base, TimestampMixin):
    __tablename__ = "calling_applications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_calling_applications_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_calling_applications_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["app_users.tenant_id", "app_users.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["app_users.tenant_id", "app_users.id"],
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)


class ApplicationCredential(Base):
    __tablename__ = "application_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["app_users.tenant_id", "app_users.id"],
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExternalUserMapping(Base, TimestampMixin):
    __tablename__ = "external_user_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_external_user_mappings_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "application_id",
            "external_user_id",
            name="uq_external_user_mappings_application_external_user",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["app_users.tenant_id", "app_users.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["app_users.tenant_id", "app_users.id"],
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    external_user_id: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)


class ApplicationAgentGrant(Base):
    __tablename__ = "application_agent_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_definition_id"],
            ["agent_definitions.tenant_id", "agent_definitions.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "granted_by"],
            ["app_users.tenant_id", "app_users.id"],
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    application_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    agent_definition_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    granted_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ApplicationToolGrant(Base):
    __tablename__ = "application_tool_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "granted_by"],
            ["app_users.tenant_id", "app_users.id"],
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    application_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tool_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    granted_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentDefinition(Base, TimestampMixin):
    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_definitions_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_agent_definitions_tenant_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    draft_instructions: Mapped[str] = mapped_column(Text, nullable=False)
    draft_model_alias: Mapped[str] = mapped_column(String(100), nullable=False, default="default")
    draft_tool_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    lifecycle: Mapped[AgentLifecycle] = mapped_column(
        SAEnum(AgentLifecycle, native_enum=False, length=20),
        nullable=False,
        default=AgentLifecycle.ACTIVE,
    )
    latest_published_version_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)

    versions: Mapped[list[AgentVersion]] = relationship(
        back_populates="definition",
        foreign_keys="AgentVersion.agent_definition_id",
        cascade="all, delete-orphan",
    )


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint(
            "agent_definition_id", "version_number", name="uq_agent_versions_definition_version"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_definition_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    model_alias: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    tool_bindings: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)

    definition: Mapped[AgentDefinition] = relationship(
        back_populates="versions", foreign_keys=[agent_definition_id]
    )


class UserAgentGrant(Base):
    __tablename__ = "user_agent_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_definition_id"],
            ["agent_definitions.tenant_id", "agent_definitions.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "granted_by"],
            ["app_users.tenant_id", "app_users.id"],
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    agent_definition_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    granted_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserToolGrant(Base):
    __tablename__ = "user_tool_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "granted_by"],
            ["app_users.tenant_id", "app_users.id"],
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tool_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    granted_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ChatSession(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    agent_definition_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    agent_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="新会话")
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, native_enum=False, length=20),
        nullable=False,
        default=SessionStatus.ACTIVE,
    )


class ApplicationConversation(Base, TimestampMixin):
    __tablename__ = "application_conversations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "application_id",
            "external_user_id",
            "conversation_key",
            name="uq_application_conversations_external_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_definition_id"],
            ["agent_definitions.tenant_id", "agent_definitions.id"],
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    external_user_id: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    conversation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_definition_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    session_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True
    )


class SessionMessage(Base):
    __tablename__ = "session_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_session_messages_session_sequence"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(
        SAEnum(MessageRole, native_enum=False, length=20), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


ACTIVE_RUN_SQL = "status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL')"


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        Index(
            "uq_task_runs_active_session",
            "session_id",
            unique=True,
            postgresql_where=text(ACTIVE_RUN_SQL),
            sqlite_where=text(ACTIVE_RUN_SQL),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    session_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_definition_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    agent_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False
    )
    trace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, default=uuid4, unique=True)
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, native_enum=False, length=30),
        nullable=False,
        default=RunStatus.QUEUED,
    )
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    effective_tool_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    tool_bindings: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatch_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Invocation(Base):
    __tablename__ = "invocations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "application_id",
            "external_request_id",
            name="uq_invocations_application_external_request",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    credential_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("application_credentials.id", ondelete="RESTRICT"), nullable=False
    )
    external_request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    conversation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    requested_agent_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    agent_version_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=True
    )
    decision: Mapped[InvocationDecision] = mapped_column(
        SAEnum(InvocationDecision, native_enum=False, length=20), nullable=False
    )
    decision_code: Mapped[str] = mapped_column(String(100), nullable=False)
    decision_message: Mapped[str] = mapped_column(Text, nullable=False)
    effective_tool_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    session_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TenantToolPolicy(Base, TimestampMixin):
    __tablename__ = "tenant_tool_policies"

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    tool_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_tenant_created", "tenant_id", "created_at", "id"),
        Index("ix_tool_calls_run_status", "run_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    application_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    trace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    tool_key: Mapped[str] = mapped_column(String(200), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[ToolCallStatus] = mapped_column(
        SAEnum(ToolCallStatus, native_enum=False, length=20), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    trace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RunApproval(Base, TimestampMixin):
    __tablename__ = "run_approvals"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_type: Mapped[str] = mapped_column(String(100), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus, native_enum=False, length=20),
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    decided_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
