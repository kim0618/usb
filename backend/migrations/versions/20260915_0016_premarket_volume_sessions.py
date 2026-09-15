"""premarket volume V2 analytics

Revision ID: 20260915_0016
Revises: 20260915_0015
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.models.types import DecimalString, UTCDateTime

revision: str = "20260915_0016"
down_revision: str | None = "20260915_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Ratios and volumes are exact Decimal text: SQLite REAL would round a ratio across a
# counterfactual threshold (1.24999999999 read back as 1.25).
V2_OBSERVATION_COLUMNS = (
    ("v1_volume_ratio", DecimalString()),
    ("v2_status", sa.String(48)),
    ("v2_median_ratio", DecimalString()),
    ("v2_today_premarket_volume", DecimalString()),
    ("v2_baseline_volume", DecimalString()),
    ("v2_baseline_method", sa.String(16)),
    ("v2_baseline_sessions", sa.Integer()),
    ("v2_baseline_start_date", sa.Date()),
    ("v2_baseline_end_date", sa.Date()),
    ("v2_collector_version", sa.String(64)),
    ("v2_projections_json", sa.JSON()),
)


def upgrade() -> None:
    op.create_table(
        "premarket_volume_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(8), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("collector_version", sa.String(64), nullable=False),
        sa.Column("premarket_volume", sa.Integer(), nullable=True),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.Column("regular_bar_count", sa.Integer(), nullable=False),
        sa.Column("first_timestamp", UTCDateTime(), nullable=True),
        sa.Column("last_timestamp", UTCDateTime(), nullable=True),
        sa.Column("pages_used", sa.Integer(), nullable=True),
        sa.Column("target_reached", sa.Boolean(), nullable=True),
        sa.Column("quality_status", sa.String(48), nullable=False),
        sa.Column("quality_reason", sa.String(128), nullable=True),
        sa.Column("collected_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint("symbol", "exchange", "trading_date", "source", "collector_version",
                            name="uq_premarket_volume_session"),
    )
    op.create_index("ix_premarket_volume_sessions_trading_date", "premarket_volume_sessions",
                    ["trading_date"])
    # Nullable with no backfill: observations written before this revision never had
    # a V2 answer, and inventing one would misreport what the observer recorded.
    with op.batch_alter_table("entry_drift_observations") as batch:
        for name, type_ in V2_OBSERVATION_COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("entry_drift_observations") as batch:
        for name, _type in reversed(V2_OBSERVATION_COLUMNS):
            batch.drop_column(name)
    op.drop_index("ix_premarket_volume_sessions_trading_date",
                  table_name="premarket_volume_sessions")
    op.drop_table("premarket_volume_sessions")
