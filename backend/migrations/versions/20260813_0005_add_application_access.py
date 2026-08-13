"""Add external user mappings and application grants.

Revision ID: 20260813_0005
Revises: 20260813_0004
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0005"
down_revision: str | None = "20260813_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_user_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("external_user_id", sa.String(length=200), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            name="fk_external_user_mappings_tenant_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_external_user_mappings_tenant_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_external_user_mappings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_external_user_mappings_tenant_updater",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_external_user_mappings_tenant_user",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_user_mappings")),
        sa.UniqueConstraint(
            "tenant_id",
            "application_id",
            "external_user_id",
            name="uq_external_user_mappings_application_external_user",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_external_user_mappings_tenant_id_id",
        ),
    )
    op.create_index(
        op.f("ix_external_user_mappings_application_id"),
        "external_user_mappings",
        ["application_id"],
    )
    op.create_index(
        op.f("ix_external_user_mappings_tenant_id"),
        "external_user_mappings",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_external_user_mappings_user_id"),
        "external_user_mappings",
        ["user_id"],
    )

    op.create_table(
        "application_agent_grants",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("agent_definition_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_definition_id"],
            ["agent_definitions.tenant_id", "agent_definitions.id"],
            name="fk_application_agent_grants_tenant_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            name="fk_application_agent_grants_tenant_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "granted_by"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_application_agent_grants_tenant_granter",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_application_agent_grants_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "application_id",
            "agent_definition_id",
            name=op.f("pk_application_agent_grants"),
        ),
    )

    op.create_table(
        "application_tool_grants",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("tool_key", sa.String(length=200), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            name="fk_application_tool_grants_tenant_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "granted_by"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_application_tool_grants_tenant_granter",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_application_tool_grants_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "application_id",
            "tool_key",
            name=op.f("pk_application_tool_grants"),
        ),
    )


def downgrade() -> None:
    op.drop_table("application_tool_grants")
    op.drop_table("application_agent_grants")
    op.drop_index(
        op.f("ix_external_user_mappings_user_id"),
        table_name="external_user_mappings",
    )
    op.drop_index(
        op.f("ix_external_user_mappings_tenant_id"),
        table_name="external_user_mappings",
    )
    op.drop_index(
        op.f("ix_external_user_mappings_application_id"),
        table_name="external_user_mappings",
    )
    op.drop_table("external_user_mappings")
