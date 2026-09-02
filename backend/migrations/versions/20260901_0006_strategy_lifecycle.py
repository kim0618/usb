"""add Stage 7 strategy lifecycle state and shadow gate metadata

Revision ID: 20260901_0006
Revises: 20260901_0005
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "20260901_0006"
down_revision: str | None = "20260901_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    exact, utc = sa.String(100), sa.String(40)
    op.create_table("strategy_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("scanner_candidate_id", sa.Integer()),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("entry_trading_date", sa.Date()),
        sa.Column("entry_price", exact), sa.Column("initial_stop", exact),
        sa.Column("active_stop", exact), sa.Column("highest_price", exact),
        sa.Column("add_count", sa.Integer(), nullable=False),
        sa.Column("add_signal_issued", sa.Boolean(), nullable=False),
        sa.Column("holding_day", sa.Integer(), nullable=False),
        sa.Column("overnight", sa.Boolean(), nullable=False),
        sa.Column("last_market_as_of", utc),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("trailing_profile", sa.String(16), nullable=False),
        sa.Column("overnight_suitability", sa.String(16), nullable=False),
        sa.Column("created_at", utc, nullable=False), sa.Column("updated_at", utc, nullable=False),
        sa.UniqueConstraint("symbol", "trading_date", name="uq_strategy_state_symbol_date"))
    op.create_index("ix_strategy_states_trading_date", "strategy_states", ["trading_date"])
    with op.batch_alter_table("shadow_trades") as batch:
        batch.add_column(sa.Column("premarket_passed", sa.Boolean()))
        batch.add_column(sa.Column("opening_passed", sa.Boolean()))
        batch.add_column(sa.Column("entry_signalled", sa.Boolean()))
        batch.add_column(sa.Column("entry_filled", sa.Boolean()))
        batch.add_column(sa.Column("no_trade_reason", sa.String(64)))
        batch.add_column(sa.Column("exit_reason", sa.String(64)))
        batch.add_column(sa.Column("gate_reached", sa.String(32)))
        batch.add_column(sa.Column("holding_days", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("shadow_trades") as batch:
        for name in ("holding_days", "gate_reached", "exit_reason", "no_trade_reason",
                     "entry_filled", "entry_signalled", "opening_passed", "premarket_passed"):
            batch.drop_column(name)
    op.drop_index("ix_strategy_states_trading_date", table_name="strategy_states")
    op.drop_table("strategy_states")
