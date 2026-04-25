"""Tests for V2 pipeline endpoints (report summary, qingtian, calibrator)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _client() -> TestClient:
    return TestClient(app)


class TestLatestReportEndpoint:
    @patch("app.main.load_score_reports")
    @patch("app.main.ensure_data_dirs")
    def test_latest_report_success(self, mock_ensure, mock_load_reports):
        mock_load_reports.return_value = [
            {
                "id": "rep1",
                "submission_id": "sub1",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 84.2,
                "pred_total_score": 86.1,
                "pred_confidence": {"sigma": 2.1, "lower": 84.0, "upper": 88.2},
                "rule_dim_scores": {},
                "penalties": [{"code": "P-CONSIST-001", "reason": "工期冲突"}],
                "lint_findings": [
                    {"issue_code": "MissingRequirement", "why_it_matters": "缺少节点"}
                ],
                "suggestions": [{"title": "补齐节点计划", "expected_gain": 3.2}],
                "created_at": "2026-02-06T10:07:10Z",
            }
        ]
        resp = _client().get("/api/v1/submissions/sub1/reports/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["report"]["rule_total_score"] == 84.2
        assert data["ui_summary"]["pred_total_score"] == 86.1
        assert len(data["ui_summary"]["top_conflicts"]) == 1
        assert len(data["ui_summary"]["top_missing_requirements"]) == 1


class TestQingTianEndpoint:
    @patch("app.main.save_projects")
    @patch("app.main.save_qingtian_results")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_ingest_qingtian_result_success(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_projects,
        mock_load_results,
        mock_save_results,
        mock_save_projects,
    ):
        mock_load_submissions.return_value = [
            {"id": "sub1", "project_id": "p1", "text": "abc", "report": {}}
        ]
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "status": "scoring_preparation",
                "qingtian_model_version": "qingtian-2026.02",
            }
        ]
        mock_load_results.return_value = []
        payload = {
            "qt_total_score": 88.5,
            "qt_dim_scores": {"01": 8.5},
            "qt_reasons": [{"kind": "missing", "text": "缺少节点"}],
            "raw_payload": {"raw": True},
        }
        resp = _client().post("/api/v1/submissions/sub1/qingtian-results", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["submission_id"] == "sub1"
        assert data["qt_total_score"] == 88.5
        assert data["qingtian_model_version"] == "qingtian-2026.02"
        mock_save_results.assert_called_once()
        mock_save_projects.assert_called_once()

    @patch("app.main.load_qingtian_results")
    @patch("app.main.ensure_data_dirs")
    def test_latest_qingtian_result_recovers_legacy_row_missing_required_fields(
        self,
        mock_ensure,
        mock_load_results,
    ):
        mock_load_results.return_value = [
            {
                "submission_id": "sub1",
                "qt_total_score": "88.5",
                "qt_reasons": ["工期控制不足"],
            }
        ]

        resp = _client().get("/api/v1/submissions/sub1/qingtian-results/latest")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == ""
        assert data["submission_id"] == "sub1"
        assert data["qingtian_model_version"] == "qingtian-2026.02"
        assert data["qt_total_score"] == 88.5
        assert data["qt_reasons"][0]["text"] == "工期控制不足"
        assert data["raw_payload"] == {}
        assert data["created_at"]

    @patch("app.main.load_qingtian_results")
    @patch("app.main.ensure_data_dirs")
    def test_latest_qingtian_result_returns_404_when_storage_corrupted(
        self,
        mock_ensure,
        mock_load_results,
    ):
        from app.storage import StorageDataError

        mock_load_results.side_effect = StorageDataError(
            Path("/tmp/qingtian_results.json"),
            "json_parse_failed",
            "数据文件 JSON 格式损坏：qingtian_results.json（第 1 行，第 1 列），请使用历史版本回滚。",
        )

        resp = _client().get("/api/v1/submissions/sub1/qingtian-results/latest")

        assert resp.status_code == 404
        assert "暂无青天评标结果" in resp.json()["detail"]


class TestCalibratorEndpoints:
    @patch("app.main._build_governance_score_preview")
    @patch("app.main._build_governance_artifact_impacts")
    @patch("app.main._train_calibrator_with_gate")
    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_train_calibrator_auto_deploy_keeps_better_existing_project_model(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_samples,
        mock_load_models,
        mock_save_models,
        mock_load_projects,
        mock_save_projects,
        mock_train_with_gate,
        mock_build_impacts,
        mock_build_preview,
    ):
        mock_build_impacts.return_value = []
        mock_build_preview.return_value = {}
        mock_load_submissions.return_value = []
        mock_load_reports.return_value = []
        mock_load_qt.return_value = []
        mock_load_samples.return_value = [
            {
                "id": f"cs{i}",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": score},
                "y_label": label,
                "submission_id": f"s{i}",
            }
            for i, (score, label) in enumerate(
                [(12.4, 82.2), (10.8, 78.0), (17.5, 80.0), (14.7, 77.9), (18.0, 80.7)],
                start=1,
            )
        ]
        mock_load_projects.return_value = [
            {"id": "p1", "name": "项目1", "calibrator_version_locked": "calib_best", "meta": {}}
        ]
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib_best",
                "model_type": "isotonic1d",
                "feature_schema_version": "v2",
                "train_filter": {"project_id": "p1"},
                "metrics": {"gate_passed": True, "cv_mae": 1.7},
                "calibrator_summary": {
                    "calibrator_version": "calib_best",
                    "model_type": "isotonic1d",
                    "gate_passed": True,
                    "cv_metrics": {"mae": 1.7, "rmse": 2.2, "spearman": -0.16},
                    "baseline_metrics": {"mae": 66.0, "rmse": 66.2, "spearman": 0.36},
                    "sample_count": 7,
                    "bootstrap_small_sample": False,
                    "deployment_mode": "cv_validated",
                    "auto_review": {},
                },
                "artifact_uri": "json://calibration_models/calib_best",
                "model_artifact": {"model_type": "isotonic1d"},
                "deployed": True,
                "created_at": "2026-03-29T15:29:58Z",
            }
        ]
        mock_train_with_gate.return_value = {
            "model_artifact": {
                "model_type": "offset",
                "feature_schema_version": "v2",
                "metrics": {"cv_mae": 3.15, "gate_passed": True},
                "gate_passed": True,
            },
            "selected_type": "offset",
            "gate_passed": True,
            "cv": {
                "ok": True,
                "metrics": {"mae": 3.15, "rmse": 4.0, "spearman": 0.05},
                "mode": "loocv",
                "pred_count": 5,
            },
            "cv_metrics": {"mae": 3.15, "rmse": 4.0, "spearman": 0.05},
            "baseline_metrics": {"mae": 66.0, "rmse": 66.2, "spearman": 0.36},
            "gate": {"passed": True, "clustered_score_override": True, "label_score_span": 4.3},
            "auto_candidates": [],
            "summary": {
                "model_type": "offset",
                "gate_passed": True,
                "cv_metrics": {"mae": 3.15, "rmse": 4.0, "spearman": 0.05},
                "baseline_metrics": {"mae": 66.0, "rmse": 66.2, "spearman": 0.36},
                "gate": {"passed": True, "clustered_score_override": True, "label_score_span": 4.3},
                "auto_candidates": [],
                "sample_count": 5,
                "bootstrap_small_sample": False,
                "full_validation_min_samples": 3,
                "deployment_mode": "candidate_only",
                "auto_review": {},
            },
            "sample_count": 5,
            "bootstrap_small_sample": False,
        }

        resp = _client().post(
            "/api/v1/calibration/train",
            json={"project_id": "p1", "model_type": "auto", "alpha": 1.0, "auto_deploy": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployed"] is False
        assert data["calibrator_summary"]["deployment_mode"] == "candidate_only"
        assert data["calibrator_summary"]["auto_review"]["action"] == "keep_existing"
        assert (
            data["calibrator_summary"]["auto_review"]["reason"]
            == "existing_better_project_calibrator_kept"
        )
        mock_save_projects.assert_not_called()
        saved_models = mock_save_models.call_args[0][0]
        current = next(row for row in saved_models if row["calibrator_version"] == "calib_best")
        assert current["deployed"] is True

    @patch("app.main.load_calibration_models")
    @patch("app.main.ensure_data_dirs")
    def test_list_calibration_models_recovers_legacy_row_missing_required_fields(
        self,
        mock_ensure,
        mock_load_models,
    ):
        mock_load_models.return_value = [
            {
                "calibrator_version": "c1",
                "metrics": None,
                "calibrator_summary": None,
            }
        ]

        resp = _client().get("/api/v1/calibration/models")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["calibrator_version"] == "c1"
        assert data[0]["model_type"] == "ridge"
        assert data[0]["feature_schema_version"] == "v2"
        assert data[0]["train_filter"] == {}
        assert data[0]["metrics"] == {}
        assert data[0]["artifact_uri"] == ""
        assert data[0]["created_at"]

    @patch("app.main.load_calibration_models")
    @patch("app.main.ensure_data_dirs")
    def test_list_calibration_models_recovers_when_storage_corrupted(
        self,
        mock_ensure,
        mock_load_models,
    ):
        from app.storage import StorageDataError

        mock_load_models.side_effect = StorageDataError(
            Path("/tmp/calibration_models.json"),
            "json_parse_failed",
            "数据文件 JSON 格式损坏：calibration_models.json（第 1 行，第 1 列），请使用历史版本回滚。",
        )

        resp = _client().get("/api/v1/calibration/models")

        assert resp.status_code == 200
        assert resp.json() == []

    @patch("app.main._build_governance_score_preview")
    @patch("app.main._build_governance_artifact_impacts")
    @patch("app.main._train_calibrator_with_gate")
    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_train_calibrator_auto_deploy_rolls_back_full_validation_candidate_when_preview_worsens(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_samples,
        mock_load_models,
        mock_save_models,
        mock_load_projects,
        mock_save_projects,
        mock_train_with_gate,
        mock_build_impacts,
        mock_build_preview,
    ):
        mock_build_impacts.return_value = []
        mock_build_preview.return_value = {
            "matched_submission_count": 2,
            "avg_abs_delta_stored": 1.2,
            "avg_abs_delta_preview": 2.0,
            "avg_abs_delta_improvement": -0.8,
            "improved_row_count": 0,
            "worsened_row_count": 2,
            "rows": [],
        }
        mock_load_submissions.return_value = []
        mock_load_reports.return_value = []
        mock_load_qt.return_value = []
        mock_load_samples.return_value = [
            {
                "id": f"cs{i}",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": score},
                "y_label": label,
                "submission_id": f"s{i}",
            }
            for i, (score, label) in enumerate(
                [
                    (12.4, 82.2),
                    (10.8, 78.0),
                    (17.5, 80.0),
                    (14.7, 77.9),
                    (18.0, 80.7),
                ],
                start=1,
            )
        ]
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_models.return_value = []
        mock_train_with_gate.return_value = {
            "model_artifact": {
                "model_type": "offset",
                "feature_schema_version": "v2",
                "metrics": {"cv_mae": 3.15, "gate_passed": True},
                "gate_passed": True,
            },
            "selected_type": "offset",
            "gate_passed": True,
            "cv": {
                "ok": True,
                "metrics": {"mae": 3.15, "rmse": 4.0, "spearman": 0.05},
                "mode": "loocv",
                "pred_count": 5,
            },
            "cv_metrics": {"mae": 3.15, "rmse": 4.0, "spearman": 0.05},
            "baseline_metrics": {"mae": 66.0, "rmse": 66.2, "spearman": 0.36},
            "gate": {"passed": True, "clustered_score_override": True, "label_score_span": 4.3},
            "auto_candidates": [],
            "summary": {
                "model_type": "offset",
                "gate_passed": True,
                "cv_metrics": {"mae": 3.15, "rmse": 4.0, "spearman": 0.05},
                "baseline_metrics": {"mae": 66.0, "rmse": 66.2, "spearman": 0.36},
                "gate": {"passed": True, "clustered_score_override": True, "label_score_span": 4.3},
                "auto_candidates": [],
                "sample_count": 5,
                "bootstrap_small_sample": False,
                "full_validation_min_samples": 3,
                "deployment_mode": "candidate_only",
                "auto_review": {},
            },
            "sample_count": 5,
            "bootstrap_small_sample": False,
        }

        resp = _client().post(
            "/api/v1/calibration/train",
            json={"project_id": "p1", "model_type": "auto", "alpha": 1.0, "auto_deploy": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployed"] is False
        assert data["calibrator_summary"]["bootstrap_small_sample"] is False
        assert data["calibrator_summary"]["deployment_mode"] == "candidate_only"
        assert data["calibrator_summary"]["auto_review"]["checked"] is True
        assert data["calibrator_summary"]["auto_review"]["passed"] is False
        assert data["calibrator_summary"]["auto_review"]["action"] == "rollback"
        assert (
            data["calibrator_summary"]["auto_review"]["reason"]
            == "preview_worsened_beyond_tolerance"
        )
        assert data["calibrator_summary"]["auto_review"]["review_mode"] == "deployment_preview"
        mock_save_projects.assert_not_called()

    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_train_calibrator_success(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_samples,
        mock_load_models,
        mock_save_models,
    ):
        mock_load_submissions.return_value = []
        mock_load_reports.return_value = []
        mock_load_qt.return_value = []
        # Use stored FEATURE_ROW samples to keep the test isolated from repo-local `data/` files.
        mock_load_samples.return_value = [
            {
                "id": "cs1",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 70},
                "y_label": 71,
                "submission_id": "s1",
            },
            {
                "id": "cs2",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 80},
                "y_label": 81,
                "submission_id": "s2",
            },
            {
                "id": "cs3",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 90},
                "y_label": 91,
                "submission_id": "s3",
            },
            {
                "id": "cs4",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 85},
                "y_label": 86,
                "submission_id": "s4",
            },
        ]
        mock_load_models.return_value = []

        resp = _client().post(
            "/api/v1/calibration/train", json={"model_type": "auto", "alpha": 1.0}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_type"] in {"offset", "linear1d", "isotonic1d", "ridge"}
        assert data["calibrator_version"].startswith("calib_auto_")
        assert data["metrics"]["gate_passed"] is True
        assert data["calibrator_summary"]["model_type"] == data["model_type"]
        assert data["calibrator_summary"]["gate_passed"] is True
        assert "mae" in data["calibrator_summary"]["cv_metrics"]
        assert "mae" in data["calibrator_summary"]["baseline_metrics"]
        assert isinstance(data["calibrator_summary"]["auto_candidates"], list)
        mock_save_models.assert_called_once()

    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.calc_metrics")
    @patch("app.main.cross_validate_calibrator")
    @patch("app.main.train_best_calibrator_auto")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_train_calibrator_gate_failed(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_samples,
        mock_train_auto,
        mock_cv,
        mock_calc_metrics,
        mock_load_models,
        mock_save_models,
    ):
        mock_load_submissions.return_value = []
        mock_load_reports.return_value = []
        mock_load_qt.return_value = []
        mock_load_samples.return_value = [
            {
                "id": "cs1",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 70},
                "y_label": 71,
                "submission_id": "s1",
            },
            {
                "id": "cs2",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 80},
                "y_label": 81,
                "submission_id": "s2",
            },
            {
                "id": "cs3",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 90},
                "y_label": 91,
                "submission_id": "s3",
            },
        ]
        mock_train_auto.return_value = {
            "model_type": "offset",
            "feature_schema_version": "v2",
            "bias": 0.0,
            "sigma": 3.0,
            "metrics": {},
        }
        mock_cv.return_value = {
            "ok": True,
            "metrics": {"mae": 5.0, "rmse": 6.2, "spearman": 0.2},
            "mode": "kfold",
            "pred_count": 3,
        }
        mock_calc_metrics.return_value = {"mae": 3.0, "rmse": 4.0, "spearman": 0.6}
        mock_load_models.return_value = []

        resp = _client().post(
            "/api/v1/calibration/train",
            json={"project_id": "p1", "model_type": "auto", "alpha": 1.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["metrics"]["gate_passed"] is False
        assert data["calibrator_summary"]["gate_passed"] is False
        assert data["calibrator_summary"]["model_type"] == "offset"
        assert data["calibrator_summary"]["cv_metrics"]["mae"] == 5.0
        assert data["calibrator_summary"]["baseline_metrics"]["mae"] == 3.0
        mock_save_models.assert_called_once()

    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_apply_calibration_prediction_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_models,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "项目1", "calibrator_version_locked": "calib1"}
        ]
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib1",
                "deployed": True,
                "created_at": "2026-02-06T12:00:00Z",
                "train_filter": {"project_id": "p1"},
                "model_artifact": {
                    "feature_keys": ["rule_total_score"],
                    "means": [80.0],
                    "stds": [10.0],
                    "weights": [5.0],
                    "bias": 80.0,
                    "sigma": 2.0,
                },
            }
        ]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1", "text": "test", "report": {"rule_total_score": 82.0}}
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1",
                "submission_id": "s1",
                "project_id": "p1",
                "rule_total_score": 82.0,
                "created_at": "2026-02-06T12:00:01Z",
            }
        ]

        resp = _client().post("/api/v1/projects/p1/calibration/predict")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["model_version"] == "calib1"
        assert data["updated_reports"] == 1
        assert data["updated_submissions"] == 1
        mock_save_reports.assert_called_once()
        mock_save_submissions.assert_called_once()

    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_apply_calibration_prediction_blends_rule_and_llm(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_models,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "项目1", "calibrator_version_locked": "calib1", "meta": {}}
        ]
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib1",
                "deployed": True,
                "created_at": "2026-02-06T12:00:00Z",
                "train_filter": {"project_id": "p1"},
                "model_artifact": {
                    "model_type": "offset",
                    "bias": 80.0,  # raw llm score => 100 (clip), then bounded by delta cap
                    "sigma": 2.0,
                },
            }
        ]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1", "text": "test", "report": {"rule_total_score": 30.0}}
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1",
                "submission_id": "s1",
                "project_id": "p1",
                "rule_total_score": 30.0,
                "created_at": "2026-02-06T12:00:01Z",
            }
        ]

        resp = _client().post("/api/v1/projects/p1/calibration/predict")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["model_version"] == "calib1"
        assert data["updated_reports"] == 1
        assert data["updated_submissions"] == 1

        saved_submissions = mock_save_submissions.call_args[0][0]
        report = saved_submissions[0]["report"]
        # rule=30, raw llm=100, bounded llm=65 (delta cap 35), fused=30*0.7+65*0.3=40.5
        assert report["llm_total_score"] == 65.0
        assert report["pred_total_score"] == 40.5
        assert report["total_score"] == 40.5
        assert report["score_blend"]["rule_weight"] == 0.7
        assert report["score_blend"]["llm_weight"] == 0.3

    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_apply_calibration_prediction_rejects_cross_project_model(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_models,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "项目1", "calibrator_version_locked": "calib1"}
        ]
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib1",
                "deployed": True,
                "created_at": "2026-02-06T12:00:00Z",
                "train_filter": {"project_id": "p2"},
                "model_artifact": {"model_type": "offset", "bias": 10.0, "sigma": 2.0},
            }
        ]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1", "text": "test", "report": {"rule_total_score": 82.0}}
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1",
                "submission_id": "s1",
                "project_id": "p1",
                "rule_total_score": 82.0,
                "created_at": "2026-02-06T12:00:01Z",
            }
        ]

        resp = _client().post("/api/v1/projects/p1/calibration/predict")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["model_version"] is None
        assert data["updated_reports"] == 0
        assert data["updated_submissions"] == 0
        mock_save_reports.assert_not_called()
        mock_save_submissions.assert_not_called()


class TestSubmissionListWithLatest:
    @patch("app.main.load_score_reports")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_list_submissions_with_latest_report(
        self,
        mock_ensure,
        mock_load_submissions,
        mock_load_projects,
        mock_load_reports,
    ):
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "A公司",
                "total_score": 80,
                "report": {},
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "B公司",
                "total_score": 82,
                "report": {},
                "created_at": "2026-02-06T10:00:01Z",
            },
        ]
        mock_load_projects.return_value = [{"id": "p1", "expert_profile_id": "ep1"}]
        mock_load_reports.return_value = [
            {
                "id": "r1",
                "submission_id": "s1",
                "project_id": "p1",
                "rule_total_score": 80,
                "pred_total_score": 83,
                "suggestions": [{"expected_gain": 3.2}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "r2",
                "submission_id": "s2",
                "project_id": "p1",
                "rule_total_score": 82,
                "pred_total_score": 81,
                "suggestions": [{"expected_gain": 1.2}],
                "created_at": "2026-02-06T11:00:00Z",
            },
        ]

        resp = _client().get("/api/v1/projects/p1/submissions?with=latest_report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "p1"
        assert data["expert_profile_id"] == "ep1"
        assert len(data["submissions"]) == 2
        assert data["submissions"][0]["latest_report"]["rank_by_rule"] is not None


class TestDeltaAndSamplesEndpoints:
    @patch("app.main.save_delta_cases")
    @patch("app.main.load_delta_cases")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rebuild_delta_cases_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_reports,
        mock_load_qt,
        mock_load_delta,
        mock_save_delta,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_reports.return_value = [
            {
                "id": "r1",
                "submission_id": "s1",
                "project_id": "p1",
                "rule_total_score": 80,
                "created_at": "2026-02-06T10:00:00Z",
            }
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 78,
                "qt_reasons": [{"text": "工期控制不足"}],
                "created_at": "2026-02-06T11:00:00Z",
            }
        ]
        mock_load_delta.return_value = []
        resp = _client().post("/api/v1/projects/p1/delta_cases/rebuild")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["submission_id"] == "s1"
        mock_save_delta.assert_called_once()

    @patch("app.main.save_calibration_samples")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rebuild_calibration_samples_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_samples,
        mock_save_samples,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "project_type": "装修及景观项目", "bid_method": "AI评标"}
        ]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1", "text": "工期365天|横道图", "image_count": 2}
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1",
                "submission_id": "s1",
                "project_id": "p1",
                "rule_total_score": 80,
                "rule_dim_scores": {},
                "penalties": [],
                "meta": {
                    "material_utilization": {
                        "retrieval_hit_rate": 0.7,
                        "retrieval_file_coverage_rate": 0.5,
                        "consistency_hit_rate": 0.6,
                        "material_dimension_hit_rate": 0.4,
                        "available_types": ["drawing", "boq"],
                        "uncovered_types": ["drawing"],
                    },
                    "material_quality": {
                        "total_files": 5,
                        "total_parsed_chars": 9000,
                        "parse_fail_ratio": 0.2,
                    },
                    "material_utilization_gate": {"passed": True, "blocked": False},
                    "evidence_trace": {"mandatory_hit_rate": 0.8, "source_files_hit_count": 2},
                },
                "created_at": "2026-02-06T10:00:00Z",
            }
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 81,
                "created_at": "2026-02-06T11:00:00Z",
            }
        ]
        mock_load_samples.return_value = []
        resp = _client().post("/api/v1/projects/p1/calibration_samples/rebuild")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["submission_id"] == "s1"
        assert data[0]["x_features"]["project_type_decoration_landscape"] == 1.0
        assert data[0]["x_features"]["bid_method_ai_comprehensive_three_stage"] == 1.0
        assert data[0]["x_features"]["material_retrieval_hit_rate"] == 0.7
        assert data[0]["x_features"]["material_total_parsed_chars"] == 9000.0
        assert data[0]["x_features"]["material_gate_passed"] == 1.0
        assert data[0]["x_features"]["evidence_mandatory_hit_rate"] == 0.8
        mock_save_samples.assert_called_once()

    @patch("app.main.load_delta_cases")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_delta_cases_recovers_legacy_row_missing_required_fields(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_delta,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_delta.return_value = [
            {
                "project_id": "p1",
                "total_error": "5.5",
                "reason_alignment": ["工期控制不足"],
            }
        ]

        resp = _client().get("/api/v1/projects/p1/delta_cases")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == ""
        assert data[0]["project_id"] == "p1"
        assert data[0]["submission_id"] == ""
        assert data[0]["total_error"] == 5.5
        assert data[0]["reason_alignment"][0]["text"] == "工期控制不足"
        assert data[0]["created_at"]

    @patch("app.main.load_delta_cases")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_delta_cases_recovers_when_storage_corrupted(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_delta,
    ):
        from app.storage import StorageDataError

        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_delta.side_effect = StorageDataError(
            Path("/tmp/delta_cases.json"),
            "json_parse_failed",
            "数据文件 JSON 格式损坏：delta_cases.json（第 1 行，第 1 列），请使用历史版本回滚。",
        )

        resp = _client().get("/api/v1/projects/p1/delta_cases")

        assert resp.status_code == 200
        assert resp.json() == []

    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_calibration_samples_recovers_legacy_row_missing_required_fields(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_samples,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_samples.return_value = [
            {
                "project_id": "p1",
                "submission_id": "s1",
                "x_features": {"a": "1.5"},
            }
        ]

        resp = _client().get("/api/v1/projects/p1/calibration_samples")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == ""
        assert data[0]["project_id"] == "p1"
        assert data[0]["submission_id"] == "s1"
        assert data[0]["feature_schema_version"] == "v2"
        assert data[0]["x_features"]["a"] == 1.5

    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_calibration_samples_recovers_when_storage_corrupted(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_samples,
    ):
        from app.storage import StorageDataError

        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_samples.side_effect = StorageDataError(
            Path("/tmp/calibration_samples.json"),
            "json_parse_failed",
            "数据文件 JSON 格式损坏：calibration_samples.json（第 1 行，第 1 列），请使用历史版本回滚。",
        )

        resp = _client().get("/api/v1/projects/p1/calibration_samples")

        assert resp.status_code == 200
        assert resp.json() == []


class TestPatchPackageEndpoints:
    @patch("app.main.save_patch_packages")
    @patch("app.main.load_patch_packages")
    @patch("app.main.load_delta_cases")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_mine_patch_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_delta,
        mock_load_packages,
        mock_save_packages,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_delta.return_value = [
            {
                "id": "d1",
                "project_id": "p1",
                "total_error": 6.0,
                "miss_types": {"UNDER_PENALIZE": 1},
                "reason_alignment": [{"qt_reason_text": "工期控制不足"}],
            },
            {
                "id": "d2",
                "project_id": "p1",
                "total_error": -3.5,
                "miss_types": {"OVER_PENALIZE": 1},
                "reason_alignment": [{"qt_reason_text": "质量标准不一致"}],
            },
        ]
        mock_load_packages.return_value = []
        resp = _client().post(
            "/api/v1/projects/p1/patches/mine", json={"patch_type": "threshold", "top_k": 2}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "p1"
        assert data["patch_type"] == "threshold"
        mock_save_packages.assert_called_once()

    @patch("app.main.save_patch_packages")
    @patch("app.main.load_delta_cases")
    @patch("app.main.load_patch_packages")
    @patch("app.main.ensure_data_dirs")
    def test_shadow_eval_patch_success(
        self,
        mock_ensure,
        mock_load_packages,
        mock_load_delta,
        mock_save_packages,
    ):
        mock_load_packages.return_value = [
            {
                "id": "pck1",
                "project_id": "p1",
                "patch_payload": {"penalty_multiplier": {"P-EMPTY-002": 1.1}},
                "status": "candidate",
            }
        ]
        mock_load_delta.return_value = [
            {"project_id": "p1", "total_error": 5.0},
            {"project_id": "p1", "total_error": -2.0},
        ]
        resp = _client().post("/api/v1/patches/pck1/shadow_eval")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["patch_id"] == "pck1"
        mock_save_packages.assert_called_once()

    @patch("app.main.save_patch_deployments")
    @patch("app.main.load_patch_deployments")
    @patch("app.main.save_patch_packages")
    @patch("app.main.load_patch_packages")
    @patch("app.main.ensure_data_dirs")
    def test_patch_deploy_success(
        self,
        mock_ensure,
        mock_load_packages,
        mock_save_packages,
        mock_load_deploys,
        mock_save_deploys,
    ):
        mock_load_packages.return_value = [
            {
                "id": "pck1",
                "project_id": "p1",
                "status": "shadow_pass",
                "shadow_metrics": {"mae_before": 4.0, "mae_after": 3.5},
            },
            {"id": "pck0", "project_id": "p1", "status": "deployed"},
        ]
        mock_load_deploys.return_value = []
        resp = _client().post("/api/v1/patches/pck1/deploy", json={"action": "deploy"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["patch_id"] == "pck1"
        assert data["deployed"] is True
        mock_save_packages.assert_called_once()
        mock_save_deploys.assert_called_once()

    @patch("app.main.load_patch_packages")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_patches_recovers_legacy_row_missing_required_fields(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_packages,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_packages.return_value = [
            {
                "project_id": "p1",
                "status": "",
                "shadow_metrics": [],
            }
        ]

        resp = _client().get("/api/v1/projects/p1/patches")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == ""
        assert data[0]["project_id"] == "p1"
        assert data[0]["patch_type"] == "threshold"
        assert data[0]["patch_payload"] == {}
        assert data[0]["target_symptom"] == {}
        assert data[0]["status"] == "candidate"
        assert data[0]["shadow_metrics"] is None
        assert data[0]["created_at"]
        assert data[0]["updated_at"]


class TestGroundTruthAutoSync:
    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_triggers_sync(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_records,
        mock_save_records,
        mock_sync,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_records.return_value = []
        payload = {
            "shigong_text": "这是一个足够长的施组文本，用于测试自动同步逻辑。" * 5,
            "judge_scores": [85, 86, 87, 88, 89],
            "final_score": 87.0,
            "source": "青天大模型",
        }
        resp = _client().post("/api/v1/projects/p1/ground_truth", json=payload)
        assert resp.status_code == 200
        mock_sync.assert_called_once()

    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_submission_triggers_sync(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_records,
        mock_save_records,
        mock_sync,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "demo.txt",
                "text": "施组正文内容" * 30,
            }
        ]
        mock_load_records.return_value = []
        payload = {
            "submission_id": "s1",
            "judge_scores": [80, 81, 82, 83, 84],
            "final_score": 82.0,
            "source": "青天大模型",
        }
        resp = _client().post("/api/v1/projects/p1/ground_truth/from_submission", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "p1"
        assert data["source"] == "青天大模型"
        assert mock_save_records.call_count >= 2
        mock_sync.assert_called_once()

    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_submission_supports_7_judges(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_records,
        mock_save_records,
        mock_sync,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "demo.txt",
                "text": "施组正文内容" * 30,
            }
        ]
        mock_load_records.return_value = []
        payload = {
            "submission_id": "s1",
            "judge_scores": [80, 81, 82, 83, 84, 85, 86],
            "final_score": 82.0,
            "source": "青天大模型",
        }
        resp = _client().post("/api/v1/projects/p1/ground_truth/from_submission", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["judge_count"] == 7
        assert len(data["judge_scores"]) == 7
        assert mock_save_records.call_count >= 2
        mock_sync.assert_called_once()

    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_submission_rejects_invalid_judge_count(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_records,
        mock_save_records,
        mock_sync,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "demo.txt",
                "text": "施组正文内容" * 30,
            }
        ]
        mock_load_records.return_value = []
        payload = {
            "submission_id": "s1",
            "judge_scores": [80, 81, 82, 83, 84, 85],
            "final_score": 82.0,
            "source": "青天大模型",
        }
        resp = _client().post("/api/v1/projects/p1/ground_truth/from_submission", json=payload)
        assert resp.status_code == 422
        assert "5 或 7" in resp.json()["detail"]
        mock_save_records.assert_not_called()
        mock_sync.assert_not_called()

    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_compat_add_ground_truth_from_submission_triggers_sync(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_records,
        mock_save_records,
        mock_sync,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "demo.txt",
                "text": "施组正文内容" * 30,
            }
        ]
        mock_load_records.return_value = []
        payload = {
            "submission_id": "s1",
            "judge_scores": [80, 81, 82, 83, 84],
            "final_score": 82.0,
            "source": "青天大模型",
        }
        resp = _client().post("/api/projects/p1/ground_truth/from_submission", json=payload)
        assert resp.status_code == 200
        assert mock_save_records.call_count >= 2
        mock_sync.assert_called_once()

    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_file_triggers_sync(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_records,
        mock_save_records,
        mock_sync,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_records.return_value = []
        file_content = ("施组正文内容" * 30).encode("utf-8")
        resp = _client().post(
            "/api/v1/projects/p1/ground_truth/from_file",
            files={"file": ("gt.txt", file_content, "text/plain")},
            data={
                "judge_scores": "[80,81,82,83,84]",
                "final_score": "82",
                "source": "青天大模型",
            },
        )
        assert resp.status_code == 200
        mock_sync.assert_called_once()

    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_file_supports_7_judges(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_records,
        mock_save_records,
        mock_sync,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_records.return_value = []
        file_content = ("施组正文内容" * 30).encode("utf-8")
        resp = _client().post(
            "/api/v1/projects/p1/ground_truth/from_file",
            files={"file": ("gt.txt", file_content, "text/plain")},
            data={
                "judge_scores": "[80,81,82,83,84,85,86]",
                "final_score": "82",
                "source": "青天大模型",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["judge_count"] == 7
        mock_sync.assert_called_once()

    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_files_batch_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_records,
        mock_save_records,
        mock_sync,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_records.return_value = []
        file_content_a = ("施组正文内容A" * 30).encode("utf-8")
        file_content_b = ("施组正文内容B" * 30).encode("utf-8")
        resp = _client().post(
            "/api/v1/projects/p1/ground_truth/from_files",
            files=[
                ("files", ("a.txt", file_content_a, "text/plain")),
                ("files", ("b.txt", file_content_b, "text/plain")),
            ],
            data={
                "judge_scores": "[80,81,82,83,84]",
                "final_score": "82",
                "source": "青天大模型",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 2
        assert data["success_count"] == 2
        assert data["failed_count"] == 0
        assert mock_sync.call_count == 2
        assert mock_save_records.call_count >= 2

    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_files_batch_partial_failure(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_records,
        mock_save_records,
        mock_sync,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_records.return_value = []
        short_content = "太短".encode("utf-8")
        long_content = ("施组正文内容" * 30).encode("utf-8")
        resp = _client().post(
            "/api/v1/projects/p1/ground_truth/from_files",
            files=[
                ("files", ("short.txt", short_content, "text/plain")),
                ("files", ("ok.txt", long_content, "text/plain")),
            ],
            data={
                "judge_scores": "[80,81,82,83,84]",
                "final_score": "82",
                "source": "青天大模型",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 2
        assert data["success_count"] == 1
        assert data["failed_count"] == 1
        assert mock_sync.call_count == 1
        assert mock_save_records.call_count >= 2

    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_compat_add_ground_truth_from_files_batch_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_records,
        mock_save_records,
        mock_sync,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_records.return_value = []
        file_content = ("施组正文内容C" * 30).encode("utf-8")
        resp = _client().post(
            "/api/projects/p1/ground_truth/from_files",
            files=[("files", ("c.txt", file_content, "text/plain"))],
            data={
                "judge_scores": "[80,81,82,83,84]",
                "final_score": "82",
                "source": "青天大模型",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 1
        assert data["success_count"] == 1
        assert data["failed_count"] == 0
        assert mock_sync.call_count == 1
        assert mock_save_records.call_count >= 2


class TestAutoRunReflection:
    @patch("app.main.save_patch_deployments")
    @patch("app.main.load_patch_deployments")
    @patch("app.main.save_patch_packages")
    @patch("app.main.evaluate_patch_shadow")
    @patch("app.main.mine_patch_package")
    @patch("app.main.load_patch_packages")
    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.save_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.train_best_calibrator_auto")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_refresh,
        mock_load_delta,
        mock_load_samples,
        mock_train,
        mock_load_models,
        mock_save_models,
        mock_save_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
        mock_load_patches,
        mock_mine_patch,
        mock_eval_patch,
        mock_save_patches,
        mock_load_deploys,
        mock_save_deploys,
    ):
        mock_load_projects.return_value = [{"id": "p1", "calibrator_version_locked": "old"}]
        mock_load_delta.return_value = [{"id": "d1", "project_id": "p1", "total_error": 3.0}]
        mock_load_samples.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 80},
                "y_label": 81,
            },
            {
                "id": "s2",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 82},
                "y_label": 83,
            },
            {
                "id": "s3",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 78},
                "y_label": 79,
            },
            {
                "id": "s4",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 85},
                "y_label": 86,
            },
        ]
        mock_train.return_value = {
            "model_type": "offset",
            "feature_schema_version": "v2",
            "bias": 1.0,
            "sigma": 2.0,
            "metrics": {"mae": 1.2},
            "gate_passed": True,
        }
        mock_load_models.return_value = []
        mock_load_submissions.return_value = [
            {"id": "sb1", "project_id": "p1", "text": "abc", "report": {"rule_total_score": 80}}
        ]
        mock_load_reports.return_value = [
            {"id": "rp1", "project_id": "p1", "submission_id": "sb1", "rule_total_score": 80}
        ]
        mock_load_patches.return_value = []
        mock_mine_patch.return_value = {
            "id": "pck1",
            "project_id": "p1",
            "patch_type": "threshold",
            "patch_payload": {"penalty_multiplier": {"P-EMPTY-002": 1.1}},
            "target_symptom": {},
            "rollback_pointer": None,
            "status": "candidate",
            "shadow_metrics": None,
            "created_at": "2026-02-06T10:00:00Z",
            "updated_at": "2026-02-06T10:00:00Z",
        }
        mock_eval_patch.return_value = {
            "ok": True,
            "patch_id": "pck1",
            "gate_passed": True,
            "metrics_before_after": {"mae_before": 4.0, "mae_after": 3.2},
        }
        mock_load_deploys.return_value = []

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["project_id"] == "p1"
        assert data["calibrator_deployed"] is True
        assert data["calibrator_summary"]["model_type"] == "offset"
        assert data["calibrator_summary"]["gate_passed"] is True
        assert "mae" in data["calibrator_summary"]["cv_metrics"]
        assert data["calibrator_model_type"] == "offset"
        assert data["calibrator_gate_passed"] is True
        assert "mae" in data["calibrator_cv_metrics"]
        assert "mae" in data["calibrator_baseline_metrics"]
        assert data["calibrator_gate"]["passed"] is True
        assert isinstance(data["calibrator_auto_candidates"], list)
        assert data["patch_deployed"] is True

    @patch("app.main.save_patch_packages")
    @patch("app.main.load_patch_packages")
    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.save_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.calc_metrics")
    @patch("app.main.cross_validate_calibrator")
    @patch("app.main.train_best_calibrator_auto")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_gate_failed_not_deploy(
        self,
        mock_ensure,
        mock_load_projects,
        mock_refresh,
        mock_load_delta,
        mock_load_samples,
        mock_train,
        mock_cv,
        mock_calc_metrics,
        mock_load_models,
        mock_save_models,
        mock_save_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
        mock_load_patches,
        mock_save_patches,
    ):
        mock_load_projects.return_value = [{"id": "p1", "calibrator_version_locked": "old"}]
        mock_load_delta.return_value = []
        mock_load_samples.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 80},
                "y_label": 81,
            },
            {
                "id": "s2",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 82},
                "y_label": 83,
            },
            {
                "id": "s3",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 78},
                "y_label": 79,
            },
        ]
        mock_train.return_value = {
            "model_type": "offset",
            "feature_schema_version": "v2",
            "bias": 1.0,
            "sigma": 2.0,
            "metrics": {},
        }
        mock_cv.return_value = {
            "ok": True,
            "metrics": {"mae": 5.0, "rmse": 6.2, "spearman": 0.2},
            "mode": "kfold",
            "pred_count": 3,
        }
        mock_calc_metrics.return_value = {"mae": 3.0, "rmse": 4.0, "spearman": 0.6}
        mock_load_models.return_value = []
        mock_load_submissions.return_value = [
            {"id": "sb1", "project_id": "p1", "text": "abc", "report": {"rule_total_score": 80}}
        ]
        mock_load_reports.return_value = [
            {"id": "rp1", "project_id": "p1", "submission_id": "sb1", "rule_total_score": 80}
        ]
        mock_load_patches.return_value = []

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["calibrator_deployed"] is False
        assert data["calibrator_summary"]["gate_passed"] is False
        assert data["calibrator_gate_passed"] is False
        assert data["prediction_updated_reports"] == 0
        assert data["prediction_updated_submissions"] == 0
        assert data["patch_deployed"] is False

    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.save_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.calc_metrics")
    @patch("app.main.cross_validate_calibrator")
    @patch("app.main.train_best_calibrator_auto")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_deploys_when_clustered_scores_mae_improves_sharply(
        self,
        mock_ensure,
        mock_load_projects,
        mock_refresh,
        mock_load_delta,
        mock_load_samples,
        mock_train,
        mock_cv,
        mock_calc_metrics,
        mock_load_models,
        mock_save_models,
        mock_save_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_delta.return_value = []
        mock_load_samples.return_value = [
            {
                "id": f"s{i}",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": score},
                "y_label": label,
                "submission_id": f"sb{i}",
            }
            for i, (score, label) in enumerate(
                [
                    (12.47, 82.20),
                    (10.79, 77.97),
                    (17.54, 80.02),
                    (14.69, 77.90),
                    (17.98, 80.74),
                    (14.87, 82.03),
                    (11.14, 77.90),
                ],
                start=1,
            )
        ]
        mock_train.return_value = {
            "model_type": "isotonic1d",
            "feature_schema_version": "v2",
            "sigma": 2.0,
            "metrics": {},
        }
        mock_cv.return_value = {
            "ok": True,
            "metrics": {"mae": 1.6929, "rmse": 2.2589, "spearman": -0.1622},
            "mode": "loocv",
            "pred_count": 7,
        }
        mock_calc_metrics.return_value = {"mae": 65.6114, "rmse": 65.6624, "spearman": 0.3604}
        mock_load_models.return_value = []
        mock_load_submissions.return_value = [
            {
                "id": f"sb{i}",
                "project_id": "p1",
                "text": "abc",
                "report": {"rule_total_score": score, "score_scale_max": 100},
            }
            for i, score in enumerate([12.47, 10.79, 17.54, 14.69, 17.98, 14.87, 11.14], start=1)
        ]
        mock_load_reports.return_value = [
            {
                "id": f"rp{i}",
                "project_id": "p1",
                "submission_id": f"sb{i}",
                "rule_total_score": score,
                "score_scale_max": 100,
            }
            for i, score in enumerate([12.47, 10.79, 17.54, 14.69, 17.98, 14.87, 11.14], start=1)
        ]

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["calibrator_deployed"] is True
        assert data["calibrator_summary"]["gate_passed"] is True
        assert data["calibrator_gate_passed"] is True
        assert data["prediction_updated_reports"] == 7
        assert data["prediction_updated_submissions"] == 7
        assert data["calibrator_summary"]["gate"]["clustered_score_override"] is True
        assert data["calibrator_summary"]["gate"]["label_score_span"] == 4.3

    @patch("app.main._train_calibrator_with_gate")
    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.save_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_keeps_better_existing_project_calibrator(
        self,
        mock_ensure,
        mock_load_projects,
        mock_refresh,
        mock_load_delta,
        mock_load_samples,
        mock_load_models,
        mock_save_models,
        mock_save_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
        mock_train_with_gate,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "项目1", "calibrator_version_locked": "calib_best", "meta": {}}
        ]
        mock_load_delta.return_value = []
        mock_load_samples.return_value = [
            {
                "id": f"s{i}",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": score},
                "y_label": label,
                "submission_id": f"sb{i}",
            }
            for i, (score, label) in enumerate(
                [
                    (12.47, 82.20),
                    (10.79, 77.97),
                    (17.54, 80.02),
                    (14.69, 77.90),
                    (17.98, 80.74),
                    (14.87, 82.03),
                    (11.14, 77.90),
                ],
                start=1,
            )
        ]
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib_best",
                "model_type": "isotonic1d",
                "feature_schema_version": "v2",
                "train_filter": {"project_id": "p1"},
                "metrics": {"gate_passed": True, "cv_mae": 1.6957},
                "calibrator_summary": {
                    "calibrator_version": "calib_best",
                    "model_type": "isotonic1d",
                    "gate_passed": True,
                    "cv_metrics": {"mae": 1.6957, "rmse": 2.2589, "spearman": -0.1622},
                    "baseline_metrics": {"mae": 66.0186, "rmse": 66.0, "spearman": 0.3604},
                    "sample_count": 7,
                    "bootstrap_small_sample": False,
                    "deployment_mode": "cv_validated",
                    "auto_review": {},
                },
                "artifact_uri": "json://calibration_models/calib_best",
                "model_artifact": {"model_type": "isotonic1d"},
                "deployed": True,
                "created_at": "2026-03-29T15:29:58Z",
            }
        ]
        mock_load_submissions.return_value = []
        mock_load_reports.return_value = []
        mock_train_with_gate.return_value = {
            "model_artifact": {
                "model_type": "offset",
                "feature_schema_version": "v2",
                "metrics": {"cv_mae": 3.1529, "gate_passed": True},
                "gate_passed": True,
            },
            "selected_type": "offset",
            "gate_passed": True,
            "cv": {
                "ok": True,
                "metrics": {"mae": 3.1529, "rmse": 4.0, "spearman": 0.05},
                "mode": "loocv",
                "pred_count": 7,
            },
            "cv_metrics": {"mae": 3.1529, "rmse": 4.0, "spearman": 0.05},
            "baseline_metrics": {"mae": 66.0186, "rmse": 66.0, "spearman": 0.3604},
            "gate": {"passed": True, "clustered_score_override": True, "label_score_span": 4.3},
            "auto_candidates": [],
            "summary": {
                "model_type": "offset",
                "gate_passed": True,
                "cv_metrics": {"mae": 3.1529, "rmse": 4.0, "spearman": 0.05},
                "baseline_metrics": {"mae": 66.0186, "rmse": 66.0, "spearman": 0.3604},
                "gate": {"passed": True, "clustered_score_override": True, "label_score_span": 4.3},
                "auto_candidates": [],
                "sample_count": 7,
                "bootstrap_small_sample": False,
                "full_validation_min_samples": 3,
                "deployment_mode": "candidate_only",
                "auto_review": {},
            },
            "sample_count": 7,
            "bootstrap_small_sample": False,
        }

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["calibrator_deployed"] is False
        assert data["prediction_updated_reports"] == 0
        assert data["prediction_updated_submissions"] == 0
        assert data["calibrator_summary"]["deployment_mode"] == "candidate_only"
        assert data["calibrator_summary"]["auto_review"]["action"] == "keep_existing"
        assert (
            data["calibrator_summary"]["auto_review"]["reason"]
            == "existing_better_project_calibrator_kept"
        )
        mock_save_projects.assert_not_called()
        saved_models = mock_save_models.call_args[0][0]
        current = next(row for row in saved_models if row["calibrator_version"] == "calib_best")
        assert current["deployed"] is True

    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_without_enough_samples(
        self,
        mock_ensure,
        mock_load_projects,
        mock_refresh,
        mock_load_delta,
        mock_load_samples,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_delta.return_value = []
        mock_load_samples.return_value = []

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["calibrator_version"] is None
        assert data["calibrator_deployed"] is False
        assert data["calibrator_summary"]["sample_count"] == 0
        assert data["calibrator_summary"]["skipped_reason"] == "insufficient_samples"
        assert data["calibrator_model_type"] is None
        assert data["calibrator_gate_passed"] is None
        assert data["calibrator_cv_metrics"] == {}
        assert data["calibrator_baseline_metrics"] == {}
        assert data["calibrator_gate"] == {}
        assert data["calibrator_auto_candidates"] == []

    @patch("app.main.save_patch_packages")
    @patch("app.main.load_patch_packages")
    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.save_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_bootstraps_single_sample_calibrator(
        self,
        mock_ensure,
        mock_load_projects,
        mock_refresh,
        mock_load_delta,
        mock_load_samples,
        mock_load_models,
        mock_save_models,
        mock_save_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
        mock_load_patches,
        mock_save_patches,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_delta.return_value = []
        mock_load_samples.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 80},
                "y_label": 84,
            }
        ]
        mock_load_models.return_value = []
        mock_load_submissions.return_value = [
            {
                "id": "sb1",
                "project_id": "p1",
                "text": "abc",
                "report": {"rule_total_score": 80, "score_scale_max": 100},
            }
        ]
        mock_load_reports.return_value = [
            {
                "id": "rp1",
                "project_id": "p1",
                "submission_id": "sb1",
                "rule_total_score": 80,
                "score_scale_max": 100,
            }
        ]
        mock_load_patches.return_value = []

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["calibrator_deployed"] is True
        assert data["calibrator_model_type"] == "offset"
        assert data["calibrator_gate_passed"] is True
        assert data["calibrator_summary"]["sample_count"] == 1
        assert data["calibrator_summary"]["cv_metrics"]["mode"] == "bootstrap_in_sample"
        assert data["calibrator_summary"]["gate_passed"] is True
        assert data["calibrator_summary"]["bootstrap_small_sample"] is True
        assert data["calibrator_summary"]["deployment_mode"] == "bootstrap_auto_deploy"
        assert data["calibrator_summary"]["auto_review"]["action"] == "keep_with_monitoring"
        assert data["calibrator_summary"]["auto_review"]["reason"] == "no_comparable_rows"
        assert data["calibrator_auto_review"]["action"] == "keep_with_monitoring"
        assert data["patch_id"] is None

    @patch("app.main._build_governance_score_preview")
    @patch("app.main._build_governance_artifact_impacts")
    @patch("app.main.save_patch_packages")
    @patch("app.main.load_patch_packages")
    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.save_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_bootstrap_rolls_back_when_preview_worsens(
        self,
        mock_ensure,
        mock_load_projects,
        mock_refresh,
        mock_load_delta,
        mock_load_samples,
        mock_load_models,
        mock_save_models,
        mock_save_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
        mock_load_patches,
        mock_save_patches,
        mock_build_impacts,
        mock_build_preview,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_delta.return_value = []
        mock_load_samples.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": 80},
                "y_label": 84,
            }
        ]
        mock_load_models.return_value = []
        mock_load_submissions.return_value = []
        mock_load_reports.return_value = []
        mock_load_patches.return_value = []
        mock_build_impacts.return_value = []
        mock_build_preview.return_value = {
            "matched_submission_count": 1,
            "avg_abs_delta_stored": 4.0,
            "avg_abs_delta_preview": 5.2,
            "avg_abs_delta_improvement": -1.2,
            "improved_row_count": 0,
            "worsened_row_count": 1,
            "rows": [],
        }

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["calibrator_deployed"] is False
        assert data["prediction_updated_reports"] == 0
        assert data["prediction_updated_submissions"] == 0
        assert data["calibrator_summary"]["bootstrap_small_sample"] is True
        assert data["calibrator_summary"]["deployment_mode"] == "bootstrap_candidate_only"
        assert data["calibrator_summary"]["auto_review"]["checked"] is True
        assert data["calibrator_summary"]["auto_review"]["passed"] is False
        assert data["calibrator_summary"]["auto_review"]["action"] == "rollback"
        assert (
            data["calibrator_summary"]["auto_review"]["reason"]
            == "preview_worsened_beyond_tolerance"
        )
        assert data["calibrator_auto_review"]["action"] == "rollback"

    @patch("app.main._build_governance_score_preview")
    @patch("app.main._build_governance_artifact_impacts")
    @patch("app.main.save_patch_packages")
    @patch("app.main.load_patch_packages")
    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.save_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_full_validation_rolls_back_when_preview_worsens(
        self,
        mock_ensure,
        mock_load_projects,
        mock_refresh,
        mock_load_delta,
        mock_load_samples,
        mock_load_models,
        mock_save_models,
        mock_save_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
        mock_load_patches,
        mock_save_patches,
        mock_build_impacts,
        mock_build_preview,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_delta.return_value = []
        mock_load_samples.return_value = [
            {
                "id": f"s{i}",
                "project_id": "p1",
                "feature_schema_version": "v2",
                "x_features": {"rule_total_score": score},
                "y_label": label,
                "submission_id": f"sb{i}",
            }
            for i, (score, label) in enumerate(
                [
                    (12.37, 82.20),
                    (10.79, 77.97),
                    (17.54, 80.02),
                    (14.69, 77.90),
                    (17.98, 80.74),
                    (14.87, 82.03),
                    (11.14, 77.90),
                ],
                start=1,
            )
        ]
        mock_load_models.return_value = []
        mock_load_submissions.return_value = []
        mock_load_reports.return_value = []
        mock_load_patches.return_value = []
        mock_build_impacts.return_value = []
        mock_build_preview.return_value = {
            "matched_submission_count": 2,
            "avg_abs_delta_stored": 1.0,
            "avg_abs_delta_preview": 1.8,
            "avg_abs_delta_improvement": -0.8,
            "improved_row_count": 0,
            "worsened_row_count": 2,
            "rows": [],
        }

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["calibrator_deployed"] is False
        assert data["prediction_updated_reports"] == 0
        assert data["prediction_updated_submissions"] == 0
        assert data["calibrator_summary"]["bootstrap_small_sample"] is False
        assert data["calibrator_summary"]["deployment_mode"] == "candidate_only"
        assert data["calibrator_summary"]["auto_review"]["checked"] is True
        assert data["calibrator_summary"]["auto_review"]["passed"] is False
        assert data["calibrator_summary"]["auto_review"]["action"] == "rollback"
        assert (
            data["calibrator_summary"]["auto_review"]["reason"]
            == "preview_worsened_beyond_tolerance"
        )
        assert data["calibrator_summary"]["auto_review"]["review_mode"] == "deployment_preview"
        assert data["calibrator_auto_review"]["action"] == "rollback"

    @patch("app.main.apply_calibration_prediction")
    @patch("app.main.deploy_calibrator")
    @patch("app.main._build_governance_score_preview")
    @patch("app.main._build_evolution_health_report")
    @patch("app.main.save_patch_packages")
    @patch("app.main.load_patch_packages")
    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.save_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_rolls_back_degraded_current_calibrator_to_historical_candidate(
        self,
        mock_ensure,
        mock_load_projects,
        mock_refresh,
        mock_load_delta,
        mock_load_samples,
        mock_load_models,
        mock_save_models,
        mock_save_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
        mock_load_patches,
        mock_save_patches,
        mock_build_health,
        mock_build_preview,
        mock_deploy_calibrator,
        mock_apply_prediction,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_delta.return_value = []
        mock_load_samples.return_value = []
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib_current",
                "train_filter": {"project_id": "p1"},
                "calibrator_summary": {
                    "deployment_mode": "cv_validated",
                    "cv_metrics": {"mae": 1.8},
                },
                "metrics": {"cv_mae": 1.8, "gate_passed": True},
                "deployed": True,
            },
            {
                "calibrator_version": "calib_prev",
                "train_filter": {"project_id": "p1"},
                "calibrator_summary": {
                    "deployment_mode": "cv_validated",
                    "cv_metrics": {"mae": 1.1},
                },
                "metrics": {"cv_mae": 1.1},
                "deployed": False,
            },
        ]
        mock_load_submissions.return_value = []
        mock_load_reports.return_value = []
        mock_load_patches.return_value = []
        mock_build_health.side_effect = [
            {
                "summary": {
                    "current_calibrator_degraded": True,
                    "current_calibrator_version": "calib_current",
                    "current_calibrator_rollback_candidate_version": "calib_prev",
                }
            },
            {
                "summary": {
                    "current_calibrator_degraded": False,
                    "current_calibrator_version": "calib_prev",
                    "current_calibrator_recent_mae": 1.6,
                    "current_calibrator_recent_rule_mae": 4.0,
                    "current_calibrator_recent_mae_delta_vs_rule": -2.4,
                }
            },
            {
                "summary": {
                    "current_calibrator_degraded": False,
                    "current_calibrator_version": "calib_prev",
                    "current_calibrator_recent_mae": 1.6,
                    "current_calibrator_recent_rule_mae": 4.0,
                    "current_calibrator_recent_mae_delta_vs_rule": -2.4,
                }
            },
        ]
        mock_build_preview.return_value = {
            "matched_submission_count": 2,
            "avg_abs_delta_stored": 4.0,
            "avg_abs_delta_preview": 1.6,
            "avg_abs_delta_improvement": 2.4,
        }
        mock_apply_prediction.return_value = type(
            "Resp",
            (),
            {"updated_reports": 7, "updated_submissions": 7},
        )()

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        governance = data["calibrator_runtime_governance"]
        assert governance["checked"] is True
        assert governance["degraded_before"] is True
        assert governance["action"] == "rollback"
        assert governance["reason"] == "rollback_preview_improved"
        assert governance["rollback_candidate_version"] == "calib_prev"
        assert governance["matched_submission_count"] == 2
        assert governance["avg_abs_delta_stored"] == pytest.approx(4.0, abs=1e-6)
        assert governance["avg_abs_delta_preview"] == pytest.approx(1.6, abs=1e-6)
        assert governance["avg_abs_delta_improvement"] == pytest.approx(2.4, abs=1e-6)
        assert governance["updated_reports"] == 7
        assert governance["updated_submissions"] == 7
        assert governance["active_calibrator_version_after"] == "calib_prev"
        assert governance["degraded_after"] is False
        assert governance["recovered_after"] is True
        assert governance["mae_after"] == pytest.approx(1.6, abs=1e-6)
        assert governance["rule_mae_after"] == pytest.approx(4.0, abs=1e-6)
        assert governance["mae_delta_vs_rule_after"] == pytest.approx(-2.4, abs=1e-6)
        assert data["post_run_health_summary"]["current_calibrator_version"] == "calib_prev"
        assert data["post_run_health_summary"]["current_calibrator_degraded"] is False
        assert data["post_run_health_summary"]["current_calibrator_recent_mae"] == pytest.approx(
            1.6, abs=1e-6
        )
        assert data["post_run_health_summary"][
            "current_calibrator_recent_rule_mae"
        ] == pytest.approx(4.0, abs=1e-6)
        mock_deploy_calibrator.assert_called_once()
        deploy_payload = mock_deploy_calibrator.call_args.args[0]
        assert deploy_payload.calibrator_version == "calib_prev"
        assert deploy_payload.project_id == "p1"
        mock_apply_prediction.assert_called_once()

    @patch("app.main.apply_calibration_prediction")
    @patch("app.main.deploy_calibrator")
    @patch("app.main._build_governance_score_preview")
    @patch("app.main._build_evolution_health_report")
    @patch("app.main.save_patch_packages")
    @patch("app.main.load_patch_packages")
    @patch("app.main.save_submissions")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.save_projects")
    @patch("app.main.save_calibration_models")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.load_delta_cases")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_auto_run_reflection_keeps_degraded_current_calibrator_when_rollback_preview_not_improved(
        self,
        mock_ensure,
        mock_load_projects,
        mock_refresh,
        mock_load_delta,
        mock_load_samples,
        mock_load_models,
        mock_save_models,
        mock_save_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_save_reports,
        mock_save_submissions,
        mock_load_patches,
        mock_save_patches,
        mock_build_health,
        mock_build_preview,
        mock_deploy_calibrator,
        mock_apply_prediction,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_delta.return_value = []
        mock_load_samples.return_value = []
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib_current",
                "train_filter": {"project_id": "p1"},
                "calibrator_summary": {
                    "deployment_mode": "cv_validated",
                    "cv_metrics": {"mae": 1.8},
                },
                "metrics": {"cv_mae": 1.8, "gate_passed": True},
                "deployed": True,
            },
            {
                "calibrator_version": "calib_prev",
                "train_filter": {"project_id": "p1"},
                "calibrator_summary": {
                    "deployment_mode": "cv_validated",
                    "cv_metrics": {"mae": 1.1},
                },
                "metrics": {"cv_mae": 1.1},
                "deployed": False,
            },
        ]
        mock_load_submissions.return_value = []
        mock_load_reports.return_value = []
        mock_load_patches.return_value = []
        mock_build_health.side_effect = [
            {
                "summary": {
                    "current_calibrator_degraded": True,
                    "current_calibrator_version": "calib_current",
                    "current_calibrator_rollback_candidate_version": "calib_prev",
                }
            },
            {
                "summary": {
                    "current_calibrator_degraded": True,
                    "current_calibrator_version": "calib_current",
                    "current_calibrator_rollback_candidate_version": "calib_prev",
                    "current_calibrator_recent_mae": 4.05,
                    "current_calibrator_recent_rule_mae": 4.0,
                    "current_calibrator_recent_mae_delta_vs_rule": 0.05,
                }
            },
        ]
        mock_build_preview.return_value = {
            "matched_submission_count": 2,
            "avg_abs_delta_stored": 4.0,
            "avg_abs_delta_preview": 4.05,
            "avg_abs_delta_improvement": -0.05,
        }

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        governance = data["calibrator_runtime_governance"]
        assert governance["checked"] is True
        assert governance["degraded_before"] is True
        assert governance["action"] == "skip"
        assert governance["reason"] == "rollback_preview_not_improved"
        assert governance["rollback_candidate_version"] == "calib_prev"
        assert data["post_run_health_summary"]["current_calibrator_version"] == "calib_current"
        assert data["post_run_health_summary"]["current_calibrator_degraded"] is True
        mock_deploy_calibrator.assert_not_called()
        mock_apply_prediction.assert_not_called()


class TestScoringFactorsEndpoint:
    @patch("app.main.load_config")
    @patch("app.main.ensure_data_dirs")
    def test_scoring_factors_default(self, mock_ensure, mock_load_config):
        mock_load_config.return_value = type(
            "Cfg",
            (),
            {
                "rubric": {
                    "version": "v1",
                    "dimensions": {
                        "07": {
                            "max_score": 10,
                            "suggestion_threshold": 6,
                            "suggested_gain": 1.5,
                            "sub_items": [
                                {
                                    "id": "07-1",
                                    "name": "test",
                                    "weight": 2,
                                    "keywords": ["a"],
                                    "regex": [],
                                }
                            ],
                        }
                    },
                    "penalties": {
                        "empty_promises": {"deduct": 0.5, "max_deduct": 3.0},
                        "action_missing": {"deduct_per": 0.5, "max_deduct": 5.0},
                    },
                },
                "lexicon": {},
            },
        )()
        resp = _client().get("/api/v1/scoring/factors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["engine_version"] == "v2"
        assert data["dimension_count"] == 16
        assert len(data["penalty_rules"]) >= 5
        assert data["source"]["chapter_requirements"] == "default"
        assert data["capability_flags"]["chapter_content_completeness_required"] is True

    @patch("app.main._build_pending_feedback_scoring_points")
    @patch("app.main._build_project_adaptive_scoring_points")
    @patch("app.main.load_project_anchors")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_projects")
    @patch("app.main.load_config")
    @patch("app.main.ensure_data_dirs")
    def test_scoring_factors_project_specific(
        self,
        mock_ensure,
        mock_load_config,
        mock_load_projects,
        mock_load_evolution,
        mock_load_anchors,
        mock_build_adaptive_points,
        mock_build_pending_points,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_config.return_value = type(
            "Cfg",
            (),
            {"rubric": {"version": "v2", "dimensions": {}, "penalties": {}}, "lexicon": {}},
        )()
        mock_build_adaptive_points.return_value = {
            "summary": {
                "total_points": 2,
                "material_points": 1,
                "feedback_points": 1,
                "feature_points": 0,
                "compilation_points": 0,
            },
            "adaptive_scoring_points": [
                {
                    "source_label": "上传资料维度锚点",
                    "dimension_id": "14",
                    "dimension_name": "设计协调与深化",
                    "title": "资料维度约束：设计协调与深化需体现项目资料关键锚点",
                    "hint_preview": ["图纸会审", "节点深化"],
                    "weight": 1.12,
                }
            ],
        }
        mock_build_pending_points.return_value = {
            "summary": {"pending_sample_count": 1, "pending_point_count": 1},
            "pending_feedback_scoring_points": [
                {
                    "source_label": "待确认真实评标反馈",
                    "dimension_id": "07",
                    "dimension_name": "重难点与专项方案",
                    "title": "待确认反馈：重难点与专项方案需向真实高分表达靠拢",
                    "source_submission_filename": "样本A.pdf",
                    "guardrail_reason": "预测与真实总分偏差较大，待人工确认。",
                }
            ],
            "patch_bundle": {
                "section_count": 1,
                "insert_after_anchor_count": 1,
                "keyword_anchor_count": 0,
                "append_new_section_count": 0,
                "sections": [
                    {
                        "dimension_id": "07",
                        "dimension_name": "重难点与专项方案",
                        "operation_label": "在「第二章、工程重点难点及危大工程的保障体系与措施」后插入该小节",
                        "target": "第二章、工程重点难点及危大工程的保障体系与措施",
                        "section_title": "07-1 危大工程闭环控制表",
                        "section_paragraphs": ["样例正文"],
                        "copy_block": "07-1 危大工程闭环控制表\n样例正文",
                    }
                ],
                "copy_markdown": "# 待确认真实评标改写补丁包\n\n## 1. 07 重难点与专项方案",
            },
        }
        mock_load_evolution.return_value = {
            "p1": {
                "compilation_instructions": {
                    "required_sections": ["组织机构与岗位分工", "重难点及危大工程"],
                    "required_charts_images": ["组织架构图", "进度横道图"],
                    "mandatory_elements": ["控制参数", "责任岗位", "验收动作"],
                    "forbidden_patterns": ["空泛承诺"],
                }
            }
        }
        mock_load_anchors.return_value = [
            {"project_id": "p1", "anchor_key": "contract_duration_days"}
        ]
        resp = _client().get("/api/v1/scoring/factors?project_id=p1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "p1"
        assert data["source"]["chapter_requirements"] == "project_evolution"
        assert data["source"]["adaptive_scoring_points"] == "runtime_material_feedback"
        assert data["consistency_anchors"] == ["contract_duration_days"]
        assert data["adaptive_summary"]["total_points"] == 2
        assert data["adaptive_scoring_points"][0]["dimension_id"] == "14"
        assert data["pending_feedback_summary"]["pending_sample_count"] == 1
        assert data["pending_feedback_scoring_points"][0]["dimension_id"] == "07"
        assert data["pending_feedback_patch_bundle"]["section_count"] == 1
        assert data["capability_flags"]["organization_structure_required"] is True

    @patch("app.main.load_config")
    @patch("app.main.ensure_data_dirs")
    def test_scoring_factors_markdown(self, mock_ensure, mock_load_config):
        mock_load_config.return_value = type(
            "Cfg",
            (),
            {"rubric": {"version": "v1", "dimensions": {}, "penalties": {}}, "lexicon": {}},
        )()
        resp = _client().get("/api/v1/scoring/factors/markdown")
        assert resp.status_code == 200
        data = resp.json()
        assert "markdown" in data
        assert "评分体系总览" in data["markdown"]

    @patch("app.main.evaluate_project_variants")
    @patch("app.main._build_scoring_factors_overview")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_project_analysis_bundle(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_build_factors,
        mock_eval,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_submissions.return_value = []
        mock_load_reports.return_value = []
        mock_load_qt.return_value = []
        mock_build_factors.return_value = {
            "engine_version": "v2",
            "project_id": "p1",
            "dimension_count": 16,
            "dimensions": [],
            "penalty_rules": [],
            "lint_issue_codes": [],
            "consistency_anchors": [],
            "chapter_requirements": {
                "required_sections": [],
                "required_charts_images": [],
                "mandatory_elements": [],
                "forbidden_patterns": [],
            },
            "capability_flags": {},
            "source": {"rubric_version": "v1", "chapter_requirements": "default"},
            "updated_at": "2026-02-06T10:00:00Z",
        }
        mock_eval.return_value = {
            "project_id": "p1",
            "sample_count_qt": 0,
            "variants": {"v1": {}, "v2": {}, "current": {}, "v2_calib": {}},
            "acceptance": {},
            "phase1_closure_readiness": {
                "status": "ready",
                "status_label": "可封第一阶段",
                "passed_gate_count": 10,
                "failed_gate_count": 0,
                "failed_gates": [],
                "recommendation": "当前项目已满足第一阶段封关条件，可进入封关核查。",
            },
            "computed_at": "2026-02-06T10:00:00Z",
        }
        resp = _client().get("/api/v1/projects/p1/analysis_bundle")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "p1"
        assert "项目分析包" in data["markdown"]
        assert "评分体系总览" in data["markdown"]
        assert "第一阶段封关就绪度" in data["markdown"]
        assert "可封第一阶段" in data["markdown"]

    @patch("app.main.project_analysis_bundle")
    def test_project_analysis_bundle_markdown_file(self, mock_bundle):
        mock_bundle.return_value = {
            "project_id": "p1",
            "markdown": "# 项目分析包：项目1",
            "generated_at": "2026-02-06T10:00:00Z",
        }
        resp = _client().get("/api/v1/projects/p1/analysis_bundle.md")
        assert resp.status_code == 200
        assert "text/markdown" in (resp.headers.get("content-type") or "")
        assert "attachment" in (resp.headers.get("content-disposition") or "")
        assert "项目分析包" in resp.text


class TestSystemSelfCheckEndpoint:
    def test_system_self_check_success(self):
        resp = _client().get("/api/v1/system/self_check")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert "required_ok" in data
        assert "degraded" in data
        assert isinstance(data.get("checks"), dict)
        assert isinstance(data.get("summary"), dict)
        assert isinstance(data.get("items"), list)
        assert data["checks"]["health"] is True
        assert any(item.get("name") == "health" for item in data.get("items", []))

    def test_system_self_check_project_missing(self):
        resp = _client().get("/api/v1/system/self_check?project_id=missing_project_id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["project_id"] == "missing_project_id"
        items = data.get("items", [])
        target = next((x for x in items if x.get("name") == "project_exists"), None)
        assert target is not None
        assert target.get("ok") is False


class TestCompatRoutes:
    @patch("app.main.scoring_factors")
    def test_compat_scoring_factors_route(self, mock_factors):
        mock_factors.return_value = {
            "engine_version": "v2",
            "project_id": None,
            "dimension_count": 16,
            "dimensions": [],
            "penalty_rules": [],
            "lint_issue_codes": [],
            "consistency_anchors": [],
            "chapter_requirements": {},
            "capability_flags": {},
            "source": {},
            "updated_at": "2026-02-06T10:00:00Z",
        }
        resp = _client().get("/api/scoring/factors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["engine_version"] == "v2"

    @patch("app.main.scoring_factors_markdown")
    def test_compat_scoring_factors_markdown_route(self, mock_factors_md):
        mock_factors_md.return_value = {
            "project_id": None,
            "markdown": "# 评分体系总览",
        }
        resp = _client().get("/api/scoring/factors/markdown")
        assert resp.status_code == 200
        data = resp.json()
        assert "评分体系总览" in data["markdown"]

    @patch("app.main.project_analysis_bundle")
    def test_compat_project_analysis_bundle_route(self, mock_bundle):
        mock_bundle.return_value = {
            "project_id": "p1",
            "markdown": "# 项目分析包：项目1",
            "generated_at": "2026-02-06T10:00:00Z",
        }
        resp = _client().get("/api/projects/p1/analysis_bundle")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "p1"
        assert "项目分析包" in data["markdown"]

    @patch("app.main.project_analysis_bundle_markdown_file")
    def test_compat_project_analysis_bundle_markdown_file_route(self, mock_bundle_file):
        from fastapi.responses import Response

        mock_bundle_file.return_value = Response(
            content="# 项目分析包：项目1",
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="analysis_bundle_p1.md"'},
        )
        resp = _client().get("/api/projects/p1/analysis_bundle.md")
        assert resp.status_code == 200
        assert "text/markdown" in (resp.headers.get("content-type") or "")
        assert "项目分析包" in resp.text

    @patch("app.main.system_self_check")
    def test_compat_system_self_check_route(self, mock_check):
        mock_check.return_value = {
            "ok": True,
            "checked_at": "2026-02-06T10:00:00Z",
            "items": [{"name": "health", "ok": True, "detail": "service reachable"}],
        }
        resp = _client().get("/api/system/self_check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["items"][0]["name"] == "health"

    @patch("app.main.get_project_expert_profile")
    def test_compat_expert_profile_route(self, mock_get):
        mock_get.return_value = {
            "project": {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "region": "合肥",
                "expert_profile_id": "ep1",
                "qingtian_model_version": "qingtian-2026.02",
                "scoring_engine_version_locked": "v2",
                "calibrator_version_locked": "calib",
                "status": "scoring_preparation",
                "created_at": "2026-02-06T10:00:00Z",
                "updated_at": "2026-02-06T10:00:00Z",
            },
            "expert_profile": {
                "id": "ep1",
                "name": "默认",
                "weights_raw": {f"{i:02d}": 5 for i in range(1, 17)},
                "weights_norm": {f"{i:02d}": 1 / 16 for i in range(1, 17)},
                "norm_rule_version": "v1_m=0.5+a/10_norm=sum",
                "created_at": "2026-02-06T10:00:00Z",
                "updated_at": "2026-02-06T10:00:00Z",
            },
        }
        resp = _client().get("/api/projects/p1/expert-profile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project"]["id"] == "p1"
        assert data["expert_profile"]["id"] == "ep1"

    @patch("app.main.auto_run_reflection_pipeline")
    def test_compat_auto_run_route(self, mock_run):
        mock_run.return_value = {
            "ok": True,
            "project_id": "p1",
            "delta_cases": 3,
            "calibration_samples": 3,
            "calibrator_version": "calib1",
            "calibrator_deployed": True,
            "prediction_updated_reports": 3,
            "prediction_updated_submissions": 3,
            "patch_id": "pck1",
            "patch_gate_passed": True,
            "patch_deployed": True,
        }
        resp = _client().post("/api/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["project_id"] == "p1"

    @patch("app.main.get_project_evaluation")
    def test_compat_evaluation_route(self, mock_eval):
        mock_eval.return_value = {
            "project_id": "p1",
            "sample_count_qt": 3,
            "variants": {
                "v1": {"sample_count": 3, "mae": 5.1, "rmse": 6.2, "spearman": 0.52},
                "v2": {"sample_count": 3, "mae": 3.2, "rmse": 4.0, "spearman": 0.66},
                "v2_calib": {"sample_count": 3, "mae": 2.8, "rmse": 3.6, "spearman": 0.71},
            },
            "acceptance": {"mae_rmse_improved_vs_v1": True},
            "computed_at": "2026-02-06T10:00:00Z",
        }
        resp = _client().get("/api/projects/p1/evaluation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "p1"
        assert data["acceptance"]["mae_rmse_improved_vs_v1"] is True

    @patch("app.main.get_evaluation_summary")
    def test_compat_evaluation_summary_route(self, mock_summary):
        mock_summary.return_value = {
            "project_count": 1,
            "project_ids": ["p1"],
            "aggregate": {"v1": {}, "v2": {}, "v2_calib": {}},
            "acceptance_pass_count": {"mae_rmse_improved_vs_v1": 1},
            "computed_at": "2026-02-06T10:00:00Z",
        }
        resp = _client().get("/api/evaluation/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_count"] == 1


class TestEvaluationEndpoint:
    @patch("app.main._build_feedback_governance_report")
    @patch("app.main._build_evolution_health_report")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_project_evaluation_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_build_evolution_health,
        mock_build_feedback_governance,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1"},
            {"id": "s2", "project_id": "p1"},
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1v1",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 70,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r1v2",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 78,
                "pred_total_score": 80,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r2v1",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 75,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r2v2",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 82,
                "pred_total_score": 83,
                "created_at": "2026-02-06T10:01:00Z",
            },
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 81,
                "qt_reasons": [{"text": "工期冲突"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q2",
                "submission_id": "s2",
                "qt_total_score": 84,
                "qt_reasons": [{"text": "措施空泛"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
        ]
        mock_build_evolution_health.return_value = {
            "summary": {
                "matched_score_record_count": 2,
                "current_calibrator_version": "calib_auto_existing",
                "current_calibrator_degraded": False,
            },
            "drift": {"level": "low"},
        }
        mock_build_feedback_governance.return_value = {
            "summary": {
                "manual_confirmation_required": False,
                "few_shot_pending_review_count": 0,
            }
        }
        resp = _client().get("/api/v1/projects/p1/evaluation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "p1"
        assert data["variants"]["v1"]["sample_count"] == 2
        assert data["variants"]["v2"]["sample_count"] == 2
        assert data["variants"]["current"]["sample_count"] == 2
        assert data["variants"]["current"]["mae"] == pytest.approx(1.0, abs=1e-4)
        assert data["acceptance"]["current_mae_rmse_not_worse_than_v2"] is True
        assert data["acceptance"]["current_rank_corr_not_worse_vs_v2"] is True
        assert data["phase1_closure_readiness"]["status"] == "not_ready"
        assert data["phase1_closure_readiness"]["minimum_ground_truth_samples"] == 3
        assert "minimum_ground_truth_samples" in data["phase1_closure_readiness"]["failed_gates"]
        assert "matched_score_records" in data["phase1_closure_readiness"]["failed_gates"]

    @patch("app.main._build_feedback_governance_report")
    @patch("app.main._build_evolution_health_report")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_project_evaluation_reports_phase1_closure_ready_when_all_gates_pass(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_build_evolution_health,
        mock_build_feedback_governance,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1"},
            {"id": "s2", "project_id": "p1"},
            {"id": "s3", "project_id": "p1"},
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1v1",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 70,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r1v2",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 78,
                "pred_total_score": 81,
                "total_score": 81,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r2v1",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 74,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r2v2",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 79,
                "pred_total_score": 82,
                "total_score": 82,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r3v1",
                "submission_id": "s3",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 76,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r3v2",
                "submission_id": "s3",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 80,
                "pred_total_score": 83,
                "total_score": 83,
                "created_at": "2026-02-06T10:01:00Z",
            },
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 81,
                "qt_reasons": [{"text": "工期冲突"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q2",
                "submission_id": "s2",
                "qt_total_score": 82,
                "qt_reasons": [{"text": "措施空泛"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q3",
                "submission_id": "s3",
                "qt_total_score": 83,
                "qt_reasons": [{"text": "逻辑完整"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
        ]
        mock_build_evolution_health.return_value = {
            "summary": {
                "matched_score_record_count": 3,
                "current_calibrator_version": "calib_auto_existing",
                "current_calibrator_degraded": False,
            },
            "drift": {"level": "low"},
        }
        mock_build_feedback_governance.return_value = {
            "summary": {
                "manual_confirmation_required": False,
                "few_shot_pending_review_count": 0,
            }
        }

        resp = _client().get("/api/v1/projects/p1/evaluation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["variants"]["current"]["sample_count"] == 3
        assert data["acceptance"]["current_display_matches_qt"] is True
        assert data["phase1_closure_readiness"]["ready"] is True
        assert data["phase1_closure_readiness"]["status"] == "ready"
        assert data["phase1_closure_readiness"]["passed_gate_count"] == 10
        assert data["phase1_closure_readiness"]["failed_gate_count"] == 0
        assert data["phase1_closure_readiness"]["failed_gates"] == []

    @patch("app.main._build_feedback_governance_report")
    @patch("app.main._build_evolution_health_report")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_project_evaluation_excludes_exact_ground_truth_rows_from_v2_calib(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_build_evolution_health,
        mock_build_feedback_governance,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [{"id": "s1", "project_id": "p1"}]
        mock_load_reports.return_value = [
            {
                "id": "r1v1",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 70,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r1v2",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 78,
                "pred_total_score": 81,
                "score_blend": {"mode": "ground_truth_exact"},
                "meta": {"ground_truth_exact_match": True},
                "created_at": "2026-02-06T10:01:00Z",
            },
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 81,
                "qt_reasons": [{"text": "工期冲突"}],
                "created_at": "2026-02-06T11:00:00Z",
            }
        ]
        mock_build_evolution_health.return_value = {
            "summary": {
                "matched_score_record_count": 1,
                "current_calibrator_version": "calib_auto_existing",
                "current_calibrator_degraded": False,
            },
            "drift": {"level": "low"},
        }
        mock_build_feedback_governance.return_value = {
            "summary": {
                "manual_confirmation_required": False,
                "few_shot_pending_review_count": 0,
            }
        }

        resp = _client().get("/api/v1/projects/p1/evaluation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["variants"]["v2"]["sample_count"] == 1
        assert data["variants"]["current"]["sample_count"] == 1
        assert data["variants"]["current"]["mae"] == pytest.approx(0.0, abs=1e-6)
        assert data["variants"]["v2_calib"]["sample_count"] == 0
        assert data["acceptance"]["current_display_matches_qt"] is True
        assert data["phase1_closure_readiness"]["status"] == "not_ready"

    @patch("app.main.load_ground_truth")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_materials")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_evaluation_summary_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_materials,
        mock_load_calibration_models,
        mock_load_ground_truth,
    ):
        mock_load_projects.return_value = [{"id": "p1"}, {"id": "p2"}]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1"},
            {"id": "s2", "project_id": "p2"},
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1v1",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 70,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r1v2",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 78,
                "pred_total_score": 80,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r2v1",
                "submission_id": "s2",
                "project_id": "p2",
                "scoring_engine_version": "v1",
                "rule_total_score": 75,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r2v2",
                "submission_id": "s2",
                "project_id": "p2",
                "scoring_engine_version": "v2",
                "rule_total_score": 82,
                "pred_total_score": 83,
                "created_at": "2026-02-06T10:01:00Z",
            },
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 81,
                "qt_reasons": [{"text": "工期冲突"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q2",
                "submission_id": "s2",
                "qt_total_score": 84,
                "qt_reasons": [{"text": "措施空泛"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
        ]
        mock_load_materials.return_value = []
        mock_load_calibration_models.return_value = [
            {
                "calibrator_version": "calib_p1",
                "model_type": "isotonic1d",
                "train_filter": {"project_id": "p1"},
                "deployed": True,
            },
            {
                "calibrator_version": "calib_p2",
                "model_type": "isotonic1d",
                "train_filter": {"project_id": "p2"},
                "deployed": True,
            },
        ]
        mock_load_ground_truth.return_value = []
        resp = _client().get("/api/v1/evaluation/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_count"] == 2
        assert "aggregate" in data
        assert "current" in data["aggregate"]
        assert "v2_calib" in data["aggregate"]
        assert "current_display_matches_qt" in data["acceptance_pass_count"]
        assert data["total_closure_readiness"]["status"] == "not_ready"
        assert data["total_closure_readiness"]["evaluated_project_count"] == 2
        assert data["total_closure_readiness"]["ready_project_count"] == 0
        assert "minimum_ready_projects" in data["total_closure_readiness"]["failed_gates"]
        assert data["total_closure_readiness"]["next_priority_project_id"] == "p1"
        assert data["total_closure_readiness"]["next_priority_project_name"] == "p1"
        assert (
            "minimum_ground_truth_samples"
            in data["total_closure_readiness"]["next_priority_failed_gates"]
        )
        assert (
            data["total_closure_readiness"]["not_ready_project_summaries"][0]["project_id"] == "p1"
        )
        assert data["total_closure_readiness"]["candidate_project_count"] == 0
        assert data["total_closure_readiness"]["next_candidate_project_id"] is None
        assert data["total_closure_readiness"]["blocker_kind"] == "close_not_ready_project"
        assert data["total_closure_readiness"]["next_step_title"] == "优先收口项目“p1”"
        assert "优先收口下一优先项目" in data["total_closure_readiness"]["next_step_detail"]
        assert data["total_closure_readiness"]["next_step_entrypoint_key"] == "ground_truth"
        assert data["total_closure_readiness"]["next_step_action_label"] == "录入真实评标"
        assert (
            data["total_closure_readiness"]["next_step_entrypoint_label"]
            == "前往「5) 自我学习与进化」录入真实评标"
        )
        assert "补录真实评标" in data["total_closure_readiness"]["next_step_entrypoint_detail"]
        assert (
            data["total_closure_readiness"]["system_closure_path"][-1]
            == "重新执行跨项目汇总评估，确认系统总封关就绪度。"
        )

    @patch("app.main.load_ground_truth")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_materials")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_evaluation_summary_reports_total_closure_ready_when_two_projects_are_ready(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_materials,
        mock_load_calibration_models,
        mock_load_ground_truth,
    ):
        mock_load_projects.return_value = [{"id": "p1"}, {"id": "p2"}]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1"},
            {"id": "s2", "project_id": "p1"},
            {"id": "s3", "project_id": "p1"},
            {"id": "s4", "project_id": "p2"},
            {"id": "s5", "project_id": "p2"},
            {"id": "s6", "project_id": "p2"},
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1v1",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 70,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r1v2",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 78,
                "pred_total_score": 81,
                "total_score": 81,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r2v1",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 71,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r2v2",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 79,
                "pred_total_score": 82,
                "total_score": 82,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r3v1",
                "submission_id": "s3",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 72,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r3v2",
                "submission_id": "s3",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 80,
                "pred_total_score": 83,
                "total_score": 83,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r4v1",
                "submission_id": "s4",
                "project_id": "p2",
                "scoring_engine_version": "v1",
                "rule_total_score": 75,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r4v2",
                "submission_id": "s4",
                "project_id": "p2",
                "scoring_engine_version": "v2",
                "rule_total_score": 82,
                "pred_total_score": 84,
                "total_score": 84,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r5v1",
                "submission_id": "s5",
                "project_id": "p2",
                "scoring_engine_version": "v1",
                "rule_total_score": 76,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r5v2",
                "submission_id": "s5",
                "project_id": "p2",
                "scoring_engine_version": "v2",
                "rule_total_score": 83,
                "pred_total_score": 85,
                "total_score": 85,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r6v1",
                "submission_id": "s6",
                "project_id": "p2",
                "scoring_engine_version": "v1",
                "rule_total_score": 77,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r6v2",
                "submission_id": "s6",
                "project_id": "p2",
                "scoring_engine_version": "v2",
                "rule_total_score": 84,
                "pred_total_score": 86,
                "total_score": 86,
                "created_at": "2026-02-06T10:01:00Z",
            },
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 81,
                "qt_reasons": [{"text": "工期冲突"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q2",
                "submission_id": "s2",
                "qt_total_score": 82,
                "qt_reasons": [{"text": "措施空泛"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q3",
                "submission_id": "s3",
                "qt_total_score": 83,
                "qt_reasons": [{"text": "逻辑完整"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q4",
                "submission_id": "s4",
                "qt_total_score": 84,
                "qt_reasons": [{"text": "措施空泛"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q5",
                "submission_id": "s5",
                "qt_total_score": 85,
                "qt_reasons": [{"text": "逻辑完整"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q6",
                "submission_id": "s6",
                "qt_total_score": 86,
                "qt_reasons": [{"text": "措施完整"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
        ]
        mock_load_materials.return_value = []
        mock_load_calibration_models.return_value = [
            {
                "calibrator_version": "calib_p1",
                "model_type": "isotonic1d",
                "train_filter": {"project_id": "p1"},
                "deployed": True,
            },
            {
                "calibrator_version": "calib_p2",
                "model_type": "isotonic1d",
                "train_filter": {"project_id": "p2"},
                "deployed": True,
            },
        ]
        mock_load_ground_truth.return_value = []

        resp = _client().get("/api/v1/evaluation/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_closure_readiness"]["ready"] is True
        assert data["total_closure_readiness"]["status"] == "ready"
        assert data["total_closure_readiness"]["evaluated_project_count"] == 2
        assert data["total_closure_readiness"]["ready_project_count"] == 2
        assert data["total_closure_readiness"]["not_ready_project_count"] == 0
        assert data["total_closure_readiness"]["candidate_project_count"] == 0
        assert data["total_closure_readiness"]["next_priority_project_id"] is None
        assert data["total_closure_readiness"]["next_priority_failed_gates"] == []
        assert data["total_closure_readiness"]["next_candidate_project_id"] is None
        assert data["total_closure_readiness"]["blocker_kind"] == "ready"
        assert data["total_closure_readiness"]["next_step_title"] == "执行系统总封关核查"
        assert data["total_closure_readiness"]["next_step_entrypoint_key"] == "evaluation_summary"
        assert data["total_closure_readiness"]["next_step_action_label"] == "执行跨项目汇总评估"
        assert (
            data["total_closure_readiness"]["next_step_entrypoint_label"]
            == "前往「5) 自我学习与进化」点击“跨项目汇总评估”"
        )
        assert "总封关结论" in data["total_closure_readiness"]["next_step_entrypoint_detail"]
        assert data["total_closure_readiness"]["system_closure_path"][0] == "执行跨项目总封关核查。"

    @patch("app.main.load_ground_truth")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_materials")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_evaluation_summary_reports_candidate_project_when_second_evaluable_project_is_missing(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_materials,
        mock_load_calibration_models,
        mock_load_ground_truth,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "已封关项目"},
            {"id": "p2", "name": "候选项目"},
        ]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1"},
            {"id": "s2", "project_id": "p1"},
            {"id": "s3", "project_id": "p1"},
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1v1",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 70,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r1v2",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 78,
                "pred_total_score": 81,
                "total_score": 81,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r2v1",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 71,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r2v2",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 79,
                "pred_total_score": 82,
                "total_score": 82,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r3v1",
                "submission_id": "s3",
                "project_id": "p1",
                "scoring_engine_version": "v1",
                "rule_total_score": 72,
                "created_at": "2026-02-06T10:00:00Z",
            },
            {
                "id": "r3v2",
                "submission_id": "s3",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 80,
                "pred_total_score": 83,
                "total_score": 83,
                "created_at": "2026-02-06T10:01:00Z",
            },
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 81,
                "qt_reasons": [{"text": "工期冲突"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q2",
                "submission_id": "s2",
                "qt_total_score": 82,
                "qt_reasons": [{"text": "措施空泛"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q3",
                "submission_id": "s3",
                "qt_total_score": 83,
                "qt_reasons": [{"text": "逻辑完整"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
        ]
        mock_load_materials.return_value = [
            {"id": "m1", "project_id": "p2", "name": "招标文件.pdf"},
            {"id": "m2", "project_id": "p2", "name": "控制价.xlsx"},
        ]
        mock_load_calibration_models.return_value = [
            {
                "calibrator_version": "calib_p1",
                "model_type": "isotonic1d",
                "train_filter": {"project_id": "p1"},
                "deployed": True,
            }
        ]
        mock_load_ground_truth.return_value = []

        resp = _client().get("/api/v1/evaluation/summary")
        assert resp.status_code == 200
        data = resp.json()
        closure = data["total_closure_readiness"]
        assert closure["status"] == "not_ready"
        assert closure["evaluated_project_count"] == 1
        assert closure["ready_project_count"] == 1
        assert closure["candidate_project_count"] == 1
        assert closure["next_candidate_project_id"] == "p2"
        assert closure["next_candidate_project_name"] == "候选项目"
        assert closure["next_candidate_stage"] == "已有资料，待上传施组并进入评分链"
        assert "先补上传施组并执行评分" in closure["next_candidate_action_hint"]
        assert closure["blocker_kind"] == "advance_candidate_project"
        assert closure["next_step_title"] == "优先推进候选项目“候选项目”"
        assert "先补上传施组并执行评分" in closure["next_step_detail"]
        assert closure["next_step_entrypoint_key"] == "upload_shigong"
        assert closure["next_step_action_label"] == "上传施组"
        assert closure["next_step_entrypoint_label"] == "前往「4) 项目施组」上传施组"
        assert "上传至少 1 份施组" in closure["next_step_entrypoint_detail"]
        assert "候选项目" in closure["recommendation"]
        assert closure["system_closure_path"][0] == "在项目“候选项目”上传至少 1 份施组文件。"
        assert (
            closure["system_closure_path"][-1] == "重新执行跨项目汇总评估，确认系统总封关就绪度。"
        )

    @patch("app.main.load_ground_truth")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_materials")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_evaluation_summary_reports_need_new_project_when_no_candidate_exists(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_materials,
        mock_load_calibration_models,
        mock_load_ground_truth,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "已封关项目"}]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1"},
            {"id": "s2", "project_id": "p1"},
            {"id": "s3", "project_id": "p1"},
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1v2",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 78,
                "pred_total_score": 81,
                "total_score": 81,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r2v2",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 79,
                "pred_total_score": 82,
                "total_score": 82,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r3v2",
                "submission_id": "s3",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 80,
                "pred_total_score": 83,
                "total_score": 83,
                "created_at": "2026-02-06T10:01:00Z",
            },
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 81,
                "qt_reasons": [{"text": "工期冲突"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q2",
                "submission_id": "s2",
                "qt_total_score": 82,
                "qt_reasons": [{"text": "措施空泛"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q3",
                "submission_id": "s3",
                "qt_total_score": 83,
                "qt_reasons": [{"text": "逻辑完整"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
        ]
        mock_load_materials.return_value = []
        mock_load_calibration_models.return_value = [
            {
                "calibrator_version": "calib_p1",
                "model_type": "isotonic1d",
                "train_filter": {"project_id": "p1"},
                "deployed": True,
            }
        ]
        mock_load_ground_truth.return_value = []

        resp = _client().get("/api/v1/evaluation/summary")
        assert resp.status_code == 200
        data = resp.json()
        closure = data["total_closure_readiness"]
        assert closure["status"] == "not_ready"
        assert closure["evaluated_project_count"] == 1
        assert closure["ready_project_count"] == 1
        assert closure["candidate_project_count"] == 0
        assert closure["next_candidate_project_id"] is None
        assert closure["blocker_kind"] == "need_new_project"
        assert closure["next_step_title"] == "新增第二个真实样本项目"
        assert "当前没有任何已导入但未闭环的候选项目" in closure["next_step_detail"]
        assert closure["next_step_entrypoint_key"] == "create_project"
        assert closure["next_step_action_label"] == "创建第二个项目"
        assert closure["next_step_entrypoint_label"] == "前往「1) 创建项目」开始新项目"
        assert "新增第二个真实业务项目" in closure["next_step_entrypoint_detail"]
        assert closure["system_closure_path"][0] == "新增并建立至少 1 个真实业务项目。"

    @patch("app.main.load_ground_truth")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_materials")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_evaluation_summary_reports_empty_project_candidate_when_second_project_exists(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_materials,
        mock_load_calibration_models,
        mock_load_ground_truth,
    ):
        recent_project_timestamp = (
            (datetime.now(timezone.utc) - timedelta(hours=1))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        mock_load_projects.return_value = [
            {"id": "p1", "name": "已封关项目"},
            {
                "id": "p2",
                "name": "新建空项目",
                "created_at": recent_project_timestamp,
                "updated_at": recent_project_timestamp,
            },
        ]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1"},
            {"id": "s2", "project_id": "p1"},
            {"id": "s3", "project_id": "p1"},
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1v2",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 78,
                "pred_total_score": 81,
                "total_score": 81,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r2v2",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 79,
                "pred_total_score": 82,
                "total_score": 82,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r3v2",
                "submission_id": "s3",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 80,
                "pred_total_score": 83,
                "total_score": 83,
                "created_at": "2026-02-06T10:01:00Z",
            },
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 81,
                "qt_reasons": [{"text": "工期冲突"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q2",
                "submission_id": "s2",
                "qt_total_score": 82,
                "qt_reasons": [{"text": "措施空泛"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q3",
                "submission_id": "s3",
                "qt_total_score": 83,
                "qt_reasons": [{"text": "逻辑完整"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
        ]
        mock_load_materials.return_value = []
        mock_load_calibration_models.return_value = [
            {
                "calibrator_version": "calib_p1",
                "model_type": "isotonic1d",
                "train_filter": {"project_id": "p1"},
                "deployed": True,
            }
        ]
        mock_load_ground_truth.return_value = []

        resp = _client().get("/api/v1/evaluation/summary")
        assert resp.status_code == 200
        data = resp.json()
        closure = data["total_closure_readiness"]
        assert closure["status"] == "not_ready"
        assert closure["evaluated_project_count"] == 1
        assert closure["ready_project_count"] == 1
        assert closure["candidate_project_count"] == 1
        assert closure["next_candidate_project_id"] == "p2"
        assert closure["next_candidate_project_name"] == "新建空项目"
        assert closure["next_candidate_stage"] == "空项目，待上传资料"
        assert "先上传至少 1 份项目资料" in closure["next_candidate_action_hint"]
        assert closure["blocker_kind"] == "advance_candidate_project"
        assert closure["next_step_title"] == "优先推进候选项目“新建空项目”"
        assert closure["next_step_entrypoint_key"] == "upload_materials"
        assert closure["next_step_action_label"] == "上传资料"
        assert closure["next_step_entrypoint_label"] == "前往「3) 项目资料」上传资料"
        assert "先补上传至少 1 份项目资料" in closure["next_step_entrypoint_detail"]
        assert closure["system_closure_path"][0] == "在项目“新建空项目”上传至少 1 份项目资料。"

    @patch("app.main.load_ground_truth")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_materials")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.load_score_reports")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_evaluation_summary_ignores_stale_or_hidden_empty_projects_when_selecting_candidates(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_reports,
        mock_load_qt,
        mock_load_materials,
        mock_load_calibration_models,
        mock_load_ground_truth,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "已封关项目"},
            {
                "id": "p2",
                "name": "新建空项目",
                "created_at": "2026-03-30T08:00:00Z",
                "updated_at": "2026-03-30T08:00:00Z",
            },
            {
                "id": "p3",
                "name": "历史空项目",
                "created_at": "2026-03-26T08:00:00Z",
                "updated_at": "2026-03-26T08:00:00Z",
            },
            {
                "id": "p4",
                "name": "OPS_SMOKE_1773937481217",
                "created_at": "2026-03-30T08:30:00Z",
                "updated_at": "2026-03-30T08:30:00Z",
            },
        ]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1"},
            {"id": "s2", "project_id": "p1"},
            {"id": "s3", "project_id": "p1"},
        ]
        mock_load_reports.return_value = [
            {
                "id": "r1v2",
                "submission_id": "s1",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 78,
                "pred_total_score": 81,
                "total_score": 81,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r2v2",
                "submission_id": "s2",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 79,
                "pred_total_score": 82,
                "total_score": 82,
                "created_at": "2026-02-06T10:01:00Z",
            },
            {
                "id": "r3v2",
                "submission_id": "s3",
                "project_id": "p1",
                "scoring_engine_version": "v2",
                "rule_total_score": 80,
                "pred_total_score": 83,
                "total_score": 83,
                "created_at": "2026-02-06T10:01:00Z",
            },
        ]
        mock_load_qt.return_value = [
            {
                "id": "q1",
                "submission_id": "s1",
                "qt_total_score": 81,
                "qt_reasons": [{"text": "工期冲突"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q2",
                "submission_id": "s2",
                "qt_total_score": 82,
                "qt_reasons": [{"text": "措施空泛"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
            {
                "id": "q3",
                "submission_id": "s3",
                "qt_total_score": 83,
                "qt_reasons": [{"text": "逻辑完整"}],
                "created_at": "2026-02-06T11:00:00Z",
            },
        ]
        mock_load_materials.return_value = []
        mock_load_calibration_models.return_value = [
            {
                "calibrator_version": "calib_p1",
                "model_type": "isotonic1d",
                "train_filter": {"project_id": "p1"},
                "deployed": True,
            }
        ]
        mock_load_ground_truth.return_value = []

        with patch("app.main.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 3, 30, 9, 0, tzinfo=timezone.utc)
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            resp = _client().get("/api/v1/evaluation/summary")

        assert resp.status_code == 200
        data = resp.json()
        closure = data["total_closure_readiness"]
        assert closure["candidate_project_count"] == 1
        assert closure["next_candidate_project_id"] == "p2"
        assert closure["next_candidate_project_name"] == "新建空项目"
        assert closure["next_candidate_stage"] == "空项目，待上传资料"


class TestScoringMeceInjection:
    @patch("app.main._rebuild_project_anchors_and_requirements")
    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_material_triggers_constraint_sync(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_save_materials,
        mock_rebuild_constraints,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_materials.return_value = []
        mock_rebuild_constraints.return_value = ([{"id": "a1"}], [{"id": "r1"}])
        resp = _client().post(
            "/api/v1/projects/p1/materials",
            files=[("file", ("a.txt", b"hello", "text/plain"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["constraint_sync"]["rebuilt"] is False
        assert data["constraint_sync"]["mode"] == "async_parse_pending"
        assert data["parse_job"]["status"] == "queued"
        mock_rebuild_constraints.assert_not_called()
        mock_save_materials.assert_called_once()

    @patch("app.main._apply_prediction_to_report")
    @patch("app.main._apply_deployed_patch_to_report")
    @patch("app.main.score_text_v2")
    @patch("app.main.build_scoring_evidence_package")
    @patch("app.main._build_runtime_custom_requirements")
    @patch("app.main._constraints_need_rebuild")
    @patch("app.main._rebuild_project_anchors_and_requirements")
    @patch("app.main.load_project_requirements")
    @patch("app.main.load_project_anchors")
    @patch("app.main.load_materials")
    @patch("app.main.load_project_context")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_learning_profiles")
    def test_score_submission_includes_mece_input_snapshot(
        self,
        mock_load_learning_profiles,
        mock_load_evolution_reports,
        mock_load_project_context,
        mock_load_materials,
        mock_load_project_anchors,
        mock_load_project_requirements,
        mock_rebuild_constraints,
        mock_constraints_need_rebuild,
        mock_build_runtime_custom_requirements,
        mock_build_scoring_evidence_package,
        mock_score_text_v2,
        mock_apply_patch,
        mock_apply_predict,
    ):
        from app.main import _score_submission_for_project

        mock_load_learning_profiles.return_value = []
        mock_load_evolution_reports.return_value = {}
        mock_load_project_context.return_value = {
            "p1": {"text": "必须体现关键线路与验收闭环", "updated_at": "2026-02-19T00:00:00+00:00"}
        }
        mock_load_materials.return_value = [
            {"id": "m1", "project_id": "p1", "created_at": "2026-02-18T00:00:00+00:00"}
        ]
        mock_load_project_anchors.return_value = [
            {"id": "a_old", "project_id": "p1", "created_at": "2026-02-17T00:00:00+00:00"}
        ]
        mock_load_project_requirements.return_value = [
            {"id": "r_old", "project_id": "p1", "created_at": "2026-02-17T00:00:00+00:00"}
        ]
        mock_constraints_need_rebuild.return_value = True
        mock_rebuild_constraints.return_value = (
            [{"id": "a1", "project_id": "p1", "created_at": "2026-02-19T00:00:01+00:00"}],
            [
                {
                    "id": "r1",
                    "project_id": "p1",
                    "dimension_id": "01",
                    "req_label": "工期节点",
                    "req_type": "presence",
                    "patterns": {"keywords": ["工期", "节点"]},
                    "mandatory": True,
                    "weight": 1.0,
                    "created_at": "2026-02-19T00:00:01+00:00",
                }
            ],
        )
        mock_build_runtime_custom_requirements.return_value = (
            [
                {
                    "id": "runtime-1",
                    "project_id": "p1",
                    "dimension_id": "01",
                    "req_label": "自定义评分指令",
                    "req_type": "semantic",
                    "patterns": {"hints": ["闭环"]},
                    "mandatory": False,
                    "weight": 0.6,
                    "created_at": "2026-02-19T00:00:01+00:00",
                }
            ],
            {"runtime_custom_requirements": 1},
        )
        mock_build_scoring_evidence_package.return_value = {
            "base_units": [{"id": "base-1"}],
            "candidate_evidence": [
                {
                    "candidate_id": "cand-1",
                    "source_ref": "submission:s1",
                    "page_locator": "page:2",
                    "confidence": 0.84,
                    "model_version": "gpt-5.4",
                    "prompt_or_policy_version": "policy-v1",
                    "extraction_time": "2026-02-19T00:00:02+00:00",
                    "validator_status": "pending_validation",
                }
            ],
            "accepted_candidates": [
                {
                    "candidate_id": "cand-1",
                    "source_ref": "submission:s1",
                    "page_locator": "page:2",
                    "confidence": 0.84,
                    "model_version": "gpt-5.4",
                    "prompt_or_policy_version": "policy-v1",
                    "extraction_time": "2026-02-19T00:00:02+00:00",
                    "validator_status": "accepted",
                }
            ],
            "rejected_candidates": [],
            "accepted_units": [{"id": "accepted-1"}],
            "scoring_units": [{"id": "base-1"}, {"id": "accepted-1"}],
            "summary": {
                "mode": "openai",
                "provider": "openai",
                "available": True,
                "model_version": "gpt-5.4",
                "prompt_or_policy_version": "policy-v1",
                "fallback_reason": None,
                "base_unit_count": 1,
                "candidate_count": 1,
                "accepted_count": 1,
                "rejected_count": 0,
                "validator_breakdown": {"accepted": 1},
            },
        }
        mock_score_text_v2.return_value = {
            "engine_version": "v2",
            "rule_total_score": 81.2,
            "dim_total_80": 63.0,
            "dim_total_90": 70.9,
            "consistency_bonus": 2.0,
            "consistency_checks": [],
            "rule_dim_scores": {},
            "penalties": [],
            "lint_findings": [],
            "suggestions": [],
            "requirement_hits": [],
            "mandatory_req_hit_rate": 0.9,
            "requirement_pack_versions": ["v1"],
            "evidence_units_count": 0,
            "evidence_units": [],
        }

        project = {
            "id": "p1",
            "region": "合肥",
            "scoring_engine_version_locked": "v2",
            "meta": {"score_scale_max": 100},
        }
        config = SimpleNamespace(lexicon={})
        report, evidence = _score_submission_for_project(
            submission_id="s1",
            text="测试施组文本",
            project_id="p1",
            project=project,
            config=config,
            multipliers={},
            profile_snapshot=None,
            scoring_engine_version="v2",
        )
        assert evidence == []
        assert report["rule_total_score"] == 81.2
        injection = (report.get("meta") or {}).get("input_injection") or {}
        assert injection["constraints_rebuilt"] is True
        assert injection["runtime_custom_requirements_count"] == 1
        assert injection["material_type_counts"]["tender_qa"] == 1
        assert injection["mece_inputs"]["custom_instructions_injected"] is True
        assert injection["mece_inputs"]["bid_requirements_loaded"] is True
        # base requirements + runtime custom requirements 都应传入评分引擎
        assert mock_score_text_v2.call_args.kwargs["requirements"]
        assert len(mock_score_text_v2.call_args.kwargs["requirements"]) == 2
        assert mock_score_text_v2.call_args.kwargs["evidence_units"] == [
            {"id": "base-1"},
            {"id": "accepted-1"},
        ]
        model_boundary = (report.get("meta") or {}).get("model_boundary") or {}
        assert model_boundary["summary"]["accepted_count"] == 1
        assert model_boundary["scoring_gate"]["accepted_evidence_only"] is True
        assert model_boundary["scoring_gate"]["final_score_authority"] == "rules_only"
        mock_apply_patch.assert_called_once()
        mock_apply_predict.assert_called_once()


class TestEvolutionClosedLoop:
    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_submission_normalizes_5_scale_final_score(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_save_ground_truth,
        mock_sync_qt,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "项目1", "meta": {"score_scale_max": 5}}
        ]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "样本施组.pdf",
                "text": "这是足够长的施组正文。" * 20,
            }
        ]
        mock_load_ground_truth.return_value = []
        payload = {
            "submission_id": "s1",
            "judge_scores": [4.2, 4.3, 4.4, 4.1, 4.2],
            "final_score": 4.3,
            "source": "青天大模型",
        }
        resp = _client().post("/api/v1/projects/p1/ground_truth/from_submission", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["final_score"] == 4.3
        assert data["score_scale_max"] == 5
        assert data["final_score_100"] == 86.0
        assert data["judge_count"] == 5
        assert mock_sync_qt.called
        synced_record = mock_sync_qt.call_args.args[1]
        assert float(synced_record["final_score_100"]) == 86.0
        assert mock_save_ground_truth.call_count >= 2

    @patch("app.main.save_evolution_reports")
    @patch("app.main.load_evolution_reports")
    def test_sync_feedback_weights_to_evolution_persists_multipliers(
        self, mock_load_reports, mock_save_reports
    ):
        from app.main import _sync_feedback_weights_to_evolution

        mock_load_reports.return_value = {"p1": {"project_id": "p1", "sample_count": 3}}
        result = _sync_feedback_weights_to_evolution(
            "p1",
            {
                "updated": True,
                "new_dimension_multipliers": {"01": 1.1, "02": 0.9},
            },
        )
        assert result["synced"] is True
        payload = mock_save_reports.call_args.args[0]
        assert payload["p1"]["scoring_evolution"]["dimension_multipliers"]["01"] == 1.1
        assert payload["p1"]["scoring_evolution"]["dimension_multipliers"]["02"] == 0.9

    @patch("app.main._build_recent_feedback_dimension_boosts")
    @patch("app.main.load_evolution_reports")
    def test_build_runtime_feedback_requirements_absorb_evolution_guidance_hints(
        self,
        mock_load_reports,
        mock_recent_feedback_boosts,
    ):
        from app.main import _build_runtime_feedback_requirements

        mock_recent_feedback_boosts.return_value = {}
        mock_load_reports.return_value = {
            "p1": {
                "sample_count": 3,
                "high_score_logic": ["高分施组在重难点及危大工程上强调专项方案、监测与验收闭环。"],
                "writing_guidance": [
                    "重难点及危大工程章节应补齐专项方案论证、监测频次和验收签认。"
                ],
                "compilation_instructions": {
                    "high_score_summary": ["危大工程专项方案与监测闭环是高分共性。"],
                    "guidance_items": ["危大工程需写清专项方案、监测频次、责任岗位、验收动作。"],
                },
                "scoring_evolution": {
                    "dimension_multipliers": {"07": 1.18},
                    "rationale": {"07": "危大工程维度与真实评分更相关"},
                },
            }
        }

        out = _build_runtime_feedback_requirements(
            "p1",
            material_knowledge_profile={
                "by_dimension": [
                    {
                        "dimension_id": "07",
                        "coverage_score": 0.44,
                        "suggested_keywords": ["专项方案", "监测", "验收闭环"],
                    }
                ]
            },
        )

        assert out
        row = next(item for item in out if str(item.get("dimension_id") or "") == "07")
        patterns = row["patterns"]
        assert patterns["guidance_lines"]
        assert any("专项方案" in str(item) for item in patterns["guidance_lines"])
        assert any("监测" in str(item) for item in patterns["hints"])

    @patch("app.main.load_submissions")
    @patch("app.main.load_ground_truth")
    def test_build_pending_feedback_scoring_points_from_guardrail_blocked_samples(
        self,
        mock_load_ground_truth,
        mock_load_submissions,
    ):
        from app.main import _build_pending_feedback_scoring_points

        mock_load_ground_truth.return_value = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "source_submission_id": "sub-1",
                "source_submission_filename": "样本施组.pdf",
                "feedback_guardrail": {
                    "blocked": True,
                    "manual_review_status": "pending",
                    "warning_message": "预测与真实总分偏差较大，待人工确认。",
                },
                "feature_confidence_update": {"updated": 0, "reason": "guardrail_blocked"},
                "few_shot_distillation": {"captured": 0, "reason": "guardrail_blocked"},
                "qualitative_tags_by_judge": [["专项方案完整", "监测频次明确"]],
            }
        ]
        mock_load_submissions.return_value = [
            {
                "id": "sub-1",
                "project_id": "p1",
                "filename": "样本施组.pdf",
                "text": "\n".join(
                    [
                        "第一章、针对工程项目整体理解",
                        "第二章、工程重点难点及危大工程的保障体系与措施",
                        "第三章、拟采用的新技术、新工艺",
                        "既有内容：目前仅写原则性说明，未写参数与验收动作。",
                        "第五章、确保人、材、机的保障体系与措施",
                    ]
                ),
                "report": {
                    "dimension_scores": {
                        "07": {
                            "score": 8.9,
                            "evidence": [
                                {
                                    "anchor_label": "危大工程",
                                    "quote": "专项方案、监测频次、验收签认闭环完整。",
                                }
                            ],
                        }
                    },
                    "suggestions": [
                        {
                            "dimension_id": "05",
                            "title": "提升维度05分数",
                            "expected_gain": 12.55,
                            "action_steps": ["补齐监测频次与验收签认动作。"],
                            "evidence_to_add": ["责任岗位", "验收动作"],
                        }
                    ],
                },
            }
        ]

        payload = _build_pending_feedback_scoring_points("p1")

        assert payload["summary"]["pending_sample_count"] == 1
        assert payload["summary"]["pending_point_count"] >= 1
        row = next(
            item
            for item in payload["pending_feedback_scoring_points"]
            if str(item.get("dimension_id") or "") == "05"
        )
        assert row["source_label"] == "待确认真实评标反馈"
        assert "真实高分表达" in row["title"]
        assert row["source_submission_filename"] == "样本施组.pdf"
        assert row["recommended_section"] == "第05章 四新技术的应用与实施方案"
        assert row["recommended_subsection"] == "05-1 四新技术的应用与实施清单"
        assert row["current_submission_anchor"] == "第三章、拟采用的新技术、新工艺"
        assert "参数化闭环表达" in row["gap_summary"]
        assert "第三章、拟采用的新技术、新工艺" in row["insertion_hint"]
        assert "既有内容" in row["current_submission_excerpt"]
        assert row["action_steps"]
        assert row["evidence_to_add"]
        assert "建议在" in row["rewrite_template"]
        assert row["rewrite_sentences"]
        assert row["insertable_paragraphs"]
        assert row["draft_section_title"] == "05-1 四新技术的应用与实施清单"
        assert row["draft_section_paragraphs"]
        assert any(
            "第三章、拟采用的新技术、新工艺" in str(item)
            for item in row["draft_section_paragraphs"]
        )
        assert row["auto_rewrite_operation"] == "insert_after_anchor"
        assert (
            row["auto_rewrite_operation_label"]
            == "在「第三章、拟采用的新技术、新工艺」后插入该小节"
        )
        assert row["auto_rewrite_target"] == "第三章、拟采用的新技术、新工艺"
        assert "既有内容" in row["auto_rewrite_before_excerpt"]
        assert row["auto_rewrite_section_title"] == "05-1 四新技术的应用与实施清单"
        assert row["auto_rewrite_section_paragraphs"]
        assert all("建议" not in str(item) for item in row["auto_rewrite_section_paragraphs"])
        assert any("四新技术" in str(item) for item in row["auto_rewrite_section_paragraphs"])
        bundle = payload["patch_bundle"]
        assert bundle["section_count"] >= 1
        assert bundle["keyword_anchor_count"] >= 0
        assert bundle["sections"]
        assert bundle["sections"][0]["copy_block"]
        assert "待确认真实评标改写补丁包" in bundle["copy_markdown"]
        assert all("建议" not in str(item) for item in row["insertable_paragraphs"])
        assert any("四新技术" in str(item) for item in row["rewrite_sentences"])
        assert any("四新技术" in str(item) for item in row["insertable_paragraphs"])
        assert any("监测频次" in str(item) for item in row["hint_preview"])

    def test_pending_feedback_section_mapping_prefers_doc_aligned_chapters(self):
        from app.main import (
            _resolve_pending_feedback_recommended_section,
            _resolve_pending_feedback_recommended_subsection,
        )

        chapter_requirements = {"required_sections": [], "mandatory_elements": []}

        assert (
            _resolve_pending_feedback_recommended_section(
                chapter_requirements=chapter_requirements,
                dimension_id="10",
                dimension_name="重点专项工程控制",
            )
            == "第10章 重点专项工程控制"
        )
        assert (
            _resolve_pending_feedback_recommended_subsection(
                dimension_id="10",
                dimension_name="重点专项工程控制",
            )
            == "10-1 专项工程控制目录表"
        )
        assert (
            _resolve_pending_feedback_recommended_section(
                chapter_requirements=chapter_requirements,
                dimension_id="15",
                dimension_name="总体资源配置与实施计划",
            )
            == "第15章 总体资源配置与实施计划"
        )
        assert (
            _resolve_pending_feedback_recommended_subsection(
                dimension_id="15",
                dimension_name="总体资源配置与实施计划",
            )
            == "15-1 资源风险与调配控制表"
        )

    def test_pending_feedback_insertion_context_falls_back_to_keyword_anchor(self):
        from app.main import _build_pending_feedback_insertion_context

        payload = _build_pending_feedback_insertion_context(
            dimension_id="15",
            dimension_name="总体资源配置与实施计划",
            submission_text=(
                "项目总体安排中强调资源配置与动态调配，现场材料和机械按施工窗口滚动补充，"
                "但尚未写成参数、频次、责任和验收闭环。"
            ),
            recommended_section="第15章 总体资源配置与实施计划",
            recommended_subsection="15-1 资源风险与调配控制表",
        )

        assert payload["current_submission_anchor"] == "关键词命中：资源配置"
        assert "资源配置" in payload["gap_summary"]
        assert "15-1 资源风险与调配控制表" in payload["insertion_hint"]
        assert "资源配置与动态调配" in payload["current_submission_excerpt"]

    @patch("app.main.save_evolution_reports")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.enhance_evolution_report_with_llm")
    @patch("app.main.build_evolution_report")
    @patch("app.main.load_project_context")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main._build_evolution_readiness")
    def test_evolve_project_uses_normalized_ground_truth_scores(
        self,
        mock_build_evolution_readiness,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_load_context,
        mock_build_report,
        mock_enhance,
        mock_load_reports,
        mock_save_reports,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_build_evolution_readiness.return_value = {
            "project_id": "p1",
            "ready": True,
            "issues": [],
            "submissions": {"total": 1, "non_empty": 1, "scored": 1},
        }
        mock_load_ground_truth.return_value = [
            {
                "id": "g1",
                "project_id": "p1",
                "shigong_text": "A" * 200,
                "judge_scores": [4.0, 4.1, 4.2, 4.3, 4.4],
                "final_score": 4.0,
                "score_scale_max": 5,
            }
        ]
        mock_load_context.return_value = {}
        mock_build_report.return_value = {
            "project_id": "p1",
            "sample_count": 1,
            "high_score_logic": ["x"],
            "writing_guidance": ["y"],
            "scoring_evolution": {"dimension_multipliers": {}},
            "compilation_instructions": {},
            "updated_at": "2026-02-19T00:00:00+00:00",
        }
        mock_enhance.return_value = None
        mock_load_reports.return_value = {}

        resp = _client().post("/api/v1/projects/p1/evolve")
        assert resp.status_code == 200
        args = mock_build_report.call_args.args
        used_records = args[1]
        assert len(used_records) == 1
        # 4.0/5 => 80.0，进化学习统一按 100 分口径计算
        assert float(used_records[0]["final_score"]) == 80.0
