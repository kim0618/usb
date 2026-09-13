"""explicit active GPT analysis authority

Revision ID: 20260913_0012
Revises: 20260908_0011
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260913_0012"
down_revision: str | None = "20260908_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scanner_runs") as batch:
        batch.add_column(sa.Column("active_gpt_analysis_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_scanner_runs_active_gpt_analysis",
            "gpt_analyses",
            ["active_gpt_analysis_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # Preserve the old production authority: latest IMPORTED by payload analysis_at,
    # with id as the deterministic tie-breaker.
    op.execute(sa.text("""
        UPDATE scanner_runs
        SET active_gpt_analysis_id = (
            SELECT ga.id FROM gpt_analyses AS ga
            WHERE ga.scanner_run_id = scanner_runs.id AND ga.status = 'IMPORTED'
            ORDER BY ga.analysis_at DESC, ga.id DESC
            LIMIT 1
        )
    """))


def downgrade() -> None:
    with op.batch_alter_table("scanner_runs") as batch:
        batch.drop_constraint("fk_scanner_runs_active_gpt_analysis", type_="foreignkey")
        batch.drop_column("active_gpt_analysis_id")
