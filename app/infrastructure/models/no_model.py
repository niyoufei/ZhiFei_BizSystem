from __future__ import annotations

from datetime import datetime, timezone

from app.contracts.models import (
    CandidateEvidenceBatch,
    CandidateEvidenceRequest,
    SemanticAlignmentRequest,
    SemanticAlignmentResponse,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NoModelService:
    provider_name = "rules"
    mode = "no-model"

    def __init__(self, *, reason: str = "model_disabled"):
        self.reason = reason

    def is_available(self) -> bool:
        return False

    def extract_candidate_evidence(
        self,
        request: CandidateEvidenceRequest,
    ) -> CandidateEvidenceBatch:
        now = _utc_now_iso()
        return CandidateEvidenceBatch(
            provider=self.provider_name,
            mode="no-model",
            model_version="none",
            prompt_or_policy_version="none",
            extraction_time=now,
            available=False,
            fallback_reason=self.reason,
            candidates=[],
        )

    def align_semantic_requirements(
        self,
        request: SemanticAlignmentRequest,
    ) -> SemanticAlignmentResponse:
        now = _utc_now_iso()
        return SemanticAlignmentResponse(
            provider=self.provider_name,
            mode="no-model",
            available=False,
            model_version="none",
            prompt_or_policy_version="none",
            extraction_time=now,
            fallback_reason=self.reason,
            gaps=[],
        )
