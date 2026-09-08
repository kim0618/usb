"""actual simulation account daily performance snapshots

Revision ID: 20260908_0010
Revises: 20260906_0009
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.models.types import DecimalString, UTCDateTime

revision: str = "20260908_0010"
down_revision: str | None = "20260906_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    exact, utc = DecimalString(), UTCDateTime()
    op.create_table(
        "account_daily_performance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("simulation_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("opening_equity", exact, nullable=False),
        sa.Column("closing_equity", exact, nullable=False),
        sa.Column("cash", exact, nullable=False),
        sa.Column("position_market_value", exact, nullable=False),
        sa.Column("daily_pnl", exact, nullable=False),
        sa.Column("daily_return", exact, nullable=False),
        sa.Column("created_at", utc, nullable=False),
        sa.Column("updated_at", utc, nullable=False),
        sa.UniqueConstraint("account_id", "trading_date", name="uq_account_daily_performance_account_date"),
    )
    op.create_index("ix_account_daily_performance_account_id", "account_daily_performance", ["account_id"])
    op.create_index("ix_account_daily_performance_trading_date", "account_daily_performance", ["trading_date"])


def downgrade() -> None:
    op.drop_index("ix_account_daily_performance_trading_date", table_name="account_daily_performance")
    op.drop_index("ix_account_daily_performance_account_id", table_name="account_daily_performance")
    op.drop_table("account_daily_performance")
