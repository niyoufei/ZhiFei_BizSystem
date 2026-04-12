from __future__ import annotations

from typing import Protocol

from app.contracts.models import (
    CandidateEvidenceBatch,
    CandidateEvidenceRequest,
    SemanticAlignmentRequest,
    SemanticAlignmentResponse,
)


class ModelService(Protocol):
    provider_name: str
    mode: str

    def is_available(self) -> bool:
        ...

    def extract_candidate_evidence(
        self,
        request: CandidateEvidenceRequest,
    ) -> CandidateEvidenceBatch:
        ...

    def align_semantic_requirements(
        self,
        request: SemanticAlignmentRequest,
    ) -> SemanticAlignmentResponse:
        ...
