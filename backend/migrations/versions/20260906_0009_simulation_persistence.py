"""simulation broker account, position, and trade persistence

Revision ID: 20260906_0009
Revises: 20260901_0008

Schema only. Creating an account is business state, so it stays an explicit
operator action; downgrade drops every durable broker state row but leaves the
execution_orders/execution_fills history untouched.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.models.types import DecimalString, UTCDateTime

revision: str = "20260906_0009"
down_revision: str | None = "20260901_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    exact, utc = DecimalString(), UTCDateTime()
    op.create_table(
        "simulation_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_key", sa.String(64), nullable=False),
        sa.Column("broker_type", sa.String(32), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("initial_cash", exact, nullable=False), sa.Column("cash", exact, nullable=False),
        sa.Column("created_at", utc, nullable=False), sa.Column("updated_at", utc, nullable=False),
        sa.Column("state_version", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("broker_type", "account_key", name="uq_simulation_accounts_broker_key"),
    )
    op.create_table(
        "simulation_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("simulation_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("quantity", exact, nullable=False), sa.Column("average_price", exact, nullable=False),
        sa.Column("cost_basis", exact, nullable=False), sa.Column("realized_pnl", exact, nullable=False),
        sa.Column("opened_at", utc, nullable=False), sa.Column("updated_at", utc, nullable=False),
        sa.UniqueConstraint("account_id", "symbol", name="uq_simulation_positions_account_symbol"),
    )
    op.create_index("ix_simulation_positions_symbol", "simulation_positions", ["symbol"])
    op.create_table(
        "simulation_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("simulation_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_uid", sa.String(128), nullable=False), sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("entry_time", utc, nullable=False), sa.Column("exit_time", utc),
        sa.Column("initial_quantity", exact, nullable=False), sa.Column("total_quantity", exact, nullable=False),
        sa.Column("average_entry_price", exact, nullable=False), sa.Column("average_exit_price", exact),
        sa.Column("gross_pnl", exact, nullable=False), sa.Column("net_pnl", exact, nullable=False),
        sa.Column("planned_initial_risk", exact, nullable=False),
        sa.Column("gross_r", exact, nullable=False), sa.Column("net_r", exact, nullable=False),
        sa.Column("total_cost", exact, nullable=False),
        sa.Column("entry_notional", exact, server_default="0", nullable=False),
        sa.Column("exit_notional", exact, server_default="0", nullable=False),
        sa.Column("sold_quantity", exact, server_default="0", nullable=False),
        sa.Column("ambiguous_bar_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("exit_reason", sa.String(64)), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", utc, nullable=False), sa.Column("updated_at", utc, nullable=False),
        sa.UniqueConstraint("account_id", "trade_uid", name="uq_simulation_trades_account_uid"),
    )
    op.create_index("ix_simulation_trades_symbol", "simulation_trades", ["symbol"])
    # The broker keeps one open trade per symbol; closed rows stay as history.
    op.create_index("uq_simulation_trades_open_symbol", "simulation_trades", ["account_id", "symbol"],
                    unique=True, sqlite_where=sa.text("status = 'OPEN'"))


def downgrade() -> None:
    op.drop_index("uq_simulation_trades_open_symbol", table_name="simulation_trades")
    op.drop_index("ix_simulation_trades_symbol", table_name="simulation_trades")
    op.drop_table("simulation_trades")
    op.drop_index("ix_simulation_positions_symbol", table_name="simulation_positions")
    op.drop_table("simulation_positions")
    op.drop_table("simulation_accounts")
