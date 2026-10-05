from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    crs: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    assets: Mapped[list[DatasetAsset]] = relationship(back_populates="dataset")


class DatasetAsset(Base):
    __tablename__ = "dataset_assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    dataset: Mapped[Dataset] = relationship(back_populates="assets")


class Parcel(Base):
    __tablename__ = "parcels"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    versions: Mapped[list[ParcelVersion]] = relationship(back_populates="parcel")


class ParcelVersion(Base):
    __tablename__ = "parcel_versions"
    __table_args__ = (UniqueConstraint("parcel_id", "version", name="uq_parcel_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parcel_id: Mapped[str] = mapped_column(ForeignKey("parcels.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry = mapped_column(Geometry("POLYGON", srid=32643), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OBSERVED")
    area: Mapped[Optional[float]] = mapped_column(Float)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    match_probability: Mapped[Optional[float]] = mapped_column(Float)
    geometry_uncertainty_m: Mapped[Optional[float]] = mapped_column(Float)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(64), default="SYSTEM")
    created_by_role: Mapped[str] = mapped_column(String(32), default="SYSTEM")
    review_case_id: Mapped[Optional[str]] = mapped_column(String(64))
    transformation_id: Mapped[Optional[str]] = mapped_column(String(64))
    model_run_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    parcel: Mapped[Parcel] = relationship(back_populates="versions")


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    geometry = mapped_column(Geometry("GEOMETRY", srid=32643), nullable=False)
    source_dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(128))
    model_confidence: Mapped[Optional[float]] = mapped_column(Float)
    positional_uncertainty_m: Mapped[Optional[float]] = mapped_column(Float)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SurveyPoint(Base):
    __tablename__ = "survey_points"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    geometry = mapped_column(Geometry("POINT", srid=32643), nullable=False)
    point_type: Mapped[str] = mapped_column(String(64), default="GNSS")
    uncertainty_m: Mapped[float] = mapped_column(Float, default=0.05)
    source_dataset: Mapped[Optional[str]] = mapped_column(String(64))
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Transformation(Base):
    __tablename__ = "transformations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_crs: Mapped[str] = mapped_column(String(64))
    target_crs: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RegistrationRun(Base):
    __tablename__ = "registration_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_dataset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_dataset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    transformation_id: Mapped[Optional[str]] = mapped_column(ForeignKey("transformations.id"))
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    global_rmse: Mapped[Optional[float]] = mapped_column(Float)
    regional_stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    residual_heatmap: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    uncertainty_m: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CandidateMatch(Base):
    __tablename__ = "candidate_matches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_parcel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_parcel_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    match_probability: Mapped[float] = mapped_column(Float, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    registration_run_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MatchEvidence(Base):
    __tablename__ = "match_evidence"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_match_id: Mapped[str] = mapped_column(ForeignKey("candidate_matches.id"))
    geometry_compatibility: Mapped[Optional[float]] = mapped_column(Float)
    attribute_compatibility: Mapped[Optional[float]] = mapped_column(Float)
    survey_support: Mapped[Optional[float]] = mapped_column(Float)
    physical_support: Mapped[Optional[float]] = mapped_column(Float)
    temporal_support: Mapped[Optional[float]] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Conflict(Base):
    __tablename__ = "conflicts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conflict_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), default="HIGH")
    parcel_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    geometry = mapped_column(Geometry("GEOMETRY", srid=32643), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewCase(Base):
    __tablename__ = "review_cases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conflict_id: Mapped[Optional[str]] = mapped_column(ForeignKey("conflicts.id"))
    candidate_match_id: Mapped[Optional[str]] = mapped_column(ForeignKey("candidate_matches.id"))
    parcel_version_id: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    summary: Mapped[str] = mapped_column(Text, default="")
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    actions: Mapped[list[ReviewAction]] = relationship(back_populates="review_case")


class ReviewAction(Base):
    __tablename__ = "review_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    review_case_id: Mapped[str] = mapped_column(ForeignKey("review_cases.id"))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    review_case: Mapped[ReviewCase] = relationship(back_populates="actions")


class Provenance(Base):
    __tablename__ = "provenance"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parcel_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    lineage: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    derived_from: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    registration_run_id: Mapped[Optional[str]] = mapped_column(String(64))
    match_model: Mapped[Optional[str]] = mapped_column(String(128))
    review_case_id: Mapped[Optional[str]] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    current_stage: Mapped[str] = mapped_column(String(64), default="")
    stages: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_dataset_id: Mapped[Optional[str]] = mapped_column(String(64))
    reference_dataset_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
