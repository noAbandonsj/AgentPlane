"""Add enterprise application invocations.

Revision ID: 20260813_0006
Revises: 20260813_0005
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0006"
down_revision: str | None = "20260813_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("external_user_id", sa.String(length=200), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_key", sa.String(length=200), nullable=False),
        sa.Column("agent_definition_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_definition_id"],
            ["agent_definitions.tenant_id", "agent_definitions.id"],
            name="fk_application_conversations_tenant_agent",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            name="fk_application_conversations_tenant_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_application_conversations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_application_conversations_tenant_user",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_application_conversations_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_conversations")),
        sa.UniqueConstraint(
            "tenant_id",
            "application_id",
            "external_user_id",
            "conversation_key",
            name="uq_application_conversations_external_key",
        ),
        sa.UniqueConstraint("session_id", name=op.f("uq_application_conversations_session_id")),
    )
    op.create_index(
        op.f("ix_application_conversations_application_id"),
        "application_conversations",
        ["application_id"],
    )
    op.create_index(
        op.f("ix_application_conversations_tenant_id"),
        "application_conversations",
        ["tenant_id"],
    )

    op.create_table(
        "invocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("external_request_id", sa.String(length=200), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("external_user_id", sa.String(length=200), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_key", sa.String(length=200), nullable=False),
        sa.Column("requested_agent_id", sa.Uuid(), nullable=False),
        sa.Column("agent_version_id", sa.Uuid(), nullable=True),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("decision_code", sa.String(length=100), nullable=False),
        sa.Column("decision_message", sa.Text(), nullable=False),
        sa.Column("effective_tool_keys", sa.JSON(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_version_id"],
            ["agent_versions.id"],
            name=op.f("fk_invocations_agent_version_id_agent_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["application_credentials.id"],
            name=op.f("fk_invocations_credential_id_application_credentials"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["task_runs.id"],
            name=op.f("fk_invocations_run_id_task_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_invocations_session_id_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            name="fk_invocations_tenant_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_invocations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_invocations_tenant_user",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invocations")),
        sa.UniqueConstraint(
            "tenant_id",
            "application_id",
            "external_request_id",
            name="uq_invocations_application_external_request",
        ),
        sa.UniqueConstraint("run_id", name=op.f("uq_invocations_run_id")),
    )
    op.create_index(op.f("ix_invocations_application_id"), "invocations", ["application_id"])
    op.create_index(op.f("ix_invocations_session_id"), "invocations", ["session_id"])
    op.create_index(op.f("ix_invocations_tenant_id"), "invocations", ["tenant_id"])
    op.create_index(op.f("ix_invocations_user_id"), "invocations", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_invocations_user_id"), table_name="invocations")
    op.drop_index(op.f("ix_invocations_tenant_id"), table_name="invocations")
    op.drop_index(op.f("ix_invocations_session_id"), table_name="invocations")
    op.drop_index(op.f("ix_invocations_application_id"), table_name="invocations")
    op.drop_table("invocations")
    op.drop_index(
        op.f("ix_application_conversations_tenant_id"),
        table_name="application_conversations",
    )
    op.drop_index(
        op.f("ix_application_conversations_application_id"),
        table_name="application_conversations",
    )
    op.drop_table("application_conversations")
