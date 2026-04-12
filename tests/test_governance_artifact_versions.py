from __future__ import annotations

from pathlib import Path

from app.domain.governance.artifact_versions import (
    build_artifact_version_history,
    build_governance_artifact_impacts,
    summarize_versioned_artifact_payload,
)


def test_summarize_versioned_artifact_payload_for_calibration_models() -> None:
    payload = [
        {
            "calibrator_version": "calib-old",
            "model_type": "ridge",
            "gate_passed": True,
            "created_at": "2026-04-10T10:00:00+08:00",
            "auto_review": {"action": "keep"},
        },
        {
            "calibrator_version": "calib-new",
            "model_type": "isotonic1d",
            "gate_passed": False,
            "updated_at": "2026-04-11T10:00:00+08:00",
            "auto_review": {"action": "rollback", "passed": False},
        },
    ]

    summary = summarize_versioned_artifact_payload(
        "calibration_models",
        payload,
        project_id="p1",
        normalize_dimension_id=lambda value: str(value or "").strip(),
        calibrator_auto_review_state=lambda row: dict(row.get("auto_review") or {}),
        calibrator_bootstrap_small_sample=lambda row: bool(row.get("bootstrap_small_sample")),
        calibrator_deployment_mode=lambda row: "candidate_only"
        if str(row.get("calibrator_version") or "").endswith("new")
        else "cv_validated",
    )

    assert summary["summary_type"] == "calibration_models"
    assert summary["primary_count"] == 2
    assert summary["latest_calibrator_version"] == "calib-new"
    assert summary["latest_model_type"] == "isotonic1d"
    assert summary["gate_passed_count"] == 1
    assert summary["latest_deployment_mode"] == "candidate_only"
    assert summary["latest_auto_review_action"] == "rollback"
    assert summary["latest_auto_review_passed"] is False


def test_build_artifact_version_history_collects_recent_versions() -> None:
    history = build_artifact_version_history(
        [
            ("high_score_features", Path("/tmp/high_score_features.json")),
            ("calibration_models", Path("/tmp/calibration_models.json")),
        ],
        list_versions=lambda path: (
            [
                {"version_id": "v2", "created_at": "2026-04-12T10:00:00+08:00"},
                {"version_id": "v1", "created_at": "2026-04-11T10:00:00+08:00"},
            ]
            if path.stem == "high_score_features"
            else []
        ),
    )

    assert history[0]["artifact"] == "high_score_features"
    assert history[0]["version_count"] == 2
    assert history[0]["latest_version_id"] == "v2"
    assert history[0]["recent_versions"][1]["version_id"] == "v1"
    assert history[1]["artifact"] == "calibration_models"
    assert history[1]["version_count"] == 0


def test_build_governance_artifact_impacts_reports_snapshot_delta() -> None:
    artifact_specs = {
        "high_score_features": {
            "path": Path("/tmp/high_score_features.json"),
            "default_payload": [],
        }
    }

    impacts = build_governance_artifact_impacts(
        artifact_specs,
        project_id="p1",
        load_payload=lambda artifact: [{"feature_id": "f-1"}, {"feature_id": "f-2"}],
        list_versions=lambda path: [
            {"version_id": "snap-1", "created_at": "2026-04-11T10:00:00+08:00"}
        ],
        load_version=lambda path, version_id, default_payload: [{"feature_id": "f-1"}],
        summarize_payload=lambda artifact, payload, project_id: {
            "summary_type": artifact,
            "primary_count": len(payload) if isinstance(payload, list) else 0,
        },
        is_snapshot_load_error=lambda exc: False,
    )

    assert len(impacts) == 1
    impact = impacts[0]
    assert impact["artifact"] == "high_score_features"
    assert impact["latest_snapshot_version_id"] == "snap-1"
    assert impact["matches_latest_snapshot"] is False
    assert impact["changed_since_latest_snapshot"] is True
    assert impact["delta_vs_latest_snapshot"]["primary_count"] == 1
