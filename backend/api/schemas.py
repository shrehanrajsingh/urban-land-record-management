from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str = "cadastral-fusion"


class IntegrateRequest(BaseModel):
    source_dataset_id: str = "CAD-LEGACY"
    reference_dataset_id: str = "SURVEY-2026"
    method: str = "auto"
    uncertainty_envelope_m: float = 0.5
    with_dtm: bool = False
    control_points: Optional[list[dict[str, Any]]] = None


class ReviewActionRequest(BaseModel):
    action: str = Field(..., pattern="^(APPROVE|EDIT|FIELD_VERIFY|REJECT)$")
    actor: str = "reviewer-07"
    actor_role: str = "REVIEWER"
    notes: Optional[str] = None
    payload: Optional[dict[str, Any]] = None


class PublishAttemptRequest(BaseModel):
    """Used by TC15 / demo: AI tries to publish (must fail)."""
    parcel_id: str
    actor: str = "ai-pipeline"
    actor_role: str = "AI_SERVICE"
