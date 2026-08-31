from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from app import expert_benchmark_service, storage
from app.assessment_contract_service import build_contract_from_inputs, verify_assessment_contract
from app.expert_benchmark_service import (
    CERTIFICATION_THRESHOLDS,
    COMMITMENT_SCHEMA_VERSION,
    DATASET_KIND_REAL,
    DATASET_KIND_SYNTHETIC,
    LABELS_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PREDICTIONS_SCHEMA_VERSION,
    RELEASE_SCHEMA_VERSION,
    SCORING_MODE,
    STATUS_ELIGIBLE,
    STATUS_NOT_CERTIFIED,
    STATUS_PASS,
    BenchmarkManifest,
    BenchmarkValidationError,
    EvidenceSpan,
    EvidenceSpanPrediction,
    canonical_sha256,
    commitment_is_trusted,
    commitment_sha256,
    compute_execution_evidence_sha256,
    compute_gold_seal,
    evaluate_expert_benchmark,
    manifest_sha256,
    parse_commitment,
    prediction_artifact_sha256,
    prediction_case_fingerprint,
    prediction_output_fingerprint,
    thresholds_sha256,
)
from app.tender_criteria_catalog import CATALOG_VERSION, catalog_entries
from app.tender_criteria_service import extract_tender_profile_draft
from scripts import expert_benchmark

SPLIT_TABLE_TENDER_TEXT = """
第三章 评标办法
详细评审标准
条款号 评审因素 分值 评审标准
2.2.2 技术文件 施工组织设
计
5 分
依据投标人提供的施工组织设计进行评审，包括但不限于以下内容：
1.针对工程项目整体理解；
2.拟采用的新技术、新工艺（如有）；
3.确保工期与质量；
4.确保人、材、机配置合理；
5.确保安全文明施工；
6.涉及绿色建筑的应体现绿色施工要求。
较差得 0 分≤F＜2，一般得 2≤F＜3.5，优秀得 3.5≤F≤5
"""

DOCUMENT_TEXT = """
编制依据
依据招标文件编制。
工程概况
本方案针对工程项目整体理解并明确范围。
施工部署
采用新技术、新工艺，安排施工段与流程。
施工进度计划
确保工期与质量，设置关键线路和纠偏措施。
施工准备与资源配置计划
确保人、材、机配置合理并实施进场验收。
主要施工方法
按样板引路和首件验收组织施工。
质量管理
落实质量保证体系、检查频次和整改闭环。
安全管理
确保安全文明施工，并落实绿色施工要求。
STRICT-SOURCE-TEXT-SENTINEL
"""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _protocol() -> dict[str, bool]:
    return {
        "labels_hidden_during_scoring": True,
        "experts_anonymized": True,
        "independent_reviews": True,
        "disagreement_adjudicated": True,
    }


def _file_reference(relative_path: str, value: str) -> dict[str, object]:
    payload = value.encode("utf-8")
    return {
        "relative_path": relative_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_length": len(payload),
        "character_length": len(value),
        "encoding": "utf-8",
    }


def _write_synthetic_case(tmp_path: Path) -> tuple[Path, Path, Path]:
    data_root = tmp_path / "data"
    (data_root / "documents").mkdir(parents=True)
    (data_root / "contexts").mkdir(parents=True)
    document_path = data_root / "documents" / "case-001.txt"
    context_path = data_root / "contexts" / "case-001.json"
    document_path.write_text(DOCUMENT_TEXT, encoding="utf-8")
    state = extract_tender_profile_draft(
        project_id="fixture-project",
        project_name="滨湖办公楼新建工程",
        source_text=SPLIT_TABLE_TENDER_TEXT,
    )
    context_text = json.dumps(
        {"tender_profile": state["profile"], "source_context": SPLIT_TABLE_TENDER_TEXT},
        ensure_ascii=False,
        sort_keys=True,
    )
    context_path.write_text(context_text, encoding="utf-8")
    engine_digest = expert_benchmark._engine_source_sha256()
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_id": "synthetic-suite-001",
        "dataset_kind": DATASET_KIND_SYNTHETIC,
        "criteria_catalog_version": CATALOG_VERSION,
        "expected_engine_source_sha256": engine_digest,
        "scoring_mode": SCORING_MODE,
        "score_scale_canonical_max": 5,
        "label_protocol": _protocol(),
        "thresholds": dict(CERTIFICATION_THRESHOLDS),
        "known_training_document_sha256": [],
        "cases": [
            {
                "case_id": "case-001",
                "project_key": "project-001",
                "ranking_group_id": "group-001",
                "document": _file_reference("documents/case-001.txt", DOCUMENT_TEXT),
                "scoring_context": _file_reference("contexts/case-001.json", context_text),
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    commitment = {
        "schema_version": COMMITMENT_SCHEMA_VERSION,
        "provenance": DATASET_KIND_SYNTHETIC,
        "commitment_id": "fixture-commitment-001",
        "dataset_id": manifest["dataset_id"],
        "manifest_sha256": manifest_sha256(BenchmarkManifest.model_validate(manifest)),
        "gold_payload_sha256": "0" * 64,
        "thresholds_sha256": thresholds_sha256(),
        "criteria_catalog_version": CATALOG_VERSION,
        "expected_engine_source_sha256": engine_digest,
        "scoring_mode": SCORING_MODE,
        "issued_at": "2026-08-30T00:00:00+00:00",
    }
    commitment_path = tmp_path / "commitment.json"
    commitment_path.write_text(json.dumps(commitment), encoding="utf-8")
    return manifest_path, commitment_path, data_root


def test_score_phase_is_read_only_external_and_never_echoes_document_text(
    tmp_path, monkeypatch
):
    manifest_path, commitment_path, data_root = _write_synthetic_case(tmp_path)
    output = tmp_path / "prediction.json"

    def forbidden_write(*args, **kwargs):
        raise AssertionError("benchmark score phase must not write production storage")

    for name in (
        "save_ground_truth",
        "save_qingtian_results",
        "save_score_reports",
        "save_submissions",
    ):
        monkeypatch.setattr(storage, name, forbidden_write)

    assert (
        expert_benchmark.main(
            [
                "score",
                "--manifest",
                str(manifest_path),
                "--commitment",
                str(commitment_path),
                "--data-root",
                str(data_root),
                "--run-id",
                "run-001",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)
    prediction = payload["cases"][0]
    assert "STRICT-SOURCE-TEXT-SENTINEL" not in serialized
    assert "document_text" not in serialized
    assert prediction["project_types"] == ["new_building"]
    assert prediction["ranking_group_id"] == "group-001"
    assert len(prediction["secondary_catalog_ids"]) == 38
    assert verify_assessment_contract(prediction["assessment_contract"])


def _judgment(
    *, project_type: str, total_score: float, band: str, redline_states: dict[str, bool]
) -> dict[str, object]:
    catalog_id = catalog_entries(project_type)[0]["catalog_id"]
    return {
        "total_score_5pt": total_score,
        "project_types": [project_type],
        "primary_item_scores_ratio": {"item-main": total_score / 5.0},
        "primary_item_bands": {"item-main": band},
        "secondary_catalog_ids": [catalog_id],
        "evidence_spans": [
            {
                "item_id": "item-main",
                "requirement_id": "requirement-main",
                "start_index": 10,
                "end_index": 30,
                "polarity": "support",
                "importance": "core",
            }
        ],
        "redlines": [
            {
                "redline_id": "missing-core-check",
                "family": "missing_core_section",
                "outcome": "triggered" if redline_states["missing_core_section"] else "not_triggered",
                "severity": "disqualify",
            },
            {
                "redline_id": "outdated-norm-check",
                "family": "outdated_norm",
                "outcome": "triggered" if redline_states["outdated_norm"] else "not_triggered",
                "severity": "zero",
            },
        ],
    }


def _certification_fixture():
    project_types = [
        "new_building",
        "building_renovation",
        "municipal",
        "mep_installation",
        "landscape",
    ]
    cases: list[dict[str, object]] = []
    case_labels: list[dict[str, object]] = []
    prediction_cases: list[dict[str, object]] = []
    for type_index, project_type in enumerate(project_types):
        shared_context_sha = _digest(f"context:group:{type_index}")
        catalog_id = catalog_entries(project_type)[0]["catalog_id"]
        for rank_index in range(5):
            case_id = f"case-{type_index + 1}-{rank_index + 1}"
            project_key = f"project-{type_index + 1}"
            group_id = f"group-{type_index + 1}"
            document_sha = _digest(f"document:{case_id}")
            cases.append(
                {
                    "case_id": case_id,
                    "project_key": project_key,
                    "ranking_group_id": group_id,
                    "document": {
                        "relative_path": f"documents/{case_id}.txt",
                        "sha256": document_sha,
                        "byte_length": 200,
                        "character_length": 200,
                        "encoding": "utf-8",
                    },
                    "scoring_context": {
                        "relative_path": f"contexts/{group_id}.json",
                        "sha256": shared_context_sha,
                        "byte_length": 100,
                        "character_length": 100,
                        "encoding": "utf-8",
                    },
                }
            )
            total_score = float(rank_index + 1)
            band = f"band-{rank_index + 1}"
            redline_states = {
                "missing_core_section": case_id == "case-1-1",
                "outdated_norm": case_id == "case-1-2",
            }
            gold = _judgment(
                project_type=project_type,
                total_score=total_score,
                band=band,
                redline_states=redline_states,
            )
            contract = build_contract_from_inputs(
                {
                    "benchmark": {
                        "dataset_id": "expert-suite-001",
                        "case_id": case_id,
                        "ranking_group_id": group_id,
                        "document_sha256": document_sha,
                        "scoring_context_sha256": shared_context_sha,
                        "criteria_catalog_version": CATALOG_VERSION,
                        "engine_source_sha256": "a" * 64,
                        "project_types": [project_type],
                        "secondary_catalog_ids": [catalog_id],
                        "scoring_mode": SCORING_MODE,
                    },
                    "project": {"id": project_key},
                }
            )
            redline_predictions = []
            if redline_states["missing_core_section"]:
                redline_predictions.append(
                    {
                        "redline_id": "missing-core-check",
                        "family": "missing_core_section",
                        "severity": "disqualify",
                    }
                )
            if redline_states["outdated_norm"]:
                redline_predictions.append(
                    {
                        "redline_id": "outdated-norm-check",
                        "family": "outdated_norm",
                        "severity": "zero",
                    }
                )
            prediction = {
                "case_id": case_id,
                "project_key": project_key,
                "ranking_group_id": group_id,
                "document_sha256": document_sha,
                "scoring_context_sha256": shared_context_sha,
                "total_score_5pt": total_score,
                "project_types": [project_type],
                "primary_items": [
                    {"item_id": "item-main", "score_ratio": total_score / 5.0, "band": band}
                ],
                "secondary_catalog_ids": [catalog_id],
                "evidence_spans": [
                    {
                        "item_id": "item-main",
                        "requirement_id": "requirement-main",
                        "start_index": 10,
                        "end_index": 30,
                        "polarity": "support",
                        "importance": "core",
                    }
                ],
                "redline_predictions": redline_predictions,
                "uncovered_tender_redline_ids": [],
                "assessment_contract_hash": contract["contract_hash"],
                "assessment_contract": contract,
            }
            prediction["semantic_fingerprint"] = prediction_case_fingerprint(prediction)
            prediction_cases.append(prediction)
            reviews = [
                {
                    "review_id": f"review-{expert_id}-{case_id}",
                    "expert_id": expert_id,
                    "label_origin": "independent_human",
                    "judgment": copy.deepcopy(gold),
                    "reviewed_at": "2026-08-29T00:00:00+00:00",
                }
                for expert_id in ("expert-a", "expert-b")
            ]
            case_labels.append(
                {
                    "case_id": case_id,
                    "expert_reviews": reviews,
                    "resolution": {
                        "resolution_id": f"resolution-{case_id}",
                        "case_id": case_id,
                        "method": "expert_adjudication",
                        "source_review_ids": [row["review_id"] for row in reviews],
                        "adjudicator_id": "adjudicator-a",
                        "final_judgment": copy.deepcopy(gold),
                        "resolved_at": "2026-08-29T12:00:00+00:00",
                    },
                }
            )

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_id": "expert-suite-001",
        "dataset_kind": DATASET_KIND_SYNTHETIC,
        "criteria_catalog_version": CATALOG_VERSION,
        "expected_engine_source_sha256": "a" * 64,
        "scoring_mode": SCORING_MODE,
        "score_scale_canonical_max": 5,
        "label_protocol": _protocol(),
        "thresholds": dict(CERTIFICATION_THRESHOLDS),
        "known_training_document_sha256": [],
        "cases": cases,
    }
    labels = {
        "schema_version": LABELS_SCHEMA_VERSION,
        "dataset_id": "expert-suite-001",
        "dataset_kind": DATASET_KIND_SYNTHETIC,
        "experts": [
            *[
                {
                    "expert_id": expert_id,
                    "qualification_verified": True,
                    "independence_attested": True,
                    "role": "independent",
                    "scene_tags": project_types,
                }
                for expert_id in ("expert-a", "expert-b")
            ],
            {
                "expert_id": "adjudicator-a",
                "qualification_verified": True,
                "independence_attested": True,
                "role": "adjudicator",
                "scene_tags": project_types,
            },
        ],
        "cases": case_labels,
        "gold_seal_sha256": "0" * 64,
    }
    labels["gold_seal_sha256"] = compute_gold_seal(labels)
    manifest_digest = manifest_sha256(BenchmarkManifest.model_validate(manifest))
    commitment = {
        "schema_version": COMMITMENT_SCHEMA_VERSION,
        "provenance": DATASET_KIND_SYNTHETIC,
        "commitment_id": "fixture-commitment-001",
        "dataset_id": manifest["dataset_id"],
        "manifest_sha256": manifest_digest,
        "gold_payload_sha256": labels["gold_seal_sha256"],
        "thresholds_sha256": thresholds_sha256(),
        "criteria_catalog_version": CATALOG_VERSION,
        "expected_engine_source_sha256": "a" * 64,
        "scoring_mode": SCORING_MODE,
        "issued_at": "2026-08-30T00:00:00+00:00",
    }
    artifacts = []
    frozen_commitment_digest = commitment_sha256(parse_commitment(commitment))
    for run_index in range(3):
        artifact = {
                "schema_version": PREDICTIONS_SCHEMA_VERSION,
                "dataset_id": manifest["dataset_id"],
                "dataset_kind": DATASET_KIND_SYNTHETIC,
                "run_id": f"run-{run_index + 1}",
                "scoring_mode": SCORING_MODE,
                "criteria_catalog_version": CATALOG_VERSION,
                "cases_sha256": manifest_digest,
                "engine_source_sha256": "a" * 64,
                "semantic_output_sha256": prediction_output_fingerprint(prediction_cases),
                "execution_evidence_sha256": "0" * 64,
                "execution_nonce": f"execution-{run_index + 1}",
                "execution_started_at": f"2026-08-30T01:00:0{run_index}+00:00",
                "generated_at": f"2026-08-30T01:01:0{run_index}+00:00",
                "cases": copy.deepcopy(prediction_cases),
            }
        artifact["execution_evidence_sha256"] = compute_execution_evidence_sha256(
            run_id=artifact["run_id"],
            execution_nonce=artifact["execution_nonce"],
            execution_started_at=artifact["execution_started_at"],
            generated_at=artifact["generated_at"],
            manifest_digest=manifest_digest,
            commitment_digest=frozen_commitment_digest,
            engine_source_sha256=artifact["engine_source_sha256"],
            semantic_output_sha256=artifact["semantic_output_sha256"],
        )
        artifacts.append(artifact)
    release = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "provenance": DATASET_KIND_SYNTHETIC,
        "commitment_sha256": commitment_sha256(parse_commitment(commitment)),
        "prediction_artifact_sha256": [
            prediction_artifact_sha256(artifact) for artifact in artifacts
        ],
        "independent_execution_verified": True,
        "released_at": "2026-08-30T02:00:00+00:00",
    }
    return manifest, commitment, labels, release, artifacts


def _reseal_predictions(commitment, release, predictions):
    frozen_commitment_digest = commitment_sha256(parse_commitment(commitment))
    for artifact in predictions:
        artifact["semantic_output_sha256"] = prediction_output_fingerprint(artifact["cases"])
        artifact["execution_evidence_sha256"] = compute_execution_evidence_sha256(
            run_id=artifact["run_id"],
            execution_nonce=artifact["execution_nonce"],
            execution_started_at=artifact["execution_started_at"],
            generated_at=artifact["generated_at"],
            manifest_digest=artifact["cases_sha256"],
            commitment_digest=frozen_commitment_digest,
            engine_source_sha256=artifact["engine_source_sha256"],
            semantic_output_sha256=artifact["semantic_output_sha256"],
        )
    release["prediction_artifact_sha256"] = [
        prediction_artifact_sha256(artifact) for artifact in predictions
    ]
    release["commitment_sha256"] = frozen_commitment_digest


def _evaluate_fixture(manifest, commitment, labels, release, predictions):
    return evaluate_expert_benchmark(
        manifest_payload=manifest,
        commitment_payload=commitment,
        labels_payload=labels,
        release_payload=release,
        prediction_payloads=predictions,
        generated_at="2026-08-31T00:00:00+00:00",
    )


def _trusted_real_fixture(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    manifest, commitment, labels, release, predictions = _certification_fixture()
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    authority_key_id = "trusted-test-authority"
    monkeypatch.setitem(
        expert_benchmark_service.TRUSTED_AUTHORITY_PUBLIC_KEYS,
        authority_key_id,
        base64.b64encode(public_key).decode("ascii"),
    )

    manifest["dataset_kind"] = DATASET_KIND_REAL
    labels["dataset_kind"] = DATASET_KIND_REAL
    labels["gold_seal_sha256"] = compute_gold_seal(labels)
    manifest_digest = manifest_sha256(BenchmarkManifest.model_validate(manifest))
    commitment.update(
        {
            "provenance": DATASET_KIND_REAL,
            "manifest_sha256": manifest_digest,
            "gold_payload_sha256": labels["gold_seal_sha256"],
            "authority_key_id": authority_key_id,
        }
    )
    commitment["signature_ed25519"] = base64.b64encode(
        private_key.sign(expert_benchmark_service.canonical_bytes(commitment))
    ).decode("ascii")
    for artifact in predictions:
        artifact["dataset_kind"] = DATASET_KIND_REAL
        artifact["cases_sha256"] = manifest_digest
    _reseal_predictions(commitment, release, predictions)
    release.update(
        {
            "provenance": DATASET_KIND_REAL,
            "authority_key_id": authority_key_id,
        }
    )
    release["signature_ed25519"] = base64.b64encode(
        private_key.sign(expert_benchmark_service.canonical_bytes(release))
    ).decode("ascii")
    return manifest, commitment, labels, release, predictions


def test_synthetic_fixture_exercises_all_gates_but_never_certifies():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    report = _evaluate_fixture(manifest, commitment, labels, release, predictions)
    assert report["status"] == STATUS_NOT_CERTIFIED
    assert report["reason_codes"] == ["SYNTHETIC_FIXTURE_NOT_CERTIFIABLE"]
    assert all(gate["status"] == "PASS" for gate in report["gates"])
    assert report["metrics"]["total_score_5pt"]["mae"] == 0.0
    assert report["metrics"]["evidence_spans"]["f1"] == 1.0
    assert report["metrics"]["redlines"]["specificity"] == 1.0
    serialized = json.dumps(report, ensure_ascii=False)
    assert "case-1-1" not in serialized
    assert "expert-a" not in serialized


def test_mapping_service_cannot_issue_pass_and_official_cli_rechecks_current_code(
    monkeypatch, tmp_path
):
    manifest, commitment, labels, release, predictions = _trusted_real_fixture(monkeypatch)
    report = _evaluate_fixture(manifest, commitment, labels, release, predictions)
    assert report["status"] == STATUS_ELIGIBLE

    paths = {}
    for name, payload in (
        ("manifest", manifest),
        ("commitment", commitment),
        ("labels", labels),
        ("release", release),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path
    prediction_paths = []
    for index, payload in enumerate(predictions):
        path = tmp_path / f"prediction-{index}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        prediction_paths.append(path)
    output = tmp_path / "report.json"
    monkeypatch.setattr(expert_benchmark, "_engine_source_sha256", lambda: "a" * 64)
    assert (
        expert_benchmark.main(
            [
                "evaluate",
                "--manifest",
                str(paths["manifest"]),
                "--commitment",
                str(paths["commitment"]),
                "--labels",
                str(paths["labels"]),
                "--release",
                str(paths["release"]),
                "--predictions",
                *(str(path) for path in prediction_paths),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    official_report = json.loads(output.read_text(encoding="utf-8"))
    assert official_report["status"] == STATUS_PASS
    assert official_report["official_certification"] == {
        "entrypoint": "file_based_external_expert_gold_v1",
        "all_case_level_inputs_outside_repository": True,
        "current_engine_source_sha256": "a" * 64,
    }


def test_external_commitment_and_release_cannot_mix_trusted_authorities(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    manifest, commitment, labels, release, predictions = _trusted_real_fixture(monkeypatch)
    second_private_key = Ed25519PrivateKey.generate()
    second_public_key = second_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setitem(
        expert_benchmark_service.TRUSTED_AUTHORITY_PUBLIC_KEYS,
        "second-trusted-authority",
        base64.b64encode(second_public_key).decode("ascii"),
    )
    release["authority_key_id"] = "second-trusted-authority"
    release.pop("signature_ed25519")
    release["signature_ed25519"] = base64.b64encode(
        second_private_key.sign(expert_benchmark_service.canonical_bytes(release))
    ).decode("ascii")
    with pytest.raises(BenchmarkValidationError, match="same trusted authority key"):
        _evaluate_fixture(manifest, commitment, labels, release, predictions)


def test_future_release_cannot_be_certified():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    release["released_at"] = "2026-09-01T00:00:00+00:00"
    _reseal_predictions(commitment, release, predictions)
    with pytest.raises(BenchmarkValidationError, match="later than evaluation time"):
        _evaluate_fixture(manifest, commitment, labels, release, predictions)


def test_engine_digest_binds_python_runtime(monkeypatch):
    current_digest = expert_benchmark._engine_source_sha256()
    monkeypatch.setattr(expert_benchmark.platform, "python_version", lambda: "0.0.0")
    assert expert_benchmark._engine_source_sha256() != current_digest


def test_untrusted_self_declared_real_dataset_is_rejected():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    manifest["dataset_kind"] = DATASET_KIND_REAL
    labels["dataset_kind"] = DATASET_KIND_REAL
    for artifact in predictions:
        artifact["dataset_kind"] = DATASET_KIND_REAL
    commitment.update(
        {
            "provenance": DATASET_KIND_REAL,
            "manifest_sha256": manifest_sha256(BenchmarkManifest.model_validate(manifest)),
            "authority_key_id": "self-generated-key",
            "signature_ed25519": "ZmFrZQ==",
        }
    )
    release.update(
        {
            "provenance": DATASET_KIND_REAL,
            "authority_key_id": "self-generated-key",
            "signature_ed25519": "ZmFrZQ==",
        }
    )
    with pytest.raises(BenchmarkValidationError, match="trusted authority"):
        _evaluate_fixture(manifest, commitment, labels, release, predictions)


def test_external_commitment_accepts_only_a_code_pinned_ed25519_key(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    _, commitment, _, _, _ = _certification_fixture()
    commitment.update(
        {
            "provenance": DATASET_KIND_REAL,
            "authority_key_id": "trusted-test-authority",
        }
    )
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setitem(
        expert_benchmark_service.TRUSTED_AUTHORITY_PUBLIC_KEYS,
        "trusted-test-authority",
        base64.b64encode(public_key).decode("ascii"),
    )
    commitment["signature_ed25519"] = base64.b64encode(
        private_key.sign(canonical_sha256(commitment).encode("ascii"))
    ).decode("ascii")
    assert not commitment_is_trusted(parse_commitment(commitment))

    unsigned_payload = dict(commitment)
    unsigned_payload.pop("signature_ed25519")
    from app.expert_benchmark_service import canonical_bytes

    commitment["signature_ed25519"] = base64.b64encode(
        private_key.sign(canonical_bytes(unsigned_payload))
    ).decode("ascii")
    assert commitment_is_trusted(parse_commitment(commitment))


def test_rank_groups_cannot_be_silently_skipped():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    for case in labels["cases"]:
        if case["case_id"].startswith(("case-2-", "case-3-", "case-4-", "case-5-")):
            case["resolution"]["final_judgment"]["total_score_5pt"] = 3.0
            for review in case["expert_reviews"]:
                review["judgment"]["total_score_5pt"] = 3.0
    labels["gold_seal_sha256"] = compute_gold_seal(labels)
    commitment["gold_payload_sha256"] = labels["gold_seal_sha256"]
    _reseal_predictions(commitment, release, predictions)
    report = _evaluate_fixture(manifest, commitment, labels, release, predictions)
    rank_gate = next(gate for gate in report["gates"] if gate["name"] == "rank_group_integrity")
    assert rank_gate["status"] == "FAIL"
    assert report["coverage"]["evaluable_ranking_group_count"] == 1


def test_short_evidence_fragment_does_not_match_long_gold_span():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    for case in labels["cases"]:
        case["resolution"]["final_judgment"]["evidence_spans"][0]["end_index"] = 110
        for review in case["expert_reviews"]:
            review["judgment"]["evidence_spans"][0]["end_index"] = 110
    labels["gold_seal_sha256"] = compute_gold_seal(labels)
    commitment["gold_payload_sha256"] = labels["gold_seal_sha256"]
    for artifact in predictions:
        for case in artifact["cases"]:
            case["evidence_spans"][0]["end_index"] = 11
            case["semantic_fingerprint"] = prediction_case_fingerprint(case)
    _reseal_predictions(commitment, release, predictions)
    report = _evaluate_fixture(manifest, commitment, labels, release, predictions)
    assert report["metrics"]["evidence_spans"]["tp"] == 0
    gate = next(gate for gate in report["gates"] if gate["name"] == "evidence_span_recall")
    assert gate["status"] == "FAIL"


def test_single_catastrophic_score_error_is_not_hidden_by_global_mae():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    for artifact in predictions:
        case = artifact["cases"][0]
        case["total_score_5pt"] = 4.0
        case["semantic_fingerprint"] = prediction_case_fingerprint(case)
    _reseal_predictions(commitment, release, predictions)
    report = _evaluate_fixture(manifest, commitment, labels, release, predictions)
    max_error_gate = next(
        gate
        for gate in report["gates"]
        if gate["name"] == "total_score_max_absolute_error_5pt"
    )
    assert max_error_gate["status"] == "FAIL"
    assert report["metrics"]["total_score_5pt"]["max_absolute_error"] == 3.0


def test_duplicate_execution_is_fail_closed():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    predictions[0]["execution_evidence_sha256"] = predictions[1][
        "execution_evidence_sha256"
    ]
    with pytest.raises(BenchmarkValidationError, match="unique evidence"):
        _evaluate_fixture(manifest, commitment, labels, release, predictions)


def test_execution_evidence_is_recomputed_not_merely_unique():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    predictions[0]["execution_evidence_sha256"] = "f" * 64
    with pytest.raises(BenchmarkValidationError, match="execution evidence digest mismatch"):
        _evaluate_fixture(manifest, commitment, labels, release, predictions)


def test_primary_item_scores_and_bands_are_certification_gates():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    for artifact in predictions:
        for case in artifact["cases"]:
            case["primary_items"][0]["score_ratio"] = 0.0
            case["primary_items"][0]["band"] = "wrong-band"
            case["semantic_fingerprint"] = prediction_case_fingerprint(case)
    _reseal_predictions(commitment, release, predictions)
    report = _evaluate_fixture(manifest, commitment, labels, release, predictions)
    gates = {gate["name"]: gate["status"] for gate in report["gates"]}
    assert gates["primary_item_score_ratio_mae"] == "FAIL"
    assert gates["primary_item_band_accuracy"] == "FAIL"


def test_expert_gold_must_be_frozen_before_commitment():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    for case in labels["cases"]:
        for review in case["expert_reviews"]:
            review["reviewed_at"] = "2026-08-30T00:30:00+00:00"
        case["resolution"]["resolved_at"] = "2026-08-30T00:45:00+00:00"
    labels["gold_seal_sha256"] = compute_gold_seal(labels)
    commitment["gold_payload_sha256"] = labels["gold_seal_sha256"]
    _reseal_predictions(commitment, release, predictions)
    report = _evaluate_fixture(manifest, commitment, labels, release, predictions)
    gate = next(
        gate for gate in report["gates"] if gate["name"] == "independent_reviews_per_case"
    )
    assert gate["status"] == "FAIL"


def test_span_matching_uses_maximum_cardinality_not_greedy_pairing():
    from app.expert_benchmark_service import _span_match_counts

    gold = [
        EvidenceSpan(item_id="item", requirement_id="requirement", start_index=0, end_index=10),
        EvidenceSpan(item_id="item", requirement_id="requirement", start_index=5, end_index=15),
    ]
    predicted = [
        EvidenceSpanPrediction(
            item_id="item", requirement_id="requirement", start_index=0, end_index=15
        ),
        EvidenceSpanPrediction(
            item_id="item", requirement_id="requirement", start_index=0, end_index=10
        ),
    ]
    assert _span_match_counts(gold, predicted) == (2, 0, 0)


def test_duplicate_label_identity_is_rejected_before_dict_indexing():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    labels["cases"].append(copy.deepcopy(labels["cases"][0]))
    labels["gold_seal_sha256"] = compute_gold_seal(labels)
    commitment["gold_payload_sha256"] = labels["gold_seal_sha256"]
    with pytest.raises(BenchmarkValidationError, match="case_id values must be unique"):
        _evaluate_fixture(manifest, commitment, labels, release, predictions)


def test_threshold_digest_is_fixed_and_not_caller_relaxable():
    manifest, commitment, labels, release, predictions = _certification_fixture()
    manifest["thresholds"]["total_score_mae_5pt_max"] = 5.0
    manifest_digest = manifest_sha256(BenchmarkManifest.model_validate(manifest))
    commitment["manifest_sha256"] = manifest_digest
    for artifact in predictions:
        artifact["cases_sha256"] = manifest_digest
    _reseal_predictions(commitment, release, predictions)
    report = _evaluate_fixture(manifest, commitment, labels, release, predictions)
    gate = next(gate for gate in report["gates"] if gate["name"] == "fixed_threshold_policy")
    assert gate["status"] == "FAIL"
    assert canonical_sha256(CERTIFICATION_THRESHOLDS) == thresholds_sha256()
