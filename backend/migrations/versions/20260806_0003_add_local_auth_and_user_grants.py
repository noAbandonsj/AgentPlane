"""Add local authentication and user-level Agent/tool grants.

Revision ID: 20260806_0003
Revises: 20260806_0002
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0003"
down_revision: str | None = "20260806_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("login_name", sa.String(length=100), nullable=True))
    op.add_column("app_users", sa.Column("role", sa.String(length=20), nullable=True))
    op.add_column("app_users", sa.Column("status", sa.String(length=20), nullable=True))
    op.execute(
        """
        UPDATE app_users
        SET login_name = CASE
                WHEN id = '00000000-0000-0000-0000-000000000001' THEN 'dev-user'
                ELSE 'legacy-' || replace(id::text, '-', '')
            END,
            role = 'USER',
            status = CASE WHEN active THEN 'ACTIVE' ELSE 'DISABLED' END
        """
    )
    op.alter_column("app_users", "login_name", nullable=False)
    op.alter_column("app_users", "role", nullable=False)
    op.alter_column("app_users", "status", nullable=False)
    op.create_unique_constraint(
        "uq_app_users_tenant_login_name", "app_users", ["tenant_id", "login_name"]
    )
    op.drop_column("app_users", "active")

    op.create_unique_constraint(
        "uq_agent_definitions_tenant_id_id", "agent_definitions", ["tenant_id", "id"]
    )

    op.add_column("task_runs", sa.Column("effective_tool_keys", sa.JSON(), nullable=True))
    op.execute(
        """
        UPDATE task_runs AS run
        SET effective_tool_keys = version.tool_keys
        FROM agent_versions AS version
        WHERE version.id = run.agent_version_id
        """
    )
    op.execute(
        "UPDATE task_runs SET effective_tool_keys = '[]'::json WHERE effective_tool_keys IS NULL"
    )
    op.alter_column("task_runs", "effective_tool_keys", nullable=False)

    op.create_table(
        "local_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_local_credentials_tenant_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_local_credentials")),
    )
    op.create_index(op.f("ix_local_credentials_tenant_id"), "local_credentials", ["tenant_id"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_auth_sessions_tenant_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    op.create_index(op.f("ix_auth_sessions_expires_at"), "auth_sessions", ["expires_at"])
    op.create_index(op.f("ix_auth_sessions_tenant_id"), "auth_sessions", ["tenant_id"])
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"])

    op.create_table(
        "user_agent_grants",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_definition_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_definition_id"],
            ["agent_definitions.tenant_id", "agent_definitions.id"],
            name="fk_user_agent_grants_tenant_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "granted_by"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_user_agent_grants_tenant_grantor",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_user_agent_grants_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_user_agent_grants_tenant_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "user_id",
            "agent_definition_id",
            name=op.f("pk_user_agent_grants"),
        ),
    )

    op.create_table(
        "user_tool_grants",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tool_key", sa.String(length=200), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_user_tool_grants_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_user_tool_grants_tenant_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "granted_by"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_user_tool_grants_tenant_grantor",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "user_id", "tool_key", name=op.f("pk_user_tool_grants")
        ),
    )

    op.execute(
        """
        INSERT INTO user_agent_grants (
            tenant_id, user_id, agent_definition_id, granted_by, created_at
        )
        SELECT DISTINCT tenant_id, user_id, agent_definition_id, user_id, CURRENT_TIMESTAMP
        FROM sessions
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO user_tool_grants (tenant_id, user_id, tool_key, granted_by, created_at)
        SELECT DISTINCT session.tenant_id, session.user_id, tool.value, session.user_id,
            CURRENT_TIMESTAMP
        FROM sessions AS session
        JOIN agent_versions AS version ON version.id = session.agent_version_id
        CROSS JOIN LATERAL json_array_elements_text(version.tool_keys) AS tool(value)
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("user_tool_grants")
    op.drop_table("user_agent_grants")
    op.drop_constraint("uq_agent_definitions_tenant_id_id", "agent_definitions", type_="unique")
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_index(op.f("ix_auth_sessions_tenant_id"), table_name="auth_sessions")
    op.drop_index(op.f("ix_auth_sessions_expires_at"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index(op.f("ix_local_credentials_tenant_id"), table_name="local_credentials")
    op.drop_table("local_credentials")

    op.drop_column("task_runs", "effective_tool_keys")

    op.add_column("app_users", sa.Column("active", sa.Boolean(), nullable=True))
    op.execute("UPDATE app_users SET active = status = 'ACTIVE'")
    op.alter_column("app_users", "active", nullable=False)
    op.drop_constraint("uq_app_users_tenant_login_name", "app_users", type_="unique")
    op.drop_column("app_users", "status")
    op.drop_column("app_users", "role")
    op.drop_column("app_users", "login_name")
