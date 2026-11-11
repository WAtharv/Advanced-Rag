"""Create persistent approval audit table.

Revision ID: 002_approval_audit
Revises: 001_baseline_registry
Create Date: 2026-11-11 12:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_approval_audit"
down_revision: Union[str, None] = "001_baseline_registry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "approval_audit" not in existing_tables:
        op.create_table(
            "approval_audit",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("proposal_id", sa.String(length=64), nullable=False),
            sa.Column("thread_id", sa.String(length=64), nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("sql_hash", sa.String(length=64), nullable=False),
            sa.Column("sql_query", sa.Text(), server_default="", nullable=False),
            sa.Column("dialect", sa.String(length=32), server_default="sqlite", nullable=False),
            sa.Column("tables_used", sa.Text(), server_default="[]", nullable=False),
            sa.Column("actor", sa.String(length=128), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("execution_completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("execution_outcome", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_approval_audit_proposal_id", "approval_audit", ["proposal_id"], unique=False)
        op.create_index("ix_approval_audit_thread_id", "approval_audit", ["thread_id"], unique=False)
        op.create_index("ix_approval_audit_event_type", "approval_audit", ["event_type"], unique=False)
        op.create_index("ix_approval_audit_event_timestamp", "approval_audit", ["event_timestamp"], unique=False)
        op.create_index("idx_audit_prop_time", "approval_audit", ["proposal_id", "event_timestamp"])
        op.create_index("idx_audit_thread_time", "approval_audit", ["thread_id", "event_timestamp"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "approval_audit" in existing_tables:
        op.drop_table("approval_audit")
