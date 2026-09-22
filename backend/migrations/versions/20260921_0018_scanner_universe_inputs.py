"""recorded paper scanner universe (RECORDED_PAPER_UNIVERSE)

Revision ID: 20260921_0018
Revises: 20260916_0017

Creates one table and nothing else: no existing row is read, rewritten or backfilled, and
the downgrade drops exactly what the upgrade made. Runs before this revision simply have
no recorded universe; nothing is reconstructed for them.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.models.types import UTCDateTime

revision: str = "20260921_0018"
down_revision: str | None = "20260916_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scanner_universe_inputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scanner_run_id", sa.Integer(),
                  sa.ForeignKey("scanner_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("exchange_code", sa.String(16), nullable=True),
        sa.Column("company_name", sa.String(128), nullable=True),
        sa.Column("market_cap", sa.String(64), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("acquired_at", UTCDateTime(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("scanner_rank", sa.Integer(), nullable=True),
        sa.Column("exclusion_reason", sa.String(64), nullable=True),
        sa.Column("universe_checksum", sa.String(64), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint("scanner_run_id", "position", name="uq_scanner_universe_position"),
        sa.UniqueConstraint("scanner_run_id", "symbol", name="uq_scanner_universe_symbol"),
    )
    op.create_index("ix_scanner_universe_inputs_scanner_run_id", "scanner_universe_inputs",
                    ["scanner_run_id"])


def downgrade() -> None:
    op.drop_index("ix_scanner_universe_inputs_scanner_run_id",
                  table_name="scanner_universe_inputs")
    op.drop_table("scanner_universe_inputs")
