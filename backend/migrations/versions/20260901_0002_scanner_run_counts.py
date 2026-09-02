"""add scanner run result counts

Revision ID: 20260901_0002
Revises: 20260901_0001
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0002"
down_revision: str | None = "20260901_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scanner_runs") as batch_op:
        batch_op.add_column(
            sa.Column("universe_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("excluded_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("top8_count", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("scanner_runs") as batch_op:
        batch_op.drop_column("top8_count")
        batch_op.drop_column("candidate_count")
        batch_op.drop_column("excluded_count")
        batch_op.drop_column("universe_count")
