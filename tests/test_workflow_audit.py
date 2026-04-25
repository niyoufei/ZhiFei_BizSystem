from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.application.services import workflows
from app.application.services.workflows import GovernanceApplicationService


def _patch_storage_root(monkeypatch, tmp_path: Path):
    from app import storage

    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(storage, "VERSIONED_JSON_DIR", tmp_path / "versions")
    monkeypatch.setattr(storage, "HIGH_SCORE_FEATURES_PATH", tmp_path / "high_score_features.json")
    monkeypatch.setattr(storage, "CALIBRATION_MODELS_PATH", tmp_path / "calibration_models.json")
    monkeypatch.setattr(storage, "EVOLUTION_REPORTS_PATH", tmp_path / "evolution_reports.json")
    return storage


def test_governance_accept_ignore_and_rollback_emit_audit_records(monkeypatch, tmp_path: Path):
    storage = _patch_storage_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ZHIFEI_STORAGE_ENABLE_EVENT_LOG", "true")

    legacy = SimpleNamespace(
        ensure_data_dirs=lambda: None,
        load_projects=lambda: [{"id": "p1"}],
        t=lambda key, locale=None: key,
        _execute_feedback_guardrail_review=lambda *args, **kwargs: {
            "project_id": "p1",
            "record_id": "r-guardrail",
            "action": kwargs.get("action"),
        },
        _execute_feedback_few_shot_review=lambda *args, **kwargs: {
            "project_id": "p1",
            "record_id": "r-few-shot",
            "action": kwargs.get("action"),
        },
        FeedbackGuardrailReviewResponse=lambda **kwargs: kwargs,
        FewShotReviewResponse=lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(workflows, "_runtime", lambda storage=None: legacy)

    service = GovernanceApplicationService()
    service.review_feedback_guardrail(
        project_id="p1",
        record_id="r-guardrail",
        payload=SimpleNamespace(action="accept", note="通过", rerun_closed_loop=False),
        locale="zh-CN",
    )
    service.review_feedback_few_shot(
        project_id="p1",
        record_id="r-few-shot",
        payload=SimpleNamespace(action="ignore", note="暂不采纳"),
        locale="zh-CN",
    )

    storage.save_high_score_features([{"id": "f-1", "project_id": "p1", "name": "首版"}])
    storage.save_high_score_features([{"id": "f-2", "project_id": "p1", "name": "次版"}])
    versions = storage.list_json_versions(storage.HIGH_SCORE_FEATURES_PATH)
    storage.restore_json_version(storage.HIGH_SCORE_FEATURES_PATH, versions[-1]["version_id"])

    governance_events = storage.list_domain_events(event_types=["GovernanceDecisionApplied"])
    rollback_events = storage.list_domain_events(event_types=["RollbackApplied"])

    actions = {event["payload"]["action"] for event in governance_events}
    assert actions == {"accept", "ignore"}
    assert rollback_events[0]["payload"]["collection"] == "high_score_features"


def test_learning_artifact_change_is_explainable_and_rollbackable(monkeypatch, tmp_path: Path):
    storage = _patch_storage_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ZHIFEI_STORAGE_ENABLE_EVENT_LOG", "true")

    storage.append_domain_event(
        event_type="ActualResultRecorded",
        aggregate_type="project",
        aggregate_id="p1",
        payload={
            "project_id": "p1",
            "ground_truth_id": "gt-1",
            "final_score": 91.0,
            "source": "manual",
        },
        idempotency_key="ground-truth:gt-1",
    )
    storage.save_calibration_models(
        [
            {
                "id": "cal-v1",
                "version": "cal-v1",
                "project_id": "p1",
                "metrics": {"mae": 0.32},
                "sample_count": 4,
                "explanation": "ground_truth_delta<=0.2, replay stable",
            }
        ]
    )
    storage.save_calibration_models(
        [
            {
                "id": "cal-v2",
                "version": "cal-v2",
                "project_id": "p1",
                "metrics": {"mae": 0.21},
                "sample_count": 6,
                "explanation": "new_actual_results_added and delta improved",
            }
        ]
    )
    storage.save_high_score_features(
        [
            {
                "id": "feature-v1",
                "project_id": "p1",
                "name": "进度计划网闭环",
                "confidence": 0.91,
                "reason": "actual_result_consistently_supports_schedule_evidence",
            }
        ]
    )

    cal_versions = storage.list_json_versions(storage.CALIBRATION_MODELS_PATH)
    storage.restore_json_version(storage.CALIBRATION_MODELS_PATH, cal_versions[-1]["version_id"])

    events = storage.list_domain_events(
        event_types=[
            "ActualResultRecorded",
            "CalibratorTrained",
            "FeaturePackUpdated",
            "RollbackApplied",
        ]
    )
    calibrator_event = [event for event in events if event["event_type"] == "CalibratorTrained"][-1]
    feature_event = [event for event in events if event["event_type"] == "FeaturePackUpdated"][-1]
    rollback_event = [event for event in events if event["event_type"] == "RollbackApplied"][-1]

    assert (
        calibrator_event["payload"]["explanation"] == "new_actual_results_added and delta improved"
    )
    assert calibrator_event["payload"]["sample_count"] == 6
    assert (
        feature_event["payload"]["reason"]
        == "actual_result_consistently_supports_schedule_evidence"
    )
    assert rollback_event["payload"]["collection"] == "calibration_models"
    assert len(cal_versions) >= 2
