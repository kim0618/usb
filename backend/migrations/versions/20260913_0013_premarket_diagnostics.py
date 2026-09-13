"""durable premarket gate diagnostics

Revision ID: 20260913_0013
Revises: 20260913_0012
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.models.types import DecimalString, UTCDateTime

revision: str = "20260913_0013"
down_revision: str | None = "20260913_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    exact, utc = DecimalString(), UTCDateTime()
    op.create_table(
        "premarket_diagnostics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_state_id", sa.Integer(),
                  sa.ForeignKey("strategy_states.id", ondelete="CASCADE"), nullable=False),
        sa.Column("minute_bars_count", sa.Integer(), nullable=False),
        sa.Column("premarket_bars_count", sa.Integer(), nullable=False),
        sa.Column("previous_close", exact),
        sa.Column("reference_price", exact),
        sa.Column("gap_pct", exact),
        sa.Column("premarket_volume", exact),
        sa.Column("historical_average_daily_volume", exact),
        sa.Column("volume_ratio", exact),
        sa.Column("first_timestamp", utc),
        sa.Column("last_timestamp", utc),
        sa.Column("invalid_field", sa.String(48)),
        sa.Column("created_at", utc, nullable=False),
        sa.UniqueConstraint("strategy_state_id", name="uq_premarket_diagnostic_state"),
    )


def downgrade() -> None:
    op.drop_table("premarket_diagnostics")
