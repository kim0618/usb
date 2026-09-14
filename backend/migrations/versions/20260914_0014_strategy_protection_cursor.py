"""durable stop protection cursor

Revision ID: 20260914_0014
Revises: 20260913_0013
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.models.types import UTCDateTime

revision: str = "20260914_0014"
down_revision: str | None = "20260913_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no backfill: an open position without a cursor is protected from
    # the bar its broker position was opened on, which is exactly where a cursor
    # written at the fill would have started.
    with op.batch_alter_table("strategy_states") as batch:
        batch.add_column(sa.Column("last_protected_bar_at", UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("strategy_states") as batch:
        batch.drop_column("last_protected_bar_at")
