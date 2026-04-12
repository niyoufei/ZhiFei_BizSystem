from __future__ import annotations

from collections import Counter
from typing import Any

from app.contracts.models import (
    CandidateEvidenceRecord,
    CandidateEvidenceRequest,
    EvidenceAssemblySummary,
    EvidenceSourceArtifact,
)
from app.engine.evidence_units import build_evidence_units
from app.infrastructure.models import build_default_model_service

MIN_ACCEPT_CONFIDENCE = 0.55
MODEL_POLICY_VERSION = "candidate-evidence-validator-v1"


def _hint_list(values: list[dict[str, Any]] | None, key: str) -> list[str]:
    rows = []
    for item in values or []:
        text = str(item.get(key) or "").strip()
        if text:
            rows.append(text)
    return rows


def _candidate_dimension_candidates(candidate: CandidateEvidenceRecord) -> list[dict[str, Any]]:
    dimension_id = str(candidate.dimension_id or "").strip() or "01"
    return [{"dimension_id": dimension_id, "confidence": round(float(candidate.confidence), 4)}]


def _candidate_to_evidence_unit(
    *,
    submission_id: str,
    candidate: CandidateEvidenceRecord,
) -> dict[str, Any]:
    return {
        "id": candidate.candidate_id,
        "submission_id": submission_id,
        "doc_id": candidate.source_ref,
        "text": candidate.snippet,
        "heading_path": "MODEL_ACCEPTED_EVIDENCE",
        "locator": candidate.page_locator,
        "dimension_primary": str(candidate.dimension_id or "01"),
        "dimension_candidates": _candidate_dimension_candidates(candidate),
        "specificity_score": round(float(candidate.confidence), 4),
        "anchor_links": [],
        "created_at": candidate.extraction_time,
        "source_mode": "model_candidate_validated",
        "source_ref": candidate.source_ref,
        "source_filename": candidate.file_name,
        "source_locator": candidate.page_locator,
        "confidence": round(float(candidate.confidence), 4),
        "model_version": candidate.model_version,
        "prompt_or_policy_version": candidate.prompt_or_policy_version,
        "extraction_time": candidate.extraction_time,
        "validator_status": candidate.validator_status,
        "validator_policy_version": MODEL_POLICY_VERSION,
        "matched_requirement": candidate.matched_requirement,
        "artifact_type": candidate.artifact_type,
    }


def _validate_candidates(
    *,
    candidates: list[CandidateEvidenceRecord],
    allowed_source_refs: set[str],
) -> tuple[list[CandidateEvidenceRecord], list[CandidateEvidenceRecord], dict[str, int]]:
    accepted: list[CandidateEvidenceRecord] = []
    rejected: list[CandidateEvidenceRecord] = []
    seen: set[tuple[str, str, str]] = set()
    counter: Counter[str] = Counter()
    for raw in candidates:
        row = raw.model_copy(deep=True)
        status = "accepted"
        reason = None
        snippet_key = str(row.snippet or "").strip()[:120]
        dedupe_key = (row.source_ref, row.page_locator, snippet_key)
        if not snippet_key:
            status = "rejected_missing_snippet"
            reason = "missing_snippet"
        elif not str(row.source_ref or "").strip():
            status = "rejected_missing_source_ref"
            reason = "missing_source_ref"
        elif not str(row.page_locator or "").strip():
            status = "rejected_missing_locator"
            reason = "missing_page_or_locator"
        elif not (0.0 <= float(row.confidence) <= 1.0):
            status = "rejected_invalid_confidence"
            reason = "invalid_confidence"
        elif float(row.confidence) < MIN_ACCEPT_CONFIDENCE:
            status = "rejected_low_confidence"
            reason = "low_confidence"
        elif (
            not str(row.model_version or "").strip()
            or not str(row.prompt_or_policy_version or "").strip()
            or not str(row.extraction_time or "").strip()
        ):
            status = "rejected_missing_model_metadata"
            reason = "missing_model_metadata"
        elif allowed_source_refs and row.source_ref not in allowed_source_refs:
            status = "rejected_unknown_source_ref"
            reason = "unknown_source_ref"
        elif dedupe_key in seen:
            status = "rejected_duplicate"
            reason = "duplicate_candidate"
        row.validator_status = status
        row.rejection_reason = reason
        counter[status] += 1
        if status == "accepted":
            seen.add(dedupe_key)
            accepted.append(row)
        else:
            rejected.append(row)
    return accepted, rejected, dict(counter)


def build_scoring_evidence_package(
    *,
    submission_id: str,
    project_id: str,
    text: str,
    lexicon: dict[str, Any],
    anchors: list[dict[str, Any]] | None = None,
    requirements: list[dict[str, Any]] | None = None,
    file_name: str = "inline",
    artifacts: list[EvidenceSourceArtifact] | None = None,
    model_service=None,
) -> dict[str, Any]:
    base_units = build_evidence_units(
        submission_id=submission_id,
        text=text,
        lexicon=lexicon,
        anchors=anchors or [],
    )
    service = model_service or build_default_model_service()
    runtime_artifacts = list(artifacts or [])
    if not runtime_artifacts:
        runtime_artifacts = [
            EvidenceSourceArtifact(
                source_ref=f"submission:{submission_id}",
                artifact_type="submission",
                file_name=file_name,
                page_locator="inline:text",
                content_excerpt=text[:8000],
            )
        ]
    request = CandidateEvidenceRequest(
        project_id=project_id,
        submission_id=submission_id,
        submission_text=text,
        artifacts=runtime_artifacts,
        requirement_hints=_hint_list(requirements, "req_label"),
        anchor_hints=_hint_list(anchors, "anchor_key"),
    )
    batch = service.extract_candidate_evidence(request)
    accepted, rejected, breakdown = _validate_candidates(
        candidates=list(batch.candidates or []),
        allowed_source_refs={artifact.source_ref for artifact in request.artifacts},
    )
    accepted_units = [
        _candidate_to_evidence_unit(submission_id=submission_id, candidate=item)
        for item in accepted
    ]
    summary = EvidenceAssemblySummary(
        mode=batch.mode,
        provider=batch.provider,
        available=batch.available,
        model_version=batch.model_version,
        prompt_or_policy_version=batch.prompt_or_policy_version,
        fallback_reason=batch.fallback_reason,
        base_unit_count=len(base_units),
        candidate_count=len(batch.candidates),
        accepted_count=len(accepted),
        rejected_count=len(rejected),
        validator_breakdown=breakdown,
    )
    return {
        "base_units": base_units,
        "candidate_evidence": [item.model_dump(mode="json") for item in batch.candidates],
        "accepted_candidates": [item.model_dump(mode="json") for item in accepted],
        "rejected_candidates": [item.model_dump(mode="json") for item in rejected],
        "accepted_units": accepted_units,
        "scoring_units": [*base_units, *accepted_units],
        "summary": summary.model_dump(mode="json"),
    }
