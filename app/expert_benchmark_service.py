from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.assessment_contract_service import verify_assessment_contract
from app.tender_criteria_catalog import (
    CATALOG_VERSION,
    PROJECT_TYPE_CATALOGS,
    combined_catalog_entries,
)

MANIFEST_SCHEMA_VERSION = "expert-benchmark-dataset-v2"
LABELS_SCHEMA_VERSION = "expert-benchmark-labels-v2"
PREDICTIONS_SCHEMA_VERSION = "expert-benchmark-predictions-v2"
REPORT_SCHEMA_VERSION = "expert-benchmark-report-v2"
COMMITMENT_SCHEMA_VERSION = "expert-benchmark-commitment-v1"
RELEASE_SCHEMA_VERSION = "expert-benchmark-release-v1"

DATASET_KIND_REAL = "real_expert_gold"
DATASET_KIND_SYNTHETIC = "synthetic_fixture"
SCORING_MODE = "approved_tender_profile_rule_only"

STATUS_PASS = "PASS"
STATUS_BLOCKED = "BLOCKED"
STATUS_NOT_CERTIFIED = "NOT_YET_CERTIFIED"
STATUS_ELIGIBLE = "ELIGIBLE_FOR_OFFICIAL_CERTIFICATION"

SUPPORTED_PROJECT_TYPES = frozenset(PROJECT_TYPE_CATALOGS)
SUPPORTED_REDLINE_FAMILIES = frozenset({"missing_core_section", "outdated_norm"})

# Production authority keys are deliberately code-pinned and empty until an
# independent expert-data custodian supplies a public Ed25519 key through a
# separately reviewed change.  Keys supplied by benchmark input files are never
# trusted.  This makes real-data PASS impossible in an unconfigured checkout.
TRUSTED_AUTHORITY_PUBLIC_KEYS: Dict[str, str] = {}

# These values are deliberately fixed in code.  A benchmark manifest must freeze
# the same values before prediction, so thresholds cannot be relaxed after labels
# are released.
CERTIFICATION_THRESHOLDS: Dict[str, float] = {
    "minimum_case_count": 25.0,
    "minimum_project_type_count": 5.0,
    "minimum_independent_reviews_per_case": 2.0,
    "minimum_prediction_runs": 3.0,
    "minimum_rank_group_size": 5.0,
    "determinism_rate_min": 1.0,
    "assessment_contract_valid_rate_min": 1.0,
    "total_score_mae_5pt_max": 0.25,
    "total_score_rmse_5pt_max": 0.35,
    "total_score_max_absolute_error_5pt_max": 0.75,
    "project_type_mae_5pt_max": 0.30,
    "ranking_group_mae_5pt_max": 0.30,
    "primary_item_score_ratio_mae_max": 0.10,
    "primary_item_band_accuracy_min": 0.90,
    "rank_spearman_min": 0.90,
    "rank_kendall_tau_b_min": 0.80,
    "project_type_exact_match_rate_min": 1.0,
    "secondary_catalog_f1_min": 0.92,
    "evidence_span_precision_min": 0.95,
    "evidence_span_recall_min": 0.90,
    "evidence_span_f1_min": 0.92,
    "evidence_span_core_recall_min": 0.90,
    "evidence_span_core_f1_min": 0.92,
    "evidence_span_core_case_recall_min": 0.80,
    "evidence_span_core_case_coverage_min": 1.0,
    "redline_precision_min": 1.0,
    "redline_recall_min": 1.0,
    "redline_specificity_min": 1.0,
    "severe_redline_misses_max": 0.0,
    "cross_project_leakage_max": 0.0,
    "training_overlap_max": 0.0,
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


class BenchmarkValidationError(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_sha256(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


def _validate_opaque_id(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be an opaque identifier")
    return normalized


def _parse_utc(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field_name} must use UTC")
    return parsed


class FileReference(StrictModel):
    relative_path: str
    sha256: str
    byte_length: int = Field(gt=0)
    character_length: int = Field(gt=0)
    encoding: Literal["utf-8"] = "utf-8"

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = str(value or "").strip()
        path = Path(normalized)
        if not normalized or path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must stay below the external data root")
        return normalized

    @field_validator("sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return _validate_sha256(value, "sha256")


class LabelProtocol(StrictModel):
    labels_hidden_during_scoring: bool
    experts_anonymized: bool
    independent_reviews: bool
    disagreement_adjudicated: bool


class BenchmarkCase(StrictModel):
    case_id: str
    project_key: str
    ranking_group_id: str
    document: FileReference
    scoring_context: FileReference

    @field_validator("case_id", "project_key", "ranking_group_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return _validate_opaque_id(value, info.field_name)


class BenchmarkManifest(StrictModel):
    schema_version: Literal[MANIFEST_SCHEMA_VERSION]
    dataset_id: str
    dataset_kind: Literal[DATASET_KIND_REAL, DATASET_KIND_SYNTHETIC]
    criteria_catalog_version: str
    expected_engine_source_sha256: str
    scoring_mode: Literal[SCORING_MODE]
    score_scale_canonical_max: Literal[5] = 5
    label_protocol: LabelProtocol
    thresholds: Dict[str, float]
    known_training_document_sha256: List[str] = Field(default_factory=list)
    cases: List[BenchmarkCase]

    @field_validator("dataset_id")
    @classmethod
    def validate_dataset_id(cls, value: str) -> str:
        return _validate_opaque_id(value, "dataset_id")

    @field_validator("known_training_document_sha256")
    @classmethod
    def validate_training_digests(cls, values: List[str]) -> List[str]:
        return [_validate_sha256(value, "known_training_document_sha256") for value in values]

    @field_validator("expected_engine_source_sha256")
    @classmethod
    def validate_engine_digest(cls, value: str) -> str:
        return _validate_sha256(value, "expected_engine_source_sha256")

    @model_validator(mode="after")
    def validate_case_identity(self) -> "BenchmarkManifest":
        if not self.cases:
            raise ValueError("cases must not be empty")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id values must be unique")
        return self


class SyntheticFixtureCommitment(StrictModel):
    schema_version: Literal[COMMITMENT_SCHEMA_VERSION]
    provenance: Literal[DATASET_KIND_SYNTHETIC]
    commitment_id: str
    dataset_id: str
    manifest_sha256: str
    gold_payload_sha256: str
    thresholds_sha256: str
    criteria_catalog_version: str
    expected_engine_source_sha256: str
    scoring_mode: Literal[SCORING_MODE]
    issued_at: str

    @field_validator("commitment_id", "dataset_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return _validate_opaque_id(value, info.field_name)

    @field_validator(
        "manifest_sha256",
        "gold_payload_sha256",
        "thresholds_sha256",
        "expected_engine_source_sha256",
    )
    @classmethod
    def validate_digests(cls, value: str, info) -> str:
        return _validate_sha256(value, info.field_name)

    @field_validator("issued_at")
    @classmethod
    def validate_issued_at(cls, value: str) -> str:
        _parse_utc(value, "issued_at")
        return value


class ExternalExpertGoldCommitment(SyntheticFixtureCommitment):
    provenance: Literal[DATASET_KIND_REAL]
    authority_key_id: str
    signature_ed25519: str

    @field_validator("authority_key_id")
    @classmethod
    def validate_authority_key_id(cls, value: str) -> str:
        return _validate_opaque_id(value, "authority_key_id")


class SyntheticFixtureRelease(StrictModel):
    schema_version: Literal[RELEASE_SCHEMA_VERSION]
    provenance: Literal[DATASET_KIND_SYNTHETIC]
    commitment_sha256: str
    prediction_artifact_sha256: List[str]
    independent_execution_verified: bool
    released_at: str

    @field_validator("commitment_sha256")
    @classmethod
    def validate_commitment_digest(cls, value: str) -> str:
        return _validate_sha256(value, "commitment_sha256")

    @field_validator("prediction_artifact_sha256")
    @classmethod
    def validate_prediction_digests(cls, values: List[str]) -> List[str]:
        normalized = [_validate_sha256(value, "prediction_artifact_sha256") for value in values]
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("prediction artifact digests must be non-empty and unique")
        return normalized

    @field_validator("released_at")
    @classmethod
    def validate_released_at(cls, value: str) -> str:
        _parse_utc(value, "released_at")
        return value


class ExternalExpertGoldRelease(SyntheticFixtureRelease):
    provenance: Literal[DATASET_KIND_REAL]
    authority_key_id: str
    signature_ed25519: str

    @field_validator("authority_key_id")
    @classmethod
    def validate_authority_key_id(cls, value: str) -> str:
        return _validate_opaque_id(value, "authority_key_id")


class ExpertDefinition(StrictModel):
    expert_id: str
    qualification_verified: bool
    independence_attested: bool
    role: Literal["independent", "adjudicator"]
    scene_tags: List[str] = Field(default_factory=list)

    @field_validator("expert_id")
    @classmethod
    def validate_expert_id(cls, value: str) -> str:
        return _validate_opaque_id(value, "expert_id")

    @field_validator("scene_tags")
    @classmethod
    def validate_scene_tags(cls, values: List[str]) -> List[str]:
        if any(value not in SUPPORTED_PROJECT_TYPES for value in values):
            raise ValueError("expert scene_tags contain an unsupported project type")
        if len(values) != len(set(values)):
            raise ValueError("expert scene_tags must be unique")
        return values


class EvidenceSpan(StrictModel):
    item_id: str
    requirement_id: str
    start_index: int = Field(ge=0)
    end_index: int = Field(gt=0)
    polarity: Literal["support", "contradict"] = "support"
    importance: Literal["core", "secondary"] = "core"

    @model_validator(mode="after")
    def validate_span(self) -> "EvidenceSpan":
        if self.end_index <= self.start_index:
            raise ValueError("evidence span end_index must be greater than start_index")
        return self


class RedlineLabel(StrictModel):
    redline_id: str
    family: str
    outcome: Literal["triggered", "not_triggered", "uncertain"]
    severity: Literal["disqualify", "zero", "warning"]


class FullCaseJudgment(StrictModel):
    total_score_5pt: float = Field(ge=0.0, le=5.0)
    project_types: List[str]
    primary_item_scores_ratio: Dict[str, float] = Field(default_factory=dict)
    primary_item_bands: Dict[str, str] = Field(default_factory=dict)
    secondary_catalog_ids: List[str] = Field(default_factory=list)
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
    redlines: List[RedlineLabel] = Field(default_factory=list)

    @field_validator("project_types")
    @classmethod
    def validate_project_types(cls, values: List[str]) -> List[str]:
        if not values or any(value not in SUPPORTED_PROJECT_TYPES for value in values):
            raise ValueError("project_types must use the certified construction catalog")
        if len(values) != len(set(values)):
            raise ValueError("project_types must be unique")
        return values

    @field_validator("primary_item_scores_ratio")
    @classmethod
    def validate_ratios(cls, values: Dict[str, float]) -> Dict[str, float]:
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in values.values()):
            raise ValueError("primary item score ratios must be finite values in [0, 1]")
        return values

    @model_validator(mode="after")
    def validate_natural_keys(self) -> "FullCaseJudgment":
        if (
            not self.primary_item_scores_ratio
            or set(self.primary_item_scores_ratio) != set(self.primary_item_bands)
        ):
            raise ValueError(
                "primary item ratio and band labels must be non-empty and use identical IDs"
            )
        if len(self.secondary_catalog_ids) != len(set(self.secondary_catalog_ids)):
            raise ValueError("secondary_catalog_ids must be unique")
        span_keys = [
            (
                row.item_id,
                row.requirement_id,
                row.start_index,
                row.end_index,
                row.polarity,
            )
            for row in self.evidence_spans
        ]
        if len(span_keys) != len(set(span_keys)):
            raise ValueError("evidence spans must be unique")
        redline_ids = [row.redline_id for row in self.redlines]
        if len(redline_ids) != len(set(redline_ids)):
            raise ValueError("redline_id values must be unique")
        return self


class ExpertReview(StrictModel):
    review_id: str
    expert_id: str
    label_origin: Literal["independent_human"]
    judgment: FullCaseJudgment
    reviewed_at: str

    @field_validator("review_id", "expert_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return _validate_opaque_id(value, info.field_name)

    @field_validator("reviewed_at")
    @classmethod
    def validate_reviewed_at(cls, value: str) -> str:
        _parse_utc(value, "reviewed_at")
        return value


class CaseResolution(StrictModel):
    resolution_id: str
    case_id: str
    method: Literal["expert_adjudication"]
    source_review_ids: List[str]
    adjudicator_id: str
    final_judgment: FullCaseJudgment
    resolved_at: str

    @field_validator("resolution_id", "case_id", "adjudicator_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return _validate_opaque_id(value, info.field_name)

    @field_validator("source_review_ids")
    @classmethod
    def validate_source_review_ids(cls, values: List[str]) -> List[str]:
        normalized = [_validate_opaque_id(value, "source_review_ids") for value in values]
        if len(normalized) < 2 or len(normalized) != len(set(normalized)):
            raise ValueError("source_review_ids must contain at least two unique reviews")
        return normalized

    @field_validator("resolved_at")
    @classmethod
    def validate_resolved_at(cls, value: str) -> str:
        _parse_utc(value, "resolved_at")
        return value


class CaseLabels(StrictModel):
    case_id: str
    expert_reviews: List[ExpertReview]
    resolution: CaseResolution

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        return _validate_opaque_id(value, "case_id")

    @model_validator(mode="after")
    def validate_review_bindings(self) -> "CaseLabels":
        review_ids = [review.review_id for review in self.expert_reviews]
        reviewer_ids = [review.expert_id for review in self.expert_reviews]
        if len(review_ids) < 2 or len(review_ids) != len(set(review_ids)):
            raise ValueError("each case requires at least two uniquely identified reviews")
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("each case requires distinct independent experts")
        if self.resolution.case_id != self.case_id:
            raise ValueError("resolution case_id does not match case labels")
        if set(self.resolution.source_review_ids) != set(review_ids):
            raise ValueError("resolution source_review_ids must bind every case review")
        return self


class ExpertLabels(StrictModel):
    schema_version: Literal[LABELS_SCHEMA_VERSION]
    dataset_id: str
    dataset_kind: Literal[DATASET_KIND_REAL, DATASET_KIND_SYNTHETIC]
    experts: List[ExpertDefinition]
    cases: List[CaseLabels]
    gold_seal_sha256: str

    @field_validator("gold_seal_sha256")
    @classmethod
    def validate_gold_seal(cls, value: str) -> str:
        return _validate_sha256(value, "gold_seal_sha256")

    @model_validator(mode="after")
    def validate_unique_identity(self) -> "ExpertLabels":
        expert_ids = [expert.expert_id for expert in self.experts]
        case_ids = [case.case_id for case in self.cases]
        review_ids = [
            review.review_id for case in self.cases for review in case.expert_reviews
        ]
        resolution_ids = [case.resolution.resolution_id for case in self.cases]
        if len(expert_ids) != len(set(expert_ids)):
            raise ValueError("expert_id values must be unique")
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("label case_id values must be unique")
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("review_id values must be globally unique")
        if len(resolution_ids) != len(set(resolution_ids)):
            raise ValueError("resolution_id values must be globally unique")
        return self


class PrimaryItemPrediction(StrictModel):
    item_id: str
    score_ratio: float = Field(ge=0.0, le=1.0)
    band: str = ""


class EvidenceSpanPrediction(StrictModel):
    item_id: str
    requirement_id: str
    start_index: int = Field(ge=0)
    end_index: int = Field(gt=0)
    polarity: Literal["support", "contradict"] = "support"
    importance: Literal["core", "secondary"] = "core"

    @model_validator(mode="after")
    def validate_span(self) -> "EvidenceSpanPrediction":
        if self.end_index <= self.start_index:
            raise ValueError("prediction span end_index must be greater than start_index")
        return self


class RedlinePrediction(StrictModel):
    redline_id: str
    family: Literal["missing_core_section", "outdated_norm"]
    severity: Literal["disqualify", "zero", "warning"]


class CasePrediction(StrictModel):
    case_id: str
    project_key: str
    ranking_group_id: str
    document_sha256: str
    scoring_context_sha256: str
    total_score_5pt: float = Field(ge=0.0, le=5.0)
    project_types: List[str]
    primary_items: List[PrimaryItemPrediction]
    secondary_catalog_ids: List[str]
    evidence_spans: List[EvidenceSpanPrediction]
    redline_predictions: List[RedlinePrediction]
    uncovered_tender_redline_ids: List[str]
    assessment_contract_hash: str
    assessment_contract: Dict[str, object]
    semantic_fingerprint: str

    @field_validator("document_sha256", "scoring_context_sha256", "assessment_contract_hash", "semantic_fingerprint")
    @classmethod
    def validate_digests(cls, value: str, info) -> str:
        return _validate_sha256(value, info.field_name)

    @field_validator("case_id", "project_key", "ranking_group_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return _validate_opaque_id(value, info.field_name)

    @model_validator(mode="after")
    def validate_prediction_keys(self) -> "CasePrediction":
        if not self.project_types or len(self.project_types) != len(set(self.project_types)):
            raise ValueError("prediction project_types must be non-empty and unique")
        if any(value not in SUPPORTED_PROJECT_TYPES for value in self.project_types):
            raise ValueError("prediction contains unsupported project_types")
        item_ids = [item.item_id for item in self.primary_items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("prediction primary item_id values must be unique")
        if len(self.secondary_catalog_ids) != len(set(self.secondary_catalog_ids)):
            raise ValueError("prediction secondary_catalog_ids must be unique")
        span_keys = [
            (row.item_id, row.requirement_id, row.start_index, row.end_index, row.polarity)
            for row in self.evidence_spans
        ]
        if len(span_keys) != len(set(span_keys)):
            raise ValueError("prediction evidence spans must be unique")
        redline_ids = [row.redline_id for row in self.redline_predictions]
        if len(redline_ids) != len(set(redline_ids)):
            raise ValueError("prediction redline_id values must be unique")
        return self


class PredictionArtifact(StrictModel):
    schema_version: Literal[PREDICTIONS_SCHEMA_VERSION]
    dataset_id: str
    dataset_kind: Literal[DATASET_KIND_REAL, DATASET_KIND_SYNTHETIC]
    run_id: str
    scoring_mode: Literal[SCORING_MODE]
    criteria_catalog_version: str
    cases_sha256: str
    engine_source_sha256: str
    semantic_output_sha256: str
    execution_evidence_sha256: str
    execution_nonce: str
    execution_started_at: str
    generated_at: str
    cases: List[CasePrediction]

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _validate_opaque_id(value, "run_id")

    @field_validator(
        "cases_sha256",
        "engine_source_sha256",
        "semantic_output_sha256",
        "execution_evidence_sha256",
    )
    @classmethod
    def validate_digests(cls, value: str, info) -> str:
        return _validate_sha256(value, info.field_name)

    @field_validator("execution_nonce")
    @classmethod
    def validate_execution_nonce(cls, value: str) -> str:
        return _validate_opaque_id(value, "execution_nonce")

    @field_validator("execution_started_at", "generated_at")
    @classmethod
    def validate_timestamps(cls, value: str, info) -> str:
        _parse_utc(value, info.field_name)
        return value


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def parse_commitment(
    payload: Mapping[str, object],
) -> SyntheticFixtureCommitment | ExternalExpertGoldCommitment:
    try:
        if payload.get("provenance") == DATASET_KIND_REAL:
            return ExternalExpertGoldCommitment.model_validate(payload)
        return SyntheticFixtureCommitment.model_validate(payload)
    except Exception as exc:
        raise BenchmarkValidationError(str(exc)) from exc


def parse_release(
    payload: Mapping[str, object],
) -> SyntheticFixtureRelease | ExternalExpertGoldRelease:
    try:
        if payload.get("provenance") == DATASET_KIND_REAL:
            return ExternalExpertGoldRelease.model_validate(payload)
        return SyntheticFixtureRelease.model_validate(payload)
    except Exception as exc:
        raise BenchmarkValidationError(str(exc)) from exc


def _signed_payload(model: StrictModel) -> Dict[str, object]:
    payload = model.model_dump(mode="json")
    payload.pop("signature_ed25519", None)
    return payload


def commitment_sha256(
    commitment: SyntheticFixtureCommitment | ExternalExpertGoldCommitment,
) -> str:
    return canonical_sha256(commitment.model_dump(mode="json"))


def thresholds_sha256() -> str:
    return canonical_sha256(CERTIFICATION_THRESHOLDS)


def _verify_ed25519(*, key_id: str, signature_b64: str, payload: object) -> bool:
    encoded_key = TRUSTED_AUTHORITY_PUBLIC_KEYS.get(key_id)
    if not encoded_key:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded_key))
        public_key.verify(base64.b64decode(signature_b64), canonical_bytes(payload))
    except Exception:
        return False
    return True


def commitment_is_trusted(
    commitment: SyntheticFixtureCommitment | ExternalExpertGoldCommitment,
) -> bool:
    if isinstance(commitment, SyntheticFixtureCommitment) and not isinstance(
        commitment, ExternalExpertGoldCommitment
    ):
        return False
    assert isinstance(commitment, ExternalExpertGoldCommitment)
    return _verify_ed25519(
        key_id=commitment.authority_key_id,
        signature_b64=commitment.signature_ed25519,
        payload=_signed_payload(commitment),
    )


def release_is_trusted(
    release: SyntheticFixtureRelease | ExternalExpertGoldRelease,
) -> bool:
    if isinstance(release, SyntheticFixtureRelease) and not isinstance(
        release, ExternalExpertGoldRelease
    ):
        return False
    assert isinstance(release, ExternalExpertGoldRelease)
    return _verify_ed25519(
        key_id=release.authority_key_id,
        signature_b64=release.signature_ed25519,
        payload=_signed_payload(release),
    )


def manifest_sha256(manifest: BenchmarkManifest) -> str:
    return canonical_sha256(manifest.model_dump(mode="json"))


def labels_gold_seal_payload(labels: ExpertLabels | Mapping[str, object]) -> Dict[str, object]:
    payload = labels.model_dump(mode="json") if isinstance(labels, ExpertLabels) else dict(labels)
    return {
        "schema_version": payload.get("schema_version"),
        "dataset_id": payload.get("dataset_id"),
        "dataset_kind": payload.get("dataset_kind"),
        "experts": payload.get("experts"),
        "cases": payload.get("cases"),
    }


def compute_gold_seal(labels: ExpertLabels | Mapping[str, object]) -> str:
    return canonical_sha256(labels_gold_seal_payload(labels))


def prediction_semantic_payload(case: CasePrediction | Mapping[str, object]) -> Dict[str, object]:
    payload = case.model_dump(mode="json") if isinstance(case, CasePrediction) else dict(case)
    return {
        "case_id": payload.get("case_id"),
        "project_key": payload.get("project_key"),
        "ranking_group_id": payload.get("ranking_group_id"),
        "document_sha256": payload.get("document_sha256"),
        "scoring_context_sha256": payload.get("scoring_context_sha256"),
        "total_score_5pt": payload.get("total_score_5pt"),
        "project_types": sorted(payload.get("project_types") or []),
        "primary_items": sorted(
            payload.get("primary_items") or [], key=lambda item: str(item.get("item_id") or "")
        ),
        "secondary_catalog_ids": sorted(payload.get("secondary_catalog_ids") or []),
        "evidence_spans": sorted(
            payload.get("evidence_spans") or [],
            key=lambda item: (
                str(item.get("item_id") or ""),
                str(item.get("requirement_id") or ""),
                int(item.get("start_index") or 0),
                int(item.get("end_index") or 0),
            ),
        ),
        "redline_predictions": sorted(
            payload.get("redline_predictions") or [],
            key=lambda item: str(item.get("redline_id") or ""),
        ),
        "uncovered_tender_redline_ids": sorted(
            payload.get("uncovered_tender_redline_ids") or []
        ),
        "assessment_contract_hash": payload.get("assessment_contract_hash"),
    }


def prediction_case_fingerprint(case: CasePrediction | Mapping[str, object]) -> str:
    return canonical_sha256(prediction_semantic_payload(case))


def prediction_output_fingerprint(cases: Iterable[CasePrediction | Mapping[str, object]]) -> str:
    payloads = [prediction_semantic_payload(case) for case in cases]
    payloads.sort(key=lambda item: str(item.get("case_id") or ""))
    return canonical_sha256(payloads)


def prediction_artifact_sha256(artifact: PredictionArtifact | Mapping[str, object]) -> str:
    payload = artifact.model_dump(mode="json") if isinstance(artifact, PredictionArtifact) else dict(artifact)
    return canonical_sha256(payload)


def compute_execution_evidence_sha256(
    *,
    run_id: str,
    execution_nonce: str,
    execution_started_at: str,
    generated_at: str,
    manifest_digest: str,
    commitment_digest: str,
    engine_source_sha256: str,
    semantic_output_sha256: str,
) -> str:
    return canonical_sha256(
        {
            "run_id": run_id,
            "execution_nonce": execution_nonce,
            "execution_started_at": execution_started_at,
            "generated_at": generated_at,
            "manifest_sha256": manifest_digest,
            "commitment_sha256": commitment_digest,
            "engine_source_sha256": engine_source_sha256,
            "semantic_output_sha256": semantic_output_sha256,
        }
    )


def _average_ranks(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index
        while end + 1 < len(indexed) and indexed[end + 1][1] == indexed[index][1]:
            end += 1
        rank = (index + end + 2) / 2.0
        for cursor in range(index, end + 1):
            ranks[indexed[cursor][0]] = rank
        index = end + 1
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_var = sum((value - left_mean) ** 2 for value in left)
    right_var = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_var * right_var)
    if denominator <= 1e-12:
        return None
    return numerator / denominator


def spearman(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    if len(left) != len(right) or len(left) < 2:
        return None
    return _pearson(_average_ranks(left), _average_ranks(right))


def kendall_tau_b(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    if len(left) != len(right) or len(left) < 2:
        return None
    concordant = discordant = ties_left = ties_right = 0
    for first in range(len(left) - 1):
        for second in range(first + 1, len(left)):
            delta_left = left[first] - left[second]
            delta_right = right[first] - right[second]
            if delta_left == 0 and delta_right == 0:
                continue
            if delta_left == 0:
                ties_left += 1
            elif delta_right == 0:
                ties_right += 1
            elif delta_left * delta_right > 0:
                concordant += 1
            else:
                discordant += 1
    denominator = math.sqrt(
        (concordant + discordant + ties_left)
        * (concordant + discordant + ties_right)
    )
    if denominator <= 1e-12:
        return None
    return (concordant - discordant) / denominator


def _prf(tp: int, fp: int, fn: int) -> Dict[str, Optional[float]]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = None
    if precision is not None and recall is not None and precision + recall > 0:
        f1 = 2 * precision * recall / (precision + recall)
    elif precision == 0 or recall == 0:
        f1 = 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def _span_match_counts(
    gold_spans: Sequence[EvidenceSpan],
    predicted_spans: Sequence[EvidenceSpanPrediction],
) -> tuple[int, int, int]:
    gold_by_key: Dict[tuple[str, str, str], List[EvidenceSpan]] = defaultdict(list)
    predicted_by_key: Dict[tuple[str, str, str], List[EvidenceSpanPrediction]] = defaultdict(list)
    for span in gold_spans:
        gold_by_key[(span.item_id, span.requirement_id, span.polarity)].append(span)
    for span in predicted_spans:
        predicted_by_key[(span.item_id, span.requirement_id, span.polarity)].append(span)

    tp = 0
    for key in set(gold_by_key) | set(predicted_by_key):
        gold_rows = gold_by_key.get(key, [])
        predicted_rows = predicted_by_key.get(key, [])
        adjacency: Dict[int, List[tuple[float, int]]] = defaultdict(list)
        for pred_index, predicted in enumerate(predicted_rows):
            for gold_index, gold in enumerate(gold_rows):
                overlap = max(
                    0,
                    min(gold.end_index, predicted.end_index)
                    - max(gold.start_index, predicted.start_index),
                )
                gold_length = gold.end_index - gold.start_index
                predicted_length = predicted.end_index - predicted.start_index
                gold_coverage = overlap / gold_length if gold_length > 0 else 0.0
                predicted_coverage = overlap / predicted_length if predicted_length > 0 else 0.0
                if gold_coverage >= 0.5 and predicted_coverage >= 0.5:
                    union = gold_length + predicted_length - overlap
                    iou = overlap / union if union > 0 else 0.0
                    adjacency[pred_index].append((iou, gold_index))

        matched_gold_to_prediction: Dict[int, int] = {}

        def augment(pred_index: int, visited_gold: set[int]) -> bool:
            for _, gold_index in sorted(adjacency.get(pred_index, []), reverse=True):
                if gold_index in visited_gold:
                    continue
                visited_gold.add(gold_index)
                previous = matched_gold_to_prediction.get(gold_index)
                if previous is None or augment(previous, visited_gold):
                    matched_gold_to_prediction[gold_index] = pred_index
                    return True
            return False

        tp += sum(
            1 for pred_index in range(len(predicted_rows)) if augment(pred_index, set())
        )
    gold_total = sum(len(rows) for rows in gold_by_key.values())
    predicted_total = sum(len(rows) for rows in predicted_by_key.values())
    return tp, predicted_total - tp, gold_total - tp


def _gate(
    name: str,
    *,
    value: object,
    threshold: object,
    comparator: str,
    passed: Optional[bool],
) -> Dict[str, object]:
    return {
        "name": name,
        "value": value,
        "threshold": threshold,
        "comparator": comparator,
        "status": "PASS" if passed is True else ("FAIL" if passed is False else "NOT_EVALUABLE"),
    }


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _round_optional(value: Optional[float], digits: int = 6) -> Optional[float]:
    return round(value, digits) if value is not None else None


def _allowed_catalog_ids(project_types: Iterable[str]) -> set[str]:
    return {entry["catalog_id"] for entry in combined_catalog_entries(project_types)}


def _judgment_catalog_valid(judgment: FullCaseJudgment) -> bool:
    return set(judgment.secondary_catalog_ids) <= _allowed_catalog_ids(
        judgment.project_types
    )


def _prediction_catalog_valid(prediction: CasePrediction) -> bool:
    return set(prediction.secondary_catalog_ids) <= _allowed_catalog_ids(
        prediction.project_types
    )


def _assert_aggregate_report_privacy(
    report: Mapping[str, object],
    *,
    forbidden_values: Iterable[str],
) -> None:
    forbidden_keys = {
        "case_id",
        "project_key",
        "expert_id",
        "review_id",
        "resolution_id",
        "document_text",
        "source_text",
        "document_sha256",
        "scoring_context_sha256",
    }
    blocked_values = {value for value in forbidden_values if value}

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key) in forbidden_keys:
                    raise BenchmarkValidationError(
                        f"aggregate report contains forbidden key: {key}"
                    )
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str) and value in blocked_values:
            raise BenchmarkValidationError(
                "aggregate report contains a case-level or expert identifier"
            )

    walk(dict(report))


def _contract_context_mismatch(
    *,
    prediction: CasePrediction,
    case: BenchmarkCase,
    manifest: BenchmarkManifest,
    artifact: PredictionArtifact,
) -> bool:
    contract = prediction.assessment_contract
    if not verify_assessment_contract(contract):
        return True
    if prediction.assessment_contract_hash != contract.get("contract_hash"):
        return True
    inputs = contract.get("inputs") if isinstance(contract.get("inputs"), dict) else {}
    benchmark = inputs.get("benchmark") if isinstance(inputs.get("benchmark"), dict) else {}
    project = inputs.get("project") if isinstance(inputs.get("project"), dict) else {}
    contract_project_types = benchmark.get("project_types")
    contract_project_types = (
        contract_project_types if isinstance(contract_project_types, list) else []
    )
    contract_project_types_valid = all(
        isinstance(value, str) for value in contract_project_types
    )
    normalized_contract_project_types = [str(value) for value in contract_project_types]
    contract_catalog_ids = benchmark.get("secondary_catalog_ids")
    contract_catalog_ids = (
        contract_catalog_ids if isinstance(contract_catalog_ids, list) else []
    )
    contract_catalog_ids_valid = all(
        isinstance(value, str) for value in contract_catalog_ids
    )
    normalized_contract_catalog_ids = [str(value) for value in contract_catalog_ids]
    checks = (
        contract.get("status") == "certified",
        prediction.case_id == case.case_id,
        prediction.project_key == case.project_key,
        prediction.ranking_group_id == case.ranking_group_id,
        prediction.document_sha256 == case.document.sha256,
        prediction.scoring_context_sha256 == case.scoring_context.sha256,
        benchmark.get("dataset_id") == manifest.dataset_id,
        benchmark.get("case_id") == case.case_id,
        benchmark.get("ranking_group_id") == case.ranking_group_id,
        benchmark.get("document_sha256") == case.document.sha256,
        benchmark.get("scoring_context_sha256") == case.scoring_context.sha256,
        benchmark.get("criteria_catalog_version") == manifest.criteria_catalog_version,
        benchmark.get("engine_source_sha256") == manifest.expected_engine_source_sha256,
        benchmark.get("engine_source_sha256") == artifact.engine_source_sha256,
        benchmark.get("scoring_mode") == manifest.scoring_mode,
        benchmark.get("scoring_mode") == artifact.scoring_mode,
        contract_project_types_valid,
        len(normalized_contract_project_types)
        == len(set(normalized_contract_project_types)),
        set(normalized_contract_project_types) == set(prediction.project_types),
        contract_catalog_ids_valid,
        len(normalized_contract_catalog_ids) == len(set(normalized_contract_catalog_ids)),
        set(normalized_contract_catalog_ids) == set(prediction.secondary_catalog_ids),
        project.get("id") == case.project_key,
    )
    return not all(checks)


def evaluate_expert_benchmark(
    *,
    manifest_payload: Mapping[str, object],
    commitment_payload: Mapping[str, object],
    labels_payload: Mapping[str, object],
    release_payload: Mapping[str, object],
    prediction_payloads: Sequence[Mapping[str, object]],
    generated_at: Optional[str] = None,
) -> Dict[str, object]:
    try:
        manifest = BenchmarkManifest.model_validate(manifest_payload)
        commitment = parse_commitment(commitment_payload)
        labels = ExpertLabels.model_validate(labels_payload)
        release = parse_release(release_payload)
        predictions = [PredictionArtifact.model_validate(payload) for payload in prediction_payloads]
    except Exception as exc:
        raise BenchmarkValidationError(str(exc)) from exc

    provenance = commitment.provenance
    if provenance == DATASET_KIND_REAL and not commitment_is_trusted(commitment):
        raise BenchmarkValidationError("external expert commitment is not signed by a trusted authority")
    if provenance == DATASET_KIND_REAL and not release_is_trusted(release):
        raise BenchmarkValidationError("expert label release is not signed by a trusted authority")
    if provenance == DATASET_KIND_REAL and (
        commitment.authority_key_id != release.authority_key_id
    ):
        raise BenchmarkValidationError(
            "commitment and release must use the same trusted authority key"
        )
    if release.provenance != provenance:
        raise BenchmarkValidationError("release provenance does not match commitment")
    if labels.dataset_id != manifest.dataset_id or labels.dataset_kind != manifest.dataset_kind:
        raise BenchmarkValidationError("labels do not belong to the benchmark manifest")
    if commitment.dataset_id != manifest.dataset_id or provenance != manifest.dataset_kind:
        raise BenchmarkValidationError("commitment does not belong to the benchmark manifest")
    if not predictions:
        raise BenchmarkValidationError("at least one prediction artifact is required")
    run_ids = [artifact.run_id for artifact in predictions]
    if len(run_ids) != len(set(run_ids)):
        raise BenchmarkValidationError("prediction run_id values must be unique")
    execution_nonces = [artifact.execution_nonce for artifact in predictions]
    if len(execution_nonces) != len(set(execution_nonces)):
        raise BenchmarkValidationError("prediction execution_nonce values must be unique")
    execution_digests = [artifact.execution_evidence_sha256 for artifact in predictions]
    if len(execution_digests) != len(set(execution_digests)):
        raise BenchmarkValidationError("prediction executions must carry unique evidence digests")

    manifest_digest = manifest_sha256(manifest)
    frozen_commitment_sha256 = commitment_sha256(commitment)
    if commitment.manifest_sha256 != manifest_digest:
        raise BenchmarkValidationError("commitment manifest digest mismatch")
    if commitment.thresholds_sha256 != thresholds_sha256():
        raise BenchmarkValidationError("commitment threshold digest mismatch")
    if commitment.criteria_catalog_version != manifest.criteria_catalog_version:
        raise BenchmarkValidationError("commitment criteria catalog version mismatch")
    if commitment.expected_engine_source_sha256 != manifest.expected_engine_source_sha256:
        raise BenchmarkValidationError("commitment engine source digest mismatch")
    if commitment.scoring_mode != manifest.scoring_mode:
        raise BenchmarkValidationError("commitment scoring mode mismatch")
    if labels.gold_seal_sha256 != compute_gold_seal(labels):
        raise BenchmarkValidationError("expert gold payload seal is invalid")
    if labels.gold_seal_sha256 != commitment.gold_payload_sha256:
        raise BenchmarkValidationError("expert gold payload does not match the pre-score commitment")

    manifest_case_ids = {case.case_id for case in manifest.cases}
    label_case_ids = {case.case_id for case in labels.cases}
    if label_case_ids != manifest_case_ids:
        raise BenchmarkValidationError("label case set does not match manifest case set")
    for artifact in predictions:
        if artifact.dataset_id != manifest.dataset_id or artifact.dataset_kind != manifest.dataset_kind:
            raise BenchmarkValidationError("prediction artifact does not belong to the manifest")
        if artifact.criteria_catalog_version != manifest.criteria_catalog_version:
            raise BenchmarkValidationError("prediction criteria catalog version mismatch")
        if artifact.engine_source_sha256 != manifest.expected_engine_source_sha256:
            raise BenchmarkValidationError("prediction engine source digest mismatch")
        if artifact.scoring_mode != manifest.scoring_mode:
            raise BenchmarkValidationError("prediction scoring mode mismatch")
        if artifact.cases_sha256 != manifest_digest:
            raise BenchmarkValidationError("prediction artifact manifest digest mismatch")
        expected_execution_evidence = compute_execution_evidence_sha256(
            run_id=artifact.run_id,
            execution_nonce=artifact.execution_nonce,
            execution_started_at=artifact.execution_started_at,
            generated_at=artifact.generated_at,
            manifest_digest=manifest_digest,
            commitment_digest=frozen_commitment_sha256,
            engine_source_sha256=artifact.engine_source_sha256,
            semantic_output_sha256=artifact.semantic_output_sha256,
        )
        if artifact.execution_evidence_sha256 != expected_execution_evidence:
            raise BenchmarkValidationError("prediction execution evidence digest mismatch")
        artifact_case_ids = [case.case_id for case in artifact.cases]
        if (
            len(artifact_case_ids) != len(set(artifact_case_ids))
            or set(artifact_case_ids) != manifest_case_ids
        ):
            raise BenchmarkValidationError("prediction case set does not match manifest case set")

    artifact_digests = [prediction_artifact_sha256(artifact) for artifact in predictions]
    release_digests = release.prediction_artifact_sha256
    release_attested = (
        release.commitment_sha256 == frozen_commitment_sha256
        and sorted(release_digests) == sorted(artifact_digests)
        and release.independent_execution_verified
    )
    commitment_time = _parse_utc(commitment.issued_at, "issued_at")
    release_time = _parse_utc(release.released_at, "released_at")
    effective_generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    evaluation_time = _parse_utc(effective_generated_at, "generated_at")
    if release_time <= commitment_time:
        raise BenchmarkValidationError("release must be later than commitment")
    if release_time > evaluation_time:
        raise BenchmarkValidationError("release cannot be later than evaluation time")
    for artifact in predictions:
        started = _parse_utc(artifact.execution_started_at, "execution_started_at")
        frozen = _parse_utc(artifact.generated_at, "generated_at")
        if not commitment_time < started <= frozen < release_time:
            raise BenchmarkValidationError(
                "prediction execution timestamps must fall between commitment and release"
            )

    manifest_by_case = {case.case_id: case for case in manifest.cases}
    labels_by_case = {case.case_id: case for case in labels.cases}
    predictions_by_run = [
        {case.case_id: case for case in artifact.cases} for artifact in predictions
    ]
    primary_predictions = predictions_by_run[0]

    semantic_artifact_valid = True
    contract_valid_count = 0
    contract_total = 0
    cross_project_leakage = 0
    deterministic_case_count = 0
    for run_index, artifact in enumerate(predictions):
        if prediction_output_fingerprint(artifact.cases) != artifact.semantic_output_sha256:
            semantic_artifact_valid = False
        for case_id, prediction in predictions_by_run[run_index].items():
            contract_total += 1
            if prediction_case_fingerprint(prediction) != prediction.semantic_fingerprint:
                semantic_artifact_valid = False
            contract_valid = verify_assessment_contract(prediction.assessment_contract)
            if contract_valid:
                contract_valid_count += 1
            if _contract_context_mismatch(
                prediction=prediction,
                case=manifest_by_case[case_id],
                manifest=manifest,
                artifact=artifact,
            ):
                cross_project_leakage += 1

    for case_id in manifest_case_ids:
        semantic_hashes = {
            predictions_by_run[run_index][case_id].semantic_fingerprint
            for run_index in range(len(predictions_by_run))
        }
        contract_hashes = {
            predictions_by_run[run_index][case_id].assessment_contract_hash
            for run_index in range(len(predictions_by_run))
        }
        if len(semantic_hashes) == 1 and len(contract_hashes) == 1:
            deterministic_case_count += 1

    case_count = len(manifest.cases)
    determinism_rate = deterministic_case_count / case_count if case_count else None
    contract_valid_rate = contract_valid_count / contract_total if contract_total else None

    total_gold: List[float] = []
    total_predicted: List[float] = []
    project_type_exact = 0
    project_types_covered: set[str] = set()
    secondary_tp = secondary_fp = secondary_fn = 0
    evidence_tp = evidence_fp = evidence_fn = 0
    evidence_core_tp = evidence_core_fp = evidence_core_fn = 0
    core_evidence_case_count = 0
    core_case_recalls: List[float] = []
    primary_item_errors: List[float] = []
    primary_band_total = primary_band_hit = 0
    primary_item_scope_ok = True
    severe_redline_misses = 0
    severe_redline_positive_cases = 0
    uncovered_severe_redline_count = 0
    redline_tp = redline_fp = redline_fn = redline_tn = 0
    redline_family_positive: set[str] = set()
    redline_family_negative: set[str] = set()
    catalog_scope_ok = True
    span_bounds_ok = True
    project_type_errors: Dict[str, List[float]] = defaultdict(list)

    independent_review_min = math.inf
    expert_quality_ok = True
    adjudication_ok = True
    expert_definitions = {expert.expert_id: expert for expert in labels.experts}

    rank_groups: Dict[str, List[str]] = defaultdict(list)
    for manifest_case in manifest.cases:
        rank_groups[manifest_case.ranking_group_id].append(manifest_case.case_id)
        case_label = labels_by_case[manifest_case.case_id]
        predicted = primary_predictions[manifest_case.case_id]
        gold = case_label.resolution.final_judgment

        independent_ids: set[str] = set()
        for review in case_label.expert_reviews:
            expert = expert_definitions.get(review.expert_id)
            if (
                expert is None
                or expert.role != "independent"
                or not expert.qualification_verified
                or not expert.independence_attested
                or not set(review.judgment.project_types) <= set(expert.scene_tags)
                or not set(gold.project_types) <= set(expert.scene_tags)
                or not _judgment_catalog_valid(review.judgment)
            ):
                expert_quality_ok = False
                continue
            independent_ids.add(review.expert_id)
        independent_review_min = min(independent_review_min, len(independent_ids))
        resolution = case_label.resolution
        adjudicator = expert_definitions.get(resolution.adjudicator_id)
        latest_review_time = max(
            _parse_utc(review.reviewed_at, "reviewed_at")
            for review in case_label.expert_reviews
        )
        resolution_time = _parse_utc(resolution.resolved_at, "resolved_at")
        if (
            adjudicator is None
            or adjudicator.role != "adjudicator"
            or not adjudicator.qualification_verified
            or not adjudicator.independence_attested
            or adjudicator.expert_id in independent_ids
            or not set(gold.project_types) <= set(adjudicator.scene_tags)
            or latest_review_time > resolution_time
            or resolution_time > commitment_time
        ):
            adjudication_ok = False

        if not _judgment_catalog_valid(gold) or not _prediction_catalog_valid(predicted):
            catalog_scope_ok = False
        document_length = manifest_case.document.character_length
        if any(row.end_index > document_length for row in gold.evidence_spans) or any(
            row.end_index > document_length for row in predicted.evidence_spans
        ):
            span_bounds_ok = False

        total_gold.append(gold.total_score_5pt)
        total_predicted.append(predicted.total_score_5pt)
        case_error = abs(gold.total_score_5pt - predicted.total_score_5pt)
        for project_type in gold.project_types:
            project_type_errors[project_type].append(case_error)
        project_types_covered.update(gold.project_types)
        if set(gold.project_types) == set(predicted.project_types):
            project_type_exact += 1

        gold_secondary = set(gold.secondary_catalog_ids)
        predicted_secondary = set(predicted.secondary_catalog_ids)
        secondary_tp += len(gold_secondary & predicted_secondary)
        secondary_fp += len(predicted_secondary - gold_secondary)
        secondary_fn += len(gold_secondary - predicted_secondary)

        span_counts = _span_match_counts(gold.evidence_spans, predicted.evidence_spans)
        evidence_tp += span_counts[0]
        evidence_fp += span_counts[1]
        evidence_fn += span_counts[2]
        core_gold = [row for row in gold.evidence_spans if row.importance == "core"]
        core_predictions = [
            row for row in predicted.evidence_spans if row.importance == "core"
        ]
        core_counts = _span_match_counts(core_gold, core_predictions)
        evidence_core_tp += core_counts[0]
        evidence_core_fp += core_counts[1]
        evidence_core_fn += core_counts[2]
        if core_gold:
            core_evidence_case_count += 1
            core_case_recalls.append(core_counts[0] / len(core_gold))

        predicted_items = {item.item_id: item for item in predicted.primary_items}
        gold_item_ids = set(gold.primary_item_scores_ratio)
        if (
            not gold_item_ids
            or set(gold.primary_item_bands) != gold_item_ids
            or set(predicted_items) != gold_item_ids
        ):
            primary_item_scope_ok = False
        for item_id, gold_ratio in gold.primary_item_scores_ratio.items():
            item = predicted_items.get(item_id)
            predicted_ratio = item.score_ratio if item is not None else 0.0
            primary_item_errors.append(abs(gold_ratio - predicted_ratio))
        for item_id, expected_band in gold.primary_item_bands.items():
            primary_band_total += 1
            item = predicted_items.get(item_id)
            if item is not None and item.band == expected_band:
                primary_band_hit += 1

        gold_by_redline = {
            (row.redline_id, row.family, row.severity): row for row in gold.redlines
        }
        predicted_redlines = {
            (row.redline_id, row.family, row.severity)
            for row in predicted.redline_predictions
        }
        if any(
            row.outcome == "uncertain" or row.family not in SUPPORTED_REDLINE_FAMILIES
            for row in gold.redlines
        ):
            uncovered_severe_redline_count += 1
        for key, row in gold_by_redline.items():
            predicted_positive = key in predicted_redlines
            if row.outcome == "triggered":
                redline_family_positive.add(row.family)
                if predicted_positive:
                    redline_tp += 1
                else:
                    redline_fn += 1
            elif row.outcome == "not_triggered":
                redline_family_negative.add(row.family)
                if predicted_positive:
                    redline_fp += 1
                else:
                    redline_tn += 1
        redline_fp += len(predicted_redlines - set(gold_by_redline))
        gold_triggered_supported = {
            key
            for key, row in gold_by_redline.items()
            if row.outcome == "triggered"
            and row.severity in {"disqualify", "zero"}
            and row.family in SUPPORTED_REDLINE_FAMILIES
        }
        uncovered_severe_redline_count += sum(
            1
            for row in gold.redlines
            if row.outcome == "triggered"
            and row.severity in {"disqualify", "zero"}
            and row.family not in SUPPORTED_REDLINE_FAMILIES
        )
        if gold_triggered_supported:
            severe_redline_positive_cases += 1
        severe_redline_misses += len(gold_triggered_supported - predicted_redlines)
        if predicted.uncovered_tender_redline_ids:
            uncovered_severe_redline_count += len(predicted.uncovered_tender_redline_ids)

    independent_review_min = 0 if independent_review_min == math.inf else independent_review_min
    absolute_errors = [abs(gold - predicted) for gold, predicted in zip(total_gold, total_predicted)]
    squared_errors = [(gold - predicted) ** 2 for gold, predicted in zip(total_gold, total_predicted)]
    total_mae = _mean(absolute_errors)
    total_rmse = math.sqrt(_mean(squared_errors) or 0.0) if squared_errors else None

    group_spearman: List[float] = []
    group_kendall: List[float] = []
    group_mae: List[float] = []
    rank_group_integrity = True
    minimum_group_size = int(CERTIFICATION_THRESHOLDS["minimum_rank_group_size"])
    for case_ids in rank_groups.values():
        if len(case_ids) < minimum_group_size:
            rank_group_integrity = False
            continue
        group_cases = [manifest_by_case[case_id] for case_id in case_ids]
        if (
            len({case.project_key for case in group_cases}) != 1
            or len({case.scoring_context.sha256 for case in group_cases}) != 1
        ):
            rank_group_integrity = False
        gold_values = [
            labels_by_case[case_id].resolution.final_judgment.total_score_5pt
            for case_id in case_ids
        ]
        predicted_values = [primary_predictions[case_id].total_score_5pt for case_id in case_ids]
        group_s = spearman(gold_values, predicted_values)
        group_k = kendall_tau_b(gold_values, predicted_values)
        current_group_mae = _mean(
            [abs(gold - predicted) for gold, predicted in zip(gold_values, predicted_values)]
        )
        if group_s is None or group_k is None or current_group_mae is None:
            rank_group_integrity = False
            continue
        if (
            group_s < CERTIFICATION_THRESHOLDS["rank_spearman_min"]
            or group_k < CERTIFICATION_THRESHOLDS["rank_kendall_tau_b_min"]
            or current_group_mae > CERTIFICATION_THRESHOLDS["ranking_group_mae_5pt_max"]
        ):
            rank_group_integrity = False
        group_spearman.append(group_s)
        group_kendall.append(group_k)
        group_mae.append(current_group_mae)

    rank_spearman = _mean(group_spearman)
    rank_kendall = _mean(group_kendall)
    secondary_metrics = _prf(secondary_tp, secondary_fp, secondary_fn)
    evidence_metrics = _prf(evidence_tp, evidence_fp, evidence_fn)
    evidence_core_metrics = _prf(evidence_core_tp, evidence_core_fp, evidence_core_fn)
    core_evidence_case_coverage = (
        core_evidence_case_count / case_count if case_count else None
    )
    minimum_core_case_recall = min(core_case_recalls) if core_case_recalls else None
    redline_metrics = _prf(redline_tp, redline_fp, redline_fn)
    redline_specificity = (
        redline_tn / (redline_tn + redline_fp)
        if redline_tn + redline_fp
        else None
    )
    project_type_rate = project_type_exact / case_count if case_count else None
    primary_item_mae = _mean(primary_item_errors)
    primary_band_accuracy = (
        primary_band_hit / primary_band_total if primary_band_total else None
    )
    duplicate_document_count = case_count - len({case.document.sha256 for case in manifest.cases})
    training_overlap_count = len(
        {case.document.sha256 for case in manifest.cases}
        & set(manifest.known_training_document_sha256)
    )
    project_type_mae_values = {
        key: _mean(values) for key, values in project_type_errors.items()
    }
    max_project_type_mae = max(
        (value for value in project_type_mae_values.values() if value is not None),
        default=None,
    )
    max_group_mae = max(group_mae, default=None)

    thresholds_match = manifest.thresholds == CERTIFICATION_THRESHOLDS
    catalog_version_matches = manifest.criteria_catalog_version == CATALOG_VERSION
    protocol = manifest.label_protocol
    protocol_complete = all(
        (
            protocol.labels_hidden_during_scoring,
            protocol.experts_anonymized,
            protocol.independent_reviews,
            protocol.disagreement_adjudicated,
            release_attested,
        )
    )

    gates = [
        _gate(
            "criteria_catalog_version",
            value=manifest.criteria_catalog_version,
            threshold=CATALOG_VERSION,
            comparator="==",
            passed=catalog_version_matches,
        ),
        _gate(
            "fixed_threshold_policy",
            value=thresholds_match,
            threshold=True,
            comparator="==",
            passed=thresholds_match,
        ),
        _gate(
            "blind_label_release_protocol",
            value=protocol_complete,
            threshold=True,
            comparator="==",
            passed=protocol_complete,
        ),
        _gate(
            "semantic_artifact_integrity",
            value=semantic_artifact_valid,
            threshold=True,
            comparator="==",
            passed=semantic_artifact_valid,
        ),
        _gate(
            "catalog_id_scope",
            value=catalog_scope_ok,
            threshold=True,
            comparator="==",
            passed=catalog_scope_ok,
        ),
        _gate(
            "evidence_span_bounds",
            value=span_bounds_ok,
            threshold=True,
            comparator="==",
            passed=span_bounds_ok,
        ),
        _gate(
            "case_count",
            value=case_count,
            threshold=int(CERTIFICATION_THRESHOLDS["minimum_case_count"]),
            comparator=">=",
            passed=case_count >= CERTIFICATION_THRESHOLDS["minimum_case_count"],
        ),
        _gate(
            "project_type_count",
            value=len(project_types_covered),
            threshold=int(CERTIFICATION_THRESHOLDS["minimum_project_type_count"]),
            comparator=">=",
            passed=len(project_types_covered)
            >= CERTIFICATION_THRESHOLDS["minimum_project_type_count"],
        ),
        _gate(
            "independent_reviews_per_case",
            value=independent_review_min,
            threshold=int(CERTIFICATION_THRESHOLDS["minimum_independent_reviews_per_case"]),
            comparator=">=",
            passed=(
                expert_quality_ok
                and adjudication_ok
                and independent_review_min
                >= CERTIFICATION_THRESHOLDS["minimum_independent_reviews_per_case"]
            ),
        ),
        _gate(
            "prediction_run_count",
            value=len(predictions),
            threshold=int(CERTIFICATION_THRESHOLDS["minimum_prediction_runs"]),
            comparator=">=",
            passed=len(predictions) >= CERTIFICATION_THRESHOLDS["minimum_prediction_runs"],
        ),
        _gate(
            "artifact_consistency_rate",
            value=_round_optional(determinism_rate),
            threshold=CERTIFICATION_THRESHOLDS["determinism_rate_min"],
            comparator=">=",
            passed=(
                determinism_rate is not None
                and determinism_rate >= CERTIFICATION_THRESHOLDS["determinism_rate_min"]
            ),
        ),
        _gate(
            "independent_execution_attestation",
            value=release.independent_execution_verified,
            threshold=True,
            comparator="==",
            passed=release_attested,
        ),
        _gate(
            "assessment_contract_valid_rate",
            value=_round_optional(contract_valid_rate),
            threshold=CERTIFICATION_THRESHOLDS["assessment_contract_valid_rate_min"],
            comparator=">=",
            passed=(
                contract_valid_rate is not None
                and contract_valid_rate
                >= CERTIFICATION_THRESHOLDS["assessment_contract_valid_rate_min"]
            ),
        ),
        _gate(
            "total_score_mae_5pt",
            value=_round_optional(total_mae),
            threshold=CERTIFICATION_THRESHOLDS["total_score_mae_5pt_max"],
            comparator="<=",
            passed=(
                total_mae is not None
                and total_mae <= CERTIFICATION_THRESHOLDS["total_score_mae_5pt_max"]
            ),
        ),
        _gate(
            "total_score_rmse_5pt",
            value=_round_optional(total_rmse),
            threshold=CERTIFICATION_THRESHOLDS["total_score_rmse_5pt_max"],
            comparator="<=",
            passed=(
                total_rmse is not None
                and total_rmse <= CERTIFICATION_THRESHOLDS["total_score_rmse_5pt_max"]
            ),
        ),
        _gate(
            "total_score_max_absolute_error_5pt",
            value=_round_optional(max(absolute_errors) if absolute_errors else None),
            threshold=CERTIFICATION_THRESHOLDS[
                "total_score_max_absolute_error_5pt_max"
            ],
            comparator="<=",
            passed=(
                bool(absolute_errors)
                and max(absolute_errors)
                <= CERTIFICATION_THRESHOLDS[
                    "total_score_max_absolute_error_5pt_max"
                ]
            ),
        ),
        _gate(
            "project_type_mae_5pt",
            value=_round_optional(max_project_type_mae),
            threshold=CERTIFICATION_THRESHOLDS["project_type_mae_5pt_max"],
            comparator="<=",
            passed=(
                max_project_type_mae is not None
                and max_project_type_mae
                <= CERTIFICATION_THRESHOLDS["project_type_mae_5pt_max"]
            ),
        ),
        _gate(
            "ranking_group_mae_5pt",
            value=_round_optional(max_group_mae),
            threshold=CERTIFICATION_THRESHOLDS["ranking_group_mae_5pt_max"],
            comparator="<=",
            passed=(
                max_group_mae is not None
                and max_group_mae
                <= CERTIFICATION_THRESHOLDS["ranking_group_mae_5pt_max"]
            ),
        ),
        _gate(
            "rank_group_integrity",
            value={"declared": len(rank_groups), "evaluable": len(group_spearman)},
            threshold="all groups evaluable and individually passing",
            comparator="==",
            passed=(rank_group_integrity and len(group_spearman) == len(rank_groups)),
        ),
        _gate(
            "rank_spearman",
            value=_round_optional(rank_spearman),
            threshold=CERTIFICATION_THRESHOLDS["rank_spearman_min"],
            comparator=">=",
            passed=(
                rank_spearman is not None
                and rank_spearman >= CERTIFICATION_THRESHOLDS["rank_spearman_min"]
            ),
        ),
        _gate(
            "rank_kendall_tau_b",
            value=_round_optional(rank_kendall),
            threshold=CERTIFICATION_THRESHOLDS["rank_kendall_tau_b_min"],
            comparator=">=",
            passed=(
                rank_kendall is not None
                and rank_kendall >= CERTIFICATION_THRESHOLDS["rank_kendall_tau_b_min"]
            ),
        ),
        _gate(
            "primary_item_id_scope",
            value=primary_item_scope_ok,
            threshold=True,
            comparator="==",
            passed=primary_item_scope_ok,
        ),
        _gate(
            "primary_item_score_ratio_mae",
            value=_round_optional(primary_item_mae),
            threshold=CERTIFICATION_THRESHOLDS["primary_item_score_ratio_mae_max"],
            comparator="<=",
            passed=(
                primary_item_mae is not None
                and primary_item_mae
                <= CERTIFICATION_THRESHOLDS["primary_item_score_ratio_mae_max"]
            ),
        ),
        _gate(
            "primary_item_band_accuracy",
            value=_round_optional(primary_band_accuracy),
            threshold=CERTIFICATION_THRESHOLDS["primary_item_band_accuracy_min"],
            comparator=">=",
            passed=(
                primary_band_accuracy is not None
                and primary_band_accuracy
                >= CERTIFICATION_THRESHOLDS["primary_item_band_accuracy_min"]
            ),
        ),
        _gate(
            "project_type_exact_match_rate",
            value=_round_optional(project_type_rate),
            threshold=CERTIFICATION_THRESHOLDS["project_type_exact_match_rate_min"],
            comparator=">=",
            passed=(
                project_type_rate is not None
                and project_type_rate
                >= CERTIFICATION_THRESHOLDS["project_type_exact_match_rate_min"]
            ),
        ),
        _gate(
            "secondary_catalog_f1",
            value=_round_optional(secondary_metrics["f1"]),
            threshold=CERTIFICATION_THRESHOLDS["secondary_catalog_f1_min"],
            comparator=">=",
            passed=(
                secondary_metrics["f1"] is not None
                and secondary_metrics["f1"]
                >= CERTIFICATION_THRESHOLDS["secondary_catalog_f1_min"]
            ),
        ),
        _gate(
            "evidence_span_precision",
            value=_round_optional(evidence_metrics["precision"]),
            threshold=CERTIFICATION_THRESHOLDS["evidence_span_precision_min"],
            comparator=">=",
            passed=(
                evidence_metrics["precision"] is not None
                and evidence_metrics["precision"]
                >= CERTIFICATION_THRESHOLDS["evidence_span_precision_min"]
            ),
        ),
        _gate(
            "evidence_span_recall",
            value=_round_optional(evidence_metrics["recall"]),
            threshold=CERTIFICATION_THRESHOLDS["evidence_span_recall_min"],
            comparator=">=",
            passed=(
                evidence_metrics["recall"] is not None
                and evidence_metrics["recall"]
                >= CERTIFICATION_THRESHOLDS["evidence_span_recall_min"]
            ),
        ),
        _gate(
            "evidence_span_f1",
            value=_round_optional(evidence_metrics["f1"]),
            threshold=CERTIFICATION_THRESHOLDS["evidence_span_f1_min"],
            comparator=">=",
            passed=(
                evidence_metrics["f1"] is not None
                and evidence_metrics["f1"]
                >= CERTIFICATION_THRESHOLDS["evidence_span_f1_min"]
            ),
        ),
        _gate(
            "evidence_span_core_recall",
            value=_round_optional(evidence_core_metrics["recall"]),
            threshold=CERTIFICATION_THRESHOLDS["evidence_span_core_recall_min"],
            comparator=">=",
            passed=(
                evidence_core_metrics["recall"] is not None
                and evidence_core_metrics["recall"]
                >= CERTIFICATION_THRESHOLDS["evidence_span_core_recall_min"]
            ),
        ),
        _gate(
            "evidence_span_core_f1",
            value=_round_optional(evidence_core_metrics["f1"]),
            threshold=CERTIFICATION_THRESHOLDS["evidence_span_core_f1_min"],
            comparator=">=",
            passed=(
                evidence_core_metrics["f1"] is not None
                and evidence_core_metrics["f1"]
                >= CERTIFICATION_THRESHOLDS["evidence_span_core_f1_min"]
            ),
        ),
        _gate(
            "evidence_span_core_case_coverage",
            value=_round_optional(core_evidence_case_coverage),
            threshold=CERTIFICATION_THRESHOLDS[
                "evidence_span_core_case_coverage_min"
            ],
            comparator=">=",
            passed=(
                core_evidence_case_coverage is not None
                and core_evidence_case_coverage
                >= CERTIFICATION_THRESHOLDS[
                    "evidence_span_core_case_coverage_min"
                ]
                and minimum_core_case_recall is not None
                and minimum_core_case_recall
                >= CERTIFICATION_THRESHOLDS[
                    "evidence_span_core_case_recall_min"
                ]
            ),
        ),
        _gate(
            "redline_precision",
            value=_round_optional(redline_metrics["precision"]),
            threshold=CERTIFICATION_THRESHOLDS["redline_precision_min"],
            comparator=">=",
            passed=(
                redline_metrics["precision"] is not None
                and redline_metrics["precision"]
                >= CERTIFICATION_THRESHOLDS["redline_precision_min"]
            ),
        ),
        _gate(
            "redline_recall",
            value=_round_optional(redline_metrics["recall"]),
            threshold=CERTIFICATION_THRESHOLDS["redline_recall_min"],
            comparator=">=",
            passed=(
                redline_metrics["recall"] is not None
                and redline_metrics["recall"]
                >= CERTIFICATION_THRESHOLDS["redline_recall_min"]
            ),
        ),
        _gate(
            "redline_specificity",
            value=_round_optional(redline_specificity),
            threshold=CERTIFICATION_THRESHOLDS["redline_specificity_min"],
            comparator=">=",
            passed=(
                redline_specificity is not None
                and redline_specificity
                >= CERTIFICATION_THRESHOLDS["redline_specificity_min"]
                and redline_family_positive == SUPPORTED_REDLINE_FAMILIES
                and redline_family_negative == SUPPORTED_REDLINE_FAMILIES
            ),
        ),
        _gate(
            "severe_redline_misses",
            value=severe_redline_misses,
            threshold=int(CERTIFICATION_THRESHOLDS["severe_redline_misses_max"]),
            comparator="<=",
            passed=(
                severe_redline_positive_cases > 0
                and uncovered_severe_redline_count == 0
                and severe_redline_misses
                <= CERTIFICATION_THRESHOLDS["severe_redline_misses_max"]
            ),
        ),
        _gate(
            "cross_project_leakage",
            value=cross_project_leakage,
            threshold=int(CERTIFICATION_THRESHOLDS["cross_project_leakage_max"]),
            comparator="<=",
            passed=cross_project_leakage
            <= CERTIFICATION_THRESHOLDS["cross_project_leakage_max"],
        ),
        _gate(
            "rule_only_training_provenance",
            value="N/A_RULE_ONLY" if not manifest.known_training_document_sha256 else "INVALID",
            threshold="N/A_RULE_ONLY",
            comparator="==",
            passed=(
                duplicate_document_count == 0
                and not manifest.known_training_document_sha256
                and training_overlap_count == 0
            ),
        ),
    ]

    failed_gate_names = [gate["name"] for gate in gates if gate["status"] != "PASS"]
    if manifest.dataset_kind == DATASET_KIND_SYNTHETIC:
        status = STATUS_NOT_CERTIFIED
        reason_codes = ["SYNTHETIC_FIXTURE_NOT_CERTIFIABLE"]
        if failed_gate_names:
            reason_codes.extend(f"GATE_{name.upper()}" for name in failed_gate_names)
    elif failed_gate_names:
        status = STATUS_BLOCKED
        reason_codes = [f"GATE_{name.upper()}" for name in failed_gate_names]
    else:
        # The mapping-only service deliberately cannot issue a real certificate:
        # it has no trustworthy filesystem-location or current-code evidence.
        # Only the official file-based CLI may promote this intermediate state.
        status = STATUS_ELIGIBLE
        reason_codes = []

    report: Dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": status,
        "dataset_kind": manifest.dataset_kind,
        "criteria_catalog_version": manifest.criteria_catalog_version,
        "generated_at": effective_generated_at,
        "artifact_digests": {
            "manifest_sha256": manifest_digest,
            "commitment_sha256": commitment_sha256(commitment),
            "release_sha256": canonical_sha256(release.model_dump(mode="json")),
            "labels_sha256": canonical_sha256(labels.model_dump(mode="json")),
            "gold_seal_sha256": labels.gold_seal_sha256,
            "prediction_artifact_sha256": artifact_digests,
        },
        "coverage": {
            "case_count": case_count,
            "project_type_count": len(project_types_covered),
            "ranking_group_count": len(rank_groups),
            "evaluable_ranking_group_count": len(group_spearman),
            "independent_reviews_per_case_min": independent_review_min,
            "prediction_run_count": len(predictions),
            "gold_evidence_span_count": evidence_tp + evidence_fn,
            "predicted_evidence_span_count": evidence_tp + evidence_fp,
            "severe_redline_positive_case_count": severe_redline_positive_cases,
            "uncovered_severe_redline_count": uncovered_severe_redline_count,
            "duplicate_document_count": duplicate_document_count,
            "core_evidence_case_count": core_evidence_case_count,
            "redline_positive_family_count": len(redline_family_positive),
            "redline_negative_family_count": len(redline_family_negative),
        },
        "metrics": {
            "total_score_5pt": {
                "mae": _round_optional(total_mae),
                "rmse": _round_optional(total_rmse),
                "max_absolute_error": _round_optional(max(absolute_errors) if absolute_errors else None),
                "max_project_type_mae": _round_optional(max_project_type_mae),
                "max_ranking_group_mae": _round_optional(max_group_mae),
            },
            "ranking": {
                "spearman_macro": _round_optional(rank_spearman),
                "kendall_tau_b_macro": _round_optional(rank_kendall),
            },
            "primary_items": {
                "score_ratio_mae": _round_optional(primary_item_mae),
                "band_accuracy": _round_optional(primary_band_accuracy),
            },
            "project_type_exact_match_rate": _round_optional(project_type_rate),
            "secondary_catalog": {
                **{key: _round_optional(value) for key, value in secondary_metrics.items()},
                "tp": secondary_tp,
                "fp": secondary_fp,
                "fn": secondary_fn,
            },
            "evidence_spans": {
                **{key: _round_optional(value) for key, value in evidence_metrics.items()},
                "tp": evidence_tp,
                "fp": evidence_fp,
                "fn": evidence_fn,
                "core": {
                    **{
                        key: _round_optional(value)
                        for key, value in evidence_core_metrics.items()
                    },
                    "tp": evidence_core_tp,
                    "fp": evidence_core_fp,
                    "fn": evidence_core_fn,
                    "minimum_case_recall": _round_optional(minimum_core_case_recall),
                },
                "match_rule": (
                    "maximum-cardinality one-to-one match with same "
                    "item/requirement/polarity and both-side overlap >= 0.5"
                ),
            },
            "severe_redline_misses": severe_redline_misses,
            "redlines": {
                **{key: _round_optional(value) for key, value in redline_metrics.items()},
                "specificity": _round_optional(redline_specificity),
                "tp": redline_tp,
                "fp": redline_fp,
                "fn": redline_fn,
                "tn": redline_tn,
            },
            "artifact_consistency_rate": _round_optional(determinism_rate),
            "assessment_contract_valid_rate": _round_optional(contract_valid_rate),
            "cross_project_leakage_count": cross_project_leakage,
            "training_overlap_count": None,
            "training_overlap_status": "N/A_RULE_ONLY",
        },
        "thresholds": dict(CERTIFICATION_THRESHOLDS),
        "gates": gates,
        "reason_codes": reason_codes,
        "privacy": {
            "contains_document_text": False,
            "contains_expert_identity": False,
            "contains_case_level_scores": False,
        },
    }
    _assert_aggregate_report_privacy(
        report,
        forbidden_values=(
            [case.case_id for case in manifest.cases]
            + [case.project_key for case in manifest.cases]
            + [expert.expert_id for expert in labels.experts]
            + [
                review.review_id
                for case in labels.cases
                for review in case.expert_reviews
            ]
            + [case.resolution.resolution_id for case in labels.cases]
        ),
    )
    hash_payload = dict(report)
    hash_payload.pop("generated_at", None)
    report["report_sha256"] = canonical_sha256(hash_payload)
    return report
