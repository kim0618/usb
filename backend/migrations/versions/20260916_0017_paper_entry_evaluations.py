"""durable paper entry evaluation history

Revision ID: 20260916_0017
Revises: 20260915_0016

Production runs 20260915_0016, so this deployment is the single step 0016 -> 0017.
It only creates a table: no existing row is read, rewritten, or backfilled, and the
downgrade drops exactly what the upgrade made.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.models.types import DecimalString, UTCDateTime

revision: str = "20260916_0017"
down_revision: str | None = "20260915_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Observations and the thresholds they were judged against are exact Decimal text:
# a SQLite REAL would round a ratio across its own threshold (0.0499999999 as 0.05)
# and make a stored rejection unexplainable.
DECIMAL_COLUMNS = (
    "previous_close", "premarket_reference_price", "gap_pct", "gap_min", "gap_max",
    "premarket_volume", "historical_average_daily_volume", "v1_volume_ratio", "v1_volume_min",
    "opening_range_high", "opening_range_low", "signal_price", "initial_stop",
    "fill_price", "fill_quantity",
)


def upgrade() -> None:
    op.create_table(
        "paper_entry_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("analysis_trading_date", sa.Date(), nullable=False),
        sa.Column("scanner_run_id", sa.Integer(),
                  sa.ForeignKey("scanner_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gpt_analysis_id", sa.Integer(),
                  sa.ForeignKey("gpt_analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scanner_candidate_id", sa.Integer(),
                  sa.ForeignKey("scanner_candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("final_status", sa.String(32), nullable=False),
        sa.Column("final_reason", sa.String(64), nullable=True),
        sa.Column("final_detail", sa.String(128), nullable=True),
        sa.Column("last_phase", sa.String(32), nullable=True),
        sa.Column("premarket_passed", sa.Boolean(), nullable=True),
        sa.Column("opening_range_ready", sa.Boolean(), nullable=True),
        sa.Column("entry_signalled", sa.Boolean(), nullable=True),
        sa.Column("entry_filled", sa.Boolean(), nullable=True),
        *(sa.Column(name, DecimalString(), nullable=True) for name in DECIMAL_COLUMNS),
        sa.Column("signal_at", UTCDateTime(), nullable=True),
        sa.Column("intended_entry_bar_at", UTCDateTime(), nullable=True),
        sa.Column("order_id", sa.String(96), nullable=True),
        sa.Column("trade_uid", sa.String(128), nullable=True),
        sa.Column("last_progress_reason", sa.String(64), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_at", UTCDateTime(), nullable=True),
        sa.Column("error_tick_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("first_evaluated_at", UTCDateTime(), nullable=True),
        sa.Column("evaluated_at", UTCDateTime(), nullable=True),
        sa.Column("finalized_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint("trading_date", "scanner_candidate_id",
                            name="uq_paper_entry_evaluation_session_candidate"),
    )
    op.create_index("ix_paper_entry_evaluations_trading_date", "paper_entry_evaluations",
                    ["trading_date"])
    op.create_index("ix_paper_entry_evaluations_final_status", "paper_entry_evaluations",
                    ["final_status"])
    # No backfill: a past session that never recorded a reason does not gain one here.
    # The read path labels such a session INCOMPLETE_LEGACY from its existing records.


def downgrade() -> None:
    op.drop_index("ix_paper_entry_evaluations_final_status", table_name="paper_entry_evaluations")
    op.drop_index("ix_paper_entry_evaluations_trading_date", table_name="paper_entry_evaluations")
    op.drop_table("paper_entry_evaluations")
