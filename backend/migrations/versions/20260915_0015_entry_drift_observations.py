"""entry drift observer analytics

Revision ID: 20260915_0015
Revises: 20260914_0014
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.models.types import UTCDateTime

revision: str = "20260915_0015"
down_revision: str | None = "20260914_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entry_drift_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("scanner_run_id", sa.Integer(), sa.ForeignKey("scanner_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gpt_analysis_id", sa.Integer(), sa.ForeignKey("gpt_analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scanner_candidate_id", sa.Integer(), sa.ForeignKey("scanner_candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(8), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("risk_version", sa.String(64), nullable=False),
        sa.Column("observer_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(48), nullable=False),
        sa.Column("quality_reason", sa.String(128), nullable=True),
        sa.Column("signal_at", UTCDateTime(), nullable=True),
        sa.Column("signal_bar_timestamp", UTCDateTime(), nullable=True),
        sa.Column("signal_price", sa.Numeric(24, 10), nullable=True),
        sa.Column("initial_stop", sa.Numeric(24, 10), nullable=True),
        sa.Column("intended_execution_bar_timestamp", UTCDateTime(), nullable=True),
        sa.Column("intended_execution_raw_open", sa.Numeric(24, 10), nullable=True),
        sa.Column("drift_pct", sa.Numeric(24, 10), nullable=True),
        sa.Column("stop_distance_pct", sa.Numeric(24, 10), nullable=True),
        sa.Column("projections_json", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint("scanner_candidate_id", name="uq_entry_drift_candidate"),
    )
    op.create_index("ix_entry_drift_observations_trading_date", "entry_drift_observations", ["trading_date"])
    op.create_index("ix_entry_drift_observations_status", "entry_drift_observations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_entry_drift_observations_status", table_name="entry_drift_observations")
    op.drop_index("ix_entry_drift_observations_trading_date", table_name="entry_drift_observations")
    op.drop_table("entry_drift_observations")
