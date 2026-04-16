from __future__ import annotations

from app.contracts.agents import CandidateChange


class GovernanceLoopService:
    """治理闭环只生成待审批候选变更，不直接改线上状态。"""

    def build_candidate_change(
        self,
        *,
        category: str,
        summary: str,
        rationale: str,
        target_ref: str,
        impact_scope: str,
    ) -> CandidateChange:
        return CandidateChange(
            category=category,
            summary=summary.strip(),
            rationale=rationale.strip(),
            target_ref=target_ref.strip(),
            impact_scope=impact_scope.strip(),
            requires_human_review=True,
            governance_status="pending_review",
            apply_allowed=False,
        )
