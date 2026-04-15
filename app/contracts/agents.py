from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.contracts.task_runtime import FailureCategory, TaskState

AgentExecutionStatus = Literal["success", "error", "timeout", "cached"]


class AgentRetryPolicy(BaseModel):
    max_attempts: int = Field(1, ge=1, le=5)
    backoff_seconds: float = Field(0.0, ge=0.0, le=30.0)


class AgentPermissionBoundary(BaseModel):
    allowed_reads: list[str] = Field(default_factory=list)
    allowed_writes: list[str] = Field(default_factory=list)
    forbidden_effects: list[str] = Field(default_factory=list)
    proposal_only: bool = True


class AgentSpec(BaseModel):
    agent_name: str
    responsibility: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission_boundary: AgentPermissionBoundary
    timeout_seconds: float = Field(..., gt=0.0, le=120.0)
    retry_policy: AgentRetryPolicy = Field(default_factory=AgentRetryPolicy)


class AgentAuditFields(BaseModel):
    run_id: str
    agent_name: str
    trigger_event: str
    idempotency_key: str
    input_digest: str
    output_digest: str | None = None
    started_at: str
    finished_at: str
    status: AgentExecutionStatus
    attempt_count: int = Field(ge=1)
    dry_run: bool = True
    actor_type: str = "system"
    actor_id: str = "system"
    model_provider: str = "rules"
    model_name: str = "deterministic"
    prompt_version: str | None = None
    cache_hit_from_run_id: str | None = None
    task_state: TaskState = "succeeded"
    failure_category: FailureCategory = "none"
    correlation_id: str | None = None
    project_id: str | None = None


class AgentExecutionRecord(BaseModel):
    audit: AgentAuditFields
    spec: AgentSpec
    input_payload: dict[str, Any]
    output_payload: dict[str, Any] | None = None
    error: str | None = None


class AgentRunResult(BaseModel):
    spec: AgentSpec
    audit: AgentAuditFields
    output: dict[str, Any] | None = None
    cached: bool = False
    error: str | None = None


class AgentBaseInput(BaseModel):
    trigger_event: str = "manual_dry_run"
    actor_type: str = "system"
    actor_id: str = "system"


class AgentOutputBase(BaseModel):
    proposal_only: bool = True
    model_provider: str = "rules"
    model_name: str = "deterministic"
    prompt_version: str | None = None


class CandidateEvidenceGap(BaseModel):
    dimension_id: str
    dimension_name: str
    gap_type: str
    severity: Literal["low", "medium", "high"]
    current_score: float
    max_score: float
    evidence_count: int = 0
    hit_count: int = 0
    location_hint: str
    recommendation: str


class CandidateChange(BaseModel):
    category: Literal["calibration", "feature_pack", "governance_review", "ops_followup"]
    summary: str
    rationale: str
    target_ref: str
    impact_scope: str
    requires_human_review: bool = True
    governance_status: str = "pending_review"
    apply_allowed: bool = False


class OpsDiagnosticItem(BaseModel):
    source: str
    status: Literal["pass", "warn", "fail", "unknown"]
    severity: Literal["low", "medium", "high"]
    summary: str
    recommendation: str


class EvidenceCompletenessInput(AgentBaseInput):
    project_id: str
    submission_id: str | None = None
    top_n: int = Field(5, ge=1, le=20)


class EvidenceCompletenessOutput(AgentOutputBase):
    project_id: str
    submission_id: str
    filename: str
    candidate_evidence_gaps: list[CandidateEvidenceGap] = Field(default_factory=list)
    candidate_suggestions: list[str] = Field(default_factory=list)
    deterministic: bool = True
    rule_source: str = "scoring_core"


class ScoreDeviationAnalysisInput(AgentBaseInput):
    project_id: str
    ground_truth_id: str | None = None
    submission_id: str | None = None
    max_changes: int = Field(4, ge=1, le=10)


class ScoreDeviationAnalysisOutput(AgentOutputBase):
    project_id: str
    ground_truth_id: str
    submission_id: str
    actual_score_100: float
    predicted_score_100: float
    delta_score_100: float
    delta_ratio: float
    candidate_changes: list[CandidateChange] = Field(default_factory=list)
    supporting_dimensions: list[str] = Field(default_factory=list)
    deterministic: bool = True
    rule_source: str = "learning_loop"


class OpsTriageInput(AgentBaseInput):
    ops_agents_payload: dict[str, Any] | None = None
    doctor_payload: dict[str, Any] | None = None
    soak_payload: dict[str, Any] | None = None
    preflight_payload: dict[str, Any] | None = None
    acceptance_payload: dict[str, Any] | None = None
    ops_agents_json_path: str | None = None
    doctor_json_path: str | None = None
    soak_json_path: str | None = None
    preflight_json_path: str | None = None
    acceptance_json_path: str | None = None


class OpsTriageOutput(AgentOutputBase):
    overall_status: Literal["pass", "warn", "fail", "unknown"]
    severity: Literal["low", "medium", "high"]
    diagnostics: list[OpsDiagnosticItem] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    deterministic: bool = True
    rule_source: str = "ops_guard"


__all__ = [
    "AgentAuditFields",
    "AgentBaseInput",
    "AgentExecutionRecord",
    "AgentOutputBase",
    "AgentPermissionBoundary",
    "AgentRetryPolicy",
    "AgentRunResult",
    "AgentSpec",
    "CandidateChange",
    "CandidateEvidenceGap",
    "EvidenceCompletenessInput",
    "EvidenceCompletenessOutput",
    "OpsDiagnosticItem",
    "OpsTriageInput",
    "OpsTriageOutput",
    "ScoreDeviationAnalysisInput",
    "ScoreDeviationAnalysisOutput",
]
