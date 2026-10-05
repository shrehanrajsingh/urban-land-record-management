"""initial cadastral fusion schema

Revision ID: 001
Revises:
Create Date: 2026-10-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "datasets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("crs", sa.String(64), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "dataset_assets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dataset_id", sa.String(64), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("asset_type", sa.String(64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default="{}"),
    )
    op.create_table(
        "parcels",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("external_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "parcel_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("parcel_id", sa.String(64), sa.ForeignKey("parcels.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("geometry", Geometry("POLYGON", srid=32643), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="OBSERVED"),
        sa.Column("area", sa.Float()),
        sa.Column("attributes", postgresql.JSONB(), server_default="{}"),
        sa.Column("source_ids", postgresql.JSONB(), server_default="[]"),
        sa.Column("match_probability", sa.Float()),
        sa.Column("geometry_uncertainty_m", sa.Float()),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(64), server_default="SYSTEM"),
        sa.Column("created_by_role", sa.String(32), server_default="SYSTEM"),
        sa.Column("review_case_id", sa.String(64)),
        sa.Column("transformation_id", sa.String(64)),
        sa.Column("model_run_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("parcel_id", "version", name="uq_parcel_version"),
    )
    op.create_index("ix_parcel_versions_geom", "parcel_versions", ["geometry"], postgresql_using="gist")

    op.create_table(
        "observations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("geometry", Geometry("GEOMETRY", srid=32643), nullable=False),
        sa.Column("source_dataset", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128)),
        sa.Column("model_confidence", sa.Float()),
        sa.Column("positional_uncertainty_m", sa.Float()),
        sa.Column("properties", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_observations_geom", "observations", ["geometry"], postgresql_using="gist")

    op.create_table(
        "survey_points",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("geometry", Geometry("POINT", srid=32643), nullable=False),
        sa.Column("point_type", sa.String(64), server_default="GNSS"),
        sa.Column("uncertainty_m", sa.Float(), server_default="0.05"),
        sa.Column("source_dataset", sa.String(64)),
        sa.Column("properties", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_survey_points_geom", "survey_points", ["geometry"], postgresql_using="gist")

    op.create_table(
        "transformations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("params", postgresql.JSONB(), server_default="{}"),
        sa.Column("source_crs", sa.String(64)),
        sa.Column("target_crs", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "registration_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_dataset_id", sa.String(64), nullable=False),
        sa.Column("reference_dataset_id", sa.String(64), nullable=False),
        sa.Column("transformation_id", sa.String(64), sa.ForeignKey("transformations.id")),
        sa.Column("passed", sa.Boolean(), server_default="false"),
        sa.Column("global_rmse", sa.Float()),
        sa.Column("regional_stats", postgresql.JSONB(), server_default="{}"),
        sa.Column("residual_heatmap", postgresql.JSONB(), server_default="{}"),
        sa.Column("uncertainty_m", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "candidate_matches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_parcel_id", sa.String(64), nullable=False),
        sa.Column("target_parcel_ids", postgresql.JSONB(), server_default="[]"),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("match_probability", sa.Float(), nullable=False),
        sa.Column("features", postgresql.JSONB(), server_default="{}"),
        sa.Column("evidence", postgresql.JSONB(), server_default="{}"),
        sa.Column("registration_run_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "match_evidence",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("candidate_match_id", sa.String(64), sa.ForeignKey("candidate_matches.id")),
        sa.Column("geometry_compatibility", sa.Float()),
        sa.Column("attribute_compatibility", sa.Float()),
        sa.Column("survey_support", sa.Float()),
        sa.Column("physical_support", sa.Float()),
        sa.Column("temporal_support", sa.Float()),
        sa.Column("details", postgresql.JSONB(), server_default="{}"),
    )
    op.create_table(
        "conflicts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("conflict_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(32), server_default="HIGH"),
        sa.Column("parcel_ids", postgresql.JSONB(), server_default="[]"),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("geometry", Geometry("GEOMETRY", srid=32643)),
        sa.Column("details", postgresql.JSONB(), server_default="{}"),
        sa.Column("resolved", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "review_cases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("conflict_id", sa.String(64), sa.ForeignKey("conflicts.id")),
        sa.Column("candidate_match_id", sa.String(64), sa.ForeignKey("candidate_matches.id")),
        sa.Column("parcel_version_id", sa.String(64)),
        sa.Column("status", sa.String(32), server_default="OPEN"),
        sa.Column("summary", sa.Text(), server_default=""),
        sa.Column("evidence_snapshot", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "review_actions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("review_case_id", sa.String(64), sa.ForeignKey("review_cases.id")),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "provenance",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("parcel_version_id", sa.String(64), nullable=False),
        sa.Column("lineage", postgresql.JSONB(), server_default="[]"),
        sa.Column("derived_from", postgresql.JSONB(), server_default="[]"),
        sa.Column("registration_run_id", sa.String(64)),
        sa.Column("match_model", sa.String(128)),
        sa.Column("review_case_id", sa.String(64)),
        sa.Column("details", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "model_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("params", postgresql.JSONB(), server_default="{}"),
        sa.Column("metrics", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # State machine + AI write restriction triggers
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_parcel_status_transition()
        RETURNS trigger AS $$
        DECLARE
          role text := COALESCE(current_setting('app.current_role', true), NEW.created_by_role, 'SYSTEM');
          allowed boolean := false;
        BEGIN
          -- AI cannot create APPROVED or PUBLISHED
          IF role = 'AI_SERVICE' AND NEW.status IN ('APPROVED', 'PUBLISHED') THEN
            RAISE EXCEPTION 'AI_SERVICE cannot set status %', NEW.status
              USING ERRCODE = '42501';
          END IF;

          IF TG_OP = 'INSERT' THEN
            IF NEW.status IN ('OBSERVED','CANDIDATE','VALIDATED','REVIEW','FIELD_VERIFICATION','REJECTED','APPROVED','PUBLISHED') THEN
              -- INSERT allowed for initial states; AI restricted above
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'Invalid status %', NEW.status;
          END IF;

          -- UPDATE transition checks
          IF OLD.status = NEW.status THEN
            RETURN NEW;
          END IF;

          allowed := CASE OLD.status
            WHEN 'OBSERVED' THEN NEW.status IN ('CANDIDATE','REVIEW','REJECTED')
            WHEN 'CANDIDATE' THEN NEW.status IN ('VALIDATED','REVIEW','FIELD_VERIFICATION','REJECTED')
            WHEN 'VALIDATED' THEN NEW.status IN ('APPROVED','REVIEW','REJECTED')
            WHEN 'REVIEW' THEN NEW.status IN ('CANDIDATE','VALIDATED','APPROVED','REJECTED','FIELD_VERIFICATION')
            WHEN 'FIELD_VERIFICATION' THEN NEW.status IN ('APPROVED','REJECTED','REVIEW')
            WHEN 'APPROVED' THEN NEW.status IN ('PUBLISHED','REVIEW')
            WHEN 'REJECTED' THEN FALSE
            WHEN 'PUBLISHED' THEN FALSE
            ELSE FALSE
          END;

          IF NOT allowed THEN
            RAISE EXCEPTION 'Illegal status transition % -> %', OLD.status, NEW.status
              USING ERRCODE = '23514';
          END IF;

          IF role = 'AI_SERVICE' AND NEW.status IN ('APPROVED', 'PUBLISHED') THEN
            RAISE EXCEPTION 'AI_SERVICE cannot transition to %', NEW.status
              USING ERRCODE = '42501';
          END IF;

          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_parcel_status ON parcel_versions;
        CREATE TRIGGER trg_parcel_status
        BEFORE INSERT OR UPDATE OF status ON parcel_versions
        FOR EACH ROW EXECUTE PROCEDURE enforce_parcel_status_transition();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_parcel_status ON parcel_versions")
    op.execute("DROP FUNCTION IF EXISTS enforce_parcel_status_transition")
    for t in [
        "audit_events",
        "model_runs",
        "provenance",
        "review_actions",
        "review_cases",
        "conflicts",
        "match_evidence",
        "candidate_matches",
        "registration_runs",
        "transformations",
        "survey_points",
        "observations",
        "parcel_versions",
        "parcels",
        "dataset_assets",
        "datasets",
    ]:
        op.drop_table(t)
