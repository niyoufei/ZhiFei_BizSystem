from __future__ import annotations

from app.application.agents.runtime import ControlledAgent
from app.application.storage_access import StorageAccess
from app.contracts.agents import (
    AgentPermissionBoundary,
    AgentRetryPolicy,
    AgentSpec,
    CandidateEvidenceGap,
    EvidenceCompletenessInput,
    EvidenceCompletenessOutput,
)
from app.domain.scoring import ScoringCoreService


class EvidenceCompletenessAgent(ControlledAgent):
    input_model = EvidenceCompletenessInput
    output_model = EvidenceCompletenessOutput
    spec = AgentSpec(
        agent_name="evidence-completeness",
        responsibility="检查施组在16维评分点上的证据覆盖缺口，只输出候选证据缺口与补充建议。",
        input_schema=EvidenceCompletenessInput.model_json_schema(),
        output_schema=EvidenceCompletenessOutput.model_json_schema(),
        permission_boundary=AgentPermissionBoundary(
            allowed_reads=["submissions", "score_reports"],
            allowed_writes=["agent_audit_log"],
            forbidden_effects=["mutate_score", "mutate_rules", "mutate_config"],
            proposal_only=True,
        ),
        timeout_seconds=5.0,
        retry_policy=AgentRetryPolicy(max_attempts=1, backoff_seconds=0.0),
    )

    def __init__(
        self,
        *,
        scoring_core: ScoringCoreService | None = None,
        storage: StorageAccess | None = None,
    ):
        self.scoring_core = scoring_core or ScoringCoreService(storage=storage)

    def execute(self, validated_input: EvidenceCompletenessInput) -> EvidenceCompletenessOutput:
        submission = self.scoring_core.load_submission_snapshot(
            project_id=validated_input.project_id,
            submission_id=validated_input.submission_id,
        )
        rows = self.scoring_core.build_dimension_coverage_rows(
            project_id=validated_input.project_id,
            submission_id=validated_input.submission_id,
        )
        gaps: list[CandidateEvidenceGap] = []
        suggestions: list[str] = []
        for row in rows:
            score_ratio = float(row.get("score_ratio") or 0.0)
            evidence_count = int(row.get("evidence_count") or 0)
            has_evidence = bool(row.get("has_evidence"))
            if has_evidence and score_ratio >= 0.5:
                continue
            if not has_evidence:
                severity = "high"
                gap_type = "missing_evidence"
            elif score_ratio < 0.25:
                severity = "medium"
                gap_type = "weak_evidence"
            else:
                severity = "low"
                gap_type = "insufficient_detail"
            recommendation = f"在“{row['dimension_name']}”章节补充可直接引用的原文、步骤、责任人、阈值或验收动作。"
            gaps.append(
                CandidateEvidenceGap(
                    dimension_id=str(row["dimension_id"]),
                    dimension_name=str(row["dimension_name"]),
                    gap_type=gap_type,
                    severity=severity,
                    current_score=float(row["score"]),
                    max_score=float(row["max_score"]),
                    evidence_count=evidence_count,
                    hit_count=int(row.get("hit_count") or 0),
                    location_hint=str(row["location_hint"]),
                    recommendation=recommendation,
                )
            )
            if recommendation not in suggestions:
                suggestions.append(recommendation)
            if len(gaps) >= validated_input.top_n:
                break
        return EvidenceCompletenessOutput(
            project_id=validated_input.project_id,
            submission_id=str(submission.get("id") or ""),
            filename=str(submission.get("filename") or ""),
            candidate_evidence_gaps=gaps,
            candidate_suggestions=suggestions[: validated_input.top_n],
        )
