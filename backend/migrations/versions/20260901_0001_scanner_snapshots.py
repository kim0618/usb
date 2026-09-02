"""create scanner snapshot tables

Revision ID: 20260901_0001
Revises:
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scanner_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=False),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("score_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scanner_runs_trading_date", "scanner_runs", ["trading_date"])
    op.create_table(
        "scanner_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scanner_run_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("is_top8", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("score_components_json", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.String(length=40), nullable=False),
        sa.Column("available_at", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["scanner_run_id"], ["scanner_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scanner_run_id", "symbol", name="uq_scanner_candidate_run_symbol"
        ),
    )
    op.create_index(
        "ix_scanner_candidates_run_rank",
        "scanner_candidates",
        ["scanner_run_id", "rank"],
    )


def downgrade() -> None:
    op.drop_index("ix_scanner_candidates_run_rank", table_name="scanner_candidates")
    op.drop_table("scanner_candidates")
    op.drop_index("ix_scanner_runs_trading_date", table_name="scanner_runs")
    op.drop_table("scanner_runs")
