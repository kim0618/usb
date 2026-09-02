"""add Stage 5 daily planned-risk state

Revision ID: 20260901_0004
Revises: 20260901_0003
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260901_0004"
down_revision: str | None = "20260901_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    exact = sa.String(length=100)
    utc = sa.String(length=40)
    op.create_table(
        "daily_symbol_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("entry_intent_issued_at", utc, nullable=False),
        sa.Column("planned_risk_amount", exact, nullable=False),
        sa.Column("base_notional_reserved", exact, nullable=False),
        sa.Column("pyramid_notional_reserved", exact, nullable=False),
        sa.Column("add_count", sa.Integer(), nullable=False),
        sa.Column("risk_version", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("created_at", utc, nullable=False),
        sa.Column("updated_at", utc, nullable=False),
        sa.UniqueConstraint("trading_date", "symbol", name="uq_daily_symbol_state_date_symbol"),
    )
    op.create_index("ix_daily_symbol_states_trading_date", "daily_symbol_states", ["trading_date"])


def downgrade() -> None:
    op.drop_index("ix_daily_symbol_states_trading_date", table_name="daily_symbol_states")
    op.drop_table("daily_symbol_states")
