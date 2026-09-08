"""durable strategy phase reason

Revision ID: 20260908_0011
Revises: 20260908_0010
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260908_0011"
down_revision: str | None = "20260908_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no backfill: rows written before this revision genuinely have no
    # recorded reason, and inventing one would misreport why they ended where they did.
    with op.batch_alter_table("strategy_states") as batch:
        batch.add_column(sa.Column("phase_reason", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("strategy_states") as batch:
        batch.drop_column("phase_reason")
