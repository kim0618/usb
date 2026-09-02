"""runtime safety state and failure history

Revision ID: 20260901_0008
Revises: 20260901_0007
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from app.models.types import UTCDateTime

revision: str = "20260901_0008"
down_revision: str | None = "20260901_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("runtime_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("entered_mode_at", UTCDateTime(), nullable=False),
        sa.Column("reason_code", sa.String(64)), sa.Column("reason_message", sa.Text()),
        sa.Column("last_heartbeat_at", UTCDateTime()), sa.Column("last_market_data_at", UTCDateTime()),
        sa.Column("last_execution_at", UTCDateTime()), sa.Column("last_reconciliation_at", UTCDateTime()),
        sa.Column("created_at", UTCDateTime(), nullable=False), sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_runtime_state_singleton"))
    op.create_table("runtime_failures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("failure_code", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("component", sa.String(64), nullable=False), sa.Column("message", sa.Text(), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False), sa.Column("market_as_of", UTCDateTime()),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_at", UTCDateTime()), sa.Column("created_at", UTCDateTime(), nullable=False))
    op.create_index("ix_runtime_failures_failure_code", "runtime_failures", ["failure_code"])
    op.create_index("ix_runtime_failures_occurred_at", "runtime_failures", ["occurred_at"])
    op.create_index("ix_runtime_failures_resolved", "runtime_failures", ["resolved"])


def downgrade() -> None:
    op.drop_index("ix_runtime_failures_resolved", table_name="runtime_failures")
    op.drop_index("ix_runtime_failures_occurred_at", table_name="runtime_failures")
    op.drop_index("ix_runtime_failures_failure_code", table_name="runtime_failures")
    op.drop_table("runtime_failures")
    op.drop_table("runtime_state")
