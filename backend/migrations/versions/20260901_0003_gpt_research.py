"""add Stage 4 GPT research tables

Revision ID: 20260901_0003
Revises: 20260901_0002
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "20260901_0003"
down_revision: str | None = "20260901_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    utc = sa.String(length=40)
    op.create_table("gpt_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scanner_run_id", sa.Integer(), sa.ForeignKey("scanner_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False), sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128), nullable=False), sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False), sa.Column("evidence_version", sa.String(64), nullable=False),
        sa.Column("analysis_at", utc, nullable=False), sa.Column("imported_at", utc, nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("scanner_run_id", "payload_hash", name="uq_gpt_analysis_run_payload"))
    op.create_index("ix_gpt_analyses_run_latest", "gpt_analyses", ["scanner_run_id", "status", "analysis_at"])
    op.create_table("gpt_candidate_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gpt_analysis_id", sa.Integer(), sa.ForeignKey("gpt_analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scanner_candidate_id", sa.Integer(), sa.ForeignKey("scanner_candidates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False), sa.Column("gpt_rank", sa.Integer(), nullable=False),
        *[sa.Column(name, sa.Float(), nullable=False) for name in ("overall_score", "catalyst_score", "fundamental_score", "momentum_score", "risk_score")],
        sa.Column("evidence_confidence", sa.Integer(), nullable=False), sa.Column("catalyst_duration", sa.String(32), nullable=False),
        sa.Column("stop_profile", sa.String(16), nullable=False), sa.Column("trailing_profile", sa.String(16), nullable=False),
        sa.Column("overnight_suitability", sa.String(16), nullable=False), sa.Column("company_summary", sa.Text(), nullable=False),
        sa.Column("catalyst_summary", sa.Text(), nullable=False), sa.Column("risk_summary", sa.Text(), nullable=False),
        sa.Column("invalidation_summary", sa.Text(), nullable=False), sa.Column("unknown_fields_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("gpt_analysis_id", "symbol", name="uq_gpt_candidate_analysis_symbol"),
        sa.UniqueConstraint("gpt_analysis_id", "gpt_rank", name="uq_gpt_candidate_analysis_rank"))
    op.create_table("gpt_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gpt_candidate_analysis_id", sa.Integer(), sa.ForeignKey("gpt_candidate_analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim", sa.String(128), nullable=False), sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False), sa.Column("title", sa.String(500), nullable=False),
        sa.Column("published_at", utc, nullable=True), sa.Column("source_domain", sa.String(253), nullable=False))
    op.create_index("ix_gpt_sources_candidate", "gpt_sources", ["gpt_candidate_analysis_id"])
    op.create_table("human_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gpt_analysis_id", sa.Integer(), sa.ForeignKey("gpt_analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scanner_candidate_id", sa.Integer(), sa.ForeignKey("scanner_candidates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False), sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True), sa.Column("decided_at", utc, nullable=False),
        sa.UniqueConstraint("gpt_analysis_id", "symbol", name="uq_human_decision_analysis_symbol"))


def downgrade() -> None:
    op.drop_table("human_decisions")
    op.drop_index("ix_gpt_sources_candidate", table_name="gpt_sources")
    op.drop_table("gpt_sources")
    op.drop_table("gpt_candidate_analyses")
    op.drop_index("ix_gpt_analyses_run_latest", table_name="gpt_analyses")
    op.drop_table("gpt_analyses")
