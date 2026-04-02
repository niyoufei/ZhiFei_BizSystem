from __future__ import annotations

import json
from pathlib import Path

from scripts.browser_button_smoke import (
    BUTTON_SMOKE_MATRIX,
    SMOKE_PAGE_HELPERS,
    SYSTEM_CHROME_EXECUTABLE,
    WRITE_BUTTON_SMOKE_MATRIX,
    browser_launch_candidates,
    build_report_markdown,
    build_step_matrix,
    capture_config_integrity_snapshot,
    file_sha256,
    resolve_admin_api_key,
    resolve_project_context,
    resolve_project_id,
    verify_config_integrity_snapshot,
    write_smoke_enabled,
)


def test_browser_button_smoke_matrix_covers_high_value_buttons() -> None:
    ids = [item["id"] for item in BUTTON_SMOKE_MATRIX]
    by_id = {item["id"]: item for item in BUTTON_SMOKE_MATRIX}
    assert "btnSaveApiKey" in ids
    assert "btnClearApiKey" in ids
    assert "btnStartNewProject" in ids
    assert "materialsTrialPreflightFollowUpAction" in ids
    assert "btnWeightsReset" in ids
    assert "btnOptimizationReport" in ids
    assert "btnCompareReportRow" in ids
    assert "btnCompareReport" in ids
    assert "btnScoringFactors" in ids
    assert "btnScoringFactorsMd" in ids
    assert "btnCompare" in ids
    assert "btnInsights" in ids
    assert "btnEvidenceTrace" in ids
    assert "btnEvidenceTraceDownload" in ids
    assert "btnScoringBasis" in ids
    assert "btnScoringDiagnostic" in ids
    assert "btnMaterialDepthReport" in ids
    assert "btnMaterialDepthReportDownload" in ids
    assert "btnMaterialKnowledgeProfile" in ids
    assert "btnMaterialKnowledgeProfileDownload" in ids
    assert "btnFeedbackGovernance" in ids
    assert "btnEvalSummaryV2" in ids
    assert "btnEvalMetricsV2" in ids
    assert "btnAdaptive" in ids
    assert "btnAdaptivePatch" in ids
    assert "btnAdaptiveValidate" in ids
    assert "btnAnalysisBundle" in ids
    assert "btnAnalysisBundleDownload" in ids
    assert "btnWritingGuidance" in ids
    assert "btnCompilationInstructions" in ids
    assert "btnExportInstructions" in ids
    assert "btnWritingGuidanceDownload" in ids
    assert "btnWritingGuidancePatchBundleDownload" in ids
    assert "btnWritingGuidancePatchBundleDownloadDocx" in ids
    assert "refreshProjects" in ids
    assert "btnSelectProjectBySearch" in ids
    assert "btnRefreshMaterials" in ids
    assert "btnRefreshSubmissions" in ids
    assert "btnRefreshGroundTruth" in ids
    assert "btnSelfCheck" in ids
    assert "btnSystemImprovementOverview" in ids
    assert "btnDataHygiene" in ids
    assert "btnEvolutionHealth" in ids
    assert "btnTrialPreflight" in ids
    assert "btnTrialPreflightDownload" in ids
    assert "btnTrialPreflightDownloadDocx" in ids
    assert by_id["btnSaveApiKey"]["kind"] == "js_check"
    assert "window.__codexRevealForSmoke('#btnSaveApiKey')" in by_id["btnSaveApiKey"]["prepare_js"]
    assert "localStorage.removeItem('api_key')" in by_id["btnSaveApiKey"]["prepare_js"]
    assert "API Key 校验通过，已保存" in by_id["btnSaveApiKey"]["verify_js"]
    assert by_id["btnClearApiKey"]["kind"] == "js_check"
    assert by_id["btnClearApiKey"]["requires_api_key"] is True
    assert "window.__codexRevealForSmoke('#btnClearApiKey')" in by_id["btnClearApiKey"]["prepare_js"]
    assert "localStorage.getItem('api_key')" in by_id["btnClearApiKey"]["verify_js"]
    assert "input.value" in by_id["btnClearApiKey"]["verify_js"]
    assert "tag: String((tag && tag.textContent) || '')" in by_id["btnClearApiKey"]["observed_js"]
    assert by_id["btnStartNewProject"]["kind"] == "js_check"
    assert "window.__codexRevealForSmoke('#btnStartNewProject')" in by_id["btnStartNewProject"]["prepare_js"]
    assert "project_intake_mode" in by_id["btnStartNewProject"]["verify_js"]
    assert "window.__codexForceProjectContextForSmoke" in by_id["btnStartNewProject"]["verify_js"]
    assert by_id["materialsTrialPreflightFollowUpAction"]["kind"] == "js_check"
    assert (
        "window.__codexActivateTrialPreflightFollowUpForSmoke"
        in by_id["materialsTrialPreflightFollowUpAction"]["prepare_js"]
    )
    assert "section-shigong" in by_id["materialsTrialPreflightFollowUpAction"]["verify_js"]
    assert "btnUploadShigong" in by_id["materialsTrialPreflightFollowUpAction"]["verify_js"]
    assert "btnScoreShigong" in by_id["materialsTrialPreflightFollowUpAction"]["verify_js"]
    assert by_id["btnWeightsReset"]["kind"] == "js_check"
    assert "window.__codexSetWeightSliderForSmoke('09', 7)" in by_id["btnWeightsReset"]["prepare_js"]
    assert "String(input.value || '') === '5'" in by_id["btnWeightsReset"]["verify_js"]
    assert by_id["btnCompare"]["prepare_js"]
    assert by_id["btnCompareReport"]["prepare_js"]
    assert by_id["btnAdaptive"]["prepare_js"]
    assert by_id["btnScoringDiagnostic"]["expected_output_text"] == "评分证据链诊断已刷新。"
    assert "评分证据链诊断（项目级）" in by_id["btnScoringDiagnostic"]["verify_js"]
    assert "评分证据链诊断生成中..." in by_id["btnScoringDiagnostic"]["verify_js"]
    assert by_id["btnMaterialDepthReport"]["expected_text"] == "资料深读体检（评分前）"
    assert by_id["btnMaterialDepthReportDownload"]["expected_filename_prefix"] == "material_depth_report_"
    assert by_id["btnMaterialDepthReportDownload"]["prepare_js"]
    assert by_id["btnMaterialKnowledgeProfile"]["expected_text"] == "资料知识画像（按维度覆盖）"
    assert (
        by_id["btnMaterialKnowledgeProfileDownload"]["expected_filename_prefix"]
        == "material_knowledge_profile_"
    )
    assert by_id["btnFeedbackGovernance"]["prepare_js"]
    assert by_id["btnEvalSummaryV2"]["result_id"] == "evalResult"
    assert by_id["btnEvalMetricsV2"]["expected_text"] == "项目指标评估完成"
    assert by_id["btnEvalMetricsV2"]["prepare_js"]
    assert by_id["btnEvidenceTraceDownload"]["prime_wait_selector"] == "#btnEvidenceTraceDownload"
    assert by_id["btnExportInstructions"]["kind"] == "js_check"
    assert by_id["btnSelectProjectBySearch"]["kind"] == "js_check"
    assert "projectSearchExpectedId" in by_id["btnSelectProjectBySearch"]["prepare_js"]
    assert "projectSearchRestoreId" in by_id["btnSelectProjectBySearch"]["prepare_js"]
    assert "已定位到项目" in by_id["btnSelectProjectBySearch"]["verify_js"]
    assert "window.__codexForceProjectContextForSmoke" in by_id["btnSelectProjectBySearch"]["verify_js"]
    assert by_id["refreshProjects"]["kind"] == "js_check"
    assert "window.__codexInstallFetchLogForSmoke()" in by_id["refreshProjects"]["prepare_js"]
    assert "refreshProjectsNeedle" in by_id["refreshProjects"]["verify_js"]
    assert by_id["btnRefreshMaterials"]["kind"] == "js_check"
    assert "window.__codexRevealForSmoke('#btnRefreshMaterials')" in by_id["btnRefreshMaterials"]["prepare_js"]
    assert "refreshMaterialsNeedle" in by_id["btnRefreshMaterials"]["verify_js"]
    assert by_id["btnRefreshSubmissions"]["kind"] == "js_check"
    assert "window.__codexRevealForSmoke('#btnRefreshSubmissions')" in by_id["btnRefreshSubmissions"]["prepare_js"]
    assert "refreshSubmissionsNeedle" in by_id["btnRefreshSubmissions"]["verify_js"]
    assert by_id["btnRefreshGroundTruth"]["result_id"] == "evolveResult"
    assert by_id["btnRefreshGroundTruth"]["expected_text"] == "刷新完成："
    assert by_id["btnRefreshGroundTruth"]["prepare_js"]
    assert len(ids) == len(set(ids))


def test_browser_button_smoke_write_matrix_covers_safe_write_paths() -> None:
    ids = [item["id"] for item in WRITE_BUTTON_SMOKE_MATRIX]
    by_id = {item["id"]: item for item in WRITE_BUTTON_SMOKE_MATRIX}
    assert "btnCreateProject" in ids
    assert "btnCreateProjectFromTender" in ids
    assert "btnScoreShigong" in ids
    assert "btnWeightsSave" in ids
    assert "btnUploadMaterials" in ids
    assert "btnUploadBoq" in ids
    assert "btnUploadDrawing" in ids
    assert "btnUploadSitePhotos" in ids
    assert "btnUploadShigong" in ids
    assert "btnWeightsApply" in ids
    assert "btnLearning" in ids
    assert "btnRebuildDelta" in ids
    assert "btnRebuildSamples" in ids
    assert "btnTrainCalibratorV2" in ids
    assert "btnApplyCalibPredict" in ids
    assert "btnAutoRunReflection" in ids
    assert "btnAdaptiveApply" in ids
    assert "btnMinePatchV2" in ids
    assert "btnShadowPatchV2" in ids
    assert "btnDeployPatchV2" in ids
    assert "btnRollbackPatchV2" in ids
    assert "deleteCurrentProject" in ids
    assert "deleteSelectedProjects" in ids
    assert "btnCleanupE2EProjects" in ids
    assert "btnRefreshGroundTruthSubmissionOptions" in ids
    assert "btnAddGroundTruth" in ids
    assert "btnEvolve" in ids
    assert by_id["btnScoreShigong"]["precondition_optional"] is True
    assert by_id["btnScoreShigong"]["timeout_ms"] == 60000
    assert by_id["btnWeightsApply"]["precondition_optional"] is True
    assert by_id["btnWeightsSave"]["requires_api_key"] is True
    assert by_id["btnUploadMaterials"]["result_id"] == "materialsActionStatus"
    assert by_id["btnUploadMaterials"]["expected_text"] == "上传完成：成功 1，失败 0"
    assert by_id["btnUploadMaterials"]["expected_output_text"] == "招标文件和答疑上传完成：成功 1，失败 0"
    assert by_id["btnUploadMaterials"]["file_input_selector"] == "#uploadMaterialFile"
    assert by_id["btnUploadMaterials"]["input_files"] == [
        str(Path('/Users/youfeini/Desktop/ZhiFei_BizSystem/tests/fixtures/browser_smoke_tender_qa.txt'))
    ]
    assert "window.__codexRevealForSmoke('#btnUploadMaterials')" in by_id["btnUploadMaterials"]["prepare_js"]
    assert by_id["btnUploadBoq"]["result_id"] == "materialsActionStatusBoq"
    assert by_id["btnUploadBoq"]["expected_text"] == "上传完成：成功 1，失败 0"
    assert by_id["btnUploadBoq"]["expected_output_text"] == "清单上传完成：成功 1，失败 0"
    assert by_id["btnUploadBoq"]["file_input_selector"] == "#uploadMaterialBoqFile"
    assert by_id["btnUploadBoq"]["input_files"] == [
        str(Path('/Users/youfeini/Desktop/ZhiFei_BizSystem/tests/fixtures/browser_smoke_boq.csv'))
    ]
    assert by_id["btnUploadDrawing"]["result_id"] == "materialsActionStatusDrawing"
    assert by_id["btnUploadDrawing"]["expected_text"] == "上传完成：成功 1，失败 0"
    assert by_id["btnUploadDrawing"]["expected_output_text"] == "图纸上传完成：成功 1，失败 0"
    assert by_id["btnUploadDrawing"]["file_input_selector"] == "#uploadMaterialDrawingFile"
    assert by_id["btnUploadDrawing"]["input_files"] == [
        str(Path('/Users/youfeini/Desktop/ZhiFei_BizSystem/tests/fixtures/browser_smoke_drawing.txt'))
    ]
    assert by_id["btnUploadSitePhotos"]["result_id"] == "materialsActionStatusPhoto"
    assert by_id["btnUploadSitePhotos"]["expected_text"] == "上传完成：成功 1，失败 0"
    assert by_id["btnUploadSitePhotos"]["expected_output_text"] == "现场照片上传完成：成功 1，失败 0"
    assert by_id["btnUploadSitePhotos"]["file_input_selector"] == "#uploadMaterialPhotoFile"
    assert by_id["btnUploadSitePhotos"]["input_files"] == [
        str(Path('/Users/youfeini/Desktop/ZhiFei_BizSystem/tests/fixtures/browser_smoke_site_photo.png'))
    ]
    assert by_id["btnUploadShigong"]["result_id"] == "shigongActionStatus"
    assert by_id["btnUploadShigong"]["expected_text"] == "施组上传完成：成功 1，失败 0"
    assert by_id["btnUploadShigong"]["file_input_selector"] == "#uploadShigongFile"
    assert by_id["btnUploadShigong"]["input_files"] == [
        str(Path('/Users/youfeini/Desktop/ZhiFei_BizSystem/sample_shigong.txt'))
    ]
    assert by_id["btnCreateProject"]["kind"] == "js_check"
    assert by_id["btnCreateProject"]["requires_api_key"] is True
    assert "window.__codexPrepareUiProjectCreateForSmoke" in by_id["btnCreateProject"]["prepare_js"]
    assert "uiCreatedProjectId" in by_id["btnCreateProject"]["verify_js"]
    assert "localStorage.getItem('selected_project_id')" in by_id["btnCreateProject"]["verify_js"]
    assert "JSON.parse" in by_id["btnCreateProject"]["verify_js"]
    assert "await fetch('/api/v1/projects?t=' + Date.now()" in by_id["btnCreateProject"]["verify_js"]
    assert by_id["btnCreateProjectFromTender"]["kind"] == "js_check"
    assert by_id["btnCreateProjectFromTender"]["requires_api_key"] is True
    assert by_id["btnCreateProjectFromTender"]["file_input_selector"] == "#createProjectFromTenderFile"
    assert by_id["btnCreateProjectFromTender"]["input_files"] == [
        str(Path('/Users/youfeini/Desktop/ZhiFei_BizSystem/tests/fixtures/browser_smoke_tender_qa.txt'))
    ]
    assert by_id["btnCreateProjectFromTender"]["timeout_ms"] == 60000
    assert (
        "window.__codexPrepareUiProjectCreateFromTenderForSmoke"
        in by_id["btnCreateProjectFromTender"]["prepare_js"]
    )
    assert "uiTenderCreatedProjectId" in by_id["btnCreateProjectFromTender"]["verify_js"]
    assert "localStorage.getItem('selected_project_id')" in by_id["btnCreateProjectFromTender"]["verify_js"]
    assert "await fetch('/api/v1/projects?t=' + Date.now()" in by_id["btnCreateProjectFromTender"]["verify_js"]
    assert by_id["btnLearning"]["result_id"] == "learningResult"
    assert by_id["btnLearning"]["expected_text"] == "学习画像已生成/更新"
    assert "window.__codexRevealForSmoke('#btnLearning')" in by_id["btnLearning"]["prepare_js"]
    assert by_id["btnRebuildDelta"]["result_id"] == "deltaResult"
    assert by_id["btnRebuildDelta"]["expected_text"] == "DELTA_CASE 重建完成"
    assert by_id["btnRebuildSamples"]["result_id"] == "sampleResult"
    assert by_id["btnRebuildSamples"]["expected_text"] == "FEATURE_ROW 重建完成"
    assert by_id["btnTrainCalibratorV2"]["result_id"] == "calibTrainResult"
    assert by_id["btnTrainCalibratorV2"]["timeout_ms"] == 60000
    assert by_id["btnTrainCalibratorV2"]["expected_text"] == "校准器训练完成"
    assert by_id["btnApplyCalibPredict"]["result_id"] == "calibTrainResult"
    assert by_id["btnApplyCalibPredict"]["expected_text"] == "校准分回填完成"
    assert by_id["btnAutoRunReflection"]["result_id"] == "calibTrainResult"
    assert by_id["btnAutoRunReflection"]["timeout_ms"] == 90000
    assert by_id["btnAutoRunReflection"]["expected_text"] == "一键闭环执行完成"
    assert by_id["btnAdaptiveApply"]["result_id"] == "adaptiveApplyResult"
    assert by_id["btnAdaptiveApply"]["expected_text"] == "已应用。变更:"
    assert by_id["btnAdaptiveApply"]["timeout_ms"] == 60000
    assert by_id["btnAdaptiveApply"]["rollback_key"] == "adaptive_apply"
    assert by_id["btnMinePatchV2"]["result_id"] == "patchResult"
    assert by_id["btnMinePatchV2"]["timeout_ms"] == 60000
    assert by_id["btnMinePatchV2"]["expected_text"] == "PATCH_PACKAGE 挖掘完成"
    assert "patchType" in by_id["btnMinePatchV2"]["prepare_js"]
    assert by_id["btnShadowPatchV2"]["result_id"] == "patchShadowResult"
    assert by_id["btnShadowPatchV2"]["expected_text"] == "补丁影子评估完成"
    assert by_id["btnDeployPatchV2"]["result_id"] == "patchDeployResult"
    assert by_id["btnDeployPatchV2"]["expected_text"] == "补丁已发布"
    assert by_id["btnRollbackPatchV2"]["result_id"] == "patchDeployResult"
    assert by_id["btnRollbackPatchV2"]["expected_text"] == "补丁已回滚"
    assert by_id["deleteCurrentProject"]["kind"] == "js_check"
    assert by_id["deleteCurrentProject"]["api_verify"] == "state_project_removed"
    assert by_id["deleteCurrentProject"]["api_verify_state_key"] == "uiDeleteTargetId"
    assert "uiCreatedProjectId" in by_id["deleteCurrentProject"]["precondition_js"]
    assert "document.createElement('option')" in by_id["deleteCurrentProject"]["prepare_js"]
    assert "localStorage.setItem('selected_project_id', createdId)" in by_id["deleteCurrentProject"]["prepare_js"]
    assert by_id["deleteSelectedProjects"]["kind"] == "js_check"
    assert by_id["deleteSelectedProjects"]["api_verify"] == "state_projects_removed"
    assert by_id["deleteSelectedProjects"]["api_verify_state_key"] == "batchDeleteProjectIds"
    assert by_id["deleteSelectedProjects"]["api_verify_timeout_ms"] == 30000
    assert by_id["deleteSelectedProjects"]["rollback_key"] == "batch_delete_projects"
    assert (
        "window.__codexPrepareBatchDeleteProjectsForSmoke"
        in by_id["deleteSelectedProjects"]["prepare_js"]
    )
    assert "verify_js" not in by_id["deleteSelectedProjects"]
    assert by_id["btnCleanupE2EProjects"]["kind"] == "js_check"
    assert by_id["btnCleanupE2EProjects"]["api_verify"] == "project_removed"
    assert by_id["btnCleanupE2EProjects"]["api_verify_timeout_ms"] == 30000
    assert "window.__codexFillGroundTruthDraftForSmoke" in by_id["btnAddGroundTruth"]["prepare_js"]
    assert "clearGroundTruthDraftForm" in by_id["btnEvolve"]["prepare_js"]
    assert "groundTruthSubmissionSelect" in by_id["btnEvolve"]["prepare_js"]
    assert "学习完成（基于" in by_id["btnEvolve"]["expected_text"]
    merged = build_step_matrix(include_write=True)
    assert len(merged) == len(BUTTON_SMOKE_MATRIX) + len(WRITE_BUTTON_SMOKE_MATRIX)


def test_browser_button_smoke_helpers_cover_confirm_and_direct_ground_truth_prefill() -> None:
    assert "confirms: []" in SMOKE_PAGE_HELPERS
    assert "window.confirm = (message) => {" in SMOKE_PAGE_HELPERS
    assert "window.__codexPrepareUiProjectCreateForSmoke" in SMOKE_PAGE_HELPERS
    assert "window.__codexPrepareUiProjectCreateFromTenderForSmoke" in SMOKE_PAGE_HELPERS
    assert "window.__codexPrepareBatchDeleteProjectsForSmoke" in SMOKE_PAGE_HELPERS
    assert "window.__codexActivateTrialPreflightFollowUpForSmoke" in SMOKE_PAGE_HELPERS
    assert "async function(prefix = 'E2E_UI_')" in SMOKE_PAGE_HELPERS
    assert "await window.startNewProjectIntake()" in SMOKE_PAGE_HELPERS
    assert "uiCreatedProjectName" in SMOKE_PAGE_HELPERS
    assert "uiCreatedProjectId" in SMOKE_PAGE_HELPERS
    assert "uiTenderCreatedProjectName" in SMOKE_PAGE_HELPERS
    assert "uiTenderCreatedProjectId" in SMOKE_PAGE_HELPERS
    assert "batchDeleteProjectIds" in SMOKE_PAGE_HELPERS
    assert "batchDeleteProjectNames" in SMOKE_PAGE_HELPERS
    assert "deleteSelectedProjects" in SMOKE_PAGE_HELPERS
    assert 'a[data-trial-preflight-entry="material_review"]' in SMOKE_PAGE_HELPERS
    assert "/submissions?t=" in SMOKE_PAGE_HELPERS
    assert "/ground_truth?t=" in SMOKE_PAGE_HELPERS
    assert "existingSubmissionIds" in SMOKE_PAGE_HELPERS
    assert "preferredSubmissionId" in SMOKE_PAGE_HELPERS


def test_browser_button_smoke_supports_file_input_steps() -> None:
    source = Path(
        "/Users/youfeini/Desktop/ZhiFei_BizSystem/scripts/browser_button_smoke.py"
    ).read_text(encoding="utf-8")
    assert "def install_input_files(spec: Dict[str, Any]) -> None:" in source
    assert 'input_selector = str(spec.get("file_input_selector") or "").strip()' in source
    assert "locator.set_input_files(normalized_files)" in source
    assert "install_input_files(spec)" in source
    assert 'str(ROOT_DIR / "tests" / "fixtures" / "browser_smoke_tender_qa.txt")' in source
    assert 'str(ROOT_DIR / "tests" / "fixtures" / "browser_smoke_site_photo.png")' in source


def test_browser_button_smoke_supports_adaptive_apply_rollback() -> None:
    source = Path(
        "/Users/youfeini/Desktop/ZhiFei_BizSystem/scripts/browser_button_smoke.py"
    ).read_text(encoding="utf-8")
    assert "LEXICON_PATH = ROOT_DIR / \"app\" / \"resources\" / \"lexicon.yaml\"" in source
    assert "RUBRIC_PATH = ROOT_DIR / \"app\" / \"resources\" / \"rubric.yaml\"" in source
    assert "def capture_adaptive_apply_snapshot() -> Dict[str, Any]:" in source
    assert "def restore_adaptive_apply_snapshot(snapshot: Dict[str, Any], *, base_url: str, api_key: str) -> None:" in source
    assert 'f"{base_url.rstrip(\'/\')}/api/v1/config/reload"' in source
    assert 'rollback_key == "adaptive_apply"' in source
    assert "rollback_error = apply_step_rollback(spec, rollback_state)" in source


def test_browser_button_smoke_supports_project_removed_api_verification() -> None:
    source = Path(
        "/Users/youfeini/Desktop/ZhiFei_BizSystem/scripts/browser_button_smoke.py"
    ).read_text(encoding="utf-8")
    assert "def project_exists_via_api(*, base_url: str, project_id: str, api_key: str) -> bool:" in source
    assert (
        "def wait_for_projects_removed(\n    *,\n    base_url: str,\n    project_ids: List[str],\n    api_key: str,\n    timeout_ms: int,\n) -> bool:"
        in source
    )
    assert "def wait_for_project_removed(*, base_url: str, project_id: str, api_key: str, timeout_ms: int) -> bool:" in source
    assert 'api_verify = str(spec.get("api_verify") or "").strip()' in source
    assert 'if api_verify == "project_removed":' in source
    assert 'raise RuntimeError("project_removed verification failed")' in source
    assert 'if api_verify == "state_project_removed":' in source
    assert 'raise RuntimeError("state_project_removed verification failed")' in source
    assert 'if api_verify == "state_projects_removed":' in source
    assert 'raise RuntimeError("state_projects_removed verification failed")' in source


def test_browser_button_smoke_supports_batch_delete_project_rollback() -> None:
    source = Path(
        "/Users/youfeini/Desktop/ZhiFei_BizSystem/scripts/browser_button_smoke.py"
    ).read_text(encoding="utf-8")
    assert 'if rollback_key == "batch_delete_projects":' in source
    assert 'state.batchDeleteProjectIds = [];' in source
    assert 'f"{base_url.rstrip(\'/\')}/api/v1/projects/{normalized}"' in source


def test_browser_button_smoke_supports_config_integrity_guard() -> None:
    source = Path(
        "/Users/youfeini/Desktop/ZhiFei_BizSystem/scripts/browser_button_smoke.py"
    ).read_text(encoding="utf-8")
    assert "def file_sha256(path: Path) -> str:" in source
    assert "def capture_config_integrity_snapshot() -> Dict[str, Any]:" in source
    assert "def verify_config_integrity_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:" in source
    assert 'report["config_integrity"] = verify_config_integrity_snapshot(config_integrity_snapshot)' in source
    assert "and bool((report.get(\"config_integrity\") or {}).get(\"ok\"))" in source
    assert "## Config Integrity" in source


def test_config_integrity_snapshot_matches_current_resources() -> None:
    snapshot = capture_config_integrity_snapshot()
    verification = verify_config_integrity_snapshot(snapshot)
    assert verification["ok"] is True
    assert verification["files_ok"] is True
    assert verification["backups_ok"] is True
    assert verification["current_files"]
    assert verification["expected_files"]
    lexicon = Path("/Users/youfeini/Desktop/ZhiFei_BizSystem/app/resources/lexicon.yaml")
    rubric = Path("/Users/youfeini/Desktop/ZhiFei_BizSystem/app/resources/rubric.yaml")
    assert snapshot["files"][str(lexicon.resolve())] == file_sha256(lexicon)
    assert snapshot["files"][str(rubric.resolve())] == file_sha256(rubric)


def test_resolve_project_id_prefers_explicit_value(tmp_path: Path) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"project_id": "from-summary"}), encoding="utf-8")
    assert resolve_project_id("explicit-id", summary) == "explicit-id"
    assert resolve_project_id("", summary) == "from-summary"
    assert resolve_project_id("", tmp_path / "missing.json") == ""


def test_resolve_project_context_and_write_smoke_rules(tmp_path: Path) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps({"project_id": "from-summary", "project_name": "E2E_demo"}),
        encoding="utf-8",
    )
    context = resolve_project_context("", summary)
    assert context["project_id"] == "from-summary"
    assert context["project_name"] == "E2E_demo"
    assert write_smoke_enabled("E2E_demo") is True
    assert write_smoke_enabled("项目1") is False


def test_build_report_markdown_includes_download_and_failure_details() -> None:
    report = {
        "ok": False,
        "base_url": "http://127.0.0.1:8000",
        "project_id": "p-demo",
        "project_name": "E2E_demo",
        "write_smoke_enabled": True,
        "screenshot_path": "/tmp/final.png",
        "console_log_path": "/tmp/console.log",
        "steps": [
            {
                "id": "btnScoringFactors",
                "label": "评分体系一览",
                "selector": "#btnScoringFactors",
                "result_id": "scoringFactorsResult",
                "ok": True,
                "observed_text": "评分体系总览已加载",
            },
            {
                "id": "btnWritingGuidanceDownload",
                "label": "下载编制指导(.md)",
                "selector": "#btnWritingGuidanceDownload",
                "ok": False,
                "downloaded_filename": "writing_guidance_p-demo.md",
                "saved_path": "/tmp/download.md",
                "error": "unexpected filename",
            },
        ],
        "page_errors": ["ReferenceError: boom"],
        "console_errors": ["ERROR: failed to render"],
    }
    markdown = build_report_markdown(report)
    assert "# Browser Button Smoke" in markdown
    assert "评分体系一览" in markdown
    assert "下载编制指导(.md)" in markdown
    assert "writing_guidance_p-demo.md" in markdown
    assert "ReferenceError: boom" in markdown
    assert "ERROR: failed to render" in markdown
    assert "Write Smoke Enabled" in markdown


def test_browser_button_smoke_matrix_tracks_download_and_js_check_steps() -> None:
    by_id = {item["id"]: item for item in BUTTON_SMOKE_MATRIX}
    assert by_id["btnEvidenceTraceDownload"]["kind"] == "download"
    assert by_id["btnEvidenceTraceDownload"]["expected_filename_prefix"] == "evidence_trace_"
    assert by_id["btnTrialPreflightDownload"]["expected_filename_suffix"] == ".md"
    assert by_id["btnTrialPreflightDownloadDocx"]["expected_filename_suffix"] == ".docx"
    assert "clipboardWrites.length > 0" in by_id["btnExportInstructions"]["verify_js"]


def test_browser_launch_candidates_include_requested_and_fallbacks() -> None:
    candidates = browser_launch_candidates("chrome")
    labels = [item["label"] for item in candidates]
    assert labels[0] == "chrome"
    assert "chromium-default" in labels
    if Path(SYSTEM_CHROME_EXECUTABLE).exists():
        assert "chrome-executable" in labels
    default_labels = [item["label"] for item in browser_launch_candidates("")]
    assert default_labels[0] == "chromium-default"


def test_resolve_admin_api_key_returns_string() -> None:
    assert isinstance(resolve_admin_api_key(), str)
