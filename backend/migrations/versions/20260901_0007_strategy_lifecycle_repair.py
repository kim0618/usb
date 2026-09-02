"""repair Stage 7 strategy-state grain

Revision ID: 20260901_0007
Revises: 20260901_0006
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "20260901_0007"
down_revision: str | None = "20260901_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("strategy_states") as batch:
        batch.add_column(sa.Column("book", sa.String(16), server_default="ACTUAL", nullable=False))
        batch.add_column(sa.Column("variant", sa.String(16), server_default="ACTUAL", nullable=False))
        batch.drop_constraint("uq_strategy_state_symbol_date", type_="unique")
        batch.create_unique_constraint("uq_strategy_state_grain",
                                       ["symbol", "trading_date", "book", "variant"])
        batch.alter_column("book", server_default=None)
        batch.alter_column("variant", server_default=None)


def downgrade() -> None:
    # Multiple shadow rows cannot fit the old grain.  Keep ACTUAL preferentially,
    # otherwise one deterministic row per symbol/date, before restoring 0006.
    connection = op.get_bind()
    connection.execute(sa.text("""
        DELETE FROM strategy_states WHERE id NOT IN (
          SELECT COALESCE(
            MAX(CASE WHEN book = 'ACTUAL' THEN id END), MIN(id)
          ) FROM strategy_states GROUP BY symbol, trading_date
        )
    """))
    with op.batch_alter_table("strategy_states") as batch:
        batch.drop_constraint("uq_strategy_state_grain", type_="unique")
        batch.create_unique_constraint("uq_strategy_state_symbol_date", ["symbol", "trading_date"])
        batch.drop_column("variant")
        batch.drop_column("book")
