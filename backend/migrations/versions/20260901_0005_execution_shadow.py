"""add Stage 6 execution and shadow result tables

Revision ID: 20260901_0005
Revises: 20260901_0004
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260901_0005"
down_revision: str | None = "20260901_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    exact, utc = sa.String(100), sa.String(40)
    op.create_table(
        "execution_orders",
        sa.Column("id", sa.String(96), primary_key=True), sa.Column("broker_type", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False), sa.Column("side", sa.String(8), nullable=False),
        sa.Column("requested_quantity", exact, nullable=False), sa.Column("filled_quantity", exact, nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("rejection_reason", sa.String(64)),
        sa.Column("reference_price", exact, nullable=False), sa.Column("submitted_at", utc, nullable=False),
        sa.Column("completed_at", utc), sa.Column("execution_version", sa.String(64), nullable=False),
    )
    op.create_index("ix_execution_orders_symbol", "execution_orders", ["symbol"])
    op.create_table(
        "execution_fills", sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("order_id", sa.String(96), sa.ForeignKey("execution_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quantity", exact, nullable=False), sa.Column("raw_market_price", exact, nullable=False),
        sa.Column("fill_price", exact, nullable=False), sa.Column("spread_cost", exact, nullable=False),
        sa.Column("slippage_cost", exact, nullable=False), sa.Column("commission", exact, nullable=False),
        sa.Column("fx_cost", exact, nullable=False), sa.Column("total_cost", exact, nullable=False),
        sa.Column("filled_at", utc, nullable=False),
    )
    op.create_index("ix_execution_fills_order_id", "execution_fills", ["order_id"])
    op.create_table(
        "shadow_trades", sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("scanner_run_id", sa.Integer(), sa.ForeignKey("scanner_runs.id", ondelete="SET NULL")),
        sa.Column("scanner_candidate_id", sa.Integer(), sa.ForeignKey("scanner_candidates.id", ondelete="SET NULL")),
        sa.Column("gpt_analysis_id", sa.Integer(), sa.ForeignKey("gpt_analyses.id", ondelete="SET NULL")),
        sa.Column("symbol", sa.String(32), nullable=False), sa.Column("variant", sa.String(4), nullable=False),
        sa.Column("variant_version", sa.String(64), nullable=False), sa.Column("is_control", sa.Boolean(), nullable=False),
        sa.Column("initial_planned_risk", exact, nullable=False), sa.Column("entry_at", utc), sa.Column("exit_at", utc),
        sa.Column("average_entry_price", exact), sa.Column("average_exit_price", exact),
        sa.Column("gross_pnl", exact, nullable=False), sa.Column("net_pnl", exact, nullable=False),
        sa.Column("gross_r", exact, nullable=False), sa.Column("net_r", exact, nullable=False),
        sa.Column("total_cost", exact, nullable=False), sa.Column("ambiguous_bar_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.UniqueConstraint("scanner_candidate_id", "variant", "variant_version", name="uq_shadow_candidate_variant_version"),
    )
    op.create_index("ix_shadow_trades_symbol", "shadow_trades", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_shadow_trades_symbol", table_name="shadow_trades"); op.drop_table("shadow_trades")
    op.drop_index("ix_execution_fills_order_id", table_name="execution_fills"); op.drop_table("execution_fills")
    op.drop_index("ix_execution_orders_symbol", table_name="execution_orders"); op.drop_table("execution_orders")
