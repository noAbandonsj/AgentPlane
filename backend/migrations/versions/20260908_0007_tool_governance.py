"""Pin tool versions and add tenant controls and execution audit.

Revision ID: 20260908_0007
Revises: 20260813_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0007"
down_revision: str | None = "20260813_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table, source in (("agent_versions", "tool_keys"), ("task_runs", "effective_tool_keys")):
        op.add_column(
            table, sa.Column("tool_bindings", sa.JSON(), nullable=False, server_default="{}")
        )
        # The only pre-migration registered tool is calculator.add. Never resolve old Runs
        # against a future deployment's current version.
        op.execute(
            sa.text(
                f'UPDATE {table} SET tool_bindings = \'{{"calculator.add":"1.0.0"}}\'::json '
                f"WHERE {source}::jsonb ? 'calculator.add'"
            )
        )
        op.alter_column(table, "tool_bindings", server_default=None)
    op.create_table(
        "tenant_tool_policies",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("tool_key", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "tool_key"),
    )
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("tool_key", sa.String(200), nullable=False),
        sa.Column("tool_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["task_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_calls_tenant_created", "tool_calls", ["tenant_id", "created_at", "id"])
    op.create_index("ix_tool_calls_run_status", "tool_calls", ["run_id", "status"])


def downgrade() -> None:
    op.drop_table("tool_calls")
    op.drop_table("tenant_tool_policies")
    op.drop_column("task_runs", "tool_bindings")
    op.drop_column("agent_versions", "tool_bindings")
