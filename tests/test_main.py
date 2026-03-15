"""Tests for app/main.py FastAPI endpoints."""

from __future__ import annotations

import copy
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import _build_score_self_awareness, app, create_app


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestCreateApp:
    """Tests for create_app factory function."""

    def test_create_app_returns_fastapi(self):
        """create_app should return the FastAPI app instance."""
        result = create_app()
        assert result is app


class TestAppLifespan:
    @patch("app.main._stop_material_parse_worker")
    @patch("app.main._start_material_parse_worker")
    def test_app_lifespan_starts_and_stops_parse_worker(
        self,
        mock_start_worker,
        mock_stop_worker,
    ):
        with TestClient(app):
            pass

        mock_start_worker.assert_called_once()
        mock_stop_worker.assert_called_once()


class TestStorageErrorHandling:
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_storage_data_error_returns_readable_json(
        self,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        from app.storage import StorageDataError

        mock_load_projects.side_effect = StorageDataError(
            Path("/tmp/projects.json"),
            "json_parse_failed",
            "数据文件 JSON 格式损坏：projects.json（第 1 行，第 3 列），请使用历史版本回滚。",
        )

        response = client.get("/api/v1/projects")

        assert response.status_code == 500
        data = response.json()
        assert data["error_code"] == "json_parse_failed"
        assert data["file"] == "projects.json"
        assert "历史版本回滚" in data["detail"]


class TestVersionedJsonHistoryRoutes:
    @patch("app.main.list_json_versions")
    @patch("app.main.ensure_data_dirs")
    def test_list_versioned_json_history(
        self,
        mock_ensure,
        mock_list_versions,
        client,
    ):
        mock_list_versions.return_value = [
            {
                "version_id": "20260314T010203000000Z",
                "filename": "expert_profiles_v20260314T010203000000Z.json",
                "created_at": "2026-03-14T01:02:03+00:00",
                "size_bytes": 128,
            }
        ]

        response = client.get("/api/v1/ops/versioned-json/expert_profiles")

        assert response.status_code == 200
        data = response.json()
        assert data["artifact"] == "expert_profiles"
        assert data["versions"][0]["version_id"] == "20260314T010203000000Z"

    @patch("app.main.restore_json_version")
    @patch("app.main.ensure_data_dirs")
    def test_rollback_versioned_json_history(
        self,
        mock_ensure,
        mock_restore_json_version,
        client,
    ):
        mock_restore_json_version.return_value = {
            "version_id": "20260314T010203000000Z",
            "restored_at": "2026-03-14T02:03:04+00:00",
            "backup_version_id": "20260314T020304999999Z",
        }

        response = client.post(
            "/api/v1/ops/versioned-json/calibration_models/rollback",
            json={"version_id": "20260314T010203000000Z"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["artifact"] == "calibration_models"
        assert data["restored_version_id"] == "20260314T010203000000Z"
        assert data["backup_version_id"] == "20260314T020304999999Z"


class TestMaterialParseWorkerLifecycle:
    @patch("app.main.logger.warning")
    @patch("app.main.threading.Thread")
    def test_stop_timeout_keeps_worker_reference_to_avoid_duplicate_worker(
        self,
        mock_thread_cls,
        mock_logger_warning,
    ):
        from app import main as main_module

        worker = MagicMock()
        worker.is_alive.side_effect = [True, True, True]
        original_worker = main_module._MATERIAL_PARSE_WORKER
        original_event_state = main_module._MATERIAL_PARSE_STOP_EVENT.is_set()
        main_module._MATERIAL_PARSE_WORKER = worker
        try:
            main_module._stop_material_parse_worker()
            assert main_module._MATERIAL_PARSE_WORKER is worker

            main_module._start_material_parse_worker()

            worker.join.assert_called_once_with(timeout=2.0)
            mock_thread_cls.assert_not_called()
            mock_logger_warning.assert_called_once()
            assert main_module._MATERIAL_PARSE_WORKER is worker
        finally:
            main_module._MATERIAL_PARSE_WORKER = original_worker
            if original_event_state:
                main_module._MATERIAL_PARSE_STOP_EVENT.set()
            else:
                main_module._MATERIAL_PARSE_STOP_EVENT.clear()


class TestSecureDesktopExportBlock:
    def test_scoring_factors_markdown_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/scoring/factors/markdown")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_project_analysis_bundle_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/analysis_bundle")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_material_depth_report_markdown_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/materials/depth_report/markdown")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]

    def test_submission_evidence_trace_download_returns_403_in_secure_mode(self, client):
        with patch("app.runtime_security.is_secure_desktop_mode_enabled", return_value=True):
            response = client.get("/api/v1/projects/p1/submissions/s1/evidence_trace.md")

        assert response.status_code == 403
        assert "保密模式已启用" in response.json()["detail"]


class TestScoreSelfAwareness:
    def test_build_score_self_awareness_high_when_evidence_and_dimension_support_are_strong(
        self,
    ):
        report = {
            "pred_confidence": {
                "fused_sigma": 1.2,
                "fused_ci95_lower": 84.0,
                "fused_ci95_upper": 88.0,
            },
            "meta": {
                "material_utilization": {
                    "retrieval_file_coverage_rate": 0.88,
                    "retrieval_hit_rate": 0.76,
                },
                "material_utilization_gate": {"blocked": False, "warned": False},
                "evidence_trace": {
                    "mandatory_hit_rate": 0.82,
                    "source_files_hit_count": 3,
                },
            },
        }
        material_knowledge_snapshot = {
            "summary": {
                "dimension_coverage_rate": 0.81,
                "structured_signal_total": 22,
                "structured_quality_avg": 0.78,
                "structured_quality_type_rate": 1.0,
                "numeric_category_summary": [
                    "工期/节点：90",
                    "规格/参数：1200",
                    "阈值/偏差：48",
                ],
            },
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "files": 1,
                    "structured_signal_count": 8,
                    "structured_quality_score": 0.82,
                },
                {
                    "material_type": "drawing",
                    "files": 1,
                    "structured_signal_count": 7,
                    "structured_quality_score": 0.76,
                },
                {
                    "material_type": "boq",
                    "files": 1,
                    "structured_signal_count": 7,
                    "structured_quality_score": 0.75,
                },
            ],
        }

        out = _build_score_self_awareness(
            report,
            material_knowledge_snapshot=material_knowledge_snapshot,
        )

        assert out["level"] == "high"
        assert out["score_0_1"] >= 0.72
        assert out["dimension_coverage_rate"] == pytest.approx(0.81, abs=1e-6)
        assert out["source_files_hit_count"] == 3
        assert out["structured_signal_total"] == 22
        assert out["structured_quality_avg"] == pytest.approx(0.78, abs=1e-6)
        assert out["structured_quality_type_rate"] == pytest.approx(1.0, abs=1e-6)
        assert out["structured_type_coverage_rate"] == pytest.approx(1.0, abs=1e-6)

    def test_build_score_self_awareness_forces_low_when_material_gate_blocked(self):
        report = {
            "meta": {
                "material_utilization": {
                    "retrieval_file_coverage_rate": 0.92,
                    "retrieval_hit_rate": 0.8,
                },
                "material_utilization_gate": {"blocked": True, "warned": False},
                "evidence_trace": {
                    "mandatory_hit_rate": 0.9,
                    "source_files_hit_count": 4,
                },
            }
        }
        material_knowledge_snapshot = {
            "summary": {
                "dimension_coverage_rate": 0.9,
                "numeric_category_summary": ["工期/节点：90", "规格/参数：1200"],
            }
        }

        out = _build_score_self_awareness(
            report,
            material_knowledge_snapshot=material_knowledge_snapshot,
        )

        assert out["level"] == "low"
        assert out["score_0_1"] <= 0.18
        assert "资料利用门禁阻断" in out["reasons"]

    def test_build_score_self_awareness_backfills_material_usage_metadata(self):
        report = {
            "requirement_hits": [
                {
                    "source_pack_id": "runtime_material_dimension",
                    "dimension_id": "09",
                    "label": "资料维度约束：进度计划",
                    "hit": True,
                    "source_types": ["tender_qa"],
                }
            ],
            "meta": {
                "material_retrieval": {},
                "material_utilization_gate": {"blocked": False, "warned": False},
                "evidence_trace": {
                    "mandatory_hit_rate": 0.6,
                    "source_files_hit_count": 1,
                },
            },
        }

        out = _build_score_self_awareness(report, material_knowledge_snapshot={"summary": {}})

        assert out["material_dimension_hit_rate"] == pytest.approx(1.0, abs=1e-6)
        assert report["meta"]["material_utilization"]["material_dimension_total"] == 1

    def test_build_score_self_awareness_penalizes_weak_structured_material_signals(self):
        report = {
            "meta": {
                "material_utilization": {
                    "retrieval_file_coverage_rate": 0.78,
                    "retrieval_hit_rate": 0.64,
                    "material_dimension_hit_rate": 0.66,
                },
                "material_utilization_gate": {"blocked": False, "warned": False},
                "evidence_trace": {
                    "mandatory_hit_rate": 0.68,
                    "source_files_hit_count": 2,
                },
            }
        }
        material_knowledge_snapshot = {
            "summary": {
                "dimension_coverage_rate": 0.6,
                "structured_signal_total": 1,
                "structured_quality_avg": 0.12,
                "structured_quality_type_rate": 0.0,
                "numeric_category_summary": ["工期/节点：90"],
            },
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "files": 1,
                    "structured_signal_count": 1,
                    "structured_quality_score": 0.12,
                },
                {
                    "material_type": "drawing",
                    "files": 1,
                    "structured_signal_count": 0,
                    "structured_quality_score": 0.06,
                },
                {
                    "material_type": "boq",
                    "files": 1,
                    "structured_signal_count": 0,
                    "structured_quality_score": 0.08,
                },
            ],
        }

        out = _build_score_self_awareness(
            report,
            material_knowledge_snapshot=material_knowledge_snapshot,
        )

        assert out["level"] in {"low", "medium"}
        assert out["structured_signal_total"] == 1
        assert out["structured_quality_avg"] == pytest.approx(0.12, abs=1e-6)
        assert out["structured_quality_type_rate"] == pytest.approx(0.0, abs=1e-6)
        assert out["structured_type_coverage_rate"] == pytest.approx(0.3333, abs=1e-6)
        assert any("结构化资料信号偏弱" in reason for reason in out["reasons"])
        assert any("结构化资料质量偏弱" in reason for reason in out["reasons"])


class TestIndexEndpoint:
    """Tests for GET / index endpoint."""

    def test_index_returns_html(self, client):
        """Index endpoint should return HTML page."""
        response = client.get("/")
        assert response.status_code == 200
        assert "<html>" in response.text
        assert "青天评标系统" in response.text

    def test_index_contains_forms(self, client):
        """Index page should contain all forms."""
        response = client.get("/")
        assert "createProject" in response.text
        assert "uploadMaterial" in response.text
        assert "uploadMaterialBoq" in response.text
        assert "uploadMaterialDrawing" in response.text
        assert "uploadMaterialPhoto" in response.text
        assert 'id="uploadZoneTenderQa"' in response.text
        assert 'id="uploadZoneBoq"' in response.text
        assert 'id="uploadZoneDrawing"' in response.text
        assert 'id="uploadZoneSitePhoto"' in response.text
        assert 'id="uploadZoneStateTenderQa"' in response.text
        assert 'id="uploadZoneStateBoq"' in response.text
        assert 'id="uploadZoneStateDrawing"' in response.text
        assert 'id="uploadZoneStateSitePhoto"' in response.text
        assert "uploadShigong" in response.text
        assert 'id="projectDeleteSelect"' in response.text
        assert 'id="deleteSelectedProjects"' in response.text
        assert 'id="scoreScaleSelect"' in response.text
        assert 'name="score_scale_max"' in response.text
        assert 'id="btnMaterialKnowledgeProfile"' in response.text
        assert 'id="btnMaterialKnowledgeProfileDownload"' in response.text
        assert 'id="btnEvolutionHealth"' in response.text
        assert 'id="btnEvidenceTrace"' in response.text
        assert 'id="btnScoringBasis"' in response.text
        assert 'id="btnScoringDiagnostic"' in response.text
        assert 'id="shigongGateSummary"' in response.text
        assert 'id="scoringBasisResult"' in response.text
        assert 'id="scoringDiagnosticResult"' in response.text
        assert "解析状态" in response.text
        assert 'id="groundTruthSubmissionSelect"' in response.text
        assert 'id="groundTruthFile"' not in response.text
        assert "/ground_truth/from_submission" in response.text
        assert 'id="section-adaptive" style="display:none"' in response.text
        assert "V2 反演校准闭环（核心能力，强烈建议执行）" in response.text
        assert ".dxf" in response.text

    def test_index_replaces_server_side_placeholders(self, client):
        """Index page should not leak template placeholders."""
        response = client.get("/")
        assert "__PROJECT_OPTIONS__" not in response.text
        assert "__CREATE_NOTICE_HTML__" not in response.text
        assert "__EXPERT_PROFILE_STATUS__" not in response.text
        assert "__EXPERT_WEIGHTS_ROWS__" not in response.text
        assert "__EXPERT_WEIGHTS_SUMMARY__" not in response.text
        assert "__GLOBAL_NOTICE_HTML__" not in response.text
        assert "__SELECTED_PROJECT_ID__" not in response.text
        assert "__PROJECT_SCORE_SCALE_MAX__" not in response.text

    def test_index_renders_16_dimension_weight_sliders(self, client):
        """Index page should render 16-dimension focus sliders on first paint."""
        response = client.get("/")
        assert response.status_code == 200
        for i in range(1, 17):
            dim_id = f"{i:02d}"
            assert f'id="w_{dim_id}"' in response.text

    def test_index_contains_web_fallback_forms(self, client):
        """Index page should keep non-JS fallback routes for upload/delete actions."""
        response = client.get("/")
        assert response.status_code == 200
        assert 'id="deleteProjectForm"' in response.text
        assert 'action="/web/delete_project"' in response.text
        assert 'action="/web/upload_materials"' in response.text
        assert 'action="/web/upload_shigong"' in response.text

    def test_index_frontend_has_batch_project_delete_handler(self, client):
        """Section 2 should support selecting multiple projects and batch deleting them."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert (
            "const deleteSelectedProjectsBtn = document.getElementById('deleteSelectedProjects');"
            in page
        )
        assert "const delSel = document.getElementById('projectDeleteSelect');" in page
        assert "正在批量删除项目…" in page
        assert "batch_delete_projects" in page

    def test_index_head_returns_200(self, client):
        """HEAD / should be available to avoid browser connectivity false alarms."""
        response = client.head("/")
        assert response.status_code == 200

    def test_index_frontend_does_not_double_read_fetch_response_body(self, client):
        """UI handlers should not call res.text() after res.json() on the same response."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "showJson('output', res.ok ? data : await res.text())" not in page
        assert "showJson('output', res.ok ? data : (data.detail || await res.text()))" not in page
        assert "function formatApiOutput" in page

    def test_index_frontend_has_non_blocking_handlers_for_compare_and_adaptive(self, client):
        """Section 5/6 buttons should stay clickable and show explicit project-selection feedback."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert "const NON_BLOCKING_ACTION_BUTTON_IDS" in page
        assert "function ensureProjectForAction" in page
        assert "setResultLoading('compareResult'" in page
        assert "setResultLoading('adaptiveResult'" in page
        assert "setResultLoading('scoringDiagnosticResult'" in page
        assert "function updateShigongGateSummary" in page
        assert "function materialTypeUploadAnchor" in page
        assert "function applyMaterialUploadZoneHighlights" in page
        assert "function clearMaterialUploadZoneHighlights" in page
        assert "function clearMaterialParsePolling" in page
        assert "function applyMaterialParseZoneState" in page
        assert "function scheduleMaterialParsePolling" in page
        assert "/materials/parse_status" in page
        assert "资料提取锚点：" in page
        assert "资料锚点类别：" in page
        assert "命中证据：" in page
        assert "缺口证据：" in page
        assert "命中类别：" in page
        assert "待补类别：" in page
        assert "资料支撑维度总览" in page
        assert "支撑较强维度" in page
        assert "证据薄弱维度" in page
        assert "维度支撑明细（16维）" in page
        assert "评分置信度" in page
        assert "评分进化约束总览" in page
        assert "进化反馈约束" in page
        assert "高置信逻辑骨架约束" in page
        assert "当前有效权重（Top）" in page

    def test_index_frontend_has_no_broken_multiline_regex_literal(self, client):
        """Rendered JS should not contain regex literals split by line breaks (would break entire script)."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert re.search(r"replace\(/\s*\n\s*/g", page) is None
        assert "replace(/\\n/g" in page

    def test_index_frontend_binds_core_buttons_for_sections_5_6_7(self, client):
        """Core buttons in sections 5/6/7 should have safeClick bindings in generated page."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        for button_id in (
            "btnCompare",
            "btnCompareReport",
            "btnInsights",
            "btnLearning",
            "btnScoringDiagnostic",
            "btnEvidenceTrace",
            "btnScoringBasis",
            "btnAdaptive",
            "btnAdaptivePatch",
            "btnAdaptiveValidate",
            "btnAdaptiveApply",
            "btnEvolve",
            "btnWritingGuidance",
            "btnCompilationInstructions",
            "btnRefreshGroundTruthSubmissionOptions",
        ):
            assert f"safeClick('{button_id}'" in page

    def test_index_frontend_section_5_6_7_actions_have_explicit_project_guard(self, client):
        """Section 5/6/7 action handlers should proactively show project-selection errors instead of silent failure."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        for guard_result_id in (
            "compareResult",
            "compareReportResult",
            "insightsResult",
            "learningResult",
            "scoringDiagnosticResult",
            "evidenceTraceResult",
            "scoringBasisResult",
            "adaptiveResult",
            "adaptivePatchResult",
            "adaptiveValidateResult",
            "adaptiveApplyResult",
            "evolveResult",
            "guidanceResult",
            "compilationInstructionsResult",
        ):
            assert f"ensureProjectForAction('{guard_result_id}')" in page

    def test_index_upload_buttons_use_inline_fallback_click_and_form_submit_compat(self, client):
        """Upload/score buttons should keep submit fallback while inline fallback click avoids full-page jumps."""
        response = client.get("/")
        assert response.status_code == 200
        page = response.text
        assert (
            '<button type="submit" id="btnUploadMaterials" onclick="if (window.__zhifeiFallbackClick) '
            "{ return window.__zhifeiFallbackClick(event, 'btnUploadMaterials'); } return true;\">上传资料</button>"
            in page
        )
        assert (
            'id="btnUploadShigong" name="submit_action" value="upload" onclick="if (window.__zhifeiFallbackClick) '
            "{ return window.__zhifeiFallbackClick(event, 'btnUploadShigong'); } return true;\""
            in page
        )
        assert (
            'id="btnScoreShigong" class="secondary" formaction="/web/score_shigong" name="submit_action" value="score" onclick="if (window.__zhifeiFallbackClick) '
            "{ return window.__zhifeiFallbackClick(event, 'btnScoreShigong'); } return true;\""
            in page
        )
        assert "btnUploadMaterials: { resultId: 'materialsActionStatus'" in page
        assert "btnUploadBoq: { resultId: 'materialsActionStatusBoq'" in page
        assert "btnUploadDrawing: { resultId: 'materialsActionStatusDrawing'" in page
        assert "btnUploadSitePhotos: { resultId: 'materialsActionStatusPhoto'" in page
        assert "btnUploadShigong: { resultId: 'shigongActionStatus'" in page
        assert "btnScoreShigong: { resultId: 'shigongActionStatus'" in page
        assert "safeClick('btnUploadMaterials', uploadMaterialsAction);" not in page
        assert "safeClick('btnUploadShigong', uploadShigongAction);" not in page
        assert "safeClick('btnScoreShigong', scoreShigongAction);" not in page
        assert "function captureViewportY()" not in page
        assert "function restoreViewportY(y)" not in page
        assert "let uploadShigongInFlight = false;" in page
        assert "let scoreShigongInFlight = false;" in page
        assert "let shigongSubmitIntent = 'upload';" in page
        assert (
            "const isScoreSubmit = sid === 'btnScoreShigong' || shigongSubmitIntent === 'score';"
            in page
        )
        assert "/submissions?t=' + Date.now()" in page


class TestWebCreateProjectFallback:
    """Tests for non-JS project creation fallback endpoint."""

    def test_web_create_project_empty_name_redirects_error(self, client):
        response = client.post("/web/create_project", data={"name": "   "}, follow_redirects=False)
        assert response.status_code == 303
        assert "create_error=" in response.headers.get("location", "")

    @patch("app.main.create_project")
    def test_web_create_project_success_redirects_ok(self, mock_create_project, client):
        response = client.post(
            "/web/create_project", data={"name": "测试项目"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert "create_ok=" in response.headers.get("location", "")
        assert mock_create_project.called


class TestWebFallbackOps:
    """Tests for non-JS fallback operation endpoints."""

    def test_web_delete_project_missing_id(self, client):
        response = client.post(
            "/web/delete_project", data={"project_id": ""}, follow_redirects=False
        )
        assert response.status_code == 303
        assert "msg_type=error" in response.headers.get("location", "")

    @patch("app.main._delete_project_cascade")
    def test_web_delete_project_success(self, mock_delete, client):
        mock_delete.return_value = {"project_name": "项目A"}
        response = client.post(
            "/web/delete_project", data={"project_id": "p1"}, follow_redirects=False
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=success" in location
        assert "project_id" not in location
        mock_delete.assert_called_once()

    def test_web_upload_materials_requires_file(self, client):
        response = client.post(
            "/web/upload_materials", data={"project_id": "p1"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert "msg_type=error" in response.headers.get("location", "")

    def test_web_upload_materials_get_fallback_redirects(self, client):
        response = client.get("/web/upload_materials", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=error" in location
        assert "%E4%B8%8A%E4%BC%A0%E8%B5%84%E6%96%99" in location

    def test_web_upload_materials_put_fallback_redirects(self, client):
        response = client.put("/web/upload_materials", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=error" in location
        assert "%E4%B8%8A%E4%BC%A0%E8%B5%84%E6%96%99" in location

    @patch("app.main.upload_material")
    def test_web_upload_materials_partial_success(self, mock_upload_material, client):
        def _side_effect(*args, **kwargs):
            file_obj = kwargs.get("file")
            if file_obj and file_obj.filename == "bad.txt":
                raise ValueError("bad file")
            return {"status": "ok"}

        mock_upload_material.side_effect = _side_effect
        response = client.post(
            "/web/upload_materials",
            data={"project_id": "p1"},
            files=[
                ("file", ("good.txt", BytesIO(b"ok"), "text/plain")),
                ("file", ("bad.txt", BytesIO(b"bad"), "text/plain")),
            ],
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "project_id=p1" in location
        assert "msg_type=error" in location

    @patch("app.main.upload_shigong")
    def test_web_upload_shigong_success(self, mock_upload_shigong, client):
        mock_upload_shigong.return_value = {"id": "s1"}
        response = client.post(
            "/web/upload_shigong",
            data={"project_id": "p1"},
            files=[("file", ("a.txt", BytesIO(b"demo"), "text/plain"))],
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "project_id=p1" in location
        assert "msg_type=success" in location

    def test_web_upload_shigong_get_fallback_redirects(self, client):
        response = client.get("/web/upload_shigong", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=error" in location
        assert "%E4%B8%8A%E4%BC%A0%E6%96%BD%E7%BB%84" in location

    def test_web_upload_shigong_put_fallback_redirects(self, client):
        response = client.put("/web/upload_shigong", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=error" in location
        assert "%E4%B8%8A%E4%BC%A0%E6%96%BD%E7%BB%84" in location

    @patch("app.main.rescore_project_submissions")
    def test_web_score_shigong_success_uses_reports_generated(self, mock_rescore, client):
        from types import SimpleNamespace

        mock_rescore.return_value = SimpleNamespace(reports_generated=3)
        response = client.post(
            "/web/score_shigong",
            data={"project_id": "p1"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "msg_type=success" in location
        assert "%E5%B7%B2%E9%87%8D%E7%AE%97+3+%E4%BB%BD" in location
        assert "#section-shigong" in location


class TestCleanupE2EEndpoint:
    """Tests for /projects/cleanup_e2e endpoint."""

    @patch("app.main._delete_project_cascade")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_cleanup_e2e_projects_only_matches_prefix(
        self,
        mock_ensure,
        mock_load_projects,
        mock_delete,
        client,
    ):
        mock_load_projects.return_value = [
            {"id": "1", "name": "E2E_20260210_100000"},
            {"id": "2", "name": "正式项目A"},
            {"id": "3", "name": "E2E_20260210_110000"},
        ]
        mock_delete.return_value = {"project_id": "1"}
        response = client.post("/api/v1/projects/cleanup_e2e?prefix=E2E_")
        assert response.status_code == 200
        data = response.json()
        assert data["matched"] == 2
        assert data["removed_count"] == 2
        assert data["failed_count"] == 0
        assert mock_delete.call_count == 2


class TestMetricsEndpoint:
    """Tests for Prometheus metrics endpoint."""

    def test_metrics_returns_200(self, client):
        """GET /metrics should return 200."""
        response = client.get("/metrics")
        assert response.status_code == 200

    def test_metrics_returns_text_plain(self, client):
        """GET /metrics should return text/plain content type."""
        response = client.get("/metrics")
        assert "text/plain" in response.headers["content-type"]

    def test_metrics_contains_prometheus_format(self, client):
        """Metrics response should contain Prometheus format."""
        response = client.get("/metrics")
        content = response.text
        assert "# HELP" in content
        assert "# TYPE" in content

    def test_metrics_contains_qingtian_metrics(self, client):
        """Metrics should contain qingtian custom metrics."""
        response = client.get("/metrics")
        content = response.text
        assert "qingtian_" in content

    def test_metrics_contains_project_stats(self, client):
        """Metrics should contain project statistics."""
        response = client.get("/metrics")
        content = response.text
        assert "qingtian_projects_total" in content
        assert "qingtian_submissions_total" in content

    def test_metrics_reflect_previous_http_requests(self, client):
        client.get("/health")
        response = client.get("/metrics")

        assert response.status_code == 200
        assert (
            'qingtian_http_requests_total{endpoint="/health",method="GET",status_code="200"}'
            in response.text
        )

    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_metrics_updates_project_stats(
        self, mock_ensure_dirs, mock_submissions, mock_projects, client
    ):
        """Metrics endpoint should update project stats."""
        mock_projects.return_value = [{"id": "1"}, {"id": "2"}]
        mock_submissions.return_value = [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]

        response = client.get("/metrics")
        assert response.status_code == 200
        # 指标应该被更新（验证通过无异常即可）

    @patch("app.main.ensure_data_dirs")
    def test_metrics_handles_stats_error_gracefully(self, mock_ensure_dirs, client):
        """Metrics should not fail if stats collection errors."""
        mock_ensure_dirs.side_effect = Exception("Test error")

        response = client.get("/metrics")
        # 应该仍然返回 200，即使统计收集失败
        assert response.status_code == 200


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    def test_health_returns_healthy(self, client):
        """GET /health should return healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"

    def test_health_response_structure(self, client):
        """Health response should have correct structure."""
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert "version" in data

    @patch("app.main.load_config")
    @patch("app.main.ensure_data_dirs")
    def test_ready_returns_ready_when_all_checks_pass(
        self, mock_ensure_dirs, mock_load_config, client
    ):
        """GET /ready should return ready when all checks pass."""
        mock_load_config.return_value = MagicMock()
        mock_ensure_dirs.return_value = None

        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["checks"]["config"] is True
        assert data["checks"]["data_dirs"] is True

    @patch("app.main.load_config")
    @patch("app.main.ensure_data_dirs")
    def test_ready_returns_not_ready_when_config_fails(
        self, mock_ensure_dirs, mock_load_config, client
    ):
        """GET /ready should return not_ready when config check fails."""
        mock_load_config.side_effect = Exception("Config error")
        mock_ensure_dirs.return_value = None

        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["config"] is False
        assert data["checks"]["data_dirs"] is True

    @patch("app.main.load_config")
    @patch("app.main.ensure_data_dirs")
    def test_ready_returns_not_ready_when_data_dirs_fails(
        self, mock_ensure_dirs, mock_load_config, client
    ):
        """GET /ready should return not_ready when data_dirs check fails."""
        mock_load_config.return_value = MagicMock()
        mock_ensure_dirs.side_effect = Exception("Cannot create dirs")

        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["config"] is True
        assert data["checks"]["data_dirs"] is False

    def test_ready_response_structure(self, client):
        """Ready response should have correct structure."""
        response = client.get("/ready")
        data = response.json()
        assert "status" in data
        assert "checks" in data
        assert isinstance(data["checks"], dict)


class TestScoreEndpoint:
    """Tests for POST /score endpoint."""

    @patch("app.main.load_config")
    @patch("app.main.score_text")
    @patch("app.main.get_cached_score")
    def test_score_endpoint_success(self, mock_cached, mock_score, mock_config, client):
        """Score endpoint should return score report."""
        from app.schemas import LogicLockResult, ScoreReport

        mock_cached.return_value = None
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_score.return_value = ScoreReport(
            total_score=85.0,
            dimension_scores={},
            logic_lock=LogicLockResult(
                definition_score=1.0,
                analysis_score=1.0,
                solution_score=1.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )
        response = client.post("/api/v1/score", json={"text": "测试文本"})
        assert response.status_code == 200
        mock_score.assert_called_once()


class TestProjectsEndpoints:
    """Tests for /projects endpoints."""

    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_create_project_success(self, mock_ensure, mock_load, mock_save, client):
        """Create project should return project record."""
        mock_load.return_value = []
        response = client.post("/api/v1/projects", json={"name": "测试项目"})
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["name"] == "测试项目"
        mock_save.assert_called_once()

    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_create_project_with_meta(self, mock_ensure, mock_load, mock_save, client):
        """Create project with meta should include meta."""
        mock_load.return_value = []
        response = client.post(
            "/api/v1/projects", json={"name": "测试项目", "meta": {"key": "value"}}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["meta"]["key"] == "value"
        assert data["meta"]["enforce_material_gate"] is True
        assert "required_material_types" in data["meta"]

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_projects_empty(self, mock_ensure, mock_load, client):
        """List projects should return empty list when no projects."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects")
        assert response.status_code == 200
        assert response.json() == []

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_projects_with_data(self, mock_ensure, mock_load, client):
        """List projects should return all projects."""
        mock_load.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        response = client.get("/api/v1/projects")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "项目1"

    @patch("app.main.save_projects")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_list_projects_backfills_missing_created_at(
        self, mock_ensure, mock_load, mock_save, client
    ):
        """List projects should tolerate legacy records without created_at."""
        mock_load.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        response = client.get("/api/v1/projects")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "p1"
        assert data[0]["created_at"]
        mock_save.assert_called_once()


class TestExpertProfileEndpoints:
    """Tests for /projects/{project_id}/expert-profile and rescore endpoints."""

    @patch("app.main.save_expert_profiles")
    @patch("app.main.save_projects")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_expert_profile_auto_create_default(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_profiles,
        mock_save_projects,
        mock_save_profiles,
        client,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "项目1", "meta": {}, "created_at": "2026-01-01T00:00:00Z"}
        ]
        mock_load_profiles.return_value = []

        response = client.get("/api/v1/projects/p1/expert-profile")
        assert response.status_code == 200
        data = response.json()
        assert data["project"]["id"] == "p1"
        assert data["project"]["region"] == "合肥"
        assert data["expert_profile"]["weights_raw"]["01"] == 5
        assert data["expert_profile"]["weights_raw"]["16"] == 5
        assert data["expert_profile"]["norm_rule_version"] == "v1_m=0.5+a/10_norm=sum"
        mock_save_profiles.assert_called_once()
        mock_save_projects.assert_called_once()

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_update_project_expert_profile_locked_without_force_unlock(
        self,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "status": "submitted_to_qingtian",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        weights = {f"{i:02d}": 5 for i in range(1, 17)}
        response = client.put(
            "/api/v1/projects/p1/expert-profile",
            json={"name": "锁定测试", "weights_raw": weights},
        )
        assert response.status_code == 409
        assert "force_unlock=true" in response.json()["detail"]

    @patch("app.main.save_expert_profiles")
    @patch("app.main.save_projects")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_update_project_expert_profile_locked_with_force_unlock(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_profiles,
        mock_save_projects,
        mock_save_profiles,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "status": "submitted_to_qingtian",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_load_profiles.return_value = []
        weights = {f"{i:02d}": 5 for i in range(1, 17)}
        response = client.put(
            "/api/v1/projects/p1/expert-profile",
            json={"name": "锁定解锁测试", "weights_raw": weights, "force_unlock": True},
        )
        assert response.status_code == 200
        mock_save_profiles.assert_called_once()
        mock_save_projects.assert_called_once()

    @patch("app.main.save_expert_profiles")
    @patch("app.main.save_projects")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_update_project_expert_profile_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_profiles,
        mock_save_projects,
        mock_save_profiles,
        client,
    ):
        mock_load_projects.return_value = [
            {"id": "p1", "name": "项目1", "meta": {}, "created_at": "2026-01-01T00:00:00Z"}
        ]
        mock_load_profiles.return_value = []
        weights = {f"{i:02d}": 5 for i in range(1, 17)}
        weights["02"] = 10
        response = client.put(
            "/api/v1/projects/p1/expert-profile",
            json={"name": "安全偏重", "weights_raw": weights},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["expert_profile"]["name"] == "安全偏重"
        assert data["project"]["expert_profile_id"] == data["expert_profile"]["id"]
        assert data["project"]["meta"]["expert_profile_read_only"] is True
        assert data["project"]["meta"]["expert_profile_lock_source"] == "manual"
        norm = data["expert_profile"]["weights_norm"]
        assert abs(sum(norm.values()) - 1.0) < 1e-6
        assert norm["02"] > norm["01"]
        mock_save_profiles.assert_called_once()
        mock_save_projects.assert_called_once()

    @patch("app.main.save_projects")
    @patch("app.main.save_expert_profiles")
    @patch("app.main.calibrate_weights")
    @patch("app.main._build_feedback_records_for_project")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    def test_auto_update_project_weights_keeps_project_profile_read_only(
        self,
        mock_load_projects,
        mock_load_profiles,
        mock_build_feedback_records,
        mock_calibrate_weights,
        mock_save_profiles,
        mock_save_projects,
    ):
        from app.main import _auto_update_project_weights_from_delta_cases

        project = {
            "id": "p1",
            "name": "项目1",
            "expert_profile_id": "ep1",
            "meta": {"expert_profile_read_only": True},
        }
        profile = {
            "id": "ep1",
            "name": "默认",
            "weights_raw": {f"{i:02d}": 5 for i in range(1, 17)},
            "weights_norm": {f"{i:02d}": 1 / 16 for i in range(1, 17)},
            "norm_rule_version": "v1_m=0.5+a/10_norm=sum",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_load_projects.return_value = [project]
        mock_load_profiles.return_value = [profile]
        mock_build_feedback_records.return_value = [{"final_score": 90.0, "delta": 2.0}]
        mock_calibrate_weights.return_value = {
            "weights_norm": {
                **{f"{i:02d}": 1 / 16 for i in range(1, 17)},
                "02": 0.1,
                "01": 0.025,
            },
            "stats": {"sample_count": 1},
        }

        out = _auto_update_project_weights_from_delta_cases("p1")

        assert out["updated"] is True
        assert out["project_profile_mutated"] is False
        assert project["expert_profile_id"] == "ep1"
        mock_save_profiles.assert_called_once()
        mock_save_projects.assert_not_called()

    @patch("app.main._run_feedback_closed_loop")
    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.record_history_score")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.score_text")
    @patch("app.main.load_config")
    @patch("app.main.save_projects")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rescore_project_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_profiles,
        mock_save_projects,
        mock_load_config,
        mock_score_text,
        mock_load_submissions,
        mock_save_submissions,
        mock_load_score_reports,
        mock_save_score_reports,
        mock_record_history,
        mock_material_gate,
        mock_feedback_loop,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "created_at": "2026-01-01T00:00:00Z",
                "expert_profile_id": "ep1",
            }
        ]
        mock_load_profiles.return_value = [
            {
                "id": "ep1",
                "name": "默认",
                "weights_raw": {f"{i:02d}": 5 for i in range(1, 17)},
                "weights_norm": {f"{i:02d}": 1 / 16 for i in range(1, 17)},
                "norm_rule_version": "v1_m=0.5+a/10_norm=sum",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "text": "test content",
                "total_score": 60.0,
                "report": {},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_load_score_reports.return_value = []
        mock_load_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_score_text.return_value = MagicMock(
            model_dump=lambda: {
                "total_score": 88.8,
                "dimension_scores": {},
                "penalties": [],
                "suggestions": [],
            }
        )
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 3,
                "counts_by_type": {"tender_qa": 1, "boq": 1, "drawing": 1},
                "total_parsed_chars": 26000,
                "parse_fail_ratio": 0.0,
                "gate": {"passed": True, "issues": []},
            },
            [],
        )

        response = client.post(
            "/api/v1/projects/p1/rescore",
            json={"scoring_engine_version": "v2", "scope": "project", "score_scale_max": 5},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["submission_count"] == 1
        assert data["reports_generated"] == 1
        assert data["score_scale_max"] == 5
        assert data["score_scale_label"] == "5分制"
        assert "material_utilization" in data
        assert "material_utilization_alerts" in data
        assert "material_utilization_gate" in data
        mock_save_submissions.assert_called_once()
        mock_save_score_reports.assert_called_once()
        mock_record_history.assert_called_once()
        mock_feedback_loop.assert_called_once_with("p1", locale="zh", trigger="rescore")

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rescore_project_locked_without_force_unlock(
        self,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "status": "submitted_to_qingtian",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        response = client.post(
            "/api/v1/projects/p1/rescore",
            json={"scoring_engine_version": "v2", "scope": "project"},
        )
        assert response.status_code == 409
        assert "force_unlock=true" in response.json()["detail"]

    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_submissions")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_rescore_project_rejects_when_material_gate_failed(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_profiles,
        mock_load_submissions,
        mock_material_gate,
        client,
    ):
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "name": "项目1",
                "meta": {},
                "created_at": "2026-01-01T00:00:00Z",
                "expert_profile_id": "ep1",
            }
        ]
        mock_load_profiles.return_value = [
            {
                "id": "ep1",
                "name": "默认",
                "weights_raw": {f"{i:02d}": 5 for i in range(1, 17)},
                "weights_norm": {f"{i:02d}": 1 / 16 for i in range(1, 17)},
                "norm_rule_version": "v1_m=0.5+a/10_norm=sum",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "text": "test content",
                "total_score": 60.0,
                "report": {},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_material_gate.side_effect = HTTPException(
            status_code=422,
            detail="资料门禁未通过：缺少必需资料类型：清单",
        )

        response = client.post(
            "/api/v1/projects/p1/rescore",
            json={"scoring_engine_version": "v2", "scope": "project"},
        )
        assert response.status_code == 422
        assert "资料门禁未通过" in response.json()["detail"]


class TestFeedbackLoopHooks:
    @patch("app.main._run_feedback_closed_loop")
    @patch("app.main.save_calibration_samples")
    @patch("app.main.load_calibration_samples")
    @patch("app.main.save_delta_cases")
    @patch("app.main.load_delta_cases")
    @patch("app.main.save_qingtian_results")
    @patch("app.main.load_qingtian_results")
    @patch("app.main.save_evidence_units")
    @patch("app.main.load_evidence_units")
    @patch("app.main.save_score_reports")
    @patch("app.main.load_score_reports")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_delete_submission_triggers_feedback_loop(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_save_submissions,
        mock_load_score_reports,
        mock_save_score_reports,
        mock_load_evidence_units,
        mock_save_evidence_units,
        mock_load_qingtian_results,
        mock_save_qingtian_results,
        mock_load_delta_cases,
        mock_save_delta_cases,
        mock_load_calibration_samples,
        mock_save_calibration_samples,
        mock_feedback_loop,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1", "filename": "f1.txt", "path": ""}
        ]
        mock_load_score_reports.return_value = [
            {"id": "r1", "project_id": "p1", "submission_id": "s1"}
        ]
        mock_load_evidence_units.return_value = [{"submission_id": "s1"}]
        mock_load_qingtian_results.return_value = [{"submission_id": "s1"}]
        mock_load_delta_cases.return_value = [{"submission_id": "s1"}]
        mock_load_calibration_samples.return_value = [{"submission_id": "s1"}]

        response = client.delete("/api/v1/projects/p1/submissions/s1")
        assert response.status_code == 200
        assert response.json()["ok"] is True
        mock_feedback_loop.assert_called_once_with("p1", locale="zh", trigger="delete_submission")


class TestMaterialsEndpoint:
    """Tests for /projects/{project_id}/materials endpoint."""

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_success(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        """Upload material should save file and return record."""
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1", "scoring_engine_version_locked": "v1"}]
        mock_load_mat.return_value = []

        file_content = b"test content"
        response = client.post(
            "/api/v1/projects/p1/materials",
            files={"file": ("test.txt", BytesIO(file_content), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "material" in data
        assert data["material"]["parse_status"] == "queued"
        assert data["parse_job"]["status"] == "queued"
        assert data["constraint_sync"]["mode"] == "async_parse_pending"

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_persists_material_type(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = []

        response = client.post(
            "/api/v1/projects/p1/materials",
            data={"material_type": "boq"},
            files={"file": ("工程量清单.xlsx", BytesIO(b"excel"), "application/octet-stream")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["material"]["material_type"] == "boq"
        saved_materials = mock_save.call_args[0][0]
        assert any(m.get("material_type") == "boq" for m in saved_materials)

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_same_filename_different_type_kept_separately(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = [
            {
                "id": "m-old",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "test.txt",
                "path": str(tmp_path / "old.txt"),
                "created_at": "2026-02-17T00:00:00+00:00",
            }
        ]

        response = client.post(
            "/api/v1/projects/p1/materials",
            data={"material_type": "boq"},
            files={"file": ("test.txt", BytesIO(b"new content"), "text/plain")},
        )
        assert response.status_code == 200
        saved_materials = mock_save.call_args[0][0]
        same_name = [
            m
            for m in saved_materials
            if m.get("project_id") == "p1" and m.get("filename") == "test.txt"
        ]
        assert len(same_name) == 2
        assert {m.get("material_type") for m in same_name} == {"tender_qa", "boq"}

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_material_invalid_material_type(self, mock_ensure, mock_load, client):
        mock_load.return_value = [{"id": "p1"}]
        response = client.post(
            "/api/v1/projects/p1/materials",
            data={"material_type": "bad_type"},
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
        )
        assert response.status_code == 422
        assert "material_type" in response.json()["detail"]

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_material_project_not_found(self, mock_ensure, mock_load, client):
        """Upload material should return 404 if project not found."""
        mock_load.return_value = []
        response = client.post(
            "/api/v1/projects/nonexistent/materials",
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
        )
        assert response.status_code == 404
        assert "项目不存在" in response.json()["detail"]

    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_materials_health_success(
        self, mock_ensure, mock_load_projects, mock_material_gate, client
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 4,
                "counts_by_type": {"tender_qa": 1, "boq": 1, "drawing": 1, "site_photo": 1},
                "total_parsed_chars": 32000,
                "parse_fail_ratio": 0.0,
                "gate": {"passed": True, "issues": []},
            },
            [],
        )
        response = client.get("/api/v1/projects/p1/materials/health")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["gate"]["passed"] is True
        mock_material_gate.assert_called_once()

    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_parse_status_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "path": "/tmp/招标文件.pdf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "processing",
                "parse_backend": "gpt-5.4-vision",
                "parse_confidence": 0.81,
                "job_id": "j1",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j1",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "status": "processing",
                "parse_backend": "gpt-5.4-vision",
                "attempt": 1,
            }
        ]

        response = client.get("/api/v1/projects/p1/materials/parse_status")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["summary"]["materials_total"] == 1
        assert data["summary"]["queued_materials"] == 1
        assert data["summary"]["backlog"] == 1
        assert data["jobs"][0]["status"] == "processing"
        assert data["materials"][0]["parse_backend"] == "queued"

    @patch("app.main.save_material_parse_jobs")
    @patch("app.main.save_materials")
    @patch("app.main.load_material_parse_jobs")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_reparse_project_materials_requeues_and_clears_errors(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_materials,
        mock_load_jobs,
        mock_save_materials,
        mock_save_jobs,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1"}]
        mock_load_materials.return_value = [
            {
                "id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "path": "/tmp/总图.dxf",
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:00:00+00:00",
                "parse_status": "failed",
                "parse_backend": "local",
                "parse_error_class": "ocr_failed",
                "parse_error_message": "empty_result",
                "job_id": "j-old",
            }
        ]
        mock_load_jobs.return_value = [
            {
                "id": "j-old",
                "material_id": "m1",
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "status": "failed",
                "attempt": 2,
            }
        ]

        response = client.post("/api/v1/projects/p1/materials/reparse")
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["queued_materials"] == 1
        saved_rows = mock_save_materials.call_args[0][0]
        assert saved_rows[0]["parse_status"] == "queued"
        assert saved_rows[0]["parse_error_message"] is None
        assert mock_save_jobs.called is True

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_scoring_readiness_project_not_found(self, mock_ensure, mock_load_projects, client):
        mock_load_projects.return_value = []
        response = client.get("/api/v1/projects/nonexistent/scoring_readiness")
        assert response.status_code == 404
        assert "项目不存在" in response.json()["detail"]

    @patch("app.main._now_iso", return_value="2026-02-26T00:00:00+00:00")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._resolve_material_utilization_policy")
    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_scoring_readiness_blocked_when_gate_failed(
        self,
        mock_ensure,
        mock_load_projects,
        mock_material_gate,
        mock_resolve_policy,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_now_iso,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 2,
                "parsed_ok_files": 1,
                "parse_fail_ratio": 0.5,
                "gate": {"passed": False, "issues": ["缺少必需资料类型：图纸"]},
            },
            ["缺少必需资料类型：图纸"],
        )
        mock_resolve_policy.return_value = {"required_types": ["tender_qa", "drawing"]}
        mock_load_submissions.return_value = [
            {"id": "s1", "project_id": "p1", "text": "施组内容"},
            {"id": "s2", "project_id": "p1", "text": ""},
        ]
        mock_submission_is_scored.side_effect = [False, False]

        response = client.get("/api/v1/projects/p1/scoring_readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["ready"] is False
        assert data["score_button_enabled"] is False
        assert data["gate_passed"] is False
        assert data["issues"] == ["缺少必需资料类型：图纸"]
        assert data["submissions"]["total"] == 2
        assert data["submissions"]["non_empty"] == 1
        assert data["submissions"]["scored"] == 0
        assert data["generated_at"] == "2026-02-26T00:00:00+00:00"

    @patch("app.main._now_iso", return_value="2026-02-26T00:00:00+00:00")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._resolve_material_utilization_policy")
    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_scoring_readiness_ready_when_gate_passed_and_submission_exists(
        self,
        mock_ensure,
        mock_load_projects,
        mock_material_gate,
        mock_resolve_policy,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_now_iso,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 4,
                "parsed_ok_files": 4,
                "parse_fail_ratio": 0.0,
                "gate": {"passed": True, "issues": []},
            },
            [],
        )
        mock_resolve_policy.return_value = {"required_types": ["tender_qa", "boq", "drawing"]}
        mock_load_submissions.return_value = [{"id": "s1", "project_id": "p1", "text": "施组内容"}]
        mock_submission_is_scored.return_value = True

        response = client.get("/api/v1/projects/p1/scoring_readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["ready"] is True
        assert data["score_button_enabled"] is True
        assert data["gate_passed"] is True
        assert data["issues"] == []
        assert data["submissions"]["total"] == 1
        assert data["submissions"]["non_empty"] == 1
        assert data["submissions"]["scored"] == 1
        assert data["generated_at"] == "2026-02-26T00:00:00+00:00"

    @patch("app.main._now_iso", return_value="2026-02-26T00:00:00+00:00")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._resolve_material_utilization_policy")
    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_scoring_readiness_blocked_when_depth_gate_enforced_and_failed(
        self,
        mock_ensure,
        mock_load_projects,
        mock_material_gate,
        mock_resolve_policy,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_now_iso,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 3,
                "parsed_ok_files": 3,
                "total_parsed_chunks": 9,
                "total_numeric_terms": 4,
                "gate": {"passed": True, "issues": []},
                "depth_gate": {
                    "enforce": True,
                    "passed": False,
                    "issues": ["图纸解析分块不足：1 段（建议至少 3 段）"],
                },
            },
            [],
        )
        mock_resolve_policy.return_value = {"required_types": ["tender_qa", "boq", "drawing"]}
        mock_load_submissions.return_value = [{"id": "s1", "project_id": "p1", "text": "施组内容"}]
        mock_submission_is_scored.return_value = False

        response = client.get("/api/v1/projects/p1/scoring_readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["gate_passed"] is False
        assert data["issues"][0].startswith("资料深读门禁：")
        assert data["material_depth_gate"]["enforce"] is True
        assert data["material_depth_gate"]["passed"] is False

    @patch("app.main._now_iso", return_value="2026-02-26T00:00:00+00:00")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._resolve_material_utilization_policy")
    @patch("app.main._validate_material_gate_for_scoring")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_scoring_readiness_depth_gate_warn_only_when_not_enforced(
        self,
        mock_ensure,
        mock_load_projects,
        mock_material_gate,
        mock_resolve_policy,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_now_iso,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_material_gate.return_value = (
            {
                "project_id": "p1",
                "total_files": 3,
                "parsed_ok_files": 3,
                "total_parsed_chunks": 9,
                "total_numeric_terms": 4,
                "gate": {"passed": True, "issues": []},
                "depth_gate": {
                    "enforce": False,
                    "passed": False,
                    "issues": ["清单数字约束提取不足：2 项（建议至少 8 项）"],
                },
            },
            [],
        )
        mock_resolve_policy.return_value = {"required_types": ["tender_qa", "boq", "drawing"]}
        mock_load_submissions.return_value = [{"id": "s1", "project_id": "p1", "text": "施组内容"}]
        mock_submission_is_scored.return_value = False

        response = client.get("/api/v1/projects/p1/scoring_readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["gate_passed"] is True
        assert data["issues"] == []
        assert any("资料深读预警" in w for w in data["warnings"])
        assert data["material_depth_gate"]["enforce"] is False
        assert data["material_depth_gate"]["passed"] is False

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_mece_audit_project_not_found(
        self, mock_ensure, mock_load_projects, client
    ):
        mock_load_projects.return_value = []
        response = client.get("/api/v1/projects/nonexistent/mece_audit")
        assert response.status_code == 404
        assert "项目不存在" in response.json()["detail"]

    @patch("app.main._build_submission_scoring_basis_report")
    @patch("app.main._resolve_submission_score_fields")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._run_system_self_check")
    @patch("app.main._build_evolution_health_report")
    @patch("app.main._build_material_depth_report")
    @patch("app.main._build_scoring_readiness")
    @patch("app.main._now_iso", return_value="2026-02-28T00:00:00+00:00")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_mece_audit_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_now_iso,
        mock_build_readiness,
        mock_build_depth,
        mock_build_evo_health,
        mock_self_check,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_resolve_score_fields,
        mock_scoring_basis,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_readiness.return_value = {
            "project_id": "p1",
            "ready": True,
            "gate_passed": True,
            "issues": [],
            "warnings": [],
            "material_quality": {"total_parsed_chars": 8600},
            "material_gate": {"required_types": ["tender_qa", "boq", "drawing"]},
        }
        mock_build_depth.return_value = {
            "quality_summary": {"total_files": 4},
        }
        mock_build_evo_health.return_value = {
            "summary": {
                "ground_truth_count": 4,
                "matched_prediction_count": 3,
                "has_evolved_multipliers": True,
                "current_weights_source": "evolution",
            },
            "drift": {"level": "low"},
        }
        mock_self_check.return_value = {
            "ok": True,
            "items": [{"name": "health", "ok": True}],
        }
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "created_at": "2026-02-27T00:00:00+00:00",
                "updated_at": "2026-02-27T00:00:00+00:00",
                "report": {"scoring_status": "done"},
            }
        ]
        mock_submission_is_scored.return_value = True
        mock_resolve_score_fields.return_value = {"total_score": 78.6}
        mock_scoring_basis.return_value = {
            "mece_inputs": {
                "project_materials_extracted": True,
                "shigong_parsed": True,
                "bid_requirements_loaded": True,
                "attention_16d_weights_injected": True,
                "custom_instructions_injected": True,
            },
            "evidence_trace": {"total_requirements": 10, "total_hits": 8},
        }

        response = client.get("/api/v1/projects/p1/mece_audit")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["overall"]["level"] == "good"
        assert data["overall"]["health_score"] >= 90
        assert data["summary"]["submission_scored"] == 1
        assert data["summary"]["ground_truth_count"] == 4
        assert isinstance(data["dimensions"], list) and len(data["dimensions"]) >= 4
        keys = {row["key"] for row in data["dimensions"]}
        assert "input_chain" in keys
        assert "scoring_validity" in keys
        assert "self_evolution_loop" in keys
        assert "runtime_stability" in keys

    @patch("app.main._build_submission_scoring_basis_report")
    @patch("app.main._resolve_submission_score_fields")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main._run_system_self_check")
    @patch("app.main._build_evolution_health_report")
    @patch("app.main._build_material_depth_report")
    @patch("app.main._build_scoring_readiness")
    @patch("app.main._now_iso", return_value="2026-02-28T00:00:00+00:00")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_mece_audit_marks_runtime_warn_for_optional_failures(
        self,
        mock_ensure,
        mock_load_projects,
        mock_now_iso,
        mock_build_readiness,
        mock_build_depth,
        mock_build_evo_health,
        mock_self_check,
        mock_load_submissions,
        mock_submission_is_scored,
        mock_resolve_score_fields,
        mock_scoring_basis,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_readiness.return_value = {
            "project_id": "p1",
            "ready": True,
            "gate_passed": True,
            "issues": [],
            "warnings": [],
            "material_quality": {"total_parsed_chars": 8600},
            "material_gate": {"required_types": ["tender_qa", "boq", "drawing"]},
        }
        mock_build_depth.return_value = {"quality_summary": {"total_files": 4}}
        mock_build_evo_health.return_value = {
            "summary": {
                "ground_truth_count": 4,
                "matched_prediction_count": 3,
                "has_evolved_multipliers": True,
                "stored_evolved_multipliers": True,
                "current_weights_source": "evolution",
            },
            "drift": {"level": "low"},
        }
        mock_self_check.return_value = {
            "ok": True,
            "items": [
                {"name": "health", "ok": True},
                {"name": "data_hygiene", "ok": False, "detail": "orphan_records=2"},
            ],
        }
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "created_at": "2026-02-27T00:00:00+00:00",
                "updated_at": "2026-02-27T00:00:00+00:00",
                "report": {"scoring_status": "done"},
            }
        ]
        mock_submission_is_scored.return_value = True
        mock_resolve_score_fields.return_value = {"total_score": 78.6}
        mock_scoring_basis.return_value = {
            "mece_inputs": {
                "project_materials_extracted": True,
                "shigong_parsed": True,
                "bid_requirements_loaded": True,
                "attention_16d_weights_injected": True,
                "custom_instructions_injected": True,
            },
            "evidence_trace": {"total_requirements": 10, "total_hits": 8},
        }

        response = client.get("/api/v1/projects/p1/mece_audit")
        assert response.status_code == 200
        rows = {row["key"]: row for row in response.json()["dimensions"]}
        assert rows["runtime_stability"]["status"] == "warn"
        assert any("data_hygiene" in str(x) for x in rows["runtime_stability"]["warnings"])

    @patch("app.main._build_evolution_health_report")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_project_evolution_health_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_health,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_health.return_value = {
            "project_id": "p1",
            "generated_at": "2026-02-27T00:00:00+00:00",
            "summary": {
                "ground_truth_count": 4,
                "matched_prediction_count": 4,
                "unmatched_ground_truth_count": 0,
                "current_weights_source": "evolution",
                "has_evolved_multipliers": True,
            },
            "windows": {
                "all": {"count": 4, "mae": 2.1, "rmse": 2.8},
                "recent_30d": {"count": 3, "mae": 1.9, "rmse": 2.4},
                "recent_90d": {"count": 4, "mae": 2.1, "rmse": 2.8},
                "prev_30_90d": {"count": 1, "mae": 2.7, "rmse": 2.7},
            },
            "drift": {"level": "low", "half_life_days": 30.0},
            "recommendations": ["继续录入最新真实评分以保持时效。"],
        }

        response = client.get("/api/v1/projects/p1/evolution/health")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["summary"]["ground_truth_count"] == 4
        assert data["drift"]["level"] == "low"

    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    def test_build_evolution_health_report_computes_error_windows(
        self,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_load_evo_reports,
        mock_resolve_scoring_context,
    ):
        from app.main import _build_evolution_health_report

        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "report": {"pred_total_score": 70.0, "score_scale_max": 100},
            }
        ]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt1",
                "project_id": "p1",
                "source_submission_id": "s1",
                "judge_scores": [75, 76, 77, 78, 79],
                "final_score": 78.0,
                "score_scale_max": 100,
                "created_at": "2026-02-26T00:00:00+00:00",
            }
        ]
        mock_load_evo_reports.return_value = {
            "p1": {
                "updated_at": "2026-02-26T08:00:00+00:00",
                "scoring_evolution": {"dimension_multipliers": {"01": 1.1}},
            }
        }
        mock_resolve_scoring_context.return_value = (
            {"01": 1.1, "02": 0.9},
            None,
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        payload = _build_evolution_health_report(
            "p1",
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )
        assert payload["project_id"] == "p1"
        assert payload["summary"]["ground_truth_count"] == 1
        assert payload["summary"]["matched_prediction_count"] == 1
        assert payload["windows"]["all"]["mae"] == pytest.approx(8.0, abs=1e-4)
        assert payload["windows"]["all"]["count"] == 1
        assert payload["drift"]["level"] in {"insufficient_data", "watch", "low", "medium", "high"}

    @patch("app.main.load_projects")
    @patch("app.main._resolve_project_scoring_context")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    def test_build_evolution_health_report_distinguishes_stored_vs_active_weights(
        self,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_load_evo_reports,
        mock_resolve_scoring_context,
        mock_load_projects,
    ):
        from app.main import _build_evolution_health_report

        mock_load_projects.return_value = [
            {
                "id": "p1",
                "meta": {
                    "score_scale_max": 100,
                    "evolution_weight_min_samples": 3,
                    "evolution_weight_max_age_days": 90,
                },
            }
        ]
        mock_load_submissions.return_value = []
        mock_load_ground_truth.return_value = []
        mock_load_evo_reports.return_value = {
            "p1": {
                "sample_count": 1,
                "updated_at": "2026-03-01T00:00:00+00:00",
                "scoring_evolution": {"dimension_multipliers": {"01": 1.2}},
            }
        }
        mock_resolve_scoring_context.return_value = (
            {"01": 1.0},
            {"id": "ep1", "weights_norm": {"01": 1.0}},
            {"id": "p1", "meta": {"score_scale_max": 100}},
        )

        payload = _build_evolution_health_report(
            "p1",
            {"id": "p1", "meta": {"score_scale_max": 100, "evolution_weight_min_samples": 3}},
        )
        summary = payload["summary"]
        assert summary["stored_evolved_multipliers"] is True
        assert summary["has_evolved_multipliers"] is False
        assert summary["current_weights_source"] == "expert_profile"
        assert summary["evolution_weights_inactive_reason"] == "sample_count_below_min"
        assert any("样本量未达到生效阈值" in str(x) for x in payload["recommendations"])

    @patch("app.main._resolve_dwg_converter_binaries", return_value=[])
    @patch("app.main.load_materials")
    @patch("app.main._build_scoring_readiness")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.pytesseract", None)
    @patch("app.main.Image", None)
    def test_get_material_depth_report_includes_capability_warnings(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_readiness,
        mock_load_materials,
        mock_resolve_dwg_bins,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_readiness.return_value = {
            "project_id": "p1",
            "ready": False,
            "material_quality": {
                "total_files": 2,
                "parsed_ok_files": 2,
                "parsed_failed_files": 0,
                "total_parsed_chars": 5000,
                "total_parsed_chunks": 8,
                "total_numeric_terms": 6,
                "parse_fail_ratio": 0.0,
                "counts_by_type": {"drawing": 1, "site_photo": 1},
                "chars_by_type": {"drawing": 4000, "site_photo": 1000},
                "chunks_by_type": {"drawing": 6, "site_photo": 2},
                "numeric_terms_by_type": {"drawing": 5, "site_photo": 1},
                "parsed_ok_by_type": {"drawing": 1, "site_photo": 1},
                "parsed_fail_by_type": {"drawing": 0, "site_photo": 0},
                "parsed_fail_details": [],
            },
            "material_gate": {"required_types": ["drawing", "site_photo"], "passed": True},
            "material_depth_gate": {"passed": True},
            "issues": [],
            "warnings": [],
        }
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dwg",
            },
            {
                "project_id": "p1",
                "material_type": "site_photo",
                "filename": "现场照片1.jpg",
            },
        ]

        response = client.get("/api/v1/projects/p1/materials/depth_report")
        assert response.status_code == 200
        data = response.json()
        assert data["capabilities"]["ocr_available"] is False
        assert data["capabilities"]["dwg_converter_available"] is False
        assert data["capabilities"]["dwg_file_count"] == 1
        assert data["capabilities"]["site_photo_file_count"] == 1
        rec_text = " ".join(data.get("recommendations") or [])
        assert "OCR" in rec_text
        assert "DWG" in rec_text

    @patch("app.main._resolve_dwg_converter_binaries", return_value=["/usr/local/bin/dwg2dxf"])
    @patch("app.main.load_materials")
    @patch("app.main._build_scoring_readiness")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_depth_report_markdown_contains_capability_section(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_readiness,
        mock_load_materials,
        mock_resolve_dwg_bins,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_readiness.return_value = {
            "project_id": "p1",
            "ready": True,
            "material_quality": {
                "total_files": 1,
                "parsed_ok_files": 1,
                "parsed_failed_files": 0,
                "total_parsed_chars": 3000,
                "total_parsed_chunks": 5,
                "total_numeric_terms": 3,
                "parse_fail_ratio": 0.0,
                "counts_by_type": {"drawing": 1},
                "chars_by_type": {"drawing": 3000},
                "chunks_by_type": {"drawing": 5},
                "numeric_terms_by_type": {"drawing": 3},
                "parsed_ok_by_type": {"drawing": 1},
                "parsed_fail_by_type": {"drawing": 0},
                "parsed_fail_details": [],
            },
            "material_gate": {"required_types": ["drawing"], "passed": True},
            "material_depth_gate": {"passed": True},
            "issues": [],
            "warnings": [],
        }
        mock_load_materials.return_value = [
            {"project_id": "p1", "material_type": "drawing", "filename": "总图.dwg"}
        ]

        response = client.get("/api/v1/projects/p1/materials/depth_report/markdown")
        assert response.status_code == 200
        markdown = response.json()["markdown"]
        assert "## 解析能力" in markdown
        assert "DWG 转换器可用" in markdown

    @patch("app.main._build_material_knowledge_profile")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_knowledge_profile_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_knowledge,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_knowledge.return_value = {
            "project_id": "p1",
            "generated_at": "2026-02-27T00:00:00+00:00",
            "capabilities": {"ocr_available": True, "dwg_converter_available": True},
            "summary": {
                "total_files": 4,
                "parsed_ok_files": 4,
                "numeric_category_summary": ["工期/节点：90", "数量/工程量：1200"],
                "covered_dimensions": 12,
                "dimension_coverage_rate": 0.75,
            },
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "files": 1,
                    "numeric_category_summary": ["工期/节点：90"],
                }
            ],
            "by_dimension": [
                {
                    "dimension_id": "01",
                    "dimension_name": "工程项目整体理解",
                    "coverage_score": 0.8,
                    "coverage_level": "high",
                }
            ],
            "recommendations": ["建议补充危大工程专项方案约束。"],
        }

        response = client.get("/api/v1/projects/p1/materials/knowledge_profile")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["summary"]["total_files"] == 4
        assert data["summary"]["numeric_category_summary"] == ["工期/节点：90", "数量/工程量：1200"]
        assert data["by_dimension"][0]["dimension_id"] == "01"
        assert data["recommendations"]

    @patch("app.main._build_material_knowledge_profile")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_material_knowledge_profile_markdown_contains_sections(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_knowledge,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_knowledge.return_value = {
            "project_id": "p1",
            "generated_at": "2026-02-27T00:00:00+00:00",
            "capabilities": {"ocr_available": False, "dwg_converter_available": False},
            "summary": {
                "total_files": 2,
                "parsed_ok_files": 2,
                "parsed_failed_files": 0,
                "total_parsed_chars": 1000,
                "total_parsed_chunks": 6,
                "total_numeric_terms": 12,
                "numeric_category_summary": ["工期/节点：90", "规格/参数：1200", "阈值/偏差：48"],
                "dimension_coverage_rate": 0.5,
            },
            "by_type": [
                {
                    "material_type_label": "招标文件和答疑",
                    "files": 1,
                    "parsed_chars": 600,
                    "parsed_chunks": 3,
                    "unique_terms": 18,
                    "numeric_terms": 4,
                    "numeric_category_summary": ["工期/节点：90", "阈值/偏差：48"],
                },
            ],
            "by_dimension": [
                {
                    "dimension_id": "01",
                    "dimension_name": "工程项目整体理解",
                    "keyword_hits": 6,
                    "source_types": ["tender_qa"],
                    "coverage_score": 0.62,
                    "coverage_level": "medium",
                }
            ],
            "recommendations": ["建议补充图纸节点与危大工程量化指标。"],
        }

        response = client.get("/api/v1/projects/p1/materials/knowledge_profile/markdown")
        assert response.status_code == 200
        markdown = response.json()["markdown"]
        assert "## 按资料类型" in markdown
        assert "## 按评分维度" in markdown
        assert "## 建议动作" in markdown
        assert "数值约束簇" in markdown

    @patch("app.main._resolve_dwg_converter_binaries", return_value=["/usr/local/bin/dwg2dxf"])
    @patch("app.main._now_iso", return_value="2026-02-27T00:00:00+00:00")
    @patch("app.main.load_materials")
    def test_build_material_knowledge_profile_extracts_dimension_coverage(
        self,
        mock_load_materials,
        mock_now_iso,
        mock_dwg_bins,
        tmp_path,
    ):
        from app.main import _build_material_knowledge_profile

        tender_path = tmp_path / "招标答疑.txt"
        boq_path = tmp_path / "工程量清单.txt"
        tender_path.write_text(
            "招标答疑要求：工期节点、质量标准、危大工程专项方案、应急预案必须明确。",
            encoding="utf-8",
        )
        boq_path.write_text(
            "清单工程量 1200m3，综合单价 580 元，措施费 30 万，设备进场计划。",
            encoding="utf-8",
        )
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标答疑.txt",
                "path": str(tender_path),
                "created_at": "2026-02-26T00:00:00+00:00",
            },
            {
                "project_id": "p1",
                "material_type": "boq",
                "filename": "工程量清单.txt",
                "path": str(boq_path),
                "created_at": "2026-02-26T00:00:01+00:00",
            },
        ]

        payload = _build_material_knowledge_profile("p1")
        assert payload["project_id"] == "p1"
        assert payload["summary"]["total_files"] == 2
        assert payload["summary"]["parsed_ok_files"] == 2
        assert payload["summary"]["covered_dimensions"] >= 1
        assert payload["summary"]["structured_quality_avg"] > 0
        assert payload["summary"]["numeric_category_summary"]
        assert payload["summary"]["cross_type_consensus_score"] > 0
        assert payload["summary"]["cross_type_consensus_type_count"] >= 2
        assert len(payload["by_type"]) >= 2
        assert any(row.get("numeric_category_summary") for row in payload["by_type"])
        dim_ids = {row["dimension_id"] for row in payload["by_dimension"]}
        assert "01" in dim_ids

    @patch("app.main._resolve_dwg_converter_binaries", return_value=["/usr/local/bin/dwg2dxf"])
    @patch("app.main._now_iso", return_value="2026-02-27T00:00:00+00:00")
    @patch("app.main.load_materials")
    def test_build_material_knowledge_profile_includes_structured_material_signals(
        self,
        mock_load_materials,
        mock_now_iso,
        mock_dwg_bins,
        tmp_path,
    ):
        from app.main import _build_material_knowledge_profile

        drawing_path = tmp_path / "总图.dxf"
        site_photo_path = tmp_path / "现场照片.txt"
        drawing_path.write_text(
            "0\nSECTION\n2\nENTITIES\n"
            "0\nTEXT\n8\nM-EQPM\n1\n节点深化 BIM 综合管线 碰撞 净高 预留预埋 600 3.5\n"
            "0\nINSERT\n8\nP-PIPE\n2\nPUMP_TAG\n"
            "0\nENDSEC\n0\nEOF\n",
            encoding="utf-8",
        )
        site_photo_path.write_text(
            "[图像资料] 文件: 现场.jpg\n" "[OCR文本提取]\n" "临边防护 扬尘治理 围挡 样板 实测 48",
            encoding="utf-8",
        )
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "drawing",
                "filename": "总图.dxf",
                "path": str(drawing_path),
                "created_at": "2026-02-26T00:00:01+00:00",
            },
            {
                "project_id": "p1",
                "material_type": "site_photo",
                "filename": "现场照片.txt",
                "path": str(site_photo_path),
                "created_at": "2026-02-26T00:00:02+00:00",
            },
        ]

        payload = _build_material_knowledge_profile("p1")
        by_type = {row["material_type"]: row for row in payload["by_type"]}
        assert by_type["drawing"]["structured_signal_count"] > 0
        assert by_type["drawing"]["structured_quality_score"] > 0
        assert "机电综合" in (by_type["drawing"]["structured_terms_preview"] or [])
        assert "14" in (by_type["drawing"]["focused_dimensions"] or [])
        assert by_type["site_photo"]["structured_signal_count"] > 0
        assert by_type["site_photo"]["structured_quality_score"] > 0
        assert "高处临边" in (by_type["site_photo"]["structured_terms_preview"] or [])
        by_dimension = {row["dimension_id"]: row for row in payload["by_dimension"]}
        assert by_dimension["14"]["structured_signal_hits"] > 0
        assert by_dimension["03"]["structured_signal_hits"] > 0

    @patch("app.main._resolve_dwg_converter_binaries", return_value=["/usr/local/bin/dwg2dxf"])
    @patch("app.main._now_iso", return_value="2026-02-27T00:00:00+00:00")
    @patch("app.main.load_materials")
    def test_build_material_knowledge_profile_includes_tender_qa_structured_signals(
        self,
        mock_load_materials,
        mock_now_iso,
        mock_dwg_bins,
        tmp_path,
    ):
        from app.main import _build_material_knowledge_profile

        tender_path = tmp_path / "招标答疑.txt"
        tender_path.write_text(
            "答疑澄清：总工期120日历天，评分办法要求BIM深化、危大工程专项方案、质量验收标准。"
            "投标文件必须响应关键节点，不得缺少专项方案。",
            encoding="utf-8",
        )
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标答疑.txt",
                "path": str(tender_path),
                "created_at": "2026-02-26T00:00:01+00:00",
            }
        ]

        payload = _build_material_knowledge_profile("p1")
        by_type = {row["material_type"]: row for row in payload["by_type"]}
        assert by_type["tender_qa"]["structured_signal_count"] > 0
        assert by_type["tender_qa"]["structured_quality_score"] > 0
        assert "工期/里程碑" in (by_type["tender_qa"]["structured_terms_preview"] or [])
        assert "09" in (by_type["tender_qa"]["focused_dimensions"] or [])
        assert by_type["tender_qa"]["mandatory_clause_terms_preview"]
        by_dimension = {row["dimension_id"]: row for row in payload["by_dimension"]}
        assert by_dimension["09"]["structured_signal_hits"] > 0

    @patch("app.main._now_iso", return_value="2026-02-27T00:00:00+00:00")
    @patch("app.main._build_project_material_index")
    def test_build_material_knowledge_profile_includes_outline_and_table_anchor_previews(
        self,
        mock_build_material_index,
        mock_now_iso,
    ):
        from app.main import _build_material_knowledge_profile

        mock_build_material_index.return_value = {
            "project_id": "p1",
            "available_types": ["tender_qa"],
            "files": [
                {
                    "material_type": "tender_qa",
                    "filename": "招标答疑.pdf",
                    "parsed_ok": True,
                    "text": "危大工程专项方案必须逐项响应，关键节点需闭环。",
                    "chunks": ["危大工程专项方案必须逐项响应。", "关键节点需闭环。"],
                    "created_at": "2026-02-26T00:00:01+00:00",
                    "lexical_terms": ["危大工程", "专项方案", "关键节点"],
                    "numeric_terms_norm": ["3", "120"],
                    "tender_qa_structured_summary": {
                        "structured_quality_score": 0.86,
                        "structured_terms": ["危大工程", "专项方案"],
                        "section_titles": ["评分办法", "专项方案要求"],
                        "scoring_point_terms": ["节点闭环"],
                        "mandatory_clause_terms": ["必须逐项响应"],
                        "focused_dimensions": ["09", "16"],
                        "top_numeric_terms": ["120"],
                        "section_title_paths": ["第一章", "危大工程专项方案"],
                        "document_outline": [
                            {
                                "page_no": 2,
                                "page_type": "scoring_rules",
                                "section_title": "危大工程专项方案",
                                "section_level": 1,
                                "section_path": ["第一章", "危大工程专项方案"],
                                "parse_confidence": 0.91,
                            }
                        ],
                        "table_constraint_rows": [
                            {
                                "page_no": 3,
                                "label": "专项方案响应表",
                                "value": "危大工程需逐项响应",
                                "numbers": ["3"],
                            }
                        ],
                        "table_numeric_constraints": ["3"],
                        "page_type_summary": [{"page_type": "scoring_rules", "count": 1}],
                    },
                }
            ],
            "quality_snapshot": {},
        }

        payload = _build_material_knowledge_profile("p1")
        by_type = {row["material_type"]: row for row in payload["by_type"]}
        assert "危大工程专项方案" in (by_type["tender_qa"]["outline_terms_preview"] or [])
        assert "专项方案响应表" in (by_type["tender_qa"]["table_constraint_terms_preview"] or [])
        assert by_type["tender_qa"]["structured_signal_count"] >= 8

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_accepts_doc_extension(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1", "scoring_engine_version_locked": "v1"}]
        mock_load_mat.return_value = []

        response = client.post(
            "/api/v1/projects/p1/materials",
            files={"file": ("招标文件.DOC ", BytesIO(b"doc-bytes"), "application/msword")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["material"]["filename"] == "招标文件.DOC"

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_accepts_by_mime_without_extension(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = []

        response = client.post(
            "/api/v1/projects/p1/materials",
            files={"file": ("招标文件", BytesIO(b"%PDF-1.4\\n"), "application/pdf")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["material"]["filename"] == "招标文件"

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_accepts_dxf_extension(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = []

        dxf_content = (
            "0\nSECTION\n2\nENTITIES\n0\nTEXT\n8\nA-TEXT\n1\n总平面布置图\n0\nENDSEC\n0\nEOF\n"
        ).encode("utf-8")
        response = client.post(
            "/api/v1/projects/p1/materials",
            files={"file": ("总图.dxf", BytesIO(dxf_content), "application/dxf")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["material"]["filename"] == "总图.dxf"

    @patch("app.main.save_materials")
    @patch("app.main.load_materials")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.MATERIALS_DIR")
    def test_upload_material_replaces_existing_same_filename(
        self, mock_dir, mock_ensure, mock_load_proj, mock_load_mat, mock_save, client, tmp_path
    ):
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_load_mat.return_value = [
            {
                "id": "m-old",
                "project_id": "p1",
                "filename": "test.txt",
                "path": str(tmp_path / "old.txt"),
                "created_at": "2026-02-17T00:00:00+00:00",
            },
            {
                "id": "m-dup",
                "project_id": "p1",
                "filename": " test.txt ",
                "path": str(tmp_path / "dup.txt"),
                "created_at": "2026-02-17T00:00:01+00:00",
            },
            {
                "id": "m-other",
                "project_id": "p1",
                "filename": "other.txt",
                "path": str(tmp_path / "other.txt"),
                "created_at": "2026-02-17T00:00:02+00:00",
            },
        ]

        response = client.post(
            "/api/v1/projects/p1/materials",
            files={"file": ("test.txt", BytesIO(b"new content"), "text/plain")},
        )
        assert response.status_code == 200
        saved_materials = mock_save.call_args[0][0]
        same_name = [
            m
            for m in saved_materials
            if m.get("project_id") == "p1" and m.get("filename") == "test.txt"
        ]
        assert len(same_name) == 1
        assert same_name[0]["id"] == "m-old"


class TestShigongEndpoint:
    """Tests for /projects/{project_id}/shigong endpoint."""

    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_success(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        client,
    ):
        """Upload shigong should only persist as pending without immediate scoring."""
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 80.0, "dimension_scores": {}, "penalties": []}
        )

        response = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("test.txt", BytesIO(b"test content"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["project_id"] == "p1"
        assert data["total_score"] == 0.0
        assert data["report"]["scoring_status"] == "pending"
        mock_score.assert_not_called()

    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_with_learning_profile(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        client,
    ):
        """Upload shigong should stay pending even when project has learning profile."""
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = [{"project_id": "p1", "dimension_multipliers": {"D01": 1.2}}]
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(model_dump=lambda: {"total_score": 80.0})

        response = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["report"]["scoring_status"] == "pending"
        mock_score.assert_not_called()

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_shigong_project_not_found(self, mock_ensure, mock_load, client):
        """Upload shigong should return 404 if project not found."""
        mock_load.return_value = []
        response = client.post(
            "/api/v1/projects/nonexistent/shigong",
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
        )
        assert response.status_code == 404

    @patch("app.main.record_history_score")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_deduplicates_recent_same_file(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_record_history,
        client,
    ):
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        saved_state = []

        def _load_state():
            return list(saved_state)

        def _save_state(submissions):
            saved_state[:] = list(submissions)

        mock_load_sub.side_effect = _load_state
        mock_save_sub.side_effect = _save_state
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 81.0, "dimension_scores": {}, "penalties": []}
        )

        first = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("dup.txt", BytesIO(b"same content"), "text/plain")},
        )
        second = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("dup.txt", BytesIO(b"same content"), "text/plain")},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_data = first.json()
        second_data = second.json()
        assert second_data["id"] == first_data["id"]
        assert len(saved_state) == 1
        assert mock_save_sub.call_count == 1
        assert mock_score.call_count == 0
        mock_record_history.assert_not_called()


class TestDXFParser:
    def test_read_uploaded_file_content_extracts_text_from_dxf(self):
        from app.main import _read_uploaded_file_content

        dxf_content = (
            "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n9\n$INSUNITS\n70\n6\n0\nENDSEC\n"
            "0\nSECTION\n2\nENTITIES\n0\nTEXT\n8\nA-TEXT\n1\n总平面布置图\n"
            "0\nMTEXT\n8\nA-NOTE\n3\n施工进度\n1\n计划节点与验收\n0\nENDSEC\n0\nEOF\n"
        ).encode("utf-8")
        text = _read_uploaded_file_content(dxf_content, "sample.dxf")
        assert "DXF解析摘要" in text
        assert "ACAD版本: AC1032" in text
        assert "插入单位: 6(米)" in text
        assert "总平面布置图" in text
        assert "计划节点与验收" in text


class TestMaterialAdvancedParsing:
    @patch("app.main.load_project_requirements", return_value=[])
    @patch("app.main.load_project_anchors", return_value=[])
    def test_build_material_query_features_includes_material_profile_boosts(
        self,
        mock_load_anchors,
        mock_load_requirements,
    ):
        from app.main import _build_material_query_features

        profile = {
            "by_type": [
                {
                    "material_type": "boq",
                    "top_terms": ["工程量", "综合单价", "措施费", "项目"],
                    "top_numeric_terms": ["120", "30", "580"],
                    "outline_terms_preview": ["资源配置章节", "机械投入计划"],
                    "table_constraint_terms_preview": ["主要机械投入表", "劳动力峰值计划"],
                }
            ],
            "by_dimension": [
                {
                    "dimension_id": "15",
                    "coverage_score": 0.76,
                    "source_file_count": 2,
                    "source_types": ["boq"],
                }
            ],
        }

        out = _build_material_query_features(
            project_id="p1",
            submission_text="计划调配与资源组织。",
            required_sections=[],
            required_charts=[],
            mandatory_elements=[],
            custom_text_items=[],
            context_text="",
            material_knowledge_profile=profile,
        )

        assert out["material_profile_query_terms_count"] >= 2
        assert out["material_profile_query_numeric_terms_count"] >= 1
        assert "15" in out["material_profile_focus_dimensions"]
        assert "工程量" in out["query_terms"]
        assert "主要机械投入表" in out["query_terms"]
        assert "120" in out["query_numeric_terms"]

    def test_build_material_dimension_requirements_from_knowledge_profile(self):
        from app.main import _build_material_dimension_requirements

        profile = {
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "top_terms": ["工期", "质量标准", "危大工程", "项目"],
                    "top_numeric_terms": ["120", "30"],
                    "structured_terms_preview": ["里程碑", "总控计划"],
                    "section_titles_preview": ["施工组织设计总体部署"],
                    "outline_terms_preview": ["施工部署章节", "危大工程专项方案"],
                    "table_constraint_terms_preview": ["关键节点响应表", "危大工程清单响应表"],
                    "scoring_point_terms_preview": ["bim", "深化"],
                    "mandatory_clause_terms_preview": ["投标文件必须响应关键节点"],
                    "structured_quality_score": 0.78,
                    "focused_dimensions": ["09"],
                    "numeric_category_summary": ["工期/节点：120、30"],
                },
                {
                    "material_type": "drawing",
                    "top_terms": ["节点", "剖面", "深化", "图纸"],
                    "top_numeric_terms": ["3.5", "600"],
                    "structured_terms_preview": ["机电综合", "专业碰撞", "预留预埋"],
                    "structured_quality_score": 0.62,
                    "focused_dimensions": ["14", "06", "12"],
                    "numeric_category_summary": ["规格/参数：3.5、600"],
                },
            ],
            "by_dimension": [
                {
                    "dimension_id": "09",
                    "dimension_name": "进度计划体系与纠偏阈值",
                    "coverage_score": 0.81,
                    "source_file_count": 2,
                    "source_types": ["tender_qa"],
                },
                {
                    "dimension_id": "14",
                    "dimension_name": "图纸会审、深化设计与变更闭环",
                    "coverage_score": 0.72,
                    "source_file_count": 1,
                    "source_types": ["drawing"],
                },
            ],
        }

        reqs = _build_material_dimension_requirements(
            "p1",
            profile,
            available_material_types=["tender_qa", "drawing"],
        )

        assert len(reqs) == 2
        dim09 = next(r for r in reqs if r.get("dimension_id") == "09")
        patterns = dim09.get("patterns") or {}
        assert dim09.get("source_pack_id") == "runtime_material_dimension"
        assert dim09.get("source_pack_version") == "v2-material-dimension-4"
        assert patterns.get("source_mode") == "material_knowledge_profile"
        assert "tender_qa" in (patterns.get("source_types") or [])
        assert "120" in (patterns.get("top_numeric_terms") or [])
        assert "里程碑" in (patterns.get("structured_terms") or [])
        assert "施工组织设计总体部署" in (patterns.get("section_titles") or [])
        assert "危大工程专项方案" in (patterns.get("outline_terms") or [])
        assert "关键节点响应表" in (patterns.get("table_constraint_terms") or [])
        assert "bim" in [str(x).lower() for x in (patterns.get("scoring_point_terms") or [])]
        assert patterns.get("mandatory_clause_terms")
        assert int(patterns.get("minimum_hint_hits") or 0) >= 3
        assert float(patterns.get("strongest_structured_quality") or 0.0) >= 0.7
        dim14 = next(r for r in reqs if r.get("dimension_id") == "14")
        dim14_patterns = dim14.get("patterns") or {}
        assert "专业碰撞" in (dim14_patterns.get("structured_terms") or [])
        assert "14" in (dim14_patterns.get("focused_dimensions") or [])
        assert int(dim14_patterns.get("structured_alignment_hits") or 0) >= 1

    def test_build_material_consensus_requirements_from_knowledge_profile(self):
        from app.main import _build_material_consensus_requirements

        profile = {
            "summary": {
                "cross_type_consensus_score": 0.66,
                "cross_type_focus_dimensions": ["09", "14"],
                "cross_type_consensus_terms": ["关键节点", "深化设计", "BIM"],
                "cross_type_numeric_category_summary": [
                    "工期/节点：120、30",
                    "规格/参数：600、3.5",
                ],
            },
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "parsed_ok_files": 1,
                    "structured_quality_score": 0.82,
                },
                {
                    "material_type": "drawing",
                    "parsed_ok_files": 1,
                    "structured_quality_score": 0.74,
                },
                {
                    "material_type": "boq",
                    "parsed_ok_files": 1,
                    "structured_quality_score": 0.68,
                },
            ],
        }

        reqs = _build_material_consensus_requirements(
            "p1",
            profile,
            available_material_types=["tender_qa", "drawing", "boq"],
        )

        assert len(reqs) >= 2
        duration_req = next(row for row in reqs if row.get("dimension_id") == "09")
        duration_patterns = duration_req.get("patterns") or {}
        assert duration_req.get("source_pack_id") == "runtime_material_consensus"
        assert duration_patterns.get("source_mode") == "cross_material_consensus"
        assert "120" in (duration_patterns.get("must_hit_numbers") or [])
        assert "关键节点" in " ".join(duration_patterns.get("must_hit_terms") or [])
        assert float(duration_patterns.get("consensus_score") or 0.0) >= 0.6

    @patch("app.main.select_top_logic_skeletons")
    def test_build_runtime_feature_requirements_uses_high_confidence_skeletons(
        self,
        mock_select_top_logic_skeletons,
    ):
        from app.main import _build_runtime_feature_requirements
        from app.schemas import ExtractedFeature

        mock_select_top_logic_skeletons.side_effect = lambda dimension_ids, top_k=2: (
            [
                ExtractedFeature(
                    feature_id=f"feat-{dimension_ids[0]}",
                    dimension_id=dimension_ids[0],
                    logic_skeleton=[
                        "[前置条件] 风险边界清晰 + [技术/动作] 动作链与责任分工 + [量化指标类型] 频次阈值与闭环验收"
                    ],
                    confidence_score=0.82,
                    usage_count=5,
                    active=True,
                )
            ]
            if dimension_ids and dimension_ids[0] in {"09", "14"}
            else []
        )

        profile = {
            "by_dimension": [
                {
                    "dimension_id": "14",
                    "coverage_score": 0.76,
                    "source_file_count": 2,
                    "source_types": ["drawing"],
                }
            ]
        }
        reqs = _build_runtime_feature_requirements(
            "p1",
            material_knowledge_profile=profile,
            weights_norm={"09": 0.2, "14": 0.12, "01": 0.04},
        )
        assert reqs
        dim_ids = {str(row.get("dimension_id")) for row in reqs}
        assert {"09", "14"}.issubset(dim_ids)
        dim14 = next(row for row in reqs if str(row.get("dimension_id")) == "14")
        patterns = dim14.get("patterns") or {}
        assert patterns.get("source_mode") == "feature_confidence_loop"
        assert "feat-14" in (patterns.get("feature_ids") or [])
        assert "动作链与责任分工" in " ".join(patterns.get("hints") or [])
        assert float(dim14.get("weight") or 0.0) > 1.0

    @patch("app.main.load_evolution_reports")
    def test_build_runtime_feedback_requirements_uses_evolution_multipliers(
        self,
        mock_load_evolution_reports,
    ):
        from app.main import _build_runtime_feedback_requirements

        mock_load_evolution_reports.return_value = {
            "p1": {
                "sample_count": 4,
                "scoring_evolution": {
                    "dimension_multipliers": {"09": 1.16, "14": 1.08, "03": 0.97},
                    "rationale": {
                        "09": "高分施组在进度计划与里程碑闭环方面更强。",
                        "14": "高分施组在图纸深化、节点与碰撞复核方面更强。",
                    },
                },
            }
        }
        reqs = _build_runtime_feedback_requirements(
            "p1",
            material_knowledge_profile={
                "by_dimension": [
                    {
                        "dimension_id": "09",
                        "coverage_score": 0.31,
                        "suggested_keywords": ["关键节点", "里程碑", "纠偏"],
                    },
                    {
                        "dimension_id": "14",
                        "coverage_score": 0.76,
                        "suggested_keywords": ["图纸", "深化", "碰撞"],
                    },
                ]
            },
        )
        dim_ids = {str(row.get("dimension_id")) for row in reqs}
        assert {"09", "14"}.issubset(dim_ids)
        dim09 = next(row for row in reqs if str(row.get("dimension_id")) == "09")
        patterns = dim09.get("patterns") or {}
        assert patterns.get("source_mode") == "feedback_evolution"
        assert patterns.get("multiplier") == pytest.approx(1.16, abs=1e-6)
        assert patterns.get("sample_count") == 4
        assert "关键节点" in (patterns.get("hints") or [])

    @patch("app.main.load_ground_truth")
    @patch("app.main.load_evolution_reports")
    def test_build_runtime_feedback_requirements_uses_recent_ground_truth_feedback_without_evo(
        self,
        mock_load_evolution_reports,
        mock_load_ground_truth,
    ):
        from app.main import _build_runtime_feedback_requirements

        mock_load_evolution_reports.return_value = {"p1": {}}
        mock_load_ground_truth.return_value = [
            {
                "project_id": "p1",
                "created_at": "2026-03-08T12:00:00+08:00",
                "feature_confidence_update": {
                    "applied_dimension_ids": ["09"],
                    "applied_feature_ids": ["F-09"],
                    "delta_score_100": 6.0,
                    "updated": 1,
                },
            }
        ]
        reqs = _build_runtime_feedback_requirements(
            "p1",
            material_knowledge_profile={
                "by_dimension": [
                    {
                        "dimension_id": "09",
                        "coverage_score": 0.28,
                        "suggested_keywords": ["关键节点", "里程碑", "纠偏"],
                    }
                ]
            },
        )
        assert reqs
        dim09 = next(row for row in reqs if str(row.get("dimension_id")) == "09")
        patterns = dim09.get("patterns") or {}
        assert patterns.get("recent_feedback_count") == 1
        assert patterns.get("recent_feedback_positive_count") == 1
        assert float(patterns.get("recent_feedback_avg_delta_100") or 0.0) > 0.0
        assert float(dim09.get("weight") or 0.0) > 1.0
        assert float(dim09.get("weight") or 0.0) > 1.0

    @patch("app.main.load_feature_kb")
    def test_build_feature_confidence_summary_marks_project_focus_dimensions(
        self,
        mock_load_feature_kb,
    ):
        from app.main import _build_feature_confidence_summary
        from app.schemas import ExtractedFeature

        mock_load_feature_kb.return_value = [
            ExtractedFeature(
                feature_id="f-09",
                dimension_id="09",
                logic_skeleton=[
                    "[前置条件] 关键节点明确 + [技术/动作] 总控计划纠偏 + [量化指标类型] 偏差阈值与闭环"
                ],
                confidence_score=0.83,
                usage_count=6,
                active=True,
            ),
            ExtractedFeature(
                feature_id="f-14",
                dimension_id="14",
                logic_skeleton=[
                    "[前置条件] 图纸接口明确 + [技术/动作] 深化碰撞复核 + [量化指标类型] 节点闭环"
                ],
                confidence_score=0.72,
                usage_count=4,
                active=True,
            ),
            ExtractedFeature(
                feature_id="f-03-old",
                dimension_id="03",
                logic_skeleton=[
                    "[前置条件] 文明施工场景明确 + [技术/动作] 扬尘围挡联动 + [量化指标类型] 频次记录"
                ],
                confidence_score=0.18,
                usage_count=5,
                active=False,
            ),
        ]
        summary = _build_feature_confidence_summary(
            "p1",
            material_knowledge_profile={
                "by_dimension": [
                    {"dimension_id": "14", "coverage_score": 0.76},
                    {"dimension_id": "09", "coverage_score": 0.61},
                ]
            },
        )
        assert summary["active_count"] == 2
        assert summary["retired_count"] == 1
        assert summary["high_confidence_count"] == 2
        assert summary["focus_dimensions"] == ["14", "09"]
        top_dim_ids = [row["dimension_id"] for row in summary["top_dimensions"]]
        assert top_dim_ids[:2] == ["09", "14"] or top_dim_ids[:2] == ["14", "09"]

    def test_build_material_utilization_summary_tracks_dimension_requirements(self):
        from app.main import _build_material_utilization_summary

        report = {
            "requirement_hits": [
                {
                    "source_pack_id": "runtime_material_dimension",
                    "dimension_id": "09",
                    "label": "资料维度约束：进度计划",
                    "hit": True,
                    "source_types": ["tender_qa", "boq"],
                },
                {
                    "source_pack_id": "runtime_material_dimension",
                    "dimension_id": "14",
                    "label": "资料维度约束：图纸深化",
                    "hit": False,
                    "source_types": ["drawing"],
                },
            ]
        }
        runtime_req_meta = {
            "material_profile_query_terms_count": 6,
            "material_profile_query_numeric_terms_count": 3,
            "material_profile_focus_dimensions": ["09", "14"],
        }

        summary = _build_material_utilization_summary(report, runtime_req_meta)

        assert summary["material_dimension_total"] == 2
        assert summary["material_dimension_hit"] == 1
        assert summary["material_dimension_hit_rate"] == pytest.approx(0.5, abs=1e-6)
        assert summary["material_profile_query_terms_count"] == 6
        assert summary["material_profile_focus_dimensions"] == ["09", "14"]
        first_dim = summary["material_dimension_by_dimension"][0]
        assert first_dim["dimension_id"] in {"09", "14"}

    def test_build_boq_structured_summary_from_csv(self):
        from app.main import _build_boq_structured_summary

        csv_content = (
            "项目编码,项目名称,单位,工程量,综合单价,合价\n"
            "010101001,土方开挖,m3,100,35.5,3550\n"
            "010201001,钢筋制作,t,12.5,4300,53750\n"
        ).encode("utf-8")
        summary = _build_boq_structured_summary(
            csv_content,
            "boq.csv",
            parsed_text="安全文明措施 人工 工日 机械 措施项目 夜间施工",
        )
        assert summary["detected_format"] == "csv"
        assert summary["total_parsed_items"] == 2
        assert summary["total_amount"] == 57300.0
        first_sheet = summary["sheets"][0]
        assert first_sheet["detected_columns"]["code"] == 0
        assert first_sheet["detected_columns"]["amount"] == 5
        assert "工程量" in (summary.get("structured_terms") or [])
        assert "安全文明/绿色措施" in (summary.get("cost_structure_tags") or [])
        assert "人工与班组投入" in (summary.get("cost_structure_tags") or [])
        assert "措施项目/抢工" in (summary.get("cost_structure_tags") or [])
        assert "13" in (summary.get("focused_dimensions") or [])
        assert "11" in (summary.get("focused_dimensions") or [])
        assert summary["structured_quality_score"] > 0

    def test_build_tender_qa_structured_summary_extracts_constraint_tags(self):
        from app.main import _build_tender_qa_structured_summary

        parsed_text = (
            "第一章 施工组织设计总体部署\n"
            "第二章 质量管理与验收标准\n"
            "招标范围包含装修及机电改造，答疑澄清明确总工期120日历天。\n"
            "评分办法要求体现BIM深化、危大工程专项方案、绿色施工与质量验收标准。\n"
            "投标文件必须响应关键节点，不得缺少专项方案。"
        )
        summary = _build_tender_qa_structured_summary(
            b"TENDERDATA",
            "招标答疑.pdf",
            parsed_text=parsed_text,
        )
        assert "工期/里程碑" in (summary.get("constraint_tags") or [])
        assert "信息化/BIM" in (summary.get("constraint_tags") or [])
        assert "危大工程/专项方案" in (summary.get("constraint_tags") or [])
        assert "120" in (summary.get("top_numeric_terms") or [])
        assert "09" in (summary.get("focused_dimensions") or [])
        assert "05" in (summary.get("focused_dimensions") or [])
        assert "施工组织设计总体部署" in (summary.get("section_titles") or [])
        assert "bim" in [str(x).lower() for x in (summary.get("scoring_point_terms") or [])]
        assert summary["structured_quality_score"] > 0
        assert any(
            "必须" in str(x) or "不得" in str(x)
            for x in (summary.get("mandatory_clause_terms") or [])
        )

    def test_extract_tender_qa_section_titles_tolerates_ocr_spacing(self):
        from app.main import _extract_tender_qa_section_titles

        parsed_text = (
            "第 一 章 施 工 组 织 设 计 总 体 部 署\n"
            "1 . 2 质 量 管 理 与 验 收 标 准\n"
            "二 、 进 度 计 划 与 节 点 控 制\n"
        )
        titles = _extract_tender_qa_section_titles(parsed_text, limit=6)
        assert any("施工组织设计总体部署" in item for item in titles)
        assert any("质量管理与验收标准" in item for item in titles)
        assert any("进度计划与节点控制" in item for item in titles)

    def test_filter_structured_signal_terms_prefers_material_specific_terms(self):
        from app.main import _filter_structured_signal_terms

        terms = _filter_structured_signal_terms(
            [
                "文件",
                "资料",
                "text",
                "layer1",
                "综合管线",
                "综合管线净高复核",
                "节点详图",
                "A1",
                "BIM",
            ],
            limit=6,
            material_type="drawing",
        )

        assert "综合管线净高复核" in terms
        assert "节点详图" in terms
        assert "BIM" in terms
        assert "文件" not in terms
        assert "资料" not in terms
        assert "layer1" not in terms
        assert "综合管线" not in terms

    def test_build_drawing_structured_summary_extracts_structured_signals(self):
        from app.main import _build_drawing_structured_summary

        parsed_text = (
            "[DWG预处理] 文件: 总图.dwg\n"
            "实体统计: LINE:12、TEXT:3、INSERT:2\n"
            "图层: A-ANNO-TEXT、M-EQPM、P-PIPE\n"
            "块参照: DOOR_TAG、PUMP_TAG\n"
            "布局/空间: 首层平面、屋面层\n"
            "标注值: 600、3500\n"
            "二进制标识提取: PLAN_VIEW、MEP_ROUTE\n"
            "节点 深化 BIM 综合管线 碰撞 净高 预留预埋 消防 管径 600 3.5"
        )
        summary = _build_drawing_structured_summary(b"DWGDATA", "总图.dwg", parsed_text=parsed_text)
        assert summary["detected_format"] == "dwg"
        assert "A-ANNO-TEXT" in (summary.get("top_layers") or [])
        assert "首层平面" in (summary.get("layout_tags") or [])
        assert "3500" in (summary.get("dimension_markers") or [])
        assert "PLAN_VIEW" in (summary.get("binary_marker_terms") or [])
        assert "机电综合" in (summary.get("discipline_keywords") or [])
        assert "总平面/平面布置" in (summary.get("sheet_type_tags") or [])
        assert "专业碰撞" in (summary.get("risk_keywords") or [])
        assert "14" in (summary.get("focused_dimensions") or [])
        assert "12" in (summary.get("focused_dimensions") or [])
        assert "600" in (summary.get("top_numeric_terms") or [])
        assert summary["structured_quality_score"] > 0

    def test_build_site_photo_structured_summary_extracts_scene_tags(self):
        from app.main import _build_site_photo_structured_summary

        parsed_text = (
            "[图像资料] 文件: 现场.jpg\n"
            "格式: JPEG\n"
            "OCR模式: gray_2x:psm6\n"
            "OCR质量分: 4.8\n"
            "[OCR文本提取]\n"
            "临边防护 扬尘治理 围挡 道路冲洗 样板 实测 夜间施工 材料进场 48 3"
        )
        summary = _build_site_photo_structured_summary(
            b"IMGDATA",
            "现场.jpg",
            parsed_text=parsed_text,
        )
        assert summary["visual_capability"] == "ocr_multistage"
        assert summary["ocr_mode"] == "gray_2x:psm6"
        assert float(summary["ocr_quality_score"]) > 0
        assert "高处临边" in (summary.get("safety_scene_tags") or [])
        assert "扬尘治理" in (summary.get("civilization_scene_tags") or [])
        assert "样板实测" in (summary.get("quality_scene_tags") or [])
        assert "夜间施工" in (summary.get("progress_scene_tags") or [])
        assert "03" in (summary.get("focused_dimensions") or [])
        assert "09" in (summary.get("focused_dimensions") or [])
        assert summary["structured_quality_score"] > 0

    @patch("app.main.load_materials")
    def test_merge_materials_text_includes_tender_structured_summary(
        self, mock_load_materials, tmp_path
    ):
        from app.main import _merge_materials_text

        tender_path = tmp_path / "招标答疑.txt"
        tender_path.write_text(
            "答疑澄清：总工期120日历天，评分办法要求BIM深化和专项方案。",
            encoding="utf-8",
        )
        mock_load_materials.return_value = [
            {
                "project_id": "p1",
                "material_type": "tender_qa",
                "filename": "招标答疑.txt",
                "path": str(tender_path),
                "created_at": "2026-02-26T00:00:01+00:00",
            }
        ]

        merged = _merge_materials_text("p1")
        assert "[招标答疑结构化摘要]" in merged
        assert "工期/里程碑" in merged

    @patch("app.main.shutil.which")
    def test_read_uploaded_file_content_dwg_uses_preprocess_chain(self, mock_which):
        from app.main import _read_uploaded_file_content

        mock_which.return_value = None
        text = _read_uploaded_file_content(b"DWGDATA", "sample.dwg")
        assert "[DWG图纸]" in text
        assert "DWG预处理" in text
        assert "建议同时上传 PDF 或 ASCII DXF" in text

    @patch("app.main.shutil.which")
    def test_resolve_dwg_converter_binaries_uses_env_and_defaults(self, mock_which):
        from app.main import _resolve_dwg_converter_binaries

        def _fake_which(name: str):
            mapping = {
                "my_dwg_converter": "/opt/tools/my_dwg_converter",
                "dwg2dxf": "/usr/local/bin/dwg2dxf",
            }
            return mapping.get(name)

        mock_which.side_effect = _fake_which
        with patch.dict(os.environ, {"DWG_CONVERTER_BIN": "my_dwg_converter"}, clear=False):
            bins = _resolve_dwg_converter_binaries()
        assert "/opt/tools/my_dwg_converter" in bins
        assert "/usr/local/bin/dwg2dxf" in bins

    def test_read_uploaded_file_content_dwg_detects_ascii_dxf_payload(self):
        from app.main import _read_uploaded_file_content

        dxf_content = (
            "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n0\nENDSEC\n"
            "0\nSECTION\n2\nENTITIES\n0\nTEXT\n8\nA-TEXT\n1\n机电综合节点\n0\nENDSEC\n0\nEOF\n"
        ).encode("utf-8")
        text = _read_uploaded_file_content(dxf_content, "looks_like.dwg")
        assert "检测到ASCII DXF内容" in text
        assert "DXF解析摘要" in text

    def test_build_material_consistency_requirements_from_retrieval_chunks(self):
        from app.main import _build_material_consistency_requirements

        retrieval_chunks = [
            {
                "material_type": "boq",
                "dimension_id": "13",
                "filename": "清单.xlsx",
                "chunk_id": "清单.xlsx#c001",
                "matched_terms": ["工程量", "综合单价", "措施费"],
                "chunk_preview": "工程量与综合单价控制要求。",
            },
            {
                "material_type": "drawing",
                "dimension_id": "14",
                "filename": "总图.dxf",
                "chunk_id": "总图.dxf#c002",
                "matched_terms": ["节点", "剖面", "深化"],
                "chunk_preview": "节点做法与剖面表达。",
            },
        ]
        reqs = _build_material_consistency_requirements("p1", retrieval_chunks)
        assert len(reqs) == 2
        boq_req = next(r for r in reqs if r.get("material_type") == "boq")
        assert boq_req.get("req_type") == "material_consistency"
        assert boq_req.get("source_pack_id") == "runtime_material_consistency"

    def test_build_material_consistency_requirements_fallback_for_available_types(self):
        from app.main import _build_material_consistency_requirements

        reqs = _build_material_consistency_requirements(
            "p1",
            [],
            available_material_types=["tender_qa", "boq", "drawing"],
        )
        assert len(reqs) == 3
        boq_req = next(r for r in reqs if r.get("material_type") == "boq")
        terms = (boq_req.get("patterns") or {}).get("must_hit_terms") or []
        assert "工程量" in terms
        assert (boq_req.get("patterns") or {}).get("source_mode") == "fallback_keywords"

    def test_build_material_consistency_requirements_includes_numeric_constraints(self):
        from app.main import _build_material_consistency_requirements

        retrieval_chunks = [
            {
                "material_type": "tender_qa",
                "dimension_id": "09",
                "filename": "答疑.txt",
                "chunk_id": "答疑.txt#c001",
                "matched_terms": ["工期", "节点"],
                "matched_numeric_terms": ["120", "30"],
                "chunk_preview": "总工期120日历天，里程碑节点30天。",
            }
        ]
        reqs = _build_material_consistency_requirements("p1", retrieval_chunks)
        tender_req = next(r for r in reqs if r.get("material_type") == "tender_qa")
        patterns = tender_req.get("patterns") or {}
        numbers = patterns.get("must_hit_numbers") or []
        assert "120" in numbers
        assert int(patterns.get("minimum_numbers") or 0) >= 1

    @patch("app.main.load_materials")
    @patch("app.main._read_uploaded_file_content")
    def test_select_material_retrieval_chunks_keeps_type_diversity(
        self, mock_read_uploaded_file_content, mock_load_materials
    ):
        from app.main import _select_material_retrieval_chunks

        with tempfile.TemporaryDirectory() as tmp:
            tender = Path(tmp) / "tender.txt"
            boq = Path(tmp) / "boq.txt"
            drawing = Path(tmp) / "drawing.txt"
            tender.write_text("答疑 工期 质量标准 节点 闭环", encoding="utf-8")
            boq.write_text("工程量 综合单价 措施费", encoding="utf-8")
            drawing.write_text("图纸 节点 剖面 深化 BIM", encoding="utf-8")
            mock_load_materials.return_value = [
                {
                    "project_id": "p1",
                    "material_type": "tender_qa",
                    "filename": "tender.txt",
                    "path": str(tender),
                    "created_at": "2026-02-26T00:00:01+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "boq.txt",
                    "path": str(boq),
                    "created_at": "2026-02-26T00:00:02+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "drawing",
                    "filename": "drawing.txt",
                    "path": str(drawing),
                    "created_at": "2026-02-26T00:00:03+00:00",
                },
            ]
            mock_read_uploaded_file_content.side_effect = lambda _content, filename: Path(
                tmp, filename
            ).read_text(encoding="utf-8")
            chunks = _select_material_retrieval_chunks("p1", "工期 节点 工程量", top_k=6)
            types = {str(c.get("material_type")) for c in chunks}
            assert {"tender_qa", "boq", "drawing"}.issubset(types)

    @patch("app.main.load_materials")
    @patch("app.main._split_material_text_chunks")
    @patch("app.main._read_uploaded_file_content")
    def test_select_material_retrieval_chunks_respects_per_type_quota(
        self, mock_read_uploaded_file_content, mock_split_chunks, mock_load_materials
    ):
        from app.main import _select_material_retrieval_chunks

        with tempfile.TemporaryDirectory() as tmp:
            tender = Path(tmp) / "tender.txt"
            boq = Path(tmp) / "boq.txt"
            drawing = Path(tmp) / "drawing.txt"
            tender.write_text(
                "答疑 工期 节点 质量。\n\n招标条件 工期 质量 节点 约束。",
                encoding="utf-8",
            )
            boq.write_text(
                "工程量 综合单价 措施费。\n\n工程量 清单 措施项目 计量规则。",
                encoding="utf-8",
            )
            drawing.write_text(
                "图纸 节点 剖面 深化。\n\nBIM 节点 深化 剖面 图纸。",
                encoding="utf-8",
            )
            mock_load_materials.return_value = [
                {
                    "project_id": "p1",
                    "material_type": "tender_qa",
                    "filename": "tender.txt",
                    "path": str(tender),
                    "created_at": "2026-02-26T00:00:01+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "boq.txt",
                    "path": str(boq),
                    "created_at": "2026-02-26T00:00:02+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "drawing",
                    "filename": "drawing.txt",
                    "path": str(drawing),
                    "created_at": "2026-02-26T00:00:03+00:00",
                },
            ]
            mock_read_uploaded_file_content.side_effect = lambda _content, filename: Path(
                tmp, filename
            ).read_text(encoding="utf-8")
            mock_split_chunks.return_value = [
                "工期 节点 工程量 质量 安全",
                "BIM 节点 进度 工程量 清单",
            ]
            chunks = _select_material_retrieval_chunks(
                "p1",
                "工期 节点 工程量",
                top_k=9,
                per_type_quota=2,
            )
            by_type = Counter(str(c.get("material_type")) for c in chunks)
            assert by_type["tender_qa"] >= 2
            assert by_type["boq"] >= 2
            assert by_type["drawing"] >= 2

    @patch("app.main.load_materials")
    @patch("app.main._split_material_text_chunks")
    @patch("app.main._read_uploaded_file_content")
    def test_select_material_retrieval_chunks_respects_per_file_quota(
        self, mock_read_uploaded_file_content, mock_split_chunks, mock_load_materials
    ):
        from app.main import _select_material_retrieval_chunks

        with tempfile.TemporaryDirectory() as tmp:
            tender = Path(tmp) / "tender.txt"
            boq = Path(tmp) / "boq.txt"
            drawing = Path(tmp) / "drawing.txt"
            tender.write_text("答疑 工期 节点 质量", encoding="utf-8")
            boq.write_text("工程量 综合单价 措施费", encoding="utf-8")
            drawing.write_text("图纸 节点 剖面 深化", encoding="utf-8")
            mock_load_materials.return_value = [
                {
                    "project_id": "p1",
                    "material_type": "tender_qa",
                    "filename": "tender.txt",
                    "path": str(tender),
                    "created_at": "2026-02-26T00:00:01+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "boq.txt",
                    "path": str(boq),
                    "created_at": "2026-02-26T00:00:02+00:00",
                },
                {
                    "project_id": "p1",
                    "material_type": "drawing",
                    "filename": "drawing.txt",
                    "path": str(drawing),
                    "created_at": "2026-02-26T00:00:03+00:00",
                },
            ]
            mock_read_uploaded_file_content.side_effect = lambda _content, filename: Path(
                tmp, filename
            ).read_text(encoding="utf-8")
            mock_split_chunks.return_value = [
                "工期 节点 工程量 质量 安全",
                "BIM 节点 进度 工程量 清单",
                "剖面 节点 深化 质量 施工",
            ]
            chunks = _select_material_retrieval_chunks(
                "p1",
                "工期 节点 工程量",
                top_k=9,
                per_type_quota=2,
                per_file_quota=1,
            )
            by_filename = Counter(str(c.get("filename")) for c in chunks)
            assert all(cnt <= 1 for cnt in by_filename.values())
            assert len(by_filename) == 3

    @patch("app.main.load_materials")
    @patch("app.main._split_material_text_chunks")
    @patch("app.main._read_uploaded_file_content")
    def test_select_material_retrieval_chunks_supports_numeric_query_hits(
        self, mock_read_uploaded_file_content, mock_split_chunks, mock_load_materials
    ):
        from app.main import _select_material_retrieval_chunks

        with tempfile.TemporaryDirectory() as tmp:
            boq = Path(tmp) / "boq.txt"
            boq.write_text("120 30 8000", encoding="utf-8")
            mock_load_materials.return_value = [
                {
                    "project_id": "p1",
                    "material_type": "boq",
                    "filename": "boq.txt",
                    "path": str(boq),
                    "created_at": "2026-02-26T00:00:02+00:00",
                },
            ]
            mock_read_uploaded_file_content.return_value = boq.read_text(encoding="utf-8")
            mock_split_chunks.return_value = ["120 30 8000"]
            chunks = _select_material_retrieval_chunks(
                "p1",
                "120 30",
                top_k=3,
                per_type_quota=1,
                per_file_quota=2,
                query_terms_extra=[],
                query_numeric_terms=["120", "30"],
            )
            assert chunks
            numeric_hits = chunks[0].get("matched_numeric_terms") or []
            assert "120" in numeric_hits or "30" in numeric_hits

    def test_select_material_retrieval_chunks_prefers_high_quality_structured_file(self):
        from app.main import _select_material_retrieval_chunks

        material_index = {
            "available_types": ["drawing"],
            "files": [
                {
                    "material_type": "drawing",
                    "filename": "low_quality.dwg",
                    "parsed_ok": True,
                    "chunks": ["综合管线 节点 深化"],
                    "drawing_structured_summary": {
                        "structured_quality_score": 0.15,
                        "structured_terms": ["综合管线", "文件", "资料"],
                    },
                },
                {
                    "material_type": "drawing",
                    "filename": "high_quality.dwg",
                    "parsed_ok": True,
                    "chunks": ["综合管线净高复核 节点详图 BIM 深化"],
                    "drawing_structured_summary": {
                        "structured_quality_score": 0.88,
                        "structured_terms": ["综合管线净高复核", "节点详图", "BIM"],
                    },
                },
            ],
        }

        chunks = _select_material_retrieval_chunks(
            "p1",
            "综合管线 节点 BIM",
            top_k=1,
            per_type_quota=1,
            per_file_quota=1,
            material_index=material_index,
        )

        assert chunks
        assert chunks[0]["filename"] == "high_quality.dwg"
        assert float(chunks[0].get("file_structured_quality") or 0.0) >= 0.88

    def test_select_material_retrieval_chunks_uses_file_level_deep_read_anchors(self):
        from app.main import _select_material_retrieval_chunks

        material_index = {
            "available_types": ["tender_qa"],
            "files": [
                {
                    "material_type": "tender_qa",
                    "filename": "generic.pdf",
                    "parsed_ok": True,
                    "chunks": ["详见附件执行。"],
                    "tender_qa_structured_summary": {
                        "structured_quality_score": 0.72,
                        "structured_terms": ["一般要求"],
                    },
                },
                {
                    "material_type": "tender_qa",
                    "filename": "anchor.pdf",
                    "parsed_ok": True,
                    "chunks": ["详见附表执行。"],
                    "tender_qa_structured_summary": {
                        "structured_quality_score": 0.9,
                        "structured_terms": ["专项方案"],
                        "section_title_paths": ["第一章", "危大工程专项方案"],
                        "document_outline": [
                            {
                                "page_no": 2,
                                "page_type": "scoring_rules",
                                "section_title": "危大工程专项方案",
                                "section_level": 1,
                                "section_path": ["第一章", "危大工程专项方案"],
                            }
                        ],
                        "table_constraint_rows": [
                            {
                                "page_no": 3,
                                "label": "专项方案响应表",
                                "value": "危大工程需逐项响应",
                                "numbers": ["3"],
                            }
                        ],
                        "table_numeric_constraints": ["3"],
                        "page_type_summary": [{"page_type": "scoring_rules", "count": 1}],
                    },
                },
            ],
        }

        chunks = _select_material_retrieval_chunks(
            "p1",
            "危大工程专项方案 响应表",
            top_k=1,
            per_type_quota=1,
            per_file_quota=1,
            material_index=material_index,
        )

        assert chunks
        assert chunks[0]["filename"] == "anchor.pdf"
        assert "危大工程专项方案" in " ".join(chunks[0].get("matched_terms") or [])
        assert chunks[0].get("matched_file_anchor_terms")

    def test_build_material_utilization_summary_reports_uncovered_types(self):
        from app.main import _build_material_utilization_summary

        report = {
            "requirement_hits": [
                {
                    "source_pack_id": "runtime_material_rag",
                    "material_type": "tender_qa",
                    "source_mode": "type_quota",
                    "source_filename": "tender.txt",
                    "chunk_id": "tender.txt#c001",
                    "hit": True,
                    "label": "资料检索证据：招标文件和答疑 / tender.txt / c1",
                },
                {
                    "source_pack_id": "runtime_material_consistency",
                    "material_type": "boq",
                    "source_mode": "fallback_keywords",
                    "hit": False,
                    "label": "跨资料一致性：施组需体现清单关键约束",
                },
            ]
        }
        runtime_meta = {
            "material_available_types": ["tender_qa", "boq", "drawing"],
            "material_retrieval_selected_filenames": ["tender.txt", "boq.xlsx", "drawing.pdf"],
            "material_retrieval_selected_via_counts": {
                "type_quota": 1,
                "type_backfill": 2,
            },
            "material_retrieval_top_k": 18,
            "material_retrieval_per_type_quota": 2,
            "material_retrieval_per_file_quota": 3,
            "material_retrieval_base_top_k": 12,
            "material_retrieval_base_per_type_quota": 1,
            "material_retrieval_base_per_file_quota": 2,
            "material_retrieval_budget_reasons": ["top_k:12->18", "per_type_quota:1->2"],
            "material_total_size_mb": 12.5,
            "material_type_count": 3,
            "material_available_files": 3,
        }
        summary = _build_material_utilization_summary(report, runtime_meta)
        assert summary["retrieval_total"] == 1
        assert summary["retrieval_hit"] == 1
        assert summary["retrieval_file_total"] == 3
        assert summary["retrieval_file_hit"] == 1
        assert summary["retrieval_file_coverage_rate"] == pytest.approx(0.3333, abs=1e-4)
        assert summary["retrieval_unhit_file_count"] == 2
        assert "boq.xlsx" in (summary.get("retrieval_unhit_filenames") or [])
        assert "boq" in (summary.get("uncovered_types") or [])
        assert "drawing" in (summary.get("uncovered_types") or [])
        assert summary["fallback_total"] == 1
        assert summary["fallback_hit"] == 0
        assert summary["retrieval_selected_via_counts"] == {"type_quota": 1, "type_backfill": 2}
        assert summary["retrieval_total_via_counts"]["type_quota"] == 1
        assert summary["retrieval_hit_via_counts"]["type_quota"] == 1
        assert summary["retrieval_top_k"] == 18
        assert summary["retrieval_base_top_k"] == 12

    def test_compute_dynamic_retrieval_budget_scales_with_material_volume(self):
        from app.main import _compute_dynamic_retrieval_budget

        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            material_types = ["tender_qa", "boq", "drawing", "site_photo"]
            for idx in range(8):
                p = Path(tmp) / f"m{idx}.txt"
                p.write_text("x" * 200_000, encoding="utf-8")
                rows.append(
                    {
                        "project_id": "p1",
                        "material_type": material_types[idx % len(material_types)],
                        "filename": p.name,
                        "path": str(p),
                        "created_at": "2026-02-26T00:00:00+00:00",
                    }
                )

            budget = _compute_dynamic_retrieval_budget(
                "p1",
                {"top_k": 12, "per_type_quota": 1, "per_file_quota": 2},
                rows,
                available_material_types=material_types,
            )
            assert budget["top_k"] >= 12
            assert budget["per_type_quota"] >= 2
            assert budget["per_file_quota"] >= 3
            assert budget["material_file_count"] == 8
            assert budget["material_type_count"] == 4
            assert budget["material_total_size_mb"] > 0

    def test_compute_dynamic_retrieval_budget_keeps_base_for_small_inputs(self):
        from app.main import _compute_dynamic_retrieval_budget

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.txt"
            p.write_text("x" * 20, encoding="utf-8")
            rows = [
                {
                    "project_id": "p1",
                    "material_type": "tender_qa",
                    "filename": "a.txt",
                    "path": str(p),
                    "created_at": "2026-02-26T00:00:00+00:00",
                }
            ]
            budget = _compute_dynamic_retrieval_budget(
                "p1",
                {"top_k": 14, "per_type_quota": 2, "per_file_quota": 3},
                rows,
                available_material_types=["tender_qa"],
            )
            assert budget["top_k"] == 14
            assert budget["per_type_quota"] == 2
            assert budget["per_file_quota"] == 3
            assert "base_budget_kept" in (budget.get("budget_reasons") or [])

    def test_aggregate_material_utilization_summaries(self):
        from app.main import _aggregate_material_utilization_summaries

        merged = _aggregate_material_utilization_summaries(
            [
                {
                    "retrieval_total": 2,
                    "retrieval_hit": 1,
                    "retrieval_file_total": 2,
                    "retrieval_file_hit": 1,
                    "retrieval_selected_filenames": ["a.txt", "b.txt"],
                    "retrieval_hit_filenames": ["a.txt"],
                    "retrieval_selected_via_counts": {"type_quota": 2, "global_backfill": 1},
                    "retrieval_total_via_counts": {"type_quota": 2},
                    "retrieval_hit_via_counts": {"type_quota": 1},
                    "retrieval_top_k": 18,
                    "retrieval_per_type_quota": 2,
                    "retrieval_per_file_quota": 3,
                    "retrieval_base_top_k": 12,
                    "retrieval_base_per_type_quota": 1,
                    "retrieval_base_per_file_quota": 2,
                    "retrieval_budget_reasons": ["top_k:12->18"],
                    "material_total_size_mb": 15.2,
                    "material_type_count": 2,
                    "material_file_count": 2,
                    "consistency_total": 2,
                    "consistency_hit": 1,
                    "fallback_total": 1,
                    "fallback_hit": 0,
                    "available_types": ["tender_qa", "boq"],
                    "uncovered_types": ["boq"],
                    "by_type": {
                        "tender_qa": {
                            "retrieval_total": 2,
                            "retrieval_hit": 1,
                            "consistency_total": 1,
                            "consistency_hit": 1,
                            "fallback_total": 0,
                            "fallback_hit": 0,
                        },
                        "boq": {
                            "retrieval_total": 0,
                            "retrieval_hit": 0,
                            "consistency_total": 1,
                            "consistency_hit": 0,
                            "fallback_total": 1,
                            "fallback_hit": 0,
                        },
                    },
                },
                {
                    "retrieval_total": 1,
                    "retrieval_hit": 1,
                    "retrieval_file_total": 1,
                    "retrieval_file_hit": 1,
                    "retrieval_selected_filenames": ["c.txt"],
                    "retrieval_hit_filenames": ["c.txt"],
                    "retrieval_selected_via_counts": {"global_rank": 1},
                    "retrieval_total_via_counts": {"global_rank": 1},
                    "retrieval_hit_via_counts": {"global_rank": 1},
                    "retrieval_top_k": 20,
                    "retrieval_per_type_quota": 2,
                    "retrieval_per_file_quota": 3,
                    "retrieval_base_top_k": 12,
                    "retrieval_base_per_type_quota": 1,
                    "retrieval_base_per_file_quota": 2,
                    "retrieval_budget_reasons": ["top_k:12->20"],
                    "material_total_size_mb": 20.0,
                    "material_type_count": 3,
                    "material_file_count": 3,
                    "consistency_total": 1,
                    "consistency_hit": 1,
                    "fallback_total": 0,
                    "fallback_hit": 0,
                    "available_types": ["drawing"],
                    "uncovered_types": [],
                    "by_type": {
                        "drawing": {
                            "retrieval_total": 1,
                            "retrieval_hit": 1,
                            "consistency_total": 1,
                            "consistency_hit": 1,
                            "fallback_total": 0,
                            "fallback_hit": 0,
                        }
                    },
                },
            ]
        )
        assert merged["retrieval_total"] == 3
        assert merged["retrieval_hit"] == 2
        assert merged["retrieval_file_total"] == 3
        assert merged["retrieval_file_hit"] == 2
        assert merged["retrieval_file_coverage_rate"] == pytest.approx(0.6667, abs=1e-4)
        assert merged["retrieval_unhit_file_count"] == 1
        assert "b.txt" in (merged.get("retrieval_unhit_filenames") or [])
        assert merged["consistency_total"] == 3
        assert merged["consistency_hit"] == 2
        assert merged["fallback_total"] == 1
        assert merged["fallback_hit"] == 0
        assert "boq" in (merged.get("uncovered_types") or [])
        assert "drawing" in (merged.get("available_types") or [])
        assert merged["retrieval_top_k"] == 20
        assert merged["retrieval_base_top_k"] == 12
        assert merged["retrieval_selected_via_counts"]["type_quota"] == 2
        assert merged["retrieval_selected_via_counts"]["global_rank"] == 1
        assert merged["retrieval_total_via_counts"]["type_quota"] == 2
        assert merged["retrieval_hit_via_counts"]["global_rank"] == 1

    def test_evaluate_material_utilization_gate_blocks_when_required_types_uncovered(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 6,
            "retrieval_hit_rate": 0.6,
            "consistency_total": 4,
            "consistency_hit_rate": 0.5,
            "available_types": ["tender_qa", "boq", "drawing"],
            "uncovered_types": ["drawing"],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_hit_rate": 0.2,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_coverage_rate": 1.0,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["enabled"] is True
        assert gate["blocked"] is True
        assert gate["passed"] is False
        assert "drawing" in (gate.get("uncovered_required_types") or [])

    def test_evaluate_material_utilization_gate_warn_mode(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 6,
            "retrieval_hit_rate": 0.1,
            "retrieval_file_total": 4,
            "retrieval_file_coverage_rate": 0.25,
            "consistency_total": 4,
            "consistency_hit_rate": 0.1,
            "available_types": ["tender_qa", "boq", "drawing"],
            "uncovered_types": [],
        }
        policy = {
            "enabled": True,
            "mode": "warn",
            "min_retrieval_hit_rate": 0.2,
            "min_retrieval_file_coverage_rate": 0.6,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_coverage_rate": 0.6,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["warned"] is True
        assert gate["blocked"] is False
        assert gate["level"] == "warn"

    def test_evaluate_material_utilization_gate_blocks_when_file_coverage_low(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 9,
            "retrieval_hit_rate": 0.8,
            "retrieval_file_total": 5,
            "retrieval_file_coverage_rate": 0.2,
            "consistency_total": 5,
            "consistency_hit_rate": 0.7,
            "available_types": ["tender_qa", "boq", "drawing"],
            "uncovered_types": [],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_total": 2,
            "min_retrieval_hit_rate": 0.2,
            "min_retrieval_file_coverage_rate": 0.6,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_presence_rate": 0.6,
            "min_required_type_coverage_rate": 0.6,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["blocked"] is True
        assert any("文件覆盖率" in str(x) for x in (gate.get("reasons") or []))

    def test_evaluate_material_utilization_gate_blocks_when_required_upload_missing(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 8,
            "retrieval_hit_rate": 0.8,
            "consistency_total": 4,
            "consistency_hit_rate": 0.7,
            "available_types": ["tender_qa"],
            "uncovered_types": [],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_total": 2,
            "min_retrieval_hit_rate": 0.2,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_presence_rate": 1.0,
            "min_required_type_coverage_rate": 0.6,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["blocked"] is True
        assert gate["required_type_presence_rate"] == pytest.approx(0.3333, abs=1e-4)
        missing = gate.get("required_types_missing_upload") or []
        assert "boq" in missing
        assert "drawing" in missing

    def test_evaluate_material_utilization_gate_blocks_when_retrieval_total_low(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 1,
            "retrieval_hit_rate": 1.0,
            "consistency_total": 4,
            "consistency_hit_rate": 0.7,
            "available_types": ["tender_qa", "boq", "drawing"],
            "uncovered_types": [],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_total": 3,
            "min_retrieval_hit_rate": 0.2,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_presence_rate": 0.6,
            "min_required_type_coverage_rate": 0.6,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["blocked"] is True
        assert any("资料检索证据数量" in str(x) for x in (gate.get("reasons") or []))

    def test_evaluate_material_utilization_gate_blocks_when_uploaded_type_uncovered(self):
        from app.main import _evaluate_material_utilization_gate

        summary = {
            "retrieval_total": 10,
            "retrieval_hit_rate": 0.8,
            "retrieval_file_total": 6,
            "retrieval_file_coverage_rate": 0.8,
            "consistency_total": 6,
            "consistency_hit_rate": 0.8,
            "available_types": ["tender_qa", "boq", "drawing", "site_photo"],
            "uncovered_types": ["site_photo"],
        }
        policy = {
            "enabled": True,
            "mode": "block",
            "min_retrieval_total": 2,
            "min_retrieval_hit_rate": 0.2,
            "min_retrieval_file_coverage_rate": 0.2,
            "min_consistency_hit_rate": 0.2,
            "max_uncovered_required_types": 0,
            "min_required_type_presence_rate": 0.6,
            "min_required_type_coverage_rate": 0.6,
            "enforce_uploaded_type_coverage": True,
            "min_uploaded_type_coverage_rate": 1.0,
        }
        gate = _evaluate_material_utilization_gate(
            summary,
            policy=policy,
            required_types=["tender_qa", "boq", "drawing"],
        )
        assert gate["blocked"] is True
        assert gate["uploaded_type_coverage_rate"] == pytest.approx(0.75, abs=1e-4)
        assert "site_photo" in (gate.get("uncovered_uploaded_types") or [])
        assert any("已上传资料类型覆盖率" in str(x) for x in (gate.get("reasons") or []))

    def test_resolve_material_utilization_policy_defaults_to_nonzero_file_coverage(self):
        from app.main import _resolve_material_utilization_policy

        policy = _resolve_material_utilization_policy({"id": "p1", "meta": {}})
        assert policy["min_retrieval_file_coverage_rate"] == pytest.approx(0.35, abs=1e-6)


class TestScoreForProjectEndpoint:
    """Tests for /projects/{project_id}/score endpoint."""

    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_success(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        client,
    ):
        """Score for project should return submission record."""
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(model_dump=lambda: {"total_score": 75.0})

        response = client.post("/api/v1/projects/p1/score", json={"text": "测试文本"})
        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "inline"

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_score_for_project_not_found(self, mock_ensure, mock_load, client):
        """Score for project should return 404 if project not found."""
        mock_load.return_value = []
        response = client.post("/api/v1/projects/nonexistent/score", json={"text": "test"})
        assert response.status_code == 404

    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_with_learning_profile(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        client,
    ):
        """Score for project should use learning profile multipliers."""
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = [{"project_id": "p1", "dimension_multipliers": {"D01": 1.3}}]
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(model_dump=lambda: {"total_score": 78.0})

        with patch("app.main.get_cached_score", return_value=None), patch(
            "app.main.cache_score_result"
        ), patch("app.main.load_evolution_reports", return_value={}):
            response = client.post("/api/v1/projects/p1/score", json={"text": "测试文本"})
        assert response.status_code == 200
        # Check multipliers were passed
        call_args = mock_score.call_args
        assert call_args.kwargs.get("dimension_multipliers") == {"D01": 1.3}


class TestSubmissionsEndpoint:
    """Tests for /projects/{project_id}/submissions endpoint."""

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_list_submissions_empty(self, mock_ensure, mock_load, client):
        """List submissions should return empty list when no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/submissions")
        assert response.status_code == 200
        assert response.json() == []

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_list_submissions_filtered(self, mock_ensure, mock_load, client):
        """List submissions should filter by project_id."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "report": {},
                "text": "t1",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p2",
                "filename": "f2.txt",
                "total_score": 70.0,
                "report": {},
                "text": "t2",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/submissions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "s1"

    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_list_submissions_applies_project_score_scale(
        self, mock_ensure, mock_load_submissions, mock_load_projects, client
    ):
        """List submissions should render scaled score values based on project meta score_scale_max."""
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 5}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "report": {
                    "scoring_status": "scored",
                    "total_score": 80.0,
                    "rule_total_score": 70.0,
                    "pred_total_score": 80.0,
                    "llm_total_score": 78.0,
                },
                "text": "t1",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        response = client.get("/api/v1/projects/p1/submissions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["total_score"] == 3.5
        assert data[0]["report"]["pred_total_score"] is None
        assert data[0]["report"]["rule_total_score"] == 3.5
        assert data[0]["report"]["llm_total_score"] is None
        assert data[0]["report"]["score_scale_max"] == 5
        assert data[0]["report"]["score_scale_label"] == "5分制"
        assert data[0]["report"]["raw_total_score_100"] == 70.0

    def test_score_scale_round_trip_preserves_precision_for_5_scale(self):
        from app.main import _convert_score_from_100, _convert_score_to_100

        scaled = _convert_score_from_100(66.67, 5)

        assert scaled == 3.3335
        assert _convert_score_to_100(scaled, 5) == 66.67


class TestCompareEndpoints:
    """Tests for /projects/{project_id}/compare endpoints."""

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_no_submissions(self, mock_ensure, mock_load, client):
        """Compare should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 404
        assert "暂无施组记录" in response.json()["detail"]

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_with_submissions(self, mock_ensure, mock_load, client):
        """Compare should return rankings and stats."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "report": {
                    "dimension_scores": {"D01": {"score": 20.0}},
                    "penalties": [{"code": "P001"}],
                },
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "f2.txt",
                "total_score": 70.0,
                "report": {
                    "dimension_scores": {"D01": {"score": 18.0}},
                    "penalties": [],
                },
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert len(data["rankings"]) == 2
        # First should be the higher ranked submission; displayed total may be scaled by project score scale.
        assert data["rankings"][0]["submission_id"] == "s1"
        assert (
            data["rankings"][0]["ranking_sort_score"] >= data["rankings"][1]["ranking_sort_score"]
        )
        assert "ranking_evidence_bonus" in data["rankings"][0]

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_uses_evidence_bonus_for_near_ties(self, mock_ensure, mock_load, client):
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "raw_high_low_conf.txt",
                "total_score": 85.0,
                "report": {
                    "dimension_scores": {"09": {"score": 7.0}},
                    "penalties": [],
                    "meta": {
                        "score_confidence_level": "low",
                        "score_self_awareness": {
                            "level": "low",
                            "score_0_100": 24.0,
                            "structured_quality_avg": 0.12,
                            "structured_quality_type_rate": 0.0,
                            "retrieval_file_coverage_rate": 0.18,
                            "dimension_coverage_rate": 0.2,
                        },
                    },
                },
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "raw_low_high_conf.txt",
                "total_score": 84.8,
                "report": {
                    "dimension_scores": {"09": {"score": 7.2}},
                    "penalties": [],
                    "meta": {
                        "score_confidence_level": "high",
                        "score_self_awareness": {
                            "level": "high",
                            "score_0_100": 89.0,
                            "structured_quality_avg": 0.88,
                            "structured_quality_type_rate": 1.0,
                            "retrieval_file_coverage_rate": 0.92,
                            "dimension_coverage_rate": 0.86,
                        },
                    },
                },
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        assert data["rankings"][0]["submission_id"] == "s2"
        assert data["rankings"][0]["ranking_sort_score"] > data["rankings"][1]["ranking_sort_score"]
        assert (
            data["rankings"][0]["ranking_evidence_bonus"]
            > data["rankings"][1]["ranking_evidence_bonus"]
        )

    @patch("app.main.load_calibration_models")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_prefers_pred_total_score(
        self, mock_ensure, mock_load, mock_load_projects, mock_load_models, client
    ):
        """Compare ranking should prioritize pred_total_score and keep rule_total_score trace."""
        mock_load_projects.return_value = [{"id": "p1", "calibrator_version_locked": "calib1"}]
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib1",
                "deployed": True,
                "created_at": "2026-01-02T00:00:00Z",
                "train_filter": {"project_id": "p1"},
            }
        ]
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 95.0,
                "report": {
                    "rule_total_score": 70.0,
                    "pred_total_score": 65.0,
                    "meta": {
                        "score_confidence_level": "low",
                        "score_self_awareness": {"level": "low", "score_0_100": 21.0},
                    },
                    "dimension_scores": {},
                    "penalties": [],
                },
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "f2.txt",
                "total_score": 80.0,
                "report": {
                    "rule_total_score": 75.0,
                    "pred_total_score": 90.0,
                    "meta": {
                        "score_confidence_level": "high",
                        "score_self_awareness": {"level": "high", "score_0_100": 82.0},
                    },
                    "dimension_scores": {},
                    "penalties": [],
                },
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        assert data["rankings"][0]["submission_id"] == "s2"
        assert data["rankings"][0]["total_score"] == 90.0
        assert data["rankings"][0]["rule_total_score"] == 75.0
        assert data["rankings"][0]["score_source"] == "pred"
        assert data["rankings"][0]["score_confidence_level"] == "high"
        assert data["rankings"][0]["score_self_awareness"]["score_0_100"] == 82.0

    @patch("app.main.build_compare_narrative")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_report_success(self, mock_ensure, mock_load, mock_narrative, client):
        """Compare report should return narrative."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 81.0,
                "report": {"total_score": 81.0, "scoring_status": "scored"},
            }
        ]
        mock_narrative.return_value = {
            "summary": "test summary",
            "top_submission": {"id": "s1"},
            "bottom_submission": {"id": "s1"},
            "key_diffs": [],
        }
        response = client.get("/api/v1/projects/p1/compare_report")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"

    @patch("app.main.build_compare_narrative")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_projects")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_report_enriches_pred_and_rule(
        self,
        mock_ensure,
        mock_load,
        mock_load_projects,
        mock_load_models,
        mock_narrative,
        client,
    ):
        """Compare narrative should be built with pred-priority total and expose trace scores."""
        mock_load_projects.return_value = [{"id": "p1", "calibrator_version_locked": "calib1"}]
        mock_load_models.return_value = [
            {
                "calibrator_version": "calib1",
                "deployed": True,
                "created_at": "2026-01-02T00:00:00Z",
                "train_filter": {"project_id": "p1"},
            }
        ]
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 91.0,
                "report": {
                    "rule_total_score": 70.0,
                    "pred_total_score": 65.0,
                    "meta": {
                        "score_confidence_level": "low",
                        "score_self_awareness": {"level": "low", "score_0_100": 28.0},
                    },
                },
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "f2.txt",
                "total_score": 82.0,
                "report": {
                    "rule_total_score": 80.0,
                    "pred_total_score": 90.0,
                    "meta": {
                        "score_confidence_level": "high",
                        "score_self_awareness": {"level": "high", "score_0_100": 84.0},
                    },
                },
            },
        ]

        def _fake_narrative(rows):
            row_map = {str(x.get("id")): x for x in rows}
            assert float(row_map["s1"]["total_score"]) == 65.0
            assert float(row_map["s2"]["total_score"]) == 90.0
            return {
                "summary": "ok",
                "top_submission": {"id": "s2", "filename": "f2.txt", "total_score": 90.0},
                "bottom_submission": {"id": "s1", "filename": "f1.txt", "total_score": 65.0},
                "key_diffs": [],
            }

        mock_narrative.side_effect = _fake_narrative
        response = client.get("/api/v1/projects/p1/compare_report")
        assert response.status_code == 200
        data = response.json()
        assert data["top_submission"]["pred_total_score"] == 90.0
        assert data["top_submission"]["rule_total_score"] == 80.0
        assert data["top_submission"]["score_source"] == "pred"
        assert data["top_submission"]["score_confidence_level"] == "high"
        assert data["top_submission"]["score_self_awareness"]["score_0_100"] == 84.0

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_report_no_submissions(self, mock_ensure, mock_load, client):
        """Compare report should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/compare_report")
        assert response.status_code == 404

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_requires_scored_submissions(self, mock_ensure, mock_load, client):
        """Compare should require scored submissions."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 0.0,
                "report": {"scoring_status": "pending"},
            }
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 404
        assert "请先点击“评分施组”" in response.json()["detail"]


class TestEvidenceTraceEndpoints:
    """Tests for /projects/{project_id}/submissions/{submission_id}/evidence_trace endpoints."""

    @patch("app.main.load_materials")
    @patch("app.main.load_evidence_units")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_submission_evidence_trace_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_units,
        mock_load_materials,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施工组织设计.pdf",
                "text": "工期120天，关键节点分三段推进。",
                "created_at": "2026-02-27T00:00:00+00:00",
                "report": {
                    "requirement_hits": [
                        {
                            "dimension_id": "01",
                            "label": "总工期约束",
                            "hit": True,
                            "mandatory": True,
                            "reason": "命中 t2/2 n1/1",
                            "source_pack_id": "runtime_material_rag",
                            "material_type": "tender_qa",
                            "source_filename": "招标文件.pdf",
                            "chunk_id": "招标文件.pdf#c1",
                        },
                        {
                            "dimension_id": "09",
                            "label": "进度里程碑",
                            "hit": False,
                            "mandatory": True,
                            "reason": "命中不足 t1/3 n0/1",
                            "source_pack_id": "runtime_material_consistency",
                            "material_type": "boq",
                            "source_filename": "工程量清单.xlsx",
                            "chunk_id": "工程量清单.xlsx#c2",
                        },
                    ],
                    "material_consistency": {
                        "by_material_type": {
                            "boq": {
                                "total": 1,
                                "hit": 0,
                                "mandatory_total": 1,
                                "mandatory_hit": 0,
                                "hit_rate": 0.0,
                            }
                        }
                    },
                    "meta": {},
                },
            }
        ]
        mock_load_units.return_value = [
            {
                "id": "u1",
                "submission_id": "s1",
                "dimension_id": "01",
                "source_locator": "P12-L30",
                "source_filename": "招标文件.pdf",
                "confidence": 0.91,
                "text_snippet": "项目总工期120天。",
            }
        ]
        mock_load_materials.return_value = []

        response = client.get("/api/v1/projects/p1/submissions/s1/evidence_trace")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["submission_id"] == "s1"
        assert data["summary"]["total_requirements"] == 2
        assert data["material_conflicts"]["has_conflicts"] is True
        assert len(data["evidence_units"]) == 1

    @patch("app.main.load_materials")
    @patch("app.main.load_evidence_units")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_submission_evidence_trace_markdown_and_download(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_units,
        mock_load_materials,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施工组织设计.pdf",
                "text": "工期120天。",
                "created_at": "2026-02-27T00:00:00+00:00",
                "report": {
                    "requirement_hits": [],
                    "material_consistency": {"by_material_type": {}},
                    "meta": {},
                },
            }
        ]
        mock_load_units.return_value = []
        mock_load_materials.return_value = []

        md_resp = client.get("/api/v1/projects/p1/submissions/s1/evidence_trace/markdown")
        assert md_resp.status_code == 200
        markdown = md_resp.json()["markdown"]
        assert "# 评分证据追溯报告" in markdown
        assert "施组ID" in markdown

        file_resp = client.get("/api/v1/projects/p1/submissions/s1/evidence_trace.md")
        assert file_resp.status_code == 200
        assert "text/markdown" in file_resp.headers.get("content-type", "")
        assert "attachment; filename=" in file_resp.headers.get("content-disposition", "")

    @patch("app.main.load_materials")
    @patch("app.main.load_evidence_units")
    @patch("app.main._submission_is_scored")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_latest_submission_evidence_trace_prefers_scored(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_submission_scored,
        mock_load_units,
        mock_load_materials,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_submissions.return_value = [
            {
                "id": "s_new_pending",
                "project_id": "p1",
                "filename": "待评分施组.pdf",
                "created_at": "2026-02-27T10:00:00+00:00",
                "report": {"scoring_status": "pending"},
                "text": "",
            },
            {
                "id": "s_old_scored",
                "project_id": "p1",
                "filename": "已评分施组.pdf",
                "created_at": "2026-02-27T09:00:00+00:00",
                "report": {"scoring_status": "scored", "requirement_hits": [], "meta": {}},
                "text": "施组文本",
            },
        ]
        mock_submission_scored.side_effect = [False, True]
        mock_load_units.return_value = []
        mock_load_materials.return_value = []

        response = client.get("/api/v1/projects/p1/evidence_trace/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["submission_id"] == "s_old_scored"
        assert data["filename"] == "已评分施组.pdf"

    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_latest_submission_scoring_basis_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施工组织设计.pdf",
                "text": "工期120天，关键节点分三段推进。",
                "created_at": "2026-02-27T00:00:00+00:00",
                "report": {
                    "scoring_status": "scored",
                    "requirement_hits": [],
                    "meta": {
                        "input_injection": {
                            "mece_inputs": {
                                "project_materials_extracted": True,
                                "shigong_parsed": True,
                                "bid_requirements_loaded": True,
                                "attention_16d_weights_injected": True,
                                "custom_instructions_injected": True,
                                "materials_quality_gate_passed": True,
                            }
                        },
                        "material_quality": {"total_files": 4, "total_parsed_chars": 22000},
                        "material_retrieval": {"chunks": 18, "requirements": 16},
                        "material_utilization": {
                            "retrieval_hit_rate": 0.72,
                            "retrieval_file_coverage_rate": 0.8,
                        },
                        "material_utilization_gate": {
                            "passed": True,
                            "blocked": False,
                            "reasons": [],
                        },
                        "evidence_trace": {
                            "total_requirements": 20,
                            "total_hits": 14,
                            "mandatory_hit_rate": 0.75,
                            "source_files_hit_count": 3,
                            "source_files_hit": ["招标文件.pdf", "工程量清单.xlsx", "图纸.dxf"],
                        },
                    },
                },
            }
        ]

        response = client.get("/api/v1/projects/p1/scoring_basis/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["submission_id"] == "s1"
        assert data["mece_inputs"]["materials_quality_gate_passed"] is True
        assert data["material_utilization"]["retrieval_hit_rate"] == pytest.approx(0.72, abs=1e-6)
        assert data["evidence_trace"]["source_files_hit_count"] == 3
        assert data["material_retrieval"]["feedback_evolution_requirements"] == 0
        assert data["material_retrieval"]["feature_confidence_requirements"] == 0
        assert data["material_retrieval"]["feedback_evolution_preview"] == []
        assert data["material_retrieval"]["feature_confidence_preview"] == []
        assert isinstance(data["current_runtime_constraints"]["weights_source"], str)
        assert isinstance(
            data["current_runtime_constraints"]["effective_multipliers_preview"], list
        )

    @patch("app.main._build_project_scoring_diagnostic")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_latest_project_scoring_diagnostic_success(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_diagnostic,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_diagnostic.return_value = {
            "project_id": "p1",
            "generated_at": "2026-03-07T10:00:00+08:00",
            "readiness": {
                "project_id": "p1",
                "ready": True,
                "score_button_enabled": True,
                "gate_passed": True,
                "issues": [],
                "warnings": [],
                "material_quality": {"total_files": 4},
                "material_gate": {"required_types": ["tender_qa", "boq", "drawing"]},
                "material_depth_gate": {"passed": True, "enforce": True},
                "submissions": {"non_empty": 1},
                "retrieval_policy": {},
                "generated_at": "2026-03-07T10:00:00+08:00",
            },
            "material_depth": {
                "project_id": "p1",
                "generated_at": "2026-03-07T10:00:00+08:00",
                "ready_to_score": True,
                "capabilities": {"ocr_available": True},
                "gate": {"passed": True},
                "depth_gate": {"passed": True, "enforce": True},
                "quality_summary": {"total_files": 4, "total_parsed_chars": 28000},
                "by_type": [],
                "recommendations": [],
            },
            "latest_submission": {
                "exists": True,
                "submission_id": "s1",
                "filename": "施工组织设计.pdf",
                "created_at": "2026-03-07T09:00:00+08:00",
                "scoring_status": "scored",
                "is_scored": True,
            },
            "evidence_trace": {
                "project_id": "p1",
                "submission_id": "s1",
                "filename": "施工组织设计.pdf",
                "generated_at": "2026-03-07T10:00:00+08:00",
                "summary": {
                    "total_requirements": 18,
                    "total_hits": 12,
                    "mandatory_hit_rate": 0.8,
                    "source_files_hit_count": 3,
                    "source_files_hit": ["招标文件.pdf", "清单.xlsx", "图纸.dxf"],
                },
                "by_dimension": [],
                "requirement_hits": [],
                "evidence_units": [],
                "material_conflicts": {"conflict_count": 1, "high_severity_count": 0},
                "recommendations": [],
            },
            "scoring_basis": {
                "project_id": "p1",
                "submission_id": "s1",
                "filename": "施工组织设计.pdf",
                "generated_at": "2026-03-07T10:00:00+08:00",
                "scoring_status": "scored",
                "mece_inputs": {"materials_quality_gate_passed": True},
                "material_quality": {"total_files": 4},
                "material_retrieval": {
                    "chunks": 20,
                    "feedback_evolution_requirements": 1,
                    "feature_confidence_requirements": 1,
                    "feedback_evolution_preview": [
                        {
                            "dimension_id": "09",
                            "label": "进度计划体系与纠偏阈值",
                            "hints": ["关键节点", "偏差阈值"],
                            "multiplier": 1.16,
                            "sample_count": 4,
                        }
                    ],
                    "feature_confidence_preview": [
                        {
                            "dimension_id": "14",
                            "label": "图纸会审、深化设计与变更闭环",
                            "hints": ["会审问题单", "关闭条件"],
                            "feature_ids": ["feat-1"],
                            "feature_confidence_scores": [0.88],
                        }
                    ],
                },
                "material_utilization": {"retrieval_hit_rate": 0.75},
                "material_utilization_gate": {"blocked": False, "reasons": []},
                "evidence_trace": {"mandatory_hit_rate": 0.8},
                "current_runtime_constraints": {
                    "weights_source": "evolution",
                    "effective_multipliers_preview": [
                        {
                            "dimension_id": "09",
                            "dimension_name": "进度计划体系与纠偏阈值",
                            "multiplier": 1.16,
                        }
                    ],
                    "feedback_evolution_requirements": 1,
                    "feature_confidence_requirements": 1,
                },
                "recommendations": [],
            },
            "material_type_cards": [
                {
                    "material_type": "tender_qa",
                    "material_type_label": "招标文件和答疑",
                    "required": True,
                    "in_scope": True,
                    "status": "active",
                    "status_label": "已参与评分",
                    "files": 1,
                    "parse_status_counts": {"parsed": 2},
                    "queued_count": 0,
                    "processing_count": 0,
                    "parsed_count": 2,
                    "failed_count": 0,
                    "parse_backend_summary": ["GPT-5.4×2"],
                    "parse_confidence_avg": 0.86,
                    "parse_confidence_max": 0.93,
                    "parse_error_preview": [],
                    "parsed_chars": 12000,
                    "parsed_chunks": 12,
                    "numeric_terms": 8,
                    "meets_chars": True,
                    "meets_chunks": True,
                    "meets_numeric_terms": True,
                    "retrieval_hit": 4,
                    "retrieval_total": 6,
                    "consistency_hit": 2,
                    "consistency_total": 3,
                    "fallback_hit": 0,
                    "fallback_total": 0,
                    "has_evidence": True,
                    "uploaded_filenames": ["招标文件.pdf", "答疑纪要.docx"],
                    "uploaded_filename_count": 2,
                    "hit_filenames": ["招标文件.pdf"],
                    "hit_filename_count": 1,
                    "hit_requirement_labels": ["工期节点", "质量目标"],
                    "hit_requirement_count": 2,
                    "miss_requirement_labels": ["危大工程清单"],
                    "miss_requirement_count": 1,
                    "hit_evidence_preview": [
                        {
                            "label": "工期节点",
                            "source_filename": "招标文件.pdf",
                            "source_mode": "retrieval",
                            "reason": "keywords",
                        },
                        {
                            "label": "质量目标",
                            "source_filename": "答疑纪要.docx",
                            "source_mode": "retrieval",
                            "reason": "keywords",
                        },
                    ],
                    "miss_evidence_preview": [
                        {
                            "label": "危大工程清单",
                            "source_filename": "招标文件.pdf",
                            "source_mode": "material_consistency",
                            "reason": "material_consistency:t1/2;n0/1",
                        },
                    ],
                    "conflict_labels": ["危大工程清单"],
                    "conflict_label_count": 1,
                    "project_numeric_terms": ["90", "1200", "48"],
                    "project_numeric_term_count": 3,
                    "project_numeric_category_summary": [
                        "工期/节点：90",
                        "规格/参数：1200",
                        "阈值/偏差：48",
                    ],
                    "hit_numeric_terms": ["90", "1200", "7"],
                    "hit_numeric_term_count": 3,
                    "expected_numeric_terms": ["90", "1200", "7", "48"],
                    "expected_numeric_term_count": 4,
                    "missing_numeric_terms": ["48"],
                    "missing_numeric_term_count": 1,
                    "hit_numeric_category_summary": ["工期/节点：90", "规格/参数：1200、7"],
                    "expected_numeric_category_summary": [
                        "工期/节点：90",
                        "规格/参数：1200、7",
                        "阈值/偏差：48",
                    ],
                    "missing_numeric_category_summary": ["阈值/偏差：48"],
                    "guidance": ["招标文件和答疑已进入评分证据链。"],
                }
            ],
            "dimension_support_cards": [
                {
                    "dimension_id": "01",
                    "dimension_name": "总体部署与信息化管理",
                    "coverage_score": 0.82,
                    "coverage_level": "high",
                    "keyword_hits": 10,
                    "numeric_signal_hits": 2,
                    "source_types": ["tender_qa", "drawing"],
                    "source_file_count": 2,
                    "source_files_preview": ["招标文件.pdf", "总图.dxf"],
                    "suggested_keywords": ["工程范围", "项目理解"],
                },
                {
                    "dimension_id": "07",
                    "dimension_name": "危大工程闭环管理",
                    "coverage_score": 0.21,
                    "coverage_level": "low",
                    "keyword_hits": 1,
                    "numeric_signal_hits": 0,
                    "source_types": [],
                    "source_file_count": 0,
                    "source_files_preview": [],
                    "suggested_keywords": ["危大工程", "专项方案", "监测"],
                },
            ],
            "summary": {
                "latest_submission_exists": True,
                "latest_submission_scored": True,
                "evidence_total_hits": 12,
                "retrieval_hit_rate": 0.75,
                "material_dimension_coverage_rate": 0.625,
                "material_low_coverage_dimensions": 6,
                "material_covered_dimensions": 10,
                "material_numeric_category_summary": ["工期/节点：90", "规格/参数：1200"],
                "parse_job_summary": {
                    "total_jobs": 2,
                    "status_counts": {"parsed": 2},
                    "backlog": 0,
                    "failed_jobs": 0,
                    "gpt_jobs": 2,
                    "gpt_ratio": 1.0,
                },
                "parse_total_jobs": 2,
                "parse_backlog": 0,
                "parse_failed_jobs": 0,
                "parse_gpt_ratio": 1.0,
                "parsed_materials": 2,
                "queued_materials": 0,
                "processing_materials": 0,
                "failed_materials": 0,
                "current_weights_source": "evolution",
                "current_feedback_evolution_requirements": 1,
                "current_feature_confidence_requirements": 1,
                "recent_feedback_context_active": True,
            },
            "recommendations": ["补充图纸中的设备型号锚点。"],
        }

        response = client.get("/api/v1/projects/p1/scoring_diagnostic/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["latest_submission"]["filename"] == "施工组织设计.pdf"
        assert data["summary"]["evidence_total_hits"] == 12
        assert data["summary"]["retrieval_hit_rate"] == pytest.approx(0.75, abs=1e-6)
        assert data["evidence_trace"]["summary"]["source_files_hit_count"] == 3
        assert data["scoring_basis"]["material_retrieval"]["feedback_evolution_requirements"] == 1
        assert data["scoring_basis"]["material_retrieval"]["feature_confidence_requirements"] == 1
        assert (
            data["scoring_basis"]["material_retrieval"]["feedback_evolution_preview"][0][
                "dimension_id"
            ]
            == "09"
        )
        assert (
            data["scoring_basis"]["material_retrieval"]["feature_confidence_preview"][0][
                "dimension_id"
            ]
            == "14"
        )
        assert data["scoring_basis"]["current_runtime_constraints"]["weights_source"] == "evolution"
        assert data["summary"]["current_weights_source"] == "evolution"
        assert data["scoring_basis"]["material_utilization"]["retrieval_hit_rate"] == pytest.approx(
            0.75, abs=1e-6
        )
        assert len(data["material_type_cards"]) == 1
        assert data["material_type_cards"][0]["status"] == "active"
        assert data["material_type_cards"][0]["parse_backend_summary"] == ["GPT-5.4×2"]
        assert data["material_type_cards"][0]["parse_confidence_avg"] == pytest.approx(
            0.86, abs=1e-6
        )
        assert data["material_type_cards"][0]["uploaded_filenames"] == [
            "招标文件.pdf",
            "答疑纪要.docx",
        ]
        assert data["material_type_cards"][0]["hit_filenames"] == ["招标文件.pdf"]
        assert data["material_type_cards"][0]["hit_requirement_labels"] == ["工期节点", "质量目标"]
        assert data["material_type_cards"][0]["hit_evidence_preview"][0]["label"] == "工期节点"
        assert data["material_type_cards"][0]["miss_evidence_preview"][0]["label"] == "危大工程清单"
        assert data["material_type_cards"][0]["conflict_labels"] == ["危大工程清单"]
        assert data["material_type_cards"][0]["project_numeric_terms"] == ["90", "1200", "48"]
        assert data["material_type_cards"][0]["project_numeric_category_summary"] == [
            "工期/节点：90",
            "规格/参数：1200",
            "阈值/偏差：48",
        ]
        assert data["material_type_cards"][0]["hit_numeric_terms"] == ["90", "1200", "7"]
        assert data["material_type_cards"][0]["missing_numeric_terms"] == ["48"]
        assert data["material_type_cards"][0]["hit_numeric_category_summary"] == [
            "工期/节点：90",
            "规格/参数：1200、7",
        ]
        assert data["material_type_cards"][0]["missing_numeric_category_summary"] == [
            "阈值/偏差：48"
        ]
        assert data["dimension_support_cards"][0]["dimension_id"] == "01"
        assert data["dimension_support_cards"][1]["coverage_level"] == "low"
        assert data["summary"]["material_dimension_coverage_rate"] == pytest.approx(0.625, abs=1e-6)
        assert data["summary"]["material_low_coverage_dimensions"] == 6
        assert data["summary"]["parse_total_jobs"] == 2
        assert data["summary"]["parse_gpt_ratio"] == pytest.approx(1.0, abs=1e-6)
        assert data["summary"]["recent_feedback_context_active"] is True

    @patch("app.main._build_project_scoring_diagnostic")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_get_latest_project_scoring_diagnostic_without_submission(
        self,
        mock_ensure,
        mock_load_projects,
        mock_build_diagnostic,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "name": "项目1", "meta": {}}]
        mock_build_diagnostic.return_value = {
            "project_id": "p1",
            "generated_at": "2026-03-07T10:00:00+08:00",
            "readiness": {
                "project_id": "p1",
                "ready": False,
                "score_button_enabled": False,
                "gate_passed": False,
                "issues": ["缺少施组文件"],
                "warnings": [],
                "material_quality": {},
                "material_gate": {},
                "material_depth_gate": {},
                "submissions": {"non_empty": 0},
                "retrieval_policy": {},
                "generated_at": "2026-03-07T10:00:00+08:00",
            },
            "material_depth": {
                "project_id": "p1",
                "generated_at": "2026-03-07T10:00:00+08:00",
                "ready_to_score": False,
                "capabilities": {},
                "gate": {},
                "depth_gate": {},
                "quality_summary": {"total_files": 0},
                "by_type": [],
                "recommendations": ["请先上传项目资料。"],
            },
            "latest_submission": {
                "exists": False,
                "submission_id": None,
                "filename": None,
                "created_at": None,
                "scoring_status": None,
                "is_scored": False,
            },
            "evidence_trace": None,
            "scoring_basis": None,
            "material_type_cards": [],
            "summary": {
                "latest_submission_exists": False,
                "latest_submission_scored": False,
                "evidence_total_hits": 0,
                "retrieval_hit_rate": None,
            },
            "recommendations": ["暂无施组评分证据链，请先上传并评分至少 1 份施组。"],
        }

        response = client.get("/api/v1/projects/p1/scoring_diagnostic/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["latest_submission"]["exists"] is False
        assert data["evidence_trace"] is None
        assert data["scoring_basis"] is None
        assert data["summary"]["latest_submission_exists"] is False

    def test_build_material_conflict_summary_from_report(self):
        from app.main import _build_material_conflict_summary_from_report

        report = {
            "requirement_hits": [
                {
                    "dimension_id": "09",
                    "label": "工期里程碑",
                    "hit": False,
                    "mandatory": True,
                    "reason": "命中不足 t1/3 n0/2",
                    "source_pack_id": "runtime_material_consistency",
                    "material_type": "boq",
                    "source_filename": "工程量清单.xlsx",
                    "chunk_id": "工程量清单.xlsx#c3",
                    "source_mode": "consistency_check",
                }
            ],
            "material_consistency": {
                "by_material_type": {
                    "boq": {
                        "total": 1,
                        "hit": 0,
                        "mandatory_total": 1,
                        "mandatory_hit": 0,
                        "hit_rate": 0.0,
                    }
                }
            },
        }
        payload = _build_material_conflict_summary_from_report(report)
        assert payload["has_conflicts"] is True
        assert payload["conflict_count"] == 1
        assert payload["high_severity_count"] >= 1
        assert payload["conflicts"][0]["conflict_kind"] in {
            "numeric_mismatch",
            "term_coverage_missing",
            "material_consistency_missing",
        }

    @patch("app.main.save_patch_deployments")
    @patch("app.main.load_patch_deployments")
    @patch("app.main.save_patch_packages")
    @patch("app.main.evaluate_patch_shadow")
    @patch("app.main.load_patch_packages")
    def test_auto_govern_deployed_patch_rolls_back_when_shadow_failed(
        self,
        mock_load_patch_packages,
        mock_eval_shadow,
        mock_save_patch_packages,
        mock_load_patch_deployments,
        mock_save_patch_deployments,
    ):
        from app.main import _auto_govern_deployed_patch

        mock_load_patch_packages.return_value = [
            {
                "id": "patch_new",
                "project_id": "p1",
                "status": "deployed",
                "rollback_pointer": "patch_old",
                "patch_payload": {"penalty_multiplier": {"P-EMPTY-002": 1.1}},
                "updated_at": "2026-02-27T10:00:00+00:00",
            },
            {
                "id": "patch_old",
                "project_id": "p1",
                "status": "shadow_pass",
                "patch_payload": {},
                "updated_at": "2026-02-27T09:00:00+00:00",
            },
        ]
        mock_eval_shadow.return_value = {
            "ok": True,
            "patch_id": "patch_new",
            "gate_passed": False,
            "metrics_before_after": {
                "mae_before": 2.0,
                "mae_after": 2.5,
                "sample_count": 5,
                "delta_mae": 0.5,
            },
        }
        mock_load_patch_deployments.return_value = []

        result = _auto_govern_deployed_patch(
            project_id="p1",
            delta_cases=[{"id": "d1"}, {"id": "d2"}, {"id": "d3"}, {"id": "d4"}, {"id": "d5"}],
        )
        assert result["checked"] is True
        assert result["action"] == "rollback"
        assert result["rolled_back"] is True
        assert result["rollback_to_patch_id"] == "patch_old"

        saved_packages = mock_save_patch_packages.call_args[0][0]
        status_map = {str(p.get("id")): str(p.get("status")) for p in saved_packages}
        assert status_map["patch_new"] == "rolled_back"
        assert status_map["patch_old"] == "deployed"

        saved_deploys = mock_save_patch_deployments.call_args[0][0]
        actions = [str(d.get("action")) for d in saved_deploys]
        assert "auto_rollback" in actions
        assert "auto_promote_rollback_pointer" in actions


class TestAdaptiveEndpoints:
    """Tests for /projects/{project_id}/adaptive* endpoints."""

    @patch("app.main.build_adaptive_suggestions")
    @patch("app.main.load_config")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_suggestions_success(
        self, mock_ensure, mock_load, mock_config, mock_build, client
    ):
        """Adaptive suggestions should return suggestions."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 86.0,
                "report": {"total_score": 86.0, "scoring_status": "scored"},
            }
        ]
        mock_config.return_value = MagicMock(lexicon={})
        mock_build.return_value = {"penalty_stats": {}, "suggestions": []}
        response = client.get("/api/v1/projects/p1/adaptive")
        assert response.status_code == 200

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_no_submissions(self, mock_ensure, mock_load, client):
        """Adaptive should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/adaptive")
        assert response.status_code == 404

    @patch("app.main.build_adaptive_patch")
    @patch("app.main.build_adaptive_suggestions")
    @patch("app.main.load_config")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_patch_success(
        self, mock_ensure, mock_load, mock_config, mock_suggestions, mock_patch, client
    ):
        """Adaptive patch should return patch."""
        mock_load.return_value = [{"id": "s1", "project_id": "p1"}]
        mock_config.return_value = MagicMock(lexicon={})
        mock_suggestions.return_value = {"penalty_stats": {}}
        mock_patch.return_value = {"lexicon_additions": {}, "rubric_adjustments": {}}
        response = client.get("/api/v1/projects/p1/adaptive_patch")
        assert response.status_code == 200

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_patch_no_submissions(self, mock_ensure, mock_load, client):
        """Adaptive patch should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/adaptive_patch")
        assert response.status_code == 404

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_validate_no_submissions(self, mock_ensure, mock_load, client):
        """Adaptive validate should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/adaptive_validate")
        assert response.status_code == 404

    @patch("app.main.score_text")
    @patch("app.main.load_config")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_validate_success(
        self, mock_ensure, mock_load, mock_config, mock_score, client
    ):
        """Adaptive validate should compare old and new scores."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "text": "test text",
            }
        ]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_score.return_value = MagicMock(model_dump=lambda: {"total_score": 82.0})
        response = client.get("/api/v1/projects/p1/adaptive_validate")
        assert response.status_code == 200
        data = response.json()
        assert data["avg_delta"] == 2.0
        assert len(data["comparisons"]) == 1

    @patch("app.main.score_text")
    @patch("app.main.load_config")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_validate_no_text(
        self, mock_ensure, mock_load, mock_config, mock_score, client
    ):
        """Adaptive validate should skip submissions without text."""
        mock_load.return_value = [
            {"id": "s1", "project_id": "p1", "filename": "f1.txt", "total_score": 80.0}
        ]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        response = client.get("/api/v1/projects/p1/adaptive_validate")
        assert response.status_code == 200
        data = response.json()
        assert data["avg_delta"] == 0.0
        assert len(data["comparisons"]) == 0


class TestInsightsEndpoint:
    """Tests for /projects/{project_id}/insights endpoint."""

    @patch("app.main.build_project_insights")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_insights_success(self, mock_ensure, mock_load, mock_insights, client):
        """Insights should return project insights."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 88.0,
                "report": {"total_score": 88.0, "scoring_status": "scored"},
            }
        ]
        mock_insights.return_value = {
            "dimension_avg": {},
            "weakest_dims": [],
            "frequent_penalties": [],
            "recommendations": [],
        }
        response = client.get("/api/v1/projects/p1/insights")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_insights_no_submissions(self, mock_ensure, mock_load, client):
        """Insights should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/insights")
        assert response.status_code == 404

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_insights_requires_scored_submissions(self, mock_ensure, mock_load, client):
        """Insights should require scored submissions."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 0.0,
                "report": {"scoring_status": "pending"},
            }
        ]
        response = client.get("/api/v1/projects/p1/insights")
        assert response.status_code == 404
        assert "请先点击“评分施组”" in response.json()["detail"]


class TestLearningEndpoints:
    """Tests for /projects/{project_id}/learning endpoints."""

    @patch("app.main.save_learning_profiles")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.build_learning_profile")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_update_learning_profile_success(
        self, mock_ensure, mock_load_sub, mock_build, mock_load_prof, mock_save, client
    ):
        """Update learning profile should save and return profile."""
        mock_load_sub.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 90.0,
                "report": {"total_score": 90.0, "scoring_status": "scored"},
            }
        ]
        mock_build.return_value = {
            "dimension_multipliers": {"D01": 1.1},
            "rationale": {"D01": "test rationale"},
        }
        mock_load_prof.return_value = []
        response = client.post("/api/v1/projects/p1/learning")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        mock_save.assert_called_once()

    @patch("app.main.save_learning_profiles")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.build_learning_profile")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_update_learning_profile_replaces_existing(
        self, mock_ensure, mock_load_sub, mock_build, mock_load_prof, mock_save, client
    ):
        """Update learning profile should replace existing profile."""
        mock_load_sub.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 90.0,
                "report": {"total_score": 90.0, "scoring_status": "scored"},
            }
        ]
        mock_build.return_value = {
            "dimension_multipliers": {"D01": 1.2},
            "rationale": {"D01": "new rationale"},
        }
        mock_load_prof.return_value = [{"project_id": "p1", "dimension_multipliers": {"D01": 1.0}}]
        response = client.post("/api/v1/projects/p1/learning")
        assert response.status_code == 200
        # Verify old profile was removed
        call_args = mock_save.call_args[0][0]
        p1_profiles = [p for p in call_args if p.get("project_id") == "p1"]
        assert len(p1_profiles) == 1

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_update_learning_profile_no_submissions(self, mock_ensure, mock_load, client):
        """Update learning profile should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.post("/api/v1/projects/p1/learning")
        assert response.status_code == 404

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_update_learning_profile_requires_scored_submissions(
        self, mock_ensure, mock_load, client
    ):
        """Learning profile update should require scored submissions."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "total_score": 0.0,
                "report": {"scoring_status": "pending"},
            }
        ]
        response = client.post("/api/v1/projects/p1/learning")
        assert response.status_code == 404
        assert "请先点击“评分施组”" in response.json()["detail"]

    @patch("app.main.load_learning_profiles")
    @patch("app.main.ensure_data_dirs")
    def test_get_learning_profile_success(self, mock_ensure, mock_load, client):
        """Get learning profile should return existing profile."""
        mock_load.return_value = [
            {
                "project_id": "p1",
                "dimension_multipliers": {"D01": 1.1},
                "rationale": {"D01": "test"},
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        response = client.get("/api/v1/projects/p1/learning")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"

    @patch("app.main.load_learning_profiles")
    @patch("app.main.ensure_data_dirs")
    def test_get_learning_profile_not_found(self, mock_ensure, mock_load, client):
        """Get learning profile should return 404 if not found."""
        mock_load.return_value = []
        response = client.get("/api/v1/projects/p1/learning")
        assert response.status_code == 404
        assert "暂无学习画像" in response.json()["detail"]


class TestAdaptiveApplyEndpoint:
    """Tests for /projects/{project_id}/adaptive_apply endpoint."""

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_apply_no_submissions(self, mock_ensure, mock_load, client):
        """Adaptive apply should return 404 if no submissions."""
        mock_load.return_value = []
        response = client.post("/api/v1/projects/p1/adaptive_apply")
        assert response.status_code == 404

    @patch("yaml.safe_dump")
    @patch("pathlib.Path")
    @patch("app.main.apply_adaptive_patch")
    @patch("app.main.build_adaptive_patch")
    @patch("app.main.build_adaptive_suggestions")
    @patch("app.main.load_config")
    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_adaptive_apply_success(
        self,
        mock_ensure,
        mock_load_sub,
        mock_config,
        mock_suggestions,
        mock_patch,
        mock_apply,
        mock_path,
        mock_yaml_dump,
        client,
    ):
        """Adaptive apply should update lexicon and return result."""
        mock_load_sub.return_value = [{"id": "s1", "project_id": "p1"}]
        mock_config.return_value = MagicMock(lexicon={})
        mock_suggestions.return_value = {"penalty_stats": {}}
        mock_patch.return_value = {"lexicon_additions": {}, "rubric_adjustments": {}}
        mock_apply.return_value = ({"updated": True}, ["change1", "change2"])

        # Mock Path operations
        mock_lexicon_path = MagicMock()
        mock_lexicon_path.read_text.return_value = "old: content"
        mock_backup_path = MagicMock()
        mock_backup_path.__str__ = MagicMock(return_value="/backup/path.yaml")
        mock_lexicon_path.with_name.return_value = mock_backup_path

        # Configure Path mock chain
        mock_path_instance = MagicMock()
        mock_path_instance.resolve.return_value.parent.__truediv__ = (
            lambda self, x: mock_lexicon_path
        )
        mock_path.return_value = mock_path_instance

        response = client.post("/api/v1/projects/p1/adaptive_apply")
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == "p1"
        assert data["applied"] is True
        assert "changes" in data


class TestMainExecution:
    """Tests for main module execution."""

    def test_app_title(self):
        """App should have correct title."""
        assert app.title == "青天评标系统 API"

    def test_app_version(self):
        """App should have correct version."""
        assert app.version == "1.0.0"


class TestApiI18n:
    """Tests for API i18n support via Accept-Language header."""

    def test_parse_accept_language_none(self):
        """parse_accept_language should return default locale for None."""
        from app.main import parse_accept_language

        assert parse_accept_language(None) == "zh"

    def test_parse_accept_language_empty(self):
        """parse_accept_language should return default locale for empty string."""
        from app.main import parse_accept_language

        assert parse_accept_language("") == "zh"

    def test_parse_accept_language_simple_zh(self):
        """parse_accept_language should parse simple zh."""
        from app.main import parse_accept_language

        assert parse_accept_language("zh") == "zh"

    def test_parse_accept_language_simple_en(self):
        """parse_accept_language should parse simple en."""
        from app.main import parse_accept_language

        assert parse_accept_language("en") == "en"

    def test_parse_accept_language_with_region(self):
        """parse_accept_language should handle regional variants."""
        from app.main import parse_accept_language

        assert parse_accept_language("zh-CN") == "zh"
        assert parse_accept_language("en-US") == "en"
        assert parse_accept_language("en-GB") == "en"

    def test_parse_accept_language_with_quality(self):
        """parse_accept_language should respect quality values."""
        from app.main import parse_accept_language

        # en has higher quality
        assert parse_accept_language("zh;q=0.8,en;q=0.9") == "en"
        # zh has higher quality
        assert parse_accept_language("en;q=0.8,zh;q=0.9") == "zh"

    def test_parse_accept_language_complex(self):
        """parse_accept_language should handle complex Accept-Language."""
        from app.main import parse_accept_language

        assert parse_accept_language("en-US,en;q=0.9,zh;q=0.8") == "en"
        assert parse_accept_language("zh-CN,zh;q=0.9,en;q=0.8") == "zh"

    def test_parse_accept_language_unsupported(self):
        """parse_accept_language should fall back for unsupported languages."""
        from app.main import parse_accept_language

        assert parse_accept_language("fr") == "zh"
        assert parse_accept_language("de-DE,fr;q=0.9") == "zh"

    def test_parse_accept_language_invalid_quality(self):
        """parse_accept_language should handle invalid quality values."""
        from app.main import parse_accept_language

        assert parse_accept_language("en;q=invalid") == "en"

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_material_error_zh(self, mock_ensure, mock_load, client):
        """Upload material should return Chinese error with Accept-Language: zh."""
        mock_load.return_value = []
        response = client.post(
            "/api/v1/projects/nonexistent/materials",
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
            headers={"Accept-Language": "zh"},
        )
        assert response.status_code == 404
        assert "项目不存在" in response.json()["detail"]

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_upload_material_error_en(self, mock_ensure, mock_load, client):
        """Upload material should return English error with Accept-Language: en."""
        mock_load.return_value = []
        response = client.post(
            "/api/v1/projects/nonexistent/materials",
            files={"file": ("test.txt", BytesIO(b"test"), "text/plain")},
            headers={"Accept-Language": "en"},
        )
        assert response.status_code == 404
        assert "Project not found" in response.json()["detail"]

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_error_zh(self, mock_ensure, mock_load, client):
        """Compare should return Chinese error with Accept-Language: zh."""
        mock_load.return_value = []
        response = client.get(
            "/api/v1/projects/p1/compare",
            headers={"Accept-Language": "zh"},
        )
        assert response.status_code == 404
        assert "暂无施组记录" in response.json()["detail"]

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_error_en(self, mock_ensure, mock_load, client):
        """Compare should return English error with Accept-Language: en."""
        mock_load.return_value = []
        response = client.get(
            "/api/v1/projects/p1/compare",
            headers={"Accept-Language": "en"},
        )
        assert response.status_code == 404
        assert "No submission records" in response.json()["detail"]

    @patch("app.main.load_learning_profiles")
    @patch("app.main.ensure_data_dirs")
    def test_get_learning_profile_error_zh(self, mock_ensure, mock_load, client):
        """Get learning profile should return Chinese error."""
        mock_load.return_value = []
        response = client.get(
            "/api/v1/projects/p1/learning",
            headers={"Accept-Language": "zh"},
        )
        assert response.status_code == 404
        assert "暂无学习画像" in response.json()["detail"]

    @patch("app.main.load_learning_profiles")
    @patch("app.main.ensure_data_dirs")
    def test_get_learning_profile_error_en(self, mock_ensure, mock_load, client):
        """Get learning profile should return English error."""
        mock_load.return_value = []
        response = client.get(
            "/api/v1/projects/p1/learning",
            headers={"Accept-Language": "en"},
        )
        assert response.status_code == 404
        assert "No learning profile" in response.json()["detail"]

    @patch("app.main.reload_config")
    def test_config_reload_message_zh(self, mock_reload, client):
        """Config reload should return Chinese message."""
        response = client.post(
            "/api/v1/config/reload",
            headers={"Accept-Language": "zh"},
        )
        assert response.status_code == 200
        assert "配置已重新加载" in response.json()["message"]

    @patch("app.main.reload_config")
    def test_config_reload_message_en(self, mock_reload, client):
        """Config reload should return English message."""
        response = client.post(
            "/api/v1/config/reload",
            headers={"Accept-Language": "en"},
        )
        assert response.status_code == 200
        assert "Configuration reloaded" in response.json()["message"]

    def test_accept_language_with_region_code(self, client):
        """API should accept regional language codes like zh-CN."""
        from app.main import parse_accept_language

        assert parse_accept_language("zh-CN,zh;q=0.9,en;q=0.8") == "zh"
        assert parse_accept_language("en-US,en;q=0.9,zh;q=0.8") == "en"


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_dimension_avg_calculation(self, mock_ensure, mock_load, client):
        """Compare should correctly calculate dimension averages."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "report": {
                    "dimension_scores": {"D01": {"score": 20.0}, "D02": {"score": 30.0}},
                    "penalties": [],
                },
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "f2.txt",
                "total_score": 70.0,
                "report": {
                    "dimension_scores": {"D01": {"score": 24.0}, "D02": {"score": 26.0}},
                    "penalties": [],
                },
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        # D01 avg: (20 + 24) / 2 = 22
        assert data["dimension_avg"]["D01"] == 22.0
        # D02 avg: (30 + 26) / 2 = 28
        assert data["dimension_avg"]["D02"] == 28.0

    @patch("app.main.load_submissions")
    @patch("app.main.ensure_data_dirs")
    def test_compare_penalty_stats_aggregation(self, mock_ensure, mock_load, client):
        """Compare should correctly aggregate penalty stats."""
        mock_load.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "f1.txt",
                "total_score": 80.0,
                "report": {
                    "dimension_scores": {},
                    "penalties": [{"code": "P001"}, {"code": "P002"}],
                },
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "project_id": "p1",
                "filename": "f2.txt",
                "total_score": 70.0,
                "report": {
                    "dimension_scores": {},
                    "penalties": [{"code": "P001"}],
                },
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        response = client.get("/api/v1/projects/p1/compare")
        assert response.status_code == 200
        data = response.json()
        assert data["penalty_stats"]["P001"] == 2
        assert data["penalty_stats"]["P002"] == 1


class TestCacheEndpoints:
    """Tests for cache-related endpoints."""

    def test_cache_stats_returns_200(self, client):
        """GET /api/v1/cache/stats should return 200."""
        response = client.get("/api/v1/cache/stats")
        assert response.status_code == 200

    def test_cache_stats_structure(self, client):
        """Cache stats should have correct structure."""
        response = client.get("/api/v1/cache/stats")
        data = response.json()
        assert "total_requests" in data
        assert "hits" in data
        assert "misses" in data
        assert "evictions" in data
        assert "size" in data
        assert "hit_rate" in data

    def test_cache_stats_types(self, client):
        """Cache stats should have correct types."""
        response = client.get("/api/v1/cache/stats")
        data = response.json()
        assert isinstance(data["total_requests"], int)
        assert isinstance(data["hits"], int)
        assert isinstance(data["misses"], int)
        assert isinstance(data["evictions"], int)
        assert isinstance(data["size"], int)
        assert isinstance(data["hit_rate"], float)

    def test_cache_clear_returns_200(self, client):
        """POST /api/v1/cache/clear should return 200."""
        response = client.post("/api/v1/cache/clear")
        assert response.status_code == 200

    def test_cache_clear_structure(self, client):
        """Cache clear response should have correct structure."""
        response = client.post("/api/v1/cache/clear")
        data = response.json()
        assert "cleared" in data
        assert "count" in data
        assert "message" in data

    def test_cache_clear_zh_message(self, client):
        """Cache clear should return Chinese message with Accept-Language: zh."""
        response = client.post(
            "/api/v1/cache/clear",
            headers={"Accept-Language": "zh"},
        )
        data = response.json()
        # 当缓存为空时，应返回 "缓存为空" 或 "缓存已清空"
        assert "缓存" in data["message"]

    def test_cache_clear_en_message(self, client):
        """Cache clear should return English message with Accept-Language: en."""
        response = client.post(
            "/api/v1/cache/clear",
            headers={"Accept-Language": "en"},
        )
        data = response.json()
        # 应返回 "Cache cleared" 或 "Cache is empty"
        assert "Cache" in data["message"] or "cache" in data["message"]

    @patch("app.main.get_cached_score")
    @patch("app.main.cache_score_result")
    @patch("app.main.load_config")
    @patch("app.main.score_text")
    def test_score_endpoint_uses_cache(
        self, mock_score, mock_config, mock_cache_set, mock_cache_get, client
    ):
        """Score endpoint should check cache first."""
        from app.schemas import LogicLockResult, ScoreReport

        # 模拟缓存未命中
        mock_cache_get.return_value = None
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_score.return_value = ScoreReport(
            total_score=85.0,
            dimension_scores={},
            logic_lock=LogicLockResult(
                definition_score=1.0,
                analysis_score=1.0,
                solution_score=1.0,
                breaks=[],
                evidence=[],
            ),
            penalties=[],
            suggestions=[],
            meta={},
            judge_mode="local",
            judge_source="scorer",
            fallback_reason="",
        )

        response = client.post("/api/v1/score", json={"text": "测试文本"})
        assert response.status_code == 200

        # 验证缓存被检查
        mock_cache_get.assert_called_once_with("测试文本")
        # 验证结果被缓存
        mock_cache_set.assert_called_once()

    @patch("app.main.get_cached_score")
    @patch("app.main.load_config")
    @patch("app.main.score_text")
    def test_score_endpoint_returns_cached_result(
        self, mock_score, mock_config, mock_cache_get, client
    ):
        """Score endpoint should return cached result when available."""
        # 模拟缓存命中
        cached_result = {
            "total_score": 90.0,
            "dimension_scores": {},
            "logic_lock": {
                "definition_score": 1.0,
                "analysis_score": 1.0,
                "solution_score": 1.0,
                "breaks": [],
                "evidence": [],
            },
            "penalties": [],
            "penalties_logic_lock": [],
            "penalties_empty_promises": [],
            "penalties_action_missing": [],
            "suggestions": [],
            "meta": {},
            "judge_mode": "local",
            "judge_source": "scorer",
            "fallback_reason": "",
        }
        mock_cache_get.return_value = cached_result

        response = client.post("/api/v1/score", json={"text": "缓存测试"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_score"] == 90.0

        # 验证 score_text 没有被调用（使用了缓存）
        mock_score.assert_not_called()


class TestProjectLevelCacheIntegration:
    """Tests for project-level endpoint cache integration."""

    @patch("app.main.get_cached_score")
    @patch("app.main.cache_score_result")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_upload_only_skips_cache_and_scoring(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_cache_set,
        mock_cache_get,
        client,
    ):
        """Upload shigong should not trigger scoring/cache in upload-only workflow."""
        # 模拟缓存未命中
        mock_cache_get.return_value = None
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 80.0, "dimension_scores": {}, "penalties": []}
        )

        response = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("test.txt", BytesIO(b"test content"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["report"]["scoring_status"] == "pending"
        mock_cache_get.assert_not_called()
        mock_cache_set.assert_not_called()
        mock_score.assert_not_called()

    @patch("app.main.get_cached_score")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_upload_only_ignores_existing_cache(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_cache_get,
        client,
    ):
        """Upload shigong should return cached result when available."""
        # 模拟缓存命中
        cached_result = {
            "total_score": 88.0,
            "dimension_scores": {},
            "penalties": [],
        }
        mock_cache_get.return_value = cached_result
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []

        response = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("test.txt", BytesIO(b"cached text"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_score"] == 0.0
        assert data["report"]["scoring_status"] == "pending"
        mock_cache_get.assert_not_called()
        mock_score.assert_not_called()

    @patch("app.main.get_cached_score")
    @patch("app.main.cache_score_result")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_upload_shigong_upload_only_with_profile_still_skips_cache(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_cache_set,
        mock_cache_get,
        client,
    ):
        """Upload shigong should stay pending even if profile multipliers exist."""
        # 模拟缓存未命中
        mock_cache_get.return_value = None
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = [{"project_id": "p1", "dimension_multipliers": {"D01": 1.2}}]
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 82.0, "dimension_scores": {}, "penalties": []}
        )

        response = client.post(
            "/api/v1/projects/p1/shigong",
            files={"file": ("test.txt", BytesIO(b"test content"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["report"]["scoring_status"] == "pending"
        mock_cache_get.assert_not_called()
        mock_cache_set.assert_not_called()
        mock_score.assert_not_called()

    @patch("app.main.get_cached_score")
    @patch("app.main.cache_score_result")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_uses_cache(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_load_evolution_reports,
        mock_cache_set,
        mock_cache_get,
        client,
    ):
        """Score for project should check cache first."""
        # 模拟缓存未命中
        mock_load_evolution_reports.return_value = {
            "p1": {"scoring_evolution": {"total_score_scale": 1.1}}
        }
        mock_cache_get.return_value = None
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 75.0, "dimension_scores": {}, "penalties": []}
        )

        response = client.post("/api/v1/projects/p1/score", json={"text": "测试文本"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_score"] == 82.5

        # 验证缓存被检查
        mock_cache_get.assert_called_once_with("测试文本", None)
        # 验证结果被缓存
        mock_cache_set.assert_called_once()
        cached_report = mock_cache_set.call_args[0][1]
        assert cached_report["total_score"] == 75.0
        assert cached_report["rule_total_score"] == 75.0

    @patch("app.main.get_cached_score")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_returns_cached_result(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_load_evolution_reports,
        mock_cache_get,
        client,
    ):
        """Score for project should return cached result when available."""
        # 模拟缓存命中
        cached_result = {
            "total_score": 92.0,
            "dimension_scores": {},
            "penalties": [],
        }
        mock_load_evolution_reports.return_value = {
            "p1": {"scoring_evolution": {"total_score_scale": 1.1}}
        }
        mock_cache_get.return_value = cached_result
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []

        response = client.post("/api/v1/projects/p1/score", json={"text": "缓存测试"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_score"] == 100.0

        # 验证 score_text 没有被调用（使用了缓存）
        mock_score.assert_not_called()

    @patch("app.main.load_evolution_reports")
    @patch("app.main.get_cached_score")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_cached_result_applies_scale_once(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_cache_get,
        mock_load_evolution_reports,
        client,
    ):
        """Cached raw report should apply evolution total scale only once at read time."""
        mock_load_evolution_reports.return_value = {
            "p1": {"scoring_evolution": {"total_score_scale": 1.1}}
        }
        mock_cache_get.return_value = {
            "total_score": 80.0,
            "rule_total_score": 80.0,
            "dimension_scores": {},
            "penalties": [],
        }
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = []
        mock_load_sub.return_value = []

        response = client.post("/api/v1/projects/p1/score", json={"text": "缓存缩放测试"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_score"] == 88.0
        assert data["report"]["total_score"] == 88.0
        assert data["report"]["rule_total_score"] == 88.0
        mock_score.assert_not_called()

    @patch("app.main.get_cached_score")
    @patch("app.main.cache_score_result")
    @patch("app.main.save_submissions")
    @patch("app.main.load_submissions")
    @patch("app.main.load_learning_profiles")
    @patch("app.main.load_config")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.score_text")
    def test_score_for_project_cache_with_multipliers(
        self,
        mock_score,
        mock_ensure,
        mock_load_proj,
        mock_config,
        mock_profiles,
        mock_load_sub,
        mock_save_sub,
        mock_cache_set,
        mock_cache_get,
        client,
    ):
        """Score for project should use config_hash when multipliers exist."""
        # 模拟缓存未命中
        mock_cache_get.return_value = None
        mock_load_proj.return_value = [{"id": "p1"}]
        mock_config.return_value = MagicMock(rubric={}, lexicon={})
        mock_profiles.return_value = [{"project_id": "p1", "dimension_multipliers": {"D01": 1.3}}]
        mock_load_sub.return_value = []
        mock_score.return_value = MagicMock(
            model_dump=lambda: {"total_score": 78.0, "dimension_scores": {}, "penalties": []}
        )

        response = client.post("/api/v1/projects/p1/score", json={"text": "测试文本"})
        assert response.status_code == 200

        # 验证缓存被检查时包含 config_hash
        call_args = mock_cache_get.call_args
        assert call_args[0][0] == "测试文本"
        # config_hash 应该不是 None（因为有 multipliers）
        assert call_args[0][1] is not None


class TestSystemSelfCheckCapabilities:
    @patch(
        "app.main._build_data_hygiene_report",
        return_value={"orphan_records_total": 0, "datasets": []},
    )
    @patch("app.main._resolve_dwg_converter_binaries", return_value=["/usr/local/bin/dwg2dxf"])
    @patch("app.main.get_rate_limit_status", return_value={"enabled": False})
    @patch("app.main.get_auth_status", return_value={"enabled": False})
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.load_config")
    def test_system_self_check_reports_parser_capabilities(
        self,
        mock_load_config,
        mock_ensure_data_dirs,
        mock_auth_status,
        mock_rate_limit_status,
        mock_dwg_bins,
        mock_data_hygiene,
    ):
        from app.main import _run_system_self_check

        mock_load_config.return_value = MagicMock(rubric={}, lexicon={})
        payload = _run_system_self_check(None)
        names = {str(item.get("name")) for item in payload.get("items", [])}
        assert "parser_pdf" in names
        assert "parser_docx" in names
        assert "parser_ocr" in names
        assert "parser_dwg_converter" in names
        assert "data_hygiene" in names
        assert "required_ok" in payload
        assert "degraded" in payload
        assert "failed_required_count" in payload
        assert "failed_optional_count" in payload
        assert payload["checks"]["health"] is True
        assert payload["checks"]["parser_pdf"] is True
        assert payload["summary"]["parser_capability_total"] == 4
        assert payload["summary"]["data_hygiene_orphan_records"] == 0
        assert "openai_api_available" in payload["checks"]
        assert "parse_job_summary" in payload["summary"]
        assert "structured_summary_schema_ok" in payload["summary"]

    @patch(
        "app.main._build_data_hygiene_report",
        return_value={"orphan_records_total": 0, "datasets": []},
    )
    @patch("app.main._resolve_dwg_converter_binaries", return_value=[])
    @patch("app.main.get_rate_limit_status", return_value={"enabled": False})
    @patch("app.main.get_auth_status", return_value={"enabled": False})
    @patch("app.main.ensure_data_dirs")
    @patch("app.main.load_config")
    @patch("app.main.pymupdf", None)
    @patch("app.main.PdfReader", object())
    def test_system_self_check_dwg_converter_optional_not_block_overall_ok(
        self,
        mock_load_config,
        mock_ensure_data_dirs,
        mock_auth_status,
        mock_rate_limit_status,
        mock_dwg_bins,
        mock_data_hygiene,
    ):
        from app.main import _run_system_self_check

        mock_load_config.return_value = MagicMock(rubric={}, lexicon={})
        payload = _run_system_self_check(None)
        assert payload.get("ok") is True
        assert payload.get("required_ok") is True
        assert payload.get("degraded") is True
        assert payload.get("failed_required_count") == 0
        assert payload.get("failed_optional_count") >= 1
        assert payload["checks"]["parser_dwg_converter"] is False
        assert "parser_dwg_converter" in payload["summary"]["failed_optional_items"]
        assert "openai_api_available" in payload["checks"]
        items = payload.get("items") or []
        dwg_item = next((x for x in items if x.get("name") == "parser_dwg_converter"), {})
        assert dwg_item.get("ok") is False
        pdf_item = next((x for x in items if x.get("name") == "parser_pdf"), {})
        assert pdf_item.get("ok") is True


class TestDataHygieneEndpoints:
    @patch("app.main._build_data_hygiene_report")
    def test_system_data_hygiene_audit(self, mock_hygiene, client):
        mock_hygiene.return_value = {
            "generated_at": "2026-03-01T00:00:00+00:00",
            "apply_mode": False,
            "valid_project_count": 2,
            "orphan_records_total": 3,
            "cleaned_records_total": 0,
            "datasets": [
                {
                    "name": "materials",
                    "total": 10,
                    "orphan_count": 3,
                    "cleaned_count": 0,
                    "mode": "project_id",
                }
            ],
            "recommendations": ["发现孤儿记录 3 条，建议修复。"],
        }
        response = client.get("/api/v1/system/data_hygiene")
        assert response.status_code == 200
        data = response.json()
        assert data["apply_mode"] is False
        assert data["orphan_records_total"] == 3
        assert len(data["datasets"]) == 1
        mock_hygiene.assert_called_once_with(apply=False)

    @patch("app.main._build_data_hygiene_report")
    def test_system_data_hygiene_repair(self, mock_hygiene, client):
        mock_hygiene.return_value = {
            "generated_at": "2026-03-01T00:00:00+00:00",
            "apply_mode": True,
            "valid_project_count": 2,
            "orphan_records_total": 3,
            "cleaned_records_total": 3,
            "datasets": [
                {
                    "name": "materials",
                    "total": 10,
                    "orphan_count": 3,
                    "cleaned_count": 3,
                    "mode": "project_id",
                }
            ],
            "recommendations": ["已清理孤儿记录 3 条。"],
        }
        response = client.post("/api/v1/system/data_hygiene/repair")
        assert response.status_code == 200
        data = response.json()
        assert data["apply_mode"] is True
        assert data["cleaned_records_total"] == 3
        mock_hygiene.assert_called_once_with(apply=True)


class TestComputeMultipliersHash:
    """Tests for _compute_multipliers_hash helper function."""

    def test_compute_multipliers_hash_same_input(self):
        """Same multipliers should produce same hash."""
        from app.main import _compute_multipliers_hash

        hash1 = _compute_multipliers_hash({"D01": 1.2, "D02": 1.1})
        hash2 = _compute_multipliers_hash({"D01": 1.2, "D02": 1.1})
        assert hash1 == hash2

    def test_compute_multipliers_hash_different_input(self):
        """Different multipliers should produce different hash."""
        from app.main import _compute_multipliers_hash

        hash1 = _compute_multipliers_hash({"D01": 1.2})
        hash2 = _compute_multipliers_hash({"D01": 1.3})
        assert hash1 != hash2

    def test_compute_multipliers_hash_order_independent(self):
        """Hash should be order independent."""
        from app.main import _compute_multipliers_hash

        hash1 = _compute_multipliers_hash({"D01": 1.2, "D02": 1.1})
        hash2 = _compute_multipliers_hash({"D02": 1.1, "D01": 1.2})
        assert hash1 == hash2


class TestEvolutionTotalScale:
    """Tests for evolution total-score scale helper."""

    def test_apply_evolution_total_scale_scales_pred_and_total_consistently(self):
        from app.main import _apply_evolution_total_scale

        report = {
            "total_score": 65.0,
            "rule_total_score": 70.0,
            "pred_total_score": 65.0,
            "llm_total_score": 60.0,
        }
        with patch(
            "app.main.load_evolution_reports",
            return_value={"p1": {"scoring_evolution": {"total_score_scale": 1.1}}},
        ):
            _apply_evolution_total_scale("p1", report)

        assert report["pred_total_score"] == 71.5
        assert report["rule_total_score"] == 77.0
        assert report["llm_total_score"] == 66.0
        assert report["total_score"] == 71.5

    def test_compute_multipliers_hash_empty(self):
        """Empty dict should produce valid hash."""
        from app.main import _compute_multipliers_hash

        hash_value = _compute_multipliers_hash({})
        assert hash_value is not None
        assert len(hash_value) == 16  # 截断为 16 字符


class TestPdfFallbackParser:
    @patch("app.main.pymupdf", None)
    @patch("app.main.PdfReader")
    def test_read_uploaded_file_content_pdf_uses_pypdf_fallback(self, mock_pdf_reader):
        from app.main import _read_uploaded_file_content

        class _FakePage:
            def extract_text(self):
                return "这是 PDF 提取文本"

        class _FakeReader:
            def __init__(self, _stream):
                self.pages = [_FakePage()]

        mock_pdf_reader.side_effect = _FakeReader
        text = _read_uploaded_file_content(b"%PDF-fallback", "sample.pdf")
        assert "[PDF_BACKEND:pypdf]" in text
        assert "这是 PDF 提取文本" in text


class TestScoringContextEvolutionGuard:
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    def test_resolve_project_scoring_context_skips_stale_evolution_weights(
        self,
        mock_load_projects,
        mock_load_profiles,
        mock_load_evolution,
    ):
        from app.main import _resolve_project_scoring_context

        stale_at = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "meta": {
                    "evolution_weight_min_samples": 3,
                    "evolution_weight_max_age_days": 90,
                },
                "expert_profile_id": "ep1",
            }
        ]
        mock_load_profiles.return_value = [
            {
                "id": "ep1",
                "weights_norm": {"01": 0.9, "02": 0.1},
            }
        ]
        mock_load_evolution.return_value = {
            "p1": {
                "sample_count": 20,
                "updated_at": stale_at,
                "scoring_evolution": {"dimension_multipliers": {"01": 9.99}},
            }
        }

        multipliers, profile_snapshot, project = _resolve_project_scoring_context("p1")
        assert project.get("id") == "p1"
        assert isinstance(multipliers, dict)
        assert multipliers.get("01") != 9.99
        assert profile_snapshot is not None
        assert profile_snapshot.get("id") == "ep1"

    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_projects")
    def test_resolve_project_scoring_context_uses_fresh_evolution_weights(
        self,
        mock_load_projects,
        mock_load_profiles,
        mock_load_evolution,
    ):
        from app.main import _resolve_project_scoring_context

        fresh_at = datetime.now(timezone.utc).isoformat()
        mock_load_projects.return_value = [
            {
                "id": "p1",
                "meta": {
                    "evolution_weight_min_samples": 3,
                    "evolution_weight_max_age_days": 90,
                },
                "expert_profile_id": "ep1",
            }
        ]
        mock_load_profiles.return_value = [
            {
                "id": "ep1",
                "weights_norm": {"01": 0.5, "02": 0.5},
            }
        ]
        mock_load_evolution.return_value = {
            "p1": {
                "sample_count": 3,
                "updated_at": fresh_at,
                "scoring_evolution": {"dimension_multipliers": {"01": 1.3}},
            }
        }

        multipliers, profile_snapshot, _ = _resolve_project_scoring_context("p1")
        assert profile_snapshot is None
        assert multipliers.get("01") == pytest.approx(1.3, abs=1e-6)


class TestFeedbackClosedLoopSafety:
    @patch("app.main._run_feedback_closed_loop")
    def test_run_feedback_closed_loop_safe_coerces_non_dict(self, mock_run):
        from app.main import _run_feedback_closed_loop_safe

        mock_run.return_value = MagicMock(ok=True)
        payload = _run_feedback_closed_loop_safe("p1", locale="zh", trigger="rescore")
        assert isinstance(payload, dict)
        assert payload.get("project_id") == "p1"
        assert payload.get("trigger") == "rescore"

    @patch("app.main.auto_run_reflection_pipeline")
    @patch("app.main._sync_feedback_weights_to_evolution")
    @patch("app.main._auto_update_project_weights_from_delta_cases")
    @patch("app.main._refresh_evolution_report_from_ground_truth")
    @patch("app.main._refresh_project_reflection_objects")
    @patch("app.main.load_ground_truth")
    def test_run_feedback_closed_loop_skips_auto_update_when_guardrail_blocked(
        self,
        mock_load_ground_truth,
        mock_refresh_reflection,
        mock_refresh_evo,
        mock_auto_update,
        mock_sync_weights,
        mock_auto_run,
    ):
        from app.main import _run_feedback_closed_loop

        mock_load_ground_truth.return_value = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "feedback_guardrail": {
                    "blocked": True,
                    "abs_delta_100": 45.0,
                    "warning_message": "预测与真实总分偏差过大，已暂停自动调权/自动校准。",
                },
            }
        ]
        mock_refresh_evo.return_value = {"refreshed": True, "sample_count": 0}

        payload = _run_feedback_closed_loop(
            "p1",
            locale="zh",
            trigger="ground_truth_add",
            ground_truth_record_ids=["gt-1"],
        )

        assert payload["ok"] is True
        assert payload["guardrail_triggered"] is True
        assert payload["requires_manual_confirmation"] is True
        assert payload["auto_update_skipped"] is True
        assert payload["feedback_guardrail"]["blocked"] is True
        mock_refresh_reflection.assert_called_once()
        mock_refresh_evo.assert_called_once()
        mock_auto_update.assert_not_called()
        mock_sync_weights.assert_not_called()
        mock_auto_run.assert_not_called()


class TestGroundTruthGuardrailRoutes:
    @patch("app.main._run_feedback_closed_loop_safe")
    @patch("app.main._sync_ground_truth_record_to_qingtian")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_submissions")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_add_ground_truth_from_submission_returns_guardrail_metadata(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_submissions,
        mock_load_ground_truth,
        mock_save_ground_truth,
        mock_sync_gt,
        mock_run_closed_loop,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_submissions.return_value = [
            {
                "id": "s1",
                "project_id": "p1",
                "filename": "施组一.docx",
                "text": "示例施组文本" * 20,
            }
        ]
        mock_load_ground_truth.return_value = []
        mock_sync_gt.return_value = {
            "feedback_guardrail": {
                "blocked": True,
                "warning_message": "预测与真实总分偏差 45.00 分（100分口径，45.0%）。",
            },
            "few_shot_distillation": {"captured": 0, "reason": "guardrail_blocked"},
        }
        mock_run_closed_loop.return_value = {
            "ok": True,
            "guardrail_triggered": True,
            "requires_manual_confirmation": True,
            "feedback_guardrail": {"blocked": True},
        }

        response = client.post(
            "/api/v1/projects/p1/ground_truth/from_submission",
            json={
                "submission_id": "s1",
                "judge_scores": [80, 81, 82, 83, 84],
                "final_score": 80,
                "source": "青天大模型",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["feedback_guardrail"]["blocked"] is True
        assert data["few_shot_distillation"]["reason"] == "guardrail_blocked"
        assert data["feedback_closed_loop"]["guardrail_triggered"] is True
        mock_run_closed_loop.assert_called_once()
        assert mock_run_closed_loop.call_args.kwargs["ground_truth_record_ids"]

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main._collect_blocked_ground_truth_guardrails")
    def test_auto_run_reflection_requires_manual_confirm_when_guardrail_blocked(
        self,
        mock_collect_blocked,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_collect_blocked.return_value = [
            {"record_id": "gt-1", "feedback_guardrail": {"blocked": True, "abs_delta_100": 42.0}}
        ]

        response = client.post("/api/v1/projects/p1/reflection/auto_run")

        assert response.status_code == 409
        assert "confirm_extreme_sample=1" in response.json()["detail"]


class TestFeedbackGovernanceRoutes:
    @patch("app.main.load_expert_profiles")
    @patch("app.main.load_calibration_models")
    @patch("app.main.load_evolution_reports")
    @patch("app.main.load_high_score_features")
    @patch("app.main.load_json_version")
    @patch("app.main.list_json_versions")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_governance_route_summarizes_blocked_samples_and_few_shot(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_list_versions,
        mock_load_json_version,
        mock_load_high_score_features,
        mock_load_evolution_reports,
        mock_load_calibration_models,
        mock_load_expert_profiles,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_load_high_score_features.return_value = [
            {
                "feature_id": "f-current-1",
                "dimension_id": "09",
                "confidence_score": 0.88,
                "active": True,
            }
        ]
        mock_load_evolution_reports.return_value = {"p1": {"high_score_logic": ["逻辑A"]}}
        mock_load_calibration_models.return_value = [
            {"calibrator_version": "calib-1", "model_type": "ridge"}
        ]
        mock_load_expert_profiles.return_value = [
            {"id": "ep-1", "name": "默认画像", "read_only": True}
        ]
        mock_load_ground_truth.return_value = [
            {
                "id": "gt-blocked",
                "project_id": "p1",
                "final_score": 80,
                "score_scale_max": 100,
                "judge_scores": [80, 80, 80, 80, 80],
                "source_submission_filename": "异常样本.docx",
                "created_at": "2026-03-14T00:00:00+00:00",
                "feedback_guardrail": {
                    "blocked": True,
                    "threshold_blocked": True,
                    "actual_score_100": 80,
                    "predicted_score_100": 30,
                    "abs_delta_100": 50,
                    "relative_delta_ratio": 0.5,
                    "warning_message": "预测与真实总分偏差过大，已暂停自动调权/自动校准。",
                },
                "few_shot_distillation": {"captured": 0, "reason": "guardrail_blocked"},
            },
            {
                "id": "gt-good",
                "project_id": "p1",
                "final_score": 88,
                "score_scale_max": 100,
                "judge_scores": [88, 88, 88, 88, 88],
                "created_at": "2026-03-14T01:00:00+00:00",
                "few_shot_distillation": {
                    "captured": 2,
                    "reason": "captured",
                    "dimension_ids": ["09"],
                    "feature_ids": ["F-1", "F-2"],
                    "manual_review": {
                        "status": "adopted",
                        "reviewed_at": "2026-03-14T03:00:00+00:00",
                    },
                },
            },
            {
                "id": "gt-approved",
                "project_id": "p1",
                "final_score": 86,
                "score_scale_max": 100,
                "judge_scores": [86, 86, 86, 86, 86],
                "created_at": "2026-03-14T02:00:00+00:00",
                "feedback_guardrail": {
                    "blocked": False,
                    "threshold_blocked": True,
                    "actual_score_100": 86,
                    "predicted_score_100": 40,
                    "abs_delta_100": 46,
                    "manual_review": {
                        "status": "approved",
                        "reviewed_at": "2026-03-14T04:00:00+00:00",
                        "note": "人工放行",
                    },
                },
                "feedback_closed_loop": {
                    "weight_update": {"updated": True},
                    "auto_run": {
                        "delta_cases": 3,
                        "calibration_samples": 5,
                        "calibrator_version": "calib-2",
                    },
                    "evolution_refresh": {"sample_count": 4},
                },
            },
        ]

        def _versions(path):
            stem = getattr(path, "stem", "")
            if stem == "high_score_features":
                return [
                    {
                        "version_id": "20260314T010203000000Z",
                        "created_at": "2026-03-14T01:02:03+00:00",
                    }
                ]
            if stem == "evolution_reports":
                return [
                    {
                        "version_id": "20260314T020304000000Z",
                        "created_at": "2026-03-14T02:03:04+00:00",
                    }
                ]
            if stem == "calibration_models":
                return [
                    {
                        "version_id": "20260314T030405000000Z",
                        "created_at": "2026-03-14T03:04:05+00:00",
                    }
                ]
            if stem == "expert_profiles":
                return [
                    {
                        "version_id": "20260314T040506000000Z",
                        "created_at": "2026-03-14T04:05:06+00:00",
                    }
                ]
            return []

        mock_list_versions.side_effect = _versions
        mock_load_json_version.side_effect = lambda path, version_id, default: (
            [
                {
                    "feature_id": "f-snap-1",
                    "dimension_id": "09",
                    "confidence_score": 0.72,
                    "active": True,
                },
                {
                    "feature_id": "f-snap-2",
                    "dimension_id": "08",
                    "confidence_score": 0.66,
                    "active": True,
                },
            ]
            if getattr(path, "stem", "") == "high_score_features"
            else (
                {"p1": {"high_score_logic": []}}
                if getattr(path, "stem", "") == "evolution_reports"
                else (
                    [{"calibrator_version": "calib-0", "model_type": "offset"}]
                    if getattr(path, "stem", "") == "calibration_models"
                    else [{"id": "ep-old", "name": "旧画像", "read_only": False}]
                )
            )
        )

        with patch("app.main.load_submissions", return_value=[]), patch(
            "app.main.load_score_reports", return_value=[]
        ), patch("app.main.load_qingtian_results", return_value=[]):
            response = client.get("/api/v1/projects/p1/feedback/governance")

        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["ground_truth_count"] == 3
        assert data["summary"]["active_ground_truth_count"] == 2
        assert data["summary"]["blocked_ground_truth_count"] == 1
        assert data["summary"]["few_shot_recent_capture_count"] == 1
        assert data["summary"]["manual_confirmation_required"] is True
        assert data["summary"]["approved_extreme_ground_truth_count"] == 1
        assert data["summary"]["few_shot_adopted_count"] == 1
        assert data["summary"]["pending_extreme_ground_truth_count"] == 1
        assert data["summary"]["few_shot_pending_review_count"] == 0
        assert data["blocked_samples"][0]["record_id"] == "gt-blocked"
        assert data["few_shot_recent"][0]["record_id"] == "gt-good"
        assert data["approved_samples"][0]["record_id"] == "gt-approved"
        assert data["adopted_few_shot"][0]["record_id"] == "gt-good"
        assert (
            data["version_history"][0]["recent_versions"][0]["version_id"]
            == "20260314T010203000000Z"
        )
        assert data["artifact_impacts"][0]["artifact"] == "high_score_features"
        assert data["artifact_impacts"][0]["changed_since_latest_snapshot"] is True
        assert data["score_preview"]["matched_submission_count"] == 0
        assert any(row["artifact"] == "high_score_features" for row in data["version_history"])

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_governance_route_includes_score_preview(
        self,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]

        def _apply_preview(report, *, submission_like, project):
            report["pred_total_score"] = 78.0
            report["total_score"] = 78.0
            report["llm_total_score"] = 78.0
            report["pred_confidence"] = {"sigma": 1.0}
            return "calib-9"

        with patch(
            "app.main.load_ground_truth",
            return_value=[
                {
                    "id": "gt-1",
                    "project_id": "p1",
                    "final_score": 84,
                    "score_scale_max": 100,
                    "judge_scores": [84, 84, 84, 84, 84],
                    "created_at": "2026-03-15T01:00:00+00:00",
                    "source_submission_filename": "投标文件A.docx",
                }
            ],
        ), patch("app.main.load_high_score_features", return_value=[]), patch(
            "app.main.load_evolution_reports", return_value={}
        ), patch(
            "app.main.load_calibration_models",
            return_value=[
                {
                    "calibrator_version": "calib-9",
                    "model_type": "ridge",
                    "deployed": True,
                    "train_filter": {"project_id": "p1"},
                    "updated_at": "2026-03-15T06:00:00+00:00",
                }
            ],
        ), patch("app.main.load_expert_profiles", return_value=[]), patch(
            "app.main.list_json_versions",
            side_effect=lambda path: (
                [
                    {
                        "version_id": "20260315T050000000000Z",
                        "created_at": "2026-03-15T05:00:00+00:00",
                    }
                ]
                if getattr(path, "stem", "") == "calibration_models"
                else []
            ),
        ), patch(
            "app.main.load_json_version",
            side_effect=lambda path, version_id, default: (
                [{"calibrator_version": "calib-8", "model_type": "offset"}]
                if getattr(path, "stem", "") == "calibration_models"
                else default
            ),
        ), patch(
            "app.main.load_submissions",
            return_value=[
                {
                    "id": "s1",
                    "project_id": "p1",
                    "filename": "投标文件A.docx",
                    "text": "测试内容",
                    "total_score": 72.0,
                }
            ],
        ), patch(
            "app.main.load_score_reports",
            return_value=[
                {
                    "id": "r1",
                    "project_id": "p1",
                    "submission_id": "s1",
                    "created_at": "2026-03-15T02:00:00+00:00",
                    "rule_total_score": 70.0,
                    "pred_total_score": 72.0,
                    "rule_dim_scores": {
                        "09": {"dim_score": 4.0},
                        "10": {"dim_score": 5.0},
                    },
                }
            ],
        ), patch(
            "app.main.load_qingtian_results",
            return_value=[
                {
                    "submission_id": "s1",
                    "qt_total_score": 84.0,
                    "created_at": "2026-03-15T03:00:00+00:00",
                    "raw_payload": {
                        "ground_truth_record_id": "gt-1",
                        "final_score_100": 84.0,
                    },
                }
            ],
        ), patch(
            "app.main._apply_prediction_to_report",
            side_effect=_apply_preview,
        ), patch(
            "app.main._resolve_project_scoring_context",
            return_value=(
                {"09": 1.1},
                {"id": "ep-1"},
                {"id": "p1", "meta": {"score_scale_max": 100}},
            ),
        ), patch(
            "app.main._infer_weights_source",
            return_value="expert_profile",
        ), patch(
            "app.main.load_config",
            return_value=MagicMock(rubric={}, lexicon={}),
        ), patch(
            "app.main._build_material_knowledge_profile",
            return_value={},
        ), patch(
            "app.main._build_material_quality_snapshot",
            return_value={},
        ), patch(
            "app.main._build_submission_sandbox_report",
            return_value={
                "rule_total_score": 74.0,
                "pred_total_score": 79.0,
                "rule_dim_scores": {
                    "09": {"dim_score": 6.5},
                    "10": {"dim_score": 5.0},
                },
                "scoring_status": "scored",
            },
        ):
            response = client.get("/api/v1/projects/p1/feedback/governance")

        assert response.status_code == 200
        data = response.json()
        preview = data["score_preview"]
        assert preview["matched_submission_count"] == 1
        assert preview["current_calibrator_version"] == "calib-9"
        assert preview["calibrator_changed_since_latest_snapshot"] is True
        assert preview["requires_rule_rescore"] is False
        assert preview["dimension_preview_supported"] is False
        assert preview["avg_abs_delta_stored"] == 12.0
        assert preview["avg_abs_delta_preview"] == 6.0
        assert preview["avg_abs_delta_improvement"] == 6.0
        assert preview["improved_row_count"] == 1
        row = preview["rows"][0]
        assert row["submission_id"] == "s1"
        assert row["filename"] == "投标文件A.docx"
        assert row["rule_total_score"] == 70.0
        assert row["stored_total_score"] == 72.0
        assert row["preview_total_score"] == 78.0
        assert row["qt_total_score"] == 84.0
        assert row["stored_abs_delta_100"] == 12.0
        assert row["preview_abs_delta_100"] == 6.0
        assert row["abs_delta_improvement"] == 6.0
        sandbox = data["sandbox_preview"]
        assert sandbox["matched_submission_count"] == 1
        assert sandbox["executed_row_count"] == 1
        assert sandbox["weights_source"] == "expert_profile"
        assert sandbox["avg_abs_delta_stored"] == 12.0
        assert sandbox["avg_abs_delta_sandbox"] == 5.0
        assert sandbox["avg_abs_delta_improvement"] == 7.0
        sandbox_row = sandbox["rows"][0]
        assert sandbox_row["sandbox_total_score"] == 79.0
        assert sandbox_row["changed_dimension_count"] == 1
        assert sandbox_row["top_changed_dimensions"][0]["dimension_id"] == "09"
        assert sandbox_row["top_changed_dimensions"][0]["delta"] == 2.5
        assert any(
            "平均绝对偏差从 12.00 分收敛到 6.00 分" in item for item in data["recommendations"]
        )
        assert any(
            "沙箱重评分显示当前完整体系可将平均绝对偏差从 12.00 分收敛到 5.00 分" in item
            for item in data["recommendations"]
        )

    @patch("app.main._run_feedback_closed_loop_safe")
    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_guardrail_review_route_can_approve_and_rerun_closed_loop(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_save_ground_truth,
        mock_run_closed_loop,
        client,
    ):
        store = [
            {
                "id": "gt-1",
                "project_id": "p1",
                "feedback_guardrail": {
                    "blocked": True,
                    "threshold_blocked": True,
                    "actual_score_100": 88,
                    "predicted_score_100": 30,
                    "abs_delta_100": 58,
                    "relative_delta_ratio": 0.58,
                },
            }
        ]

        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_ground_truth.side_effect = lambda: copy.deepcopy(store)

        def _save(rows):
            store[:] = copy.deepcopy(rows)

        mock_save_ground_truth.side_effect = _save
        mock_run_closed_loop.return_value = {"ok": True, "guardrail_triggered": False}

        response = client.post(
            "/api/v1/projects/p1/feedback/governance/guardrail/gt-1/review",
            json={"action": "approve", "note": "人工核验后允许纳入", "rerun_closed_loop": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["feedback_guardrail"]["manual_review_status"] == "approved"
        assert data["feedback_guardrail"]["blocked"] is False
        assert data["feedback_closed_loop"]["ok"] is True
        assert store[0]["feedback_guardrail"]["manual_review_status"] == "approved"
        assert store[0]["feedback_closed_loop"]["ok"] is True
        mock_run_closed_loop.assert_called_once()

    @patch("app.main.save_ground_truth")
    @patch("app.main.load_ground_truth")
    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    def test_feedback_few_shot_review_route_marks_sample_as_adopted(
        self,
        mock_ensure,
        mock_load_projects,
        mock_load_ground_truth,
        mock_save_ground_truth,
        client,
    ):
        store = [
            {
                "id": "gt-2",
                "project_id": "p1",
                "few_shot_distillation": {
                    "captured": 2,
                    "reason": "captured",
                    "feature_ids": ["F-1"],
                },
            }
        ]
        mock_load_projects.return_value = [{"id": "p1"}]
        mock_load_ground_truth.side_effect = lambda: copy.deepcopy(store)

        def _save(rows):
            store[:] = copy.deepcopy(rows)

        mock_save_ground_truth.side_effect = _save

        response = client.post(
            "/api/v1/projects/p1/feedback/governance/few_shot/gt-2/review",
            json={"action": "adopt", "note": "纳入标准样本"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["few_shot_distillation"]["manual_review_status"] == "adopted"
        assert store[0]["few_shot_distillation"]["manual_review_status"] == "adopted"

    @patch("app.main.list_json_versions")
    @patch("app.main.ensure_data_dirs")
    def test_versioned_json_history_supports_high_score_features(
        self,
        mock_ensure,
        mock_list_versions,
        client,
    ):
        mock_list_versions.return_value = [
            {
                "version_id": "20260314T010203000000Z",
                "filename": "high_score_features_v20260314T010203000000Z.json",
                "created_at": "2026-03-14T01:02:03+00:00",
                "size_bytes": 256,
            }
        ]

        response = client.get("/api/v1/ops/versioned-json/high_score_features")

        assert response.status_code == 200
        data = response.json()
        assert data["artifact"] == "high_score_features"
        assert data["versions"][0]["version_id"] == "20260314T010203000000Z"

    @patch("app.main.load_projects")
    @patch("app.main.ensure_data_dirs")
    @patch("app.main._collect_blocked_ground_truth_guardrails")
    def test_evolve_requires_manual_confirm_when_guardrail_blocked(
        self,
        mock_collect_blocked,
        mock_ensure,
        mock_load_projects,
        client,
    ):
        mock_load_projects.return_value = [{"id": "p1", "meta": {"score_scale_max": 100}}]
        mock_collect_blocked.return_value = [
            {"record_id": "gt-1", "feedback_guardrail": {"blocked": True, "abs_delta_100": 42.0}}
        ]

        response = client.post("/api/v1/projects/p1/evolve")

        assert response.status_code == 409
        assert "confirm_extreme_sample=1" in response.json()["detail"]


class TestDynamicBlendAdjustment:
    def test_resolve_dynamic_blend_adjustment_no_signal_keeps_weights(self):
        from app.main import _resolve_dynamic_blend_adjustment

        scale, delta_scale, meta = _resolve_dynamic_blend_adjustment({"meta": {}})
        assert scale == pytest.approx(1.0, abs=1e-6)
        assert delta_scale == pytest.approx(1.0, abs=1e-6)
        assert meta.get("signal_state") == "no_material_signal"
        assert meta.get("reasons") == []

    def test_resolve_dynamic_blend_adjustment_low_coverage_downweights_llm(self):
        from app.main import _resolve_dynamic_blend_adjustment

        report = {
            "meta": {
                "material_utilization": {
                    "retrieval_file_coverage_rate": 0.2,
                    "retrieval_hit_rate": 0.1,
                },
                "material_utilization_gate": {"warned": True},
                "evidence_trace": {"mandatory_hit_rate": 0.2, "source_files_hit_count": 0},
            }
        }
        scale, delta_scale, meta = _resolve_dynamic_blend_adjustment(report)
        assert 0.0 < scale < 1.0
        assert 0.0 < delta_scale <= 1.0
        assert meta.get("signal_state") == "material_signal_detected"
        assert any("material_gate_warned" == reason for reason in (meta.get("reasons") or []))


class TestStructuredMaterialAdvancedParsing:
    def test_aggregate_tender_page_structures_builds_outline_and_table_constraints(self):
        from app.main import _aggregate_tender_page_structures

        payload = _aggregate_tender_page_structures(
            [
                {
                    "page_no": 1,
                    "page_type": "toc",
                    "section_title": "目录",
                    "section_level": 0,
                    "section_path": ["目录"],
                    "scoring_terms": [],
                    "mandatory_clauses": [],
                    "numeric_constraints": [],
                    "table_rows": [],
                    "focused_dimensions": ["01"],
                    "parse_confidence": 0.62,
                },
                {
                    "page_no": 2,
                    "page_type": "scoring_rules",
                    "section_title": "评分办法",
                    "section_level": 1,
                    "section_path": ["第一章", "评分办法"],
                    "scoring_terms": ["评分办法", "关键节点"],
                    "mandatory_clauses": ["必须满足节点工期"],
                    "numeric_constraints": ["90天"],
                    "table_rows": [{"label": "工期", "value": "90天", "numbers": ["90天"]}],
                    "focused_dimensions": ["09", "16"],
                    "parse_confidence": 0.84,
                },
            ]
        )

        assert payload["scoring_rule_pages"] == [2]
        assert payload["page_type_summary"][0]["page_type"] in {"scoring_rules", "toc"}
        assert "第一章" in payload["section_title_paths"]
        assert payload["table_constraint_rows"][0]["label"] == "工期"
        assert "90" in payload["table_numeric_constraints"]

    def test_build_drawing_structured_summary_extracts_dwg_binary_markers(self):
        from app.main import _build_drawing_structured_summary

        content = (
            b"AC1032 DWG PUMP_ROOM GRID_A1 DN200 SECTION-01 HVAC_PIPE FIRE_ALARM" b" LEVEL_1 PLAN_A"
        )
        summary = _build_drawing_structured_summary(content, "sample.dwg", parsed_text="")
        assert summary["detected_format"] == "dwg"
        assert summary["binary_marker_terms"]
        assert summary["structured_quality_score"] > 0

    def test_build_site_photo_evidence_summary_deduplicates_objects(self):
        from app.main import _build_site_photo_evidence_summary

        payload = _build_site_photo_evidence_summary(
            [
                {
                    "scene_type": "安全巡查",
                    "risk_level": "high",
                    "safety_findings": ["临边防护缺失"],
                    "quality_findings": ["钢筋外露"],
                    "visible_objects": ["脚手架", "临边洞口"],
                    "numeric_markers": ["2处"],
                    "evidence_confidence": 0.72,
                },
                {
                    "scene_type": "安全巡查",
                    "risk_level": "high",
                    "safety_findings": ["临边防护缺失"],
                    "quality_findings": ["钢筋外露"],
                    "visible_objects": ["脚手架", "临边洞口"],
                    "numeric_markers": ["2处"],
                    "evidence_confidence": 0.7,
                },
                {
                    "scene_type": "进度实景",
                    "risk_level": "medium",
                    "progress_findings": ["主体施工"],
                    "visible_objects": ["塔吊", "模板"],
                    "numeric_markers": ["3层"],
                    "evidence_confidence": 0.65,
                },
            ]
        )

        assert payload["photo_count"] == 3
        assert payload["unique_evidence_count"] == 2
        assert payload["duplicate_evidence_count"] == 1
        assert payload["avg_evidence_confidence"] > 0.6

    def test_build_material_score_shaping_rewards_consensus_and_feedback(self):
        from app.main import _build_material_score_shaping

        report = {
            "rule_total_score": 78.0,
            "meta": {
                "evidence_trace": {"mandatory_hit_rate": 0.82},
            },
        }
        material_knowledge_snapshot = {
            "summary": {
                "cross_type_consensus_score": 0.48,
                "cross_type_consensus_type_count": 3,
                "structured_quality_avg": 0.63,
                "structured_quality_type_rate": 0.67,
                "strong_structured_types": 3,
            },
            "by_type": [
                {"mandatory_clause_terms_preview": ["必须提供节点工期", "不得缺少深化设计"]},
                {"mandatory_clause_terms_preview": ["必须满足质量验收"]},
            ],
        }
        shaping = _build_material_score_shaping(
            report,
            runtime_req_meta={
                "site_photo_visual_requirements": 1,
                "feedback_evolution_requirements": 3,
                "feature_confidence_requirements": 2,
            },
            material_knowledge_snapshot=material_knowledge_snapshot,
        )
        assert shaping["net_delta"] > 0
        assert "cross_material_consensus" in shaping["reasons"]

    def test_build_material_score_shaping_penalizes_weak_material_signals(self):
        from app.main import _build_material_score_shaping

        report = {
            "rule_total_score": 78.0,
            "meta": {
                "evidence_trace": {"mandatory_hit_rate": 0.22},
            },
        }
        material_knowledge_snapshot = {
            "summary": {
                "cross_type_consensus_score": 0.08,
                "cross_type_consensus_type_count": 1,
                "structured_quality_avg": 0.16,
                "structured_quality_type_rate": 0.12,
                "strong_structured_types": 1,
            },
            "by_type": [
                {
                    "mandatory_clause_terms_preview": [
                        "必须满足节点工期",
                        "不得缺少关键方案",
                        "须提供验收表",
                    ]
                },
            ],
        }
        shaping = _build_material_score_shaping(
            report,
            runtime_req_meta={"site_photo_visual_requirements": 0},
            material_knowledge_snapshot=material_knowledge_snapshot,
        )
        assert shaping["net_delta"] < 0
        assert "mandatory_clause_underhit" in shaping["reasons"]

    def test_build_material_score_shaping_stays_neutral_without_evidence_context(self):
        from app.main import _build_material_score_shaping

        report = {
            "rule_total_score": 81.2,
            "meta": {
                "evidence_trace": {"mandatory_hit_rate": 0.9},
            },
        }
        shaping = _build_material_score_shaping(
            report,
            runtime_req_meta={"runtime_custom_requirements": 1},
            material_knowledge_snapshot={
                "summary": {
                    "total_files": 1,
                    "parsed_ok_files": 0,
                    "structured_signal_total": 0,
                    "dimension_coverage_rate": 0.0,
                },
                "by_type": [],
            },
        )
        assert shaping["enabled"] is False
        assert shaping["net_delta"] == 0.0
        assert "insufficient_material_evidence_context" in shaping["reasons"]
