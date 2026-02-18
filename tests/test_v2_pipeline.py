"""Tests for V2 pipeline endpoints (report summary, qingtian, calibrator)."""

from __future__ import annotations

from unittest.mock import patch

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


class TestCalibratorEndpoints:
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
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [{"id": "s1", "project_id": "p1", "text": "工期365天"}]
        mock_load_reports.return_value = [
            {
                "id": "r1",
                "submission_id": "s1",
                "project_id": "p1",
                "rule_total_score": 80,
                "rule_dim_scores": {},
                "penalties": [],
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
        mock_save_samples.assert_called_once()


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
        mock_save_records.assert_called_once()
        mock_sync.assert_called_once()

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
        mock_save_records.assert_called_once()
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
        mock_save_records.assert_called_once()

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
        mock_save_records.assert_called_once()

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
        mock_save_records.assert_called_once()


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
        mock_load_projects.return_value = [{"id": "p1"}]
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
        ]

        resp = _client().post("/api/v1/projects/p1/reflection/auto_run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["calibrator_version"] is None
        assert data["calibrator_deployed"] is False
        assert data["calibrator_summary"]["sample_count"] == 2
        assert data["calibrator_summary"]["skipped_reason"] == "insufficient_samples"
        assert data["calibrator_model_type"] is None
        assert data["calibrator_gate_passed"] is None
        assert data["calibrator_cv_metrics"] == {}
        assert data["calibrator_baseline_metrics"] == {}
        assert data["calibrator_gate"] == {}
        assert data["calibrator_auto_candidates"] == []


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
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_config.return_value = type(
            "Cfg",
            (),
            {"rubric": {"version": "v2", "dimensions": {}, "penalties": {}}, "lexicon": {}},
        )()
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
        assert data["consistency_anchors"] == ["contract_duration_days"]
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
            "variants": {"v1": {}, "v2": {}, "v2_calib": {}},
            "acceptance": {},
            "computed_at": "2026-02-06T10:00:00Z",
        }
        resp = _client().get("/api/v1/projects/p1/analysis_bundle")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "p1"
        assert "项目分析包" in data["markdown"]
        assert "评分体系总览" in data["markdown"]

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
        assert isinstance(data.get("items"), list)
        assert any(item.get("name") == "health" for item in data.get("items", []))

    def test_system_self_check_project_missing(self):
        resp = _client().get("/api/v1/system/self_check?project_id=missing_project_id")
        assert resp.status_code == 200
        data = resp.json()
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
        resp = _client().get("/api/v1/projects/p1/evaluation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "p1"
        assert data["variants"]["v1"]["sample_count"] == 2
        assert data["variants"]["v2"]["sample_count"] == 2

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
        resp = _client().get("/api/v1/evaluation/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_count"] == 2
        assert "aggregate" in data
        assert "v2_calib" in data["aggregate"]
