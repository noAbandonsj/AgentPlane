"""Add calling applications and credentials.

Revision ID: 20260813_0004
Revises: 20260806_0003
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0004"
down_revision: str | None = "20260806_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calling_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_calling_applications_tenant_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_calling_applications_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_calling_applications_tenant_updater",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calling_applications")),
        sa.UniqueConstraint("tenant_id", "code", name="uq_calling_applications_tenant_code"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_calling_applications_tenant_id_id"),
    )
    op.create_index(
        op.f("ix_calling_applications_tenant_id"), "calling_applications", ["tenant_id"]
    )

    op.create_table(
        "application_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            ["calling_applications.tenant_id", "calling_applications.id"],
            name="fk_application_credentials_tenant_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["app_users.tenant_id", "app_users.id"],
            name="fk_application_credentials_tenant_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_application_credentials_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_credentials")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_application_credentials_token_hash")),
    )
    op.create_index(
        op.f("ix_application_credentials_application_id"),
        "application_credentials",
        ["application_id"],
    )
    op.create_index(
        op.f("ix_application_credentials_expires_at"),
        "application_credentials",
        ["expires_at"],
    )
    op.create_index(
        op.f("ix_application_credentials_tenant_id"),
        "application_credentials",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_application_credentials_tenant_id"),
        table_name="application_credentials",
    )
    op.drop_index(
        op.f("ix_application_credentials_expires_at"),
        table_name="application_credentials",
    )
    op.drop_index(
        op.f("ix_application_credentials_application_id"),
        table_name="application_credentials",
    )
    op.drop_table("application_credentials")
    op.drop_index(op.f("ix_calling_applications_tenant_id"), table_name="calling_applications")
    op.drop_table("calling_applications")
