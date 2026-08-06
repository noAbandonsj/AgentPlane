"""Remove the checkpoint-specific session thread identifier.

Revision ID: 20260806_0002
Revises: 20260805_0001
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0002"
down_revision: str | None = "20260805_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("uq_sessions_runtime_thread_id"), "sessions", type_="unique")
    op.drop_column("sessions", "runtime_thread_id")


def downgrade() -> None:
    op.add_column("sessions", sa.Column("runtime_thread_id", sa.Uuid(), nullable=True))
    op.execute("UPDATE sessions SET runtime_thread_id = id")
    op.alter_column("sessions", "runtime_thread_id", nullable=False)
    op.create_unique_constraint(
        op.f("uq_sessions_runtime_thread_id"), "sessions", ["runtime_thread_id"]
    )
