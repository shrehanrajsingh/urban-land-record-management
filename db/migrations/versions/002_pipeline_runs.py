"""Add pipeline_runs table + ai_service role grants.

Revision ID: 002
Revises: 001
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- pipeline_runs table ---
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(32), server_default="PENDING"),
        sa.Column("current_stage", sa.String(64), server_default=""),
        sa.Column("stages", postgresql.JSONB(), server_default="{}"),
        sa.Column("source_dataset_id", sa.String(64)),
        sa.Column("reference_dataset_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- Postgres role ai_service + grants ---
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_service') THEN
                CREATE ROLE ai_service NOLOGIN;
            END IF;
        END
        $$;
        """
    )

    granted_tables = [
        "observations",
        "survey_points",
        "parcel_versions",
        "candidate_matches",
        "match_evidence",
        "conflicts",
        "review_cases",
        "provenance",
        "audit_events",
    ]
    for table in granted_tables:
        op.execute(f"GRANT INSERT ON {table} TO ai_service")


def downgrade() -> None:
    granted_tables = [
        "observations",
        "survey_points",
        "parcel_versions",
        "candidate_matches",
        "match_evidence",
        "conflicts",
        "review_cases",
        "provenance",
        "audit_events",
    ]
    for table in granted_tables:
        op.execute(f"REVOKE INSERT ON {table} FROM ai_service")

    op.drop_table("pipeline_runs")
