from __future__ import annotations

from unittest.mock import patch

from app.contracts.models import (
    CandidateEvidenceBatch,
    CandidateEvidenceRecord,
    CandidateEvidenceRequest,
    EvidenceSourceArtifact,
)
from app.domain.evidence.pipeline import build_scoring_evidence_package
from app.infrastructure.models.no_model import NoModelService
from app.infrastructure.models.openai_boundary import PROMPT_POLICY_VERSION, OpenAIModelService


class _StubModelService:
    provider_name = "stub-openai"
    mode = "openai"

    def is_available(self) -> bool:
        return True

    def extract_candidate_evidence(
        self,
        request: CandidateEvidenceRequest,
    ) -> CandidateEvidenceBatch:
        return CandidateEvidenceBatch(
            provider=self.provider_name,
            mode="openai",
            model_version="gpt-5.4",
            prompt_or_policy_version="boundary-test-v1",
            extraction_time="2026-04-12T00:00:00+00:00",
            available=True,
            candidates=[
                CandidateEvidenceRecord(
                    candidate_id="c-accepted",
                    source_ref="drawing:sheet-1",
                    artifact_type="drawing",
                    file_name="总平图.pdf",
                    page_locator="page:3",
                    snippet="总平面布置与施工部署形成闭环。",
                    matched_requirement="总体部署",
                    dimension_id="01",
                    confidence=0.86,
                    model_version="gpt-5.4",
                    prompt_or_policy_version="boundary-test-v1",
                    extraction_time="2026-04-12T00:00:00+00:00",
                ),
                CandidateEvidenceRecord(
                    candidate_id="c-low-confidence",
                    source_ref="drawing:sheet-1",
                    artifact_type="drawing",
                    file_name="总平图.pdf",
                    page_locator="page:4",
                    snippet="低置信度候选。",
                    matched_requirement="总体部署",
                    dimension_id="01",
                    confidence=0.2,
                    model_version="gpt-5.4",
                    prompt_or_policy_version="boundary-test-v1",
                    extraction_time="2026-04-12T00:00:00+00:00",
                ),
                CandidateEvidenceRecord(
                    candidate_id="c-missing-locator",
                    source_ref="drawing:sheet-1",
                    artifact_type="drawing",
                    file_name="总平图.pdf",
                    page_locator="",
                    snippet="缺定位的候选。",
                    matched_requirement="总体部署",
                    dimension_id="01",
                    confidence=0.88,
                    model_version="gpt-5.4",
                    prompt_or_policy_version="boundary-test-v1",
                    extraction_time="2026-04-12T00:00:00+00:00",
                ),
                CandidateEvidenceRecord(
                    candidate_id="c-missing-snippet",
                    source_ref="drawing:sheet-1",
                    artifact_type="drawing",
                    file_name="总平图.pdf",
                    page_locator="page:5",
                    snippet="",
                    matched_requirement="总体部署",
                    dimension_id="01",
                    confidence=0.92,
                    model_version="gpt-5.4",
                    prompt_or_policy_version="boundary-test-v1",
                    extraction_time="2026-04-12T00:00:00+00:00",
                ),
            ],
        )

    def align_semantic_requirements(self, request):  # pragma: no cover - not used in this test
        raise NotImplementedError


def test_build_scoring_evidence_package_no_model_keeps_base_units_only():
    base_units = [
        {
            "id": "base-1",
            "submission_id": "s1",
            "doc_id": "submission:s1",
            "text": "基础规则证据",
            "heading_path": "第一章",
            "locator": "para:1",
            "dimension_primary": "01",
            "dimension_candidates": [{"dimension_id": "01", "confidence": 1.0}],
            "specificity_score": 0.8,
            "anchor_links": [],
            "created_at": "2026-04-12T00:00:00+00:00",
        }
    ]

    with patch("app.domain.evidence.pipeline.build_evidence_units", return_value=base_units):
        package = build_scoring_evidence_package(
            submission_id="s1",
            project_id="p1",
            text="测试施组文本",
            lexicon={},
            file_name="施组A.docx",
            model_service=NoModelService(reason="model_mode_off"),
        )

    assert package["base_units"] == base_units
    assert package["scoring_units"] == base_units
    assert package["candidate_evidence"] == []
    assert package["accepted_units"] == []
    assert package["summary"]["mode"] == "no-model"
    assert package["summary"]["available"] is False
    assert package["summary"]["fallback_reason"] == "model_mode_off"
    assert package["summary"]["base_unit_count"] == 1
    assert package["summary"]["accepted_count"] == 0


def test_build_scoring_evidence_package_accepts_only_validated_candidates():
    base_units = [
        {
            "id": "base-1",
            "submission_id": "s1",
            "doc_id": "submission:s1",
            "text": "基础规则证据",
            "heading_path": "第一章",
            "locator": "para:1",
            "dimension_primary": "01",
            "dimension_candidates": [{"dimension_id": "01", "confidence": 1.0}],
            "specificity_score": 0.8,
            "anchor_links": [],
            "created_at": "2026-04-12T00:00:00+00:00",
        }
    ]
    artifacts = [
        EvidenceSourceArtifact(
            source_ref="drawing:sheet-1",
            artifact_type="drawing",
            file_name="总平图.pdf",
            page_locator="page:3",
            content_excerpt="总平面布置与交通组织。",
        )
    ]

    with patch("app.domain.evidence.pipeline.build_evidence_units", return_value=base_units):
        package = build_scoring_evidence_package(
            submission_id="s1",
            project_id="p1",
            text="测试施组文本",
            lexicon={},
            anchors=[{"anchor_key": "施工部署"}],
            requirements=[{"req_label": "总体部署"}],
            artifacts=artifacts,
            file_name="施组A.docx",
            model_service=_StubModelService(),
        )

    assert len(package["scoring_units"]) == 2
    assert len(package["accepted_units"]) == 1
    assert package["summary"]["accepted_count"] == 1
    assert package["summary"]["rejected_count"] == 3
    assert package["summary"]["validator_breakdown"]["accepted"] == 1
    assert package["summary"]["validator_breakdown"]["rejected_low_confidence"] == 1
    assert package["summary"]["validator_breakdown"]["rejected_missing_locator"] == 1
    assert package["summary"]["validator_breakdown"]["rejected_missing_snippet"] == 1

    accepted_unit = package["accepted_units"][0]
    assert accepted_unit["source_mode"] == "model_candidate_validated"
    assert accepted_unit["source_ref"] == "drawing:sheet-1"
    assert accepted_unit["source_locator"] == "page:3"
    assert accepted_unit["validator_status"] == "accepted"
    assert accepted_unit["model_version"] == "gpt-5.4"

    rejected_statuses = {item["validator_status"] for item in package["rejected_candidates"]}
    assert "rejected_low_confidence" in rejected_statuses
    assert "rejected_missing_locator" in rejected_statuses
    assert "rejected_missing_snippet" in rejected_statuses


@patch("app.infrastructure.models.openai_boundary.call_openai_json")
@patch("app.infrastructure.models.openai_boundary.get_openai_api_key", return_value="test-key")
def test_openai_model_service_fills_required_metadata(
    mock_api_key,
    mock_call_openai_json,
):
    mock_call_openai_json.return_value = (
        True,
        {
            "candidates": [
                {
                    "candidate_id": "cand-1",
                    "source_ref": "submission:s1",
                    "artifact_type": "submission",
                    "file_name": "施组A.docx",
                    "page_locator": "page:2",
                    "snippet": "施工部署与工期节点已经明确。",
                    "matched_requirement": "总体部署",
                    "dimension_id": "01",
                    "confidence": 0.82,
                }
            ]
        },
        "",
    )

    service = OpenAIModelService(model_name="gpt-5.4")
    batch = service.extract_candidate_evidence(
        CandidateEvidenceRequest(
            project_id="p1",
            submission_id="s1",
            submission_text="测试施组文本",
            artifacts=[
                EvidenceSourceArtifact(
                    source_ref="submission:s1",
                    artifact_type="submission",
                    file_name="施组A.docx",
                    page_locator="inline:text",
                    content_excerpt="测试施组文本",
                )
            ],
        )
    )

    assert batch.available is True
    assert batch.provider == "openai"
    assert batch.mode == "openai"
    assert len(batch.candidates) == 1

    candidate = batch.candidates[0]
    assert candidate.validator_status == "pending_validation"
    assert candidate.model_version == "gpt-5.4"
    assert candidate.prompt_or_policy_version == PROMPT_POLICY_VERSION
    assert candidate.extraction_time
    assert candidate.source_ref == "submission:s1"
