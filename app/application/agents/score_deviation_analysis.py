from __future__ import annotations

from app.application.agents.runtime import ControlledAgent
from app.application.storage_access import StorageAccess
from app.contracts.agents import (
    AgentPermissionBoundary,
    AgentRetryPolicy,
    AgentSpec,
    ScoreDeviationAnalysisInput,
    ScoreDeviationAnalysisOutput,
)
from app.domain.governance import GovernanceLoopService
from app.domain.learning import LearningLoopService


class ScoreDeviationAnalysisAgent(ControlledAgent):
    input_model = ScoreDeviationAnalysisInput
    output_model = ScoreDeviationAnalysisOutput
    spec = AgentSpec(
        agent_name="score-deviation-analysis",
        responsibility="分析系统分与真实评标结果偏差，只输出候选校准/特征/治理建议。",
        input_schema=ScoreDeviationAnalysisInput.model_json_schema(),
        output_schema=ScoreDeviationAnalysisOutput.model_json_schema(),
        permission_boundary=AgentPermissionBoundary(
            allowed_reads=["projects", "submissions", "ground_truth", "score_reports"],
            allowed_writes=["agent_audit_log"],
            forbidden_effects=["mutate_score", "deploy_calibrator", "activate_feature_pack"],
            proposal_only=True,
        ),
        timeout_seconds=5.0,
        retry_policy=AgentRetryPolicy(max_attempts=1, backoff_seconds=0.0),
    )

    def __init__(
        self,
        *,
        learning_loop: LearningLoopService | None = None,
        governance_loop: GovernanceLoopService | None = None,
        storage: StorageAccess | None = None,
    ):
        self.learning_loop = learning_loop or LearningLoopService(storage=storage)
        self.governance_loop = governance_loop or GovernanceLoopService()

    def execute(self, validated_input: ScoreDeviationAnalysisInput) -> ScoreDeviationAnalysisOutput:
        snapshot = self.learning_loop.build_score_deviation_snapshot(
            project_id=validated_input.project_id,
            ground_truth_id=validated_input.ground_truth_id,
            submission_id=validated_input.submission_id,
        )
        delta = float(snapshot["delta_score_100"])
        abs_delta = abs(delta)
        changes = []
        direction = "under_predict" if delta > 0 else "over_predict"
        if abs_delta >= 5.0:
            summary = (
                "建议复核项目级校准器"
                if direction == "under_predict"
                else "建议收紧乐观偏置与校准映射"
            )
            rationale = (
                f"系统分 {snapshot['predicted_score_100']:.2f} 与真实分 {snapshot['actual_score_100']:.2f}"
                f" 偏差 {delta:.2f} 分。"
            )
            changes.append(
                self.governance_loop.build_candidate_change(
                    category="calibration",
                    summary=summary,
                    rationale=rationale,
                    target_ref=f"project:{validated_input.project_id}:ground_truth:{snapshot['ground_truth_id']}",
                    impact_scope="project_calibration_review",
                )
            )
        if abs_delta >= 8.0:
            summary = (
                "建议检查高分特征与证据门槛是否保守"
                if direction == "under_predict"
                else "建议复核高分特征是否过度放宽"
            )
            changes.append(
                self.governance_loop.build_candidate_change(
                    category="feature_pack",
                    summary=summary,
                    rationale="偏差已达到特征包复核阈值，需人工判断是否调整学习产物。",
                    target_ref=f"submission:{snapshot['submission_id']}",
                    impact_scope="feature_pack_review",
                )
            )
        if abs_delta >= 12.0:
            changes.append(
                self.governance_loop.build_candidate_change(
                    category="governance_review",
                    summary="建议人工复核该真实评标样本",
                    rationale="偏差较大，需确认样本是否异常、评分制是否一致、证据是否完整。",
                    target_ref=f"ground_truth:{snapshot['ground_truth_id']}",
                    impact_scope="ground_truth_manual_review",
                )
            )
        return ScoreDeviationAnalysisOutput(
            project_id=validated_input.project_id,
            ground_truth_id=str(snapshot["ground_truth_id"]),
            submission_id=str(snapshot["submission_id"]),
            actual_score_100=float(snapshot["actual_score_100"]),
            predicted_score_100=float(snapshot["predicted_score_100"]),
            delta_score_100=delta,
            delta_ratio=float(snapshot["delta_ratio"]),
            candidate_changes=changes[: validated_input.max_changes],
            supporting_dimensions=[str(item) for item in snapshot.get("dimension_names") or []],
        )
