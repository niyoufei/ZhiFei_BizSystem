from __future__ import annotations

from app.application.agents.runtime import ControlledAgent
from app.contracts.agents import (
    AgentPermissionBoundary,
    AgentRetryPolicy,
    AgentSpec,
    OpsTriageInput,
    OpsTriageOutput,
)
from app.domain.ops import OpsGuardService


class OpsTriageAgent(ControlledAgent):
    input_model = OpsTriageInput
    output_model = OpsTriageOutput
    spec = AgentSpec(
        agent_name="ops-triage",
        responsibility="汇总 doctor/soak/preflight/acceptance/ops_agents 快照并输出运维诊断。",
        input_schema=OpsTriageInput.model_json_schema(),
        output_schema=OpsTriageOutput.model_json_schema(),
        permission_boundary=AgentPermissionBoundary(
            allowed_reads=[
                "build/ops_agents_status.json",
                "build/doctor_summary.json",
                "build/stability_soak_latest.json",
                "build/trial_preflight_latest.json",
                "build/acceptance_summary.json",
            ],
            allowed_writes=["agent_audit_log"],
            forbidden_effects=["restart_runtime", "mutate_score", "mutate_config"],
            proposal_only=True,
        ),
        timeout_seconds=5.0,
        retry_policy=AgentRetryPolicy(max_attempts=1, backoff_seconds=0.0),
    )

    def __init__(self, *, ops_guard: OpsGuardService | None = None):
        self.ops_guard = ops_guard or OpsGuardService()

    def execute(self, validated_input: OpsTriageInput) -> OpsTriageOutput:
        snapshot = self.ops_guard.build_triage_snapshot(
            ops_agents_payload=validated_input.ops_agents_payload,
            doctor_payload=validated_input.doctor_payload,
            soak_payload=validated_input.soak_payload,
            preflight_payload=validated_input.preflight_payload,
            acceptance_payload=validated_input.acceptance_payload,
            ops_agents_json_path=validated_input.ops_agents_json_path,
            doctor_json_path=validated_input.doctor_json_path,
            soak_json_path=validated_input.soak_json_path,
            preflight_json_path=validated_input.preflight_json_path,
            acceptance_json_path=validated_input.acceptance_json_path,
        )
        return OpsTriageOutput.model_validate(snapshot)
