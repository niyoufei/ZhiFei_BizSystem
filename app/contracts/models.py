from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ModelBoundaryMode = Literal["no-model", "openai"]
ValidatorStatus = Literal[
    "pending_validation",
    "accepted",
    "rejected_missing_snippet",
    "rejected_missing_source_ref",
    "rejected_missing_locator",
    "rejected_invalid_confidence",
    "rejected_low_confidence",
    "rejected_missing_model_metadata",
    "rejected_unknown_source_ref",
    "rejected_duplicate",
]


class EvidenceSourceArtifact(BaseModel):
    source_ref: str
    artifact_type: str
    file_name: str
    page_locator: str = "inline:text"
    content_excerpt: str = ""


class CandidateEvidenceRecord(BaseModel):
    candidate_id: str
    source_ref: str
    artifact_type: str
    file_name: str
    page_locator: str
    snippet: str
    matched_requirement: str | None = None
    dimension_id: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    model_version: str
    prompt_or_policy_version: str
    extraction_time: str
    validator_status: ValidatorStatus = "pending_validation"
    rejection_reason: str | None = None


class CandidateEvidenceRequest(BaseModel):
    project_id: str
    submission_id: str
    submission_text: str
    artifacts: list[EvidenceSourceArtifact] = Field(default_factory=list)
    requirement_hints: list[str] = Field(default_factory=list)
    anchor_hints: list[str] = Field(default_factory=list)
    max_candidates: int = Field(6, ge=1, le=20)


class CandidateEvidenceBatch(BaseModel):
    provider: str
    mode: ModelBoundaryMode
    model_version: str
    prompt_or_policy_version: str
    extraction_time: str
    available: bool
    fallback_reason: str | None = None
    candidates: list[CandidateEvidenceRecord] = Field(default_factory=list)


class SemanticAlignmentRequest(BaseModel):
    project_id: str
    artifact_text: str
    submission_text: str
    max_gaps: int = Field(6, ge=1, le=20)


class SemanticAlignmentGap(BaseModel):
    source_ref: str
    requirement_text: str
    submission_gap: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    model_version: str
    prompt_or_policy_version: str
    extraction_time: str
    validator_status: ValidatorStatus = "pending_validation"


class SemanticAlignmentResponse(BaseModel):
    provider: str
    mode: ModelBoundaryMode
    available: bool
    model_version: str
    prompt_or_policy_version: str
    extraction_time: str
    fallback_reason: str | None = None
    gaps: list[SemanticAlignmentGap] = Field(default_factory=list)


class EvidenceAssemblySummary(BaseModel):
    mode: ModelBoundaryMode
    provider: str
    available: bool
    model_version: str
    prompt_or_policy_version: str
    fallback_reason: str | None = None
    base_unit_count: int = 0
    candidate_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    validator_breakdown: dict[str, int] = Field(default_factory=dict)


__all__ = [
    "CandidateEvidenceBatch",
    "CandidateEvidenceRecord",
    "CandidateEvidenceRequest",
    "EvidenceAssemblySummary",
    "EvidenceSourceArtifact",
    "ModelBoundaryMode",
    "SemanticAlignmentGap",
    "SemanticAlignmentRequest",
    "SemanticAlignmentResponse",
    "ValidatorStatus",
]
