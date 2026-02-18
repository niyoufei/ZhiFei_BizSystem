from __future__ import annotations

import html as html_lib
import io
import json
import os
import shutil
import tempfile
import unicodedata
from datetime import datetime, timezone

# 加载 .env，使 SPARK_APIPASSWORD、OPENAI_API_KEY 等生效
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus
from uuid import uuid4

try:
    import pymupdf
except Exception:
    pymupdf = None
try:
    from docx import Document
except Exception:
    Document = None
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import get_auth_status, verify_api_key
from app.cache import (
    cache_score_result,
    clear_score_cache,
    get_cache_stats,
    get_cached_score,
)
from app.config import get_config_status, load_config, reload_config
from app.engine.adaptive import (
    apply_adaptive_patch,
    apply_rubric_patch,
    build_adaptive_patch,
    build_adaptive_suggestions,
)
from app.engine.anchors import (
    build_project_requirements_from_anchors,
    extract_project_anchors_from_text,
)
from app.engine.calibrator import (
    build_feature_row,
    calc_metrics,
    cross_validate_calibrator,
    predict_with_model,
    train_best_calibrator_auto,
    train_isotonic1d_calibrator,
    train_linear1d_calibrator,
    train_offset_calibrator,
    train_ridge_calibrator,
)
from app.engine.compare import build_compare_narrative
from app.engine.dimensions import DIMENSIONS
from app.engine.evaluation import evaluate_project_variants
from app.engine.evolution import build_evolution_report
from app.engine.history import (
    analyze_trend,
    get_history,
)
from app.engine.history import (
    record_score as record_history_score,
)
from app.engine.insights import build_project_insights
from app.engine.learning import build_learning_profile
from app.engine.llm_evolution import enhance_evolution_report_with_llm, get_llm_backend_status
from app.engine.reflection import (
    build_calibration_samples,
    build_delta_cases,
    evaluate_patch_shadow,
    mine_patch_package,
)
from app.engine.scorer import score_text
from app.engine.v2_scorer import compute_v2_rule_total, score_text_v2
from app.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, t
from app.metrics import get_metrics, record_score, update_project_stats
from app.rate_limit import get_rate_limit_status, setup_rate_limiting
from app.schemas import (
    RESPONSES_401,
    RESPONSES_404,
    RESPONSES_409,
    RESPONSES_422,
    RESPONSES_NO_PROFILE,
    RESPONSES_NO_SUBMISSIONS,
    AdaptiveApplyResult,
    AdaptivePatch,
    AdaptiveSuggestions,
    AdaptiveValidation,
    AnalysisBundleResponse,
    CacheClearResponse,
    CacheStatsResponse,
    CalibrationSampleRecord,
    CalibratorDeployRequest,
    CalibratorModelRecord,
    CalibratorPredictResponse,
    CalibratorTrainRequest,
    CompareNarrative,
    CompareReport,
    CompilationInstructions,
    ConfigReloadResponse,
    ConfigStatusResponse,
    ConstraintPack,
    DeltaCaseRecord,
    EvaluationSummaryResponse,
    EvolutionReport,
    ExpertProfileRecord,
    ExpertProfileUpdate,
    GroundTruthBatchResponse,
    GroundTruthCreate,
    GroundTruthFromSubmissionCreate,
    GroundTruthRecord,
    HealthResponse,
    InsightsReport,
    LatestReportResponse,
    LearningProfile,
    LLMBackendStatus,
    MaterialRecord,
    PatchDeploymentRecord,
    PatchDeployRequest,
    PatchMineRequest,
    PatchPackageRecord,
    PatchShadowEvalResponse,
    ProjectAnchorRecord,
    ProjectContextIn,
    ProjectContextOut,
    ProjectCreate,
    ProjectEvaluationResponse,
    ProjectExpertProfileResponse,
    ProjectPreScoreListResponse,
    ProjectRecord,
    ProjectRequirementRecord,
    ProjectScoreHistory,
    QingTianResultCreate,
    QingTianResultRecord,
    ReadyResponse,
    ReflectionAutoRunResponse,
    RescoreRequest,
    RescoreResponse,
    ScoreReport,
    ScoreRequest,
    ScoringFactorsMarkdownResponse,
    ScoringFactorsResponse,
    SelfCheckResponse,
    SubmissionRecord,
    TrendAnalysis,
    WritingGuidance,
)
from app.storage import (
    MATERIALS_DIR,
    ensure_data_dirs,
    load_calibration_models,
    load_calibration_samples,
    load_delta_cases,
    load_evidence_units,
    load_evolution_reports,
    load_expert_profiles,
    load_ground_truth,
    load_learning_profiles,
    load_materials,
    load_patch_deployments,
    load_patch_packages,
    load_project_anchors,
    load_project_context,
    load_project_requirements,
    load_projects,
    load_qingtian_results,
    load_score_history,
    load_score_reports,
    load_submissions,
    save_calibration_models,
    save_calibration_samples,
    save_delta_cases,
    save_evidence_units,
    save_evolution_reports,
    save_expert_profiles,
    save_ground_truth,
    save_learning_profiles,
    save_materials,
    save_patch_deployments,
    save_patch_packages,
    save_project_anchors,
    save_project_context,
    save_project_requirements,
    save_projects,
    save_qingtian_results,
    save_score_history,
    save_score_reports,
    save_submissions,
)

# OpenAPI 标签定义
OPENAPI_TAGS = [
    {
        "name": "健康检查",
        "description": "容器化部署健康检查端点（Kubernetes liveness/readiness probes）",
    },
    {
        "name": "监控指标",
        "description": "Prometheus 格式的运行时指标导出",
    },
    {
        "name": "系统状态",
        "description": "系统认证与限流状态查询",
    },
    {
        "name": "评分",
        "description": "施工组织设计文档评分核心功能",
    },
    {
        "name": "项目管理",
        "description": "项目创建、列表、材料上传等项目全生命周期管理",
    },
    {
        "name": "施组提交",
        "description": "施工组织设计文档上传与评分记录",
    },
    {
        "name": "对比分析",
        "description": "多次提交的横向对比与统计分析",
    },
    {
        "name": "自适应优化",
        "description": "基于历史数据的评分规则自适应调整",
    },
    {
        "name": "洞察与学习",
        "description": "项目洞察报告与学习画像生成",
    },
    {
        "name": "历史与趋势",
        "description": "评分历史记录查询与趋势分析",
    },
]

DIMENSION_IDS = sorted(DIMENSIONS.keys())
DEFAULT_REGION = "合肥"
DEFAULT_QINGTIAN_MODEL_VERSION = "qingtian-2026.02"
DEFAULT_SCORING_ENGINE_LOCKED = "v2.0.0"
DEFAULT_CALIBRATOR_LOCKED = None
DEFAULT_RULE_SCORE_WEIGHT = 0.7
DEFAULT_LLM_SCORE_WEIGHT = 0.3
DEFAULT_LLM_DELTA_CAP = 35.0
DEFAULT_SCORE_SCALE_MAX = 100
DEFAULT_NORM_RULE_VERSION = "v1_m=0.5+a/10_norm=sum"
PROFILE_LOCKED_STATUSES = {"submitted_to_qingtian"}
DEFAULT_CHAPTER_REQUIREMENTS = {
    "required_sections": [
        "重难点及危大工程（对应维度07）",
        "进度保障措施（对应维度09）",
        "安全生产与文明施工（对应维度02/03）",
        "质量保障体系（对应维度08）",
    ],
    "required_charts_images": [
        "进度计划或横道图",
        "危大工程或重难点分析示意图（如适用）",
        "组织架构或责任分工表",
    ],
    "mandatory_elements": [
        "控制参数或阈值",
        "执行频次（如日报/周检）",
        "责任岗位或责任人",
        "验收或检查动作（报验/旁站/签认等）",
    ],
    "forbidden_patterns": [
        "仅使用「保证」「严格落实」「确保」等空泛承诺而无量化或动作",
        "措施类描述缺少参数/频次/责任/验收中至少两类",
    ],
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_weights_raw() -> Dict[str, int]:
    return {dim_id: 5 for dim_id in DIMENSION_IDS}


def _normalize_weights(weights_raw: Dict[str, int]) -> Dict[str, float]:
    multipliers = {
        dim_id: 0.5 + (int(weights_raw.get(dim_id, 5)) / 10.0) for dim_id in DIMENSION_IDS
    }
    total = sum(multipliers.values()) or 1.0
    return {dim_id: multipliers[dim_id] / total for dim_id in DIMENSION_IDS}


def _coerce_weights_raw(weights_raw: Dict[str, int]) -> Dict[str, int]:
    merged = dict(_default_weights_raw())
    for dim_id in DIMENSION_IDS:
        raw_value = weights_raw.get(dim_id, merged[dim_id])
        try:
            value = int(raw_value)
        except Exception:
            raise HTTPException(status_code=422, detail=f"维度 {dim_id} 的关注度必须是整数(0..10)")
        if value < 0 or value > 10:
            raise HTTPException(status_code=422, detail=f"维度 {dim_id} 的关注度超出范围(0..10)")
        merged[dim_id] = value
    return merged


def _new_expert_profile(name: str, weights_raw: Dict[str, int]) -> Dict[str, object]:
    now = _now_iso()
    normalized = _normalize_weights(weights_raw)
    return {
        "id": str(uuid4()),
        "name": name,
        "weights_raw": weights_raw,
        "weights_norm": normalized,
        "norm_rule_version": DEFAULT_NORM_RULE_VERSION,
        "created_at": now,
        "updated_at": now,
    }


def _ensure_project_v2_fields(
    project: Dict[str, object], *, include_engine_defaults: bool = True
) -> bool:
    changed = False
    if not project.get("region"):
        project["region"] = DEFAULT_REGION
        changed = True
    if not project.get("qingtian_model_version"):
        project["qingtian_model_version"] = DEFAULT_QINGTIAN_MODEL_VERSION
        changed = True
    if include_engine_defaults and not project.get("scoring_engine_version_locked"):
        project["scoring_engine_version_locked"] = DEFAULT_SCORING_ENGINE_LOCKED
        changed = True
    # 校准器锁定版本默认留空，避免误套历史/跨项目模型。
    if include_engine_defaults and "calibrator_version_locked" not in project:
        project["calibrator_version_locked"] = DEFAULT_CALIBRATOR_LOCKED
        changed = True
    if not project.get("status"):
        project["status"] = "scoring_preparation"
        changed = True
    if not project.get("updated_at"):
        project["updated_at"] = project.get("created_at") or _now_iso()
        changed = True
    meta = project.get("meta")
    if not isinstance(meta, dict):
        project["meta"] = {}
        meta = project["meta"]
        changed = True
    if "score_scale_max" not in meta:
        meta["score_scale_max"] = DEFAULT_SCORE_SCALE_MAX
        changed = True
    return changed


def _assert_project_profile_operation_unlocked(
    project: Dict[str, object], force_unlock: bool
) -> None:
    status = str(project.get("status") or "").strip()
    if status in PROFILE_LOCKED_STATUSES and not force_unlock:
        raise HTTPException(
            status_code=409,
            detail="项目已进入青天评标阶段，默认锁定专家配置与重算。请确认后使用 force_unlock=true 重试。",
        )


def _ensure_project_expert_profile(
    project: Dict[str, object],
    all_profiles: List[Dict[str, object]],
) -> tuple[Dict[str, object], bool]:
    profile_id = str(project.get("expert_profile_id") or "")
    if profile_id:
        for profile in all_profiles:
            if profile.get("id") == profile_id:
                return profile, False

    profile_name = f"{project.get('name', '项目')} 默认配置"
    created = _new_expert_profile(profile_name, _default_weights_raw())
    all_profiles.append(created)
    project["expert_profile_id"] = created["id"]
    project["updated_at"] = _now_iso()
    return created, True


def _recover_missing_project_from_artifacts(
    project_id: str, projects: List[Dict[str, object]]
) -> Optional[Dict[str, object]]:
    pid = str(project_id or "").strip()
    if not pid:
        return None
    for p in projects:
        if str(p.get("id") or "") == pid:
            return p

    submissions = [s for s in load_submissions() if str(s.get("project_id") or "") == pid]
    materials = [m for m in load_materials() if str(m.get("project_id") or "") == pid]
    ground_truth = [g for g in load_ground_truth() if str(g.get("project_id") or "") == pid]
    evo_reports = load_evolution_reports()
    has_evolution = pid in evo_reports

    if not submissions and not materials and not ground_truth and not has_evolution:
        return None

    name_seed = ""
    for row in materials + submissions:
        filename = str(row.get("filename") or "").strip()
        if filename:
            name_seed = filename
            break
    if name_seed:
        stem = name_seed.rsplit(".", 1)[0].strip()
        recovered_name = (stem or name_seed) + "（恢复）"
    else:
        recovered_name = f"恢复项目_{pid[:8]}"

    time_points: List[str] = []
    for row in submissions:
        created_at = str(row.get("created_at") or "").strip()
        updated_at = str(row.get("updated_at") or "").strip()
        if created_at:
            time_points.append(created_at)
        if updated_at:
            time_points.append(updated_at)
    for row in materials + ground_truth:
        created_at = str(row.get("created_at") or "").strip()
        if created_at:
            time_points.append(created_at)
    evo_updated_at = str((evo_reports.get(pid) or {}).get("updated_at") or "").strip()
    if evo_updated_at:
        time_points.append(evo_updated_at)
    created_at = min(time_points) if time_points else _now_iso()
    updated_at = max(time_points) if time_points else _now_iso()

    score_scale_max = DEFAULT_SCORE_SCALE_MAX
    for s in submissions:
        report = s.get("report")
        if not isinstance(report, dict):
            continue
        meta = report.get("meta")
        if not isinstance(meta, dict):
            continue
        raw = meta.get("score_scale_max")
        if str(raw) == "5":
            score_scale_max = 5
            break
        if str(raw) == "100":
            score_scale_max = 100

    recovered = {
        "id": pid,
        "name": recovered_name,
        "meta": {"score_scale_max": score_scale_max},
        "region": DEFAULT_REGION,
        "expert_profile_id": None,
        "qingtian_model_version": DEFAULT_QINGTIAN_MODEL_VERSION,
        "scoring_engine_version_locked": DEFAULT_SCORING_ENGINE_LOCKED,
        "calibrator_version_locked": DEFAULT_CALIBRATOR_LOCKED,
        "status": "scoring_preparation",
        "created_at": created_at,
        "updated_at": updated_at,
    }
    _ensure_project_v2_fields(recovered)
    projects.append(recovered)
    save_projects(projects)
    return recovered


def _recover_latest_orphan_project(
    projects: List[Dict[str, object]],
) -> Optional[Dict[str, object]]:
    existing_ids = {str(p.get("id") or "") for p in projects}
    latest_pid = ""
    latest_at = ""

    for row in load_submissions():
        pid = str(row.get("project_id") or "").strip()
        if not pid or pid in existing_ids:
            continue
        ts = str(row.get("updated_at") or row.get("created_at") or "").strip()
        if ts and ts > latest_at:
            latest_at = ts
            latest_pid = pid
    for row in load_materials():
        pid = str(row.get("project_id") or "").strip()
        if not pid or pid in existing_ids:
            continue
        ts = str(row.get("created_at") or "").strip()
        if ts and ts > latest_at:
            latest_at = ts
            latest_pid = pid
    if not latest_pid:
        return None
    return _recover_missing_project_from_artifacts(latest_pid, projects)


def _find_project(project_id: str, projects: List[Dict[str, object]]) -> Dict[str, object]:
    for p in projects:
        if p.get("id") == project_id:
            return p
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        recovered = _recover_missing_project_from_artifacts(project_id, projects)
        if recovered is not None:
            return recovered
    raise HTTPException(status_code=404, detail="项目不存在")


def _find_submission(submission_id: str, submissions: List[Dict[str, object]]) -> Dict[str, object]:
    for s in submissions:
        if str(s.get("id")) == submission_id:
            return s
    raise HTTPException(status_code=404, detail="施组提交记录不存在")


def _weights_norm_to_dimension_multipliers(weights_norm: Dict[str, float]) -> Dict[str, float]:
    baseline = 1.0 / len(DIMENSION_IDS)
    if baseline <= 0:
        return {}
    return {
        dim_id: float(weights_norm.get(dim_id, baseline)) / baseline for dim_id in DIMENSION_IDS
    }


def _resolve_project_scoring_context(
    project_id: str,
) -> tuple[Dict[str, float], Optional[Dict[str, object]], Dict[str, object]]:
    projects = load_projects()
    project = _find_project(project_id, projects)
    _ensure_project_v2_fields(project, include_engine_defaults=False)

    profiles = load_expert_profiles()
    profile = None
    if project.get("expert_profile_id"):
        for item in profiles:
            if item.get("id") == project.get("expert_profile_id"):
                profile = item
                break

    # 进化权重优先：用户执行学习进化后，使预评分贴近青天，优先采用进化产出的 dimension_multipliers
    reports = load_evolution_reports()
    evo = reports.get(project_id) or {}
    se = evo.get("scoring_evolution") or {}
    mult = se.get("dimension_multipliers") or {}
    if mult:
        return dict(mult), None, project

    if profile and isinstance(profile.get("weights_norm"), dict):
        multipliers = _weights_norm_to_dimension_multipliers(profile.get("weights_norm", {}))
        return multipliers, profile, project

    for p in load_learning_profiles():
        if p.get("project_id") == project_id:
            return dict(p.get("dimension_multipliers") or {}), None, project
    return {}, None, project


def _get_rubric_dim_cfg(rubric_dimensions: Dict[str, object], dim_id: str) -> Dict[str, object]:
    for k, v in (rubric_dimensions or {}).items():
        if _normalize_dimension_id(str(k)) == dim_id and isinstance(v, dict):
            return v
    return {}


def _build_scoring_factors_overview(project_id: Optional[str]) -> Dict[str, object]:
    config = load_config()
    rubric = config.rubric if isinstance(config.rubric, dict) else {}
    rubric_dims = rubric.get("dimensions") if isinstance(rubric.get("dimensions"), dict) else {}
    rubric_penalties = rubric.get("penalties") if isinstance(rubric.get("penalties"), dict) else {}

    dimensions: List[Dict[str, object]] = []
    for dim_id in DIMENSION_IDS:
        dim_meta = DIMENSIONS.get(dim_id, {})
        cfg = _get_rubric_dim_cfg(rubric_dims, dim_id)
        sub_items_raw = cfg.get("sub_items") if isinstance(cfg.get("sub_items"), list) else []
        sub_items = []
        for item in sub_items_raw:
            if not isinstance(item, dict):
                continue
            sub_items.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "weight": float(item.get("weight", 0)),
                    "keywords_count": len(item.get("keywords") or []),
                    "regex_count": len(item.get("regex") or []),
                }
            )

        if dim_id in {"07", "09"}:
            model_desc = "5子项累计评分（每项通常2分，合计10分）"
        else:
            model_desc = "Coverage/Closure/Landing/Specificity 四项各2.5分，合计10分"

        dimensions.append(
            {
                "dimension_id": dim_id,
                "name": dim_meta.get("name", f"维度{dim_id}"),
                "module": dim_meta.get("module", ""),
                "max_score": float(cfg.get("max_score", 10)),
                "suggestion_threshold": float(cfg.get("suggestion_threshold", 6)),
                "suggested_gain": float(cfg.get("suggested_gain", 0)),
                "scoring_model": model_desc,
                "sub_items": sub_items,
            }
        )

    penalty_rules: List[Dict[str, object]] = []
    empty_cfg = (
        rubric_penalties.get("empty_promises")
        if isinstance(rubric_penalties.get("empty_promises"), dict)
        else {}
    )
    action_cfg = (
        rubric_penalties.get("action_missing")
        if isinstance(rubric_penalties.get("action_missing"), dict)
        else {}
    )
    penalty_rules.extend(
        [
            {
                "code": "P-EMPTY-001",
                "engine": "v1",
                "description": "空泛承诺扣分",
                "deduct_per_hit": float(empty_cfg.get("deduct", 0.5)),
                "max_deduct": float(empty_cfg.get("max_deduct", 3.0)),
            },
            {
                "code": "P-ACTION-001",
                "engine": "v1",
                "description": "措施缺动作要素扣分",
                "deduct_per_hit": float(action_cfg.get("deduct_per", 0.5)),
                "max_deduct": float(action_cfg.get("max_deduct", 5.0)),
            },
            {
                "code": "P-EMPTY-002",
                "engine": "v2",
                "description": "空泛承诺未绑定证据（缺参数/频次/责任/验收）",
                "deduct_per_hit": 0.5,
                "max_deduct": 3.0,
            },
            {
                "code": "P-ACTION-002",
                "engine": "v2",
                "description": "措施缺硬要素（至少需要责任岗位+验收动作）",
                "deduct_per_hit": 0.8,
                "max_deduct": 6.0,
            },
            {
                "code": "P-CONSIST-001",
                "engine": "v2",
                "description": "与锚点不一致（如工期冲突）",
                "deduct_per_hit": 2.0,
                "max_deduct": 6.0,
            },
        ]
    )

    chapter_requirements = dict(DEFAULT_CHAPTER_REQUIREMENTS)
    chapter_source = "default"
    if project_id:
        evo = load_evolution_reports().get(project_id) or {}
        ci = (
            evo.get("compilation_instructions")
            if isinstance(evo.get("compilation_instructions"), dict)
            else {}
        )
        if ci:
            chapter_requirements = {
                "required_sections": [str(x) for x in (ci.get("required_sections") or [])],
                "required_charts_images": [
                    str(x) for x in (ci.get("required_charts_images") or [])
                ],
                "mandatory_elements": [str(x) for x in (ci.get("mandatory_elements") or [])],
                "forbidden_patterns": [str(x) for x in (ci.get("forbidden_patterns") or [])],
            }
            chapter_source = "project_evolution"

    if project_id:
        anchors = [a for a in load_project_anchors() if str(a.get("project_id")) == project_id]
        consistency_anchors = sorted(
            {str(a.get("anchor_key")) for a in anchors if a.get("anchor_key")}
        )
    else:
        consistency_anchors = []
    if not consistency_anchors:
        consistency_anchors = [
            "contract_duration_days",
            "quality_standard",
            "dangerous_works_list",
            "key_milestones",
        ]

    req_sections = chapter_requirements.get("required_sections", [])
    req_charts = chapter_requirements.get("required_charts_images", [])
    req_elements = chapter_requirements.get("mandatory_elements", [])
    organization_markers = req_sections + req_charts
    capability_flags = {
        "organization_structure_required": any(
            ("组织架构" in s or "责任分工" in s) for s in organization_markers
        ),
        "chapter_content_completeness_required": bool(req_sections),
        "key_difficult_points_required": any(("重难点" in s or "危大" in s) for s in req_sections),
        "solutions_required": bool(req_elements),
        "graphic_content_required": bool(req_charts),
        "consistency_checks_enabled": True,
        "lint_checks_enabled": True,
    }

    return {
        "engine_version": "v2",
        "project_id": project_id,
        "dimension_count": len(DIMENSION_IDS),
        "dimensions": dimensions,
        "penalty_rules": penalty_rules,
        "lint_issue_codes": [
            "MissingRequirement",
            "AnchorMissing",
            "AnchorMismatch",
            "EmptyPromiseWithoutEvidence",
            "ActionMissingHardElements",
            "ClosureGap",
            "ConsistencyConflict",
        ],
        "consistency_anchors": consistency_anchors,
        "chapter_requirements": chapter_requirements,
        "capability_flags": capability_flags,
        "source": {
            "rubric_version": str(rubric.get("version", "unknown")),
            "chapter_requirements": chapter_source,
        },
        "updated_at": _now_iso(),
    }


def _render_scoring_factors_markdown(payload: Dict[str, object]) -> str:
    dims = payload.get("dimensions") or []
    penalties = payload.get("penalty_rules") or []
    chapter = payload.get("chapter_requirements") or {}
    flags = payload.get("capability_flags") or {}
    lines = [
        "# 评分体系总览",
        "",
        f"- 引擎版本：`{payload.get('engine_version', 'v2')}`",
        f"- 项目ID：`{payload.get('project_id') or '-'} `",
        f"- 维度数：`{payload.get('dimension_count', 0)}`",
        f"- 规则版本：`{(payload.get('source') or {}).get('rubric_version', '-')}`",
        "",
        "## 维度评分因子",
        "",
        "| 维度 | 名称 | 模块 | 满分 | 评分模型 |",
        "|---|---|---|---:|---|",
    ]
    for d in dims:
        lines.append(
            f"| {d.get('dimension_id')} | {d.get('name','')} | {d.get('module','')} | "
            f"{d.get('max_score', 10)} | {d.get('scoring_model','')} |"
        )
    lines.extend(
        [
            "",
            "## 扣分规则",
            "",
            "| 代码 | 引擎 | 单次扣分 | 最大扣分 | 说明 |",
            "|---|---|---:|---:|---|",
        ]
    )
    for p in penalties:
        lines.append(
            f"| {p.get('code')} | {p.get('engine')} | {p.get('deduct_per_hit')} | "
            f"{p.get('max_deduct')} | {p.get('description','')} |"
        )

    lines.extend(
        [
            "",
            "## 章节与编制要求",
            "",
            "### 必备章节",
        ]
    )
    for x in chapter.get("required_sections") or []:
        lines.append(f"- {x}")
    lines.append("")
    lines.append("### 必备图表/图片")
    for x in chapter.get("required_charts_images") or []:
        lines.append(f"- {x}")
    lines.append("")
    lines.append("### 必备要素")
    for x in chapter.get("mandatory_elements") or []:
        lines.append(f"- {x}")
    lines.append("")
    lines.append("### 禁止表述")
    for x in chapter.get("forbidden_patterns") or []:
        lines.append(f"- {x}")

    lines.extend(
        [
            "",
            "## 能力覆盖标识",
            "",
            f"- 组织机构要求：`{bool(flags.get('organization_structure_required'))}`",
            f"- 章节完整性要求：`{bool(flags.get('chapter_content_completeness_required'))}`",
            f"- 重难点要求：`{bool(flags.get('key_difficult_points_required'))}`",
            f"- 解决方案要素要求：`{bool(flags.get('solutions_required'))}`",
            f"- 图文要求：`{bool(flags.get('graphic_content_required'))}`",
            f"- 一致性校验启用：`{bool(flags.get('consistency_checks_enabled'))}`",
            f"- Lint校验启用：`{bool(flags.get('lint_checks_enabled'))}`",
        ]
    )
    return "\n".join(lines).strip()


def _render_project_analysis_bundle_markdown(
    *,
    project: Dict[str, object],
    factors_payload: Dict[str, object],
    evaluation_payload: Dict[str, object],
) -> str:
    factors_md = _render_scoring_factors_markdown(factors_payload)
    variants = evaluation_payload.get("variants") or {}
    acceptance = evaluation_payload.get("acceptance") or {}
    project_name = str(project.get("name") or project.get("id") or "")

    lines = [
        f"# 项目分析包：{project_name}",
        "",
        f"- 项目ID：`{project.get('id')}`",
        f"- 生成时间：`{_now_iso()}`",
        f"- 青天样本数：`{evaluation_payload.get('sample_count_qt', 0)}`",
        "",
        "## 验收指标（V1 / V2 / V2+Calib）",
        "",
        "| 版本 | 样本数 | MAE | RMSE | Spearman | 画像相似度 | 扣分命中率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, name in (("v1", "V1"), ("v2", "V2"), ("v2_calib", "V2+Calib")):
        v = variants.get(key) or {}
        lines.append(
            f"| {name} | {v.get('sample_count', 0)} | {v.get('mae', 0)} | {v.get('rmse', 0)} | "
            f"{v.get('spearman', 0)} | {v.get('profile_similarity', 0)} | {v.get('penalty_hit_rate', 0)} |"
        )
    lines.extend(
        [
            "",
            "### 验收结论",
            "",
            f"- MAE/RMSE 优于 V1：`{bool(acceptance.get('mae_rmse_improved_vs_v1'))}`",
            f"- 排序相关性不劣于 V1：`{bool(acceptance.get('rank_corr_not_worse_vs_v1'))}`",
            f"- 画像相似度优于 V1：`{bool(acceptance.get('profile_similarity_improved_vs_v1'))}`",
            f"- 扣分命中率优于 V1：`{bool(acceptance.get('penalty_hit_rate_improved_vs_v1'))}`",
            "",
            "## 评分体系（当前生效）",
            "",
            factors_md,
        ]
    )
    return "\n".join(lines).strip()


def _build_score_report_snapshot(
    submission_id: str,
    project: Dict[str, object],
    report: Dict[str, object],
    profile_snapshot: Optional[Dict[str, object]],
    scoring_engine_version: str,
) -> Dict[str, object]:
    rule_dim_scores = report.get("rule_dim_scores")
    if not isinstance(rule_dim_scores, dict):
        rule_dim_scores = report.get("dimension_scores", {})
    return {
        "id": str(uuid4()),
        "submission_id": submission_id,
        "project_id": project.get("id"),
        "scoring_engine_version": scoring_engine_version,
        "expert_profile_snapshot": profile_snapshot or {},
        "rule_dim_scores": rule_dim_scores,
        "rule_total_score": float(report.get("rule_total_score", report.get("total_score", 0.0))),
        "dim_total_80": float(report.get("dim_total_80", 0.0)),
        "dim_total_90": float(report.get("dim_total_90", 0.0)),
        "consistency_bonus": float(report.get("consistency_bonus", 0.0)),
        "pred_dim_scores": report.get("pred_dim_scores"),
        "pred_total_score": report.get("pred_total_score"),
        "llm_total_score": report.get("llm_total_score"),
        "pred_confidence": report.get("pred_confidence"),
        "score_blend": report.get("score_blend"),
        "penalties": report.get("penalties", []),
        "lint_findings": report.get("lint_findings", []),
        "suggestions": report.get("suggestions", []),
        "created_at": _now_iso(),
    }


def _normalize_dimension_id(dim_id: str) -> str:
    value = str(dim_id or "").strip().upper()
    if value.startswith("D") and len(value) == 3 and value[1:].isdigit():
        value = value[1:]
    if value.isdigit() and len(value) == 1:
        value = f"0{value}"
    if value in DIMENSION_IDS:
        return value
    return "01"


def _weights_from_multipliers(multipliers: Dict[str, float]) -> Dict[str, float]:
    if not multipliers:
        return {dim_id: 1.0 / len(DIMENSION_IDS) for dim_id in DIMENSION_IDS}
    values: Dict[str, float] = {}
    for dim_id in DIMENSION_IDS:
        v = multipliers.get(dim_id)
        if v is None:
            v = multipliers.get(f"D{dim_id}")
        values[dim_id] = max(0.0, float(v) if v is not None else 1.0)
    total = sum(values.values()) or float(len(DIMENSION_IDS))
    return {dim_id: values[dim_id] / total for dim_id in DIMENSION_IDS}


def _determine_engine_version(project: Dict[str, object], requested: Optional[str] = None) -> str:
    value = str(requested or project.get("scoring_engine_version_locked") or "").strip().lower()
    if value.startswith("v2"):
        return "v2"
    return "v1"


def _char_locator_for_snippet(text: str, snippet: str) -> str:
    src = str(text or "")
    needle = " ".join(str(snippet or "").split()).strip()
    if not src or not needle:
        return ""
    idx = src.find(needle)
    if idx < 0 and len(needle) > 24:
        idx = src.find(needle[:24])
    if idx < 0:
        return ""
    end = min(len(src), idx + len(needle))
    return f"char:{idx}-{end}"


def _build_v2_evidence_by_dim(
    evidence_units: List[Dict[str, object]], text: str
) -> Dict[str, List[Dict[str, str]]]:
    grouped: Dict[str, List[Dict[str, object]]] = {dim_id: [] for dim_id in DIMENSION_IDS}
    for unit in evidence_units or []:
        if not isinstance(unit, dict):
            continue
        dim_id = _normalize_dimension_id(str(unit.get("dimension_primary") or "01"))
        grouped.setdefault(dim_id, []).append(unit)

    result: Dict[str, List[Dict[str, str]]] = {dim_id: [] for dim_id in DIMENSION_IDS}
    for dim_id in DIMENSION_IDS:
        units = grouped.get(dim_id, [])
        units_sorted = sorted(
            units,
            key=lambda u: (
                float(u.get("specificity_score", 0.0)),
                int(bool(u.get("tag_solution"))),
                int(bool(u.get("landing_accept"))),
                int(bool(u.get("landing_role"))),
            ),
            reverse=True,
        )
        seen: set[str] = set()
        rows: List[Dict[str, str]] = []
        for unit in units_sorted:
            snippet = " ".join(str(unit.get("text") or "").split()).strip()
            if not snippet:
                continue
            short = snippet[:220] + ("..." if len(snippet) > 220 else "")
            if short in seen:
                continue
            seen.add(short)
            locator = _char_locator_for_snippet(text, snippet) or str(unit.get("locator") or "")
            rows.append({"text_snippet": short, "locator": locator})
            if len(rows) >= 4:
                break
        result[dim_id] = rows
    return result


def _legacy_dimension_scores_from_rule(
    rule_dim_scores: Dict[str, object],
    *,
    evidence_by_dim: Optional[Dict[str, List[Dict[str, str]]]] = None,
) -> Dict[str, object]:
    result: Dict[str, object] = {}
    evidence_by_dim = evidence_by_dim or {}
    for dim_id in DIMENSION_IDS:
        item = rule_dim_scores.get(dim_id) or {}
        score = float(item.get("dim_score", 0.0))
        subs = item.get("subscores") or {}
        sub_scores = [
            {"name": str(k), "score": float(v), "hits": [], "evidence": []} for k, v in subs.items()
        ]
        meta = DIMENSIONS.get(dim_id) or {}
        result[dim_id] = {
            "id": dim_id,
            "name": meta.get("name", dim_id),
            "module": meta.get("module", ""),
            "score": round(score, 2),
            "max_score": 10.0,
            "hits": [],
            "evidence": evidence_by_dim.get(dim_id) or [],
            "sub_scores": sub_scores or None,
        }
    return result


def _rule_dim_scores_from_legacy(dimension_scores: Dict[str, object]) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, raw in (dimension_scores or {}).items():
        dim_id = _normalize_dimension_id(str(key))
        item = raw if isinstance(raw, dict) else {}
        subs_raw = item.get("sub_scores") or []
        subscores: Dict[str, float] = {}
        if isinstance(subs_raw, list):
            for sub in subs_raw:
                if not isinstance(sub, dict):
                    continue
                name = str(sub.get("name") or "")
                if not name:
                    continue
                subscores[name] = round(float(sub.get("score", 0.0)), 2)
        result[dim_id] = {
            "dim_score": round(float(item.get("score", 0.0)), 2),
            "subscores": subscores,
            "coverage_rate": None,
            "evidence_count": len(item.get("evidence") or []),
        }
    for dim_id in DIMENSION_IDS:
        result.setdefault(
            dim_id,
            {
                "dim_score": 0.0,
                "subscores": {},
                "coverage_rate": None,
                "evidence_count": 0,
            },
        )
    return result


def _build_v2_report_payload(
    v2_result: Dict[str, object],
    *,
    text: str,
    project: Dict[str, object],
    profile_snapshot: Optional[Dict[str, object]],
    scoring_engine_version: str,
) -> Dict[str, object]:
    rule_dim_scores = v2_result.get("rule_dim_scores") or {}
    evidence_by_dim = _build_v2_evidence_by_dim(
        list(v2_result.get("evidence_units") or []),
        text,
    )
    report = {
        "total_score": float(v2_result.get("rule_total_score", 0.0)),
        "rule_total_score": float(v2_result.get("rule_total_score", 0.0)),
        "rule_dim_scores": rule_dim_scores,
        "dimension_scores": _legacy_dimension_scores_from_rule(
            rule_dim_scores,
            evidence_by_dim=evidence_by_dim,
        ),
        "dim_total_80": float(v2_result.get("dim_total_80", 0.0)),
        "dim_total_90": float(v2_result.get("dim_total_90", 0.0)),
        "consistency_bonus": float(v2_result.get("consistency_bonus", 0.0)),
        "consistency_checks": v2_result.get("consistency_checks", []),
        "pred_dim_scores": None,
        "pred_total_score": None,
        "llm_total_score": None,
        "pred_confidence": None,
        "score_blend": None,
        "penalties": v2_result.get("penalties", []),
        "lint_findings": v2_result.get("lint_findings", []),
        "suggestions": v2_result.get("suggestions", []),
        "requirement_hits": v2_result.get("requirement_hits", []),
        "mandatory_req_hit_rate": v2_result.get("mandatory_req_hit_rate"),
        "requirement_pack_versions": v2_result.get("requirement_pack_versions", []),
        "evidence_units_count": int(v2_result.get("evidence_units_count", 0) or 0),
        "meta": {
            "engine_version": "v2",
            "region": project.get("region", DEFAULT_REGION),
            "scoring_engine_version": scoring_engine_version,
            "requirement_pack_versions": v2_result.get("requirement_pack_versions", []),
        },
    }
    if profile_snapshot:
        report["meta"]["expert_profile_snapshot"] = profile_snapshot
        report["meta"]["expert_profile_id"] = profile_snapshot.get("id")
    return report


def _select_calibrator_model(project: Dict[str, object]) -> Optional[Dict[str, object]]:
    models = sorted(
        load_calibration_models(), key=lambda x: str(x.get("created_at", "")), reverse=True
    )
    if not models:
        return None
    project_id = str(project.get("id") or "")
    locked_version = str(project.get("calibrator_version_locked") or "")

    def _scope_project_id(model: Dict[str, object]) -> str:
        return str(((model.get("train_filter") or {}).get("project_id") or "")).strip()

    def _compatible(model: Dict[str, object]) -> bool:
        # 仅允许同项目训练出的校准器生效，避免跨项目污染总分。
        return _scope_project_id(model) == project_id

    if locked_version:
        for model in models:
            if str(model.get("calibrator_version") or "") == locked_version:
                return model if _compatible(model) else None
        return None

    for model in models:
        if bool(model.get("deployed")) and _compatible(model):
            return model
    return None


def _select_deployed_patch(project_id: str) -> Optional[Dict[str, object]]:
    packages = [
        p
        for p in load_patch_packages()
        if str(p.get("project_id")) == project_id and str(p.get("status")) == "deployed"
    ]
    if not packages:
        return None
    packages.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
    return packages[0]


def _apply_deployed_patch_to_report(project_id: str, report: Dict[str, object]) -> None:
    patch = _select_deployed_patch(project_id)
    if not patch:
        return
    payload = patch.get("patch_payload") or {}
    penalties = report.get("penalties")
    if not isinstance(penalties, list) or not penalties:
        report.setdefault("meta", {})
        report["meta"]["patch_id"] = patch.get("id")
        return

    multipliers = payload.get("penalty_multiplier") or {}
    old_penalty_total = sum(
        float(p.get("points", p.get("deduct", 0.0))) for p in penalties if isinstance(p, dict)
    )
    new_penalty_total = 0.0
    for penalty in penalties:
        if not isinstance(penalty, dict):
            continue
        code = str(penalty.get("code") or "")
        mul = float(multipliers.get(code, 1.0)) if code else 1.0
        if "points" in penalty and penalty.get("points") is not None:
            penalty["points"] = round(float(penalty.get("points", 0.0)) * mul, 2)
            new_penalty_total += float(penalty["points"])
        elif "deduct" in penalty and penalty.get("deduct") is not None:
            penalty["deduct"] = round(float(penalty.get("deduct", 0.0)) * mul, 2)
            new_penalty_total += float(penalty["deduct"])
        else:
            new_penalty_total += 0.0

    has_dim_components = ("dim_total_90" in report) or ("dim_total_80" in report)
    if has_dim_components:
        dim_total_80 = float(report.get("dim_total_80", 0.0))
        dim_total_90 = report.get("dim_total_90")
        if dim_total_90 is not None:
            # 统一回推为 dim_total_80 再走同一聚合公式，避免历史/新字段混用时口径漂移
            dim_total_80 = max(0.0, min(80.0, float(dim_total_90) * (80.0 / 90.0)))
        consistency_bonus = float(report.get("consistency_bonus", 0.0))
        new_rule_total, normalized_dim_total_90 = compute_v2_rule_total(
            dim_total_80=dim_total_80,
            consistency_bonus=consistency_bonus,
            penalty_points=new_penalty_total,
        )
        report["rule_total_score"] = new_rule_total
        report["total_score"] = new_rule_total
        report["dim_total_80"] = round(dim_total_80, 2)
        report["dim_total_90"] = normalized_dim_total_90
    else:
        old_total = float(report.get("rule_total_score", report.get("total_score", 0.0)))
        delta = new_penalty_total - old_penalty_total
        new_total = max(0.0, min(100.0, round(old_total - delta, 2)))
        report["rule_total_score"] = new_total
        report["total_score"] = new_total

    report.setdefault("meta", {})
    report["meta"]["patch_id"] = patch.get("id")
    report["meta"]["patch_status"] = patch.get("status")


def _clip_score(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _normalize_score_scale_max(value: object, default: int = DEFAULT_SCORE_SCALE_MAX) -> int:
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        numeric = int(default)
    return 5 if numeric == 5 else 100


def _resolve_project_score_scale_max(project: Dict[str, object]) -> int:
    meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
    return _normalize_score_scale_max(
        (meta or {}).get("score_scale_max"),
        default=DEFAULT_SCORE_SCALE_MAX,
    )


def _score_scale_label(score_scale_max: int) -> str:
    return "5分制" if int(score_scale_max) == 5 else "100分制"


def _convert_score_from_100(score: object, score_scale_max: int) -> Optional[float]:
    value = _to_float_or_none(score)
    if value is None:
        return None
    clipped = _clip_score(value, 0.0, 100.0)
    factor = float(_normalize_score_scale_max(score_scale_max)) / 100.0
    return round(clipped * factor, 2)


def _resolve_score_blend_weights(project: Dict[str, object]) -> tuple[float, float, float]:
    meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
    blend_raw = meta.get("score_blend") if isinstance(meta, dict) else {}
    blend_cfg = blend_raw if isinstance(blend_raw, dict) else {}

    def _f(v: object, default: float) -> float:
        try:
            return float(v)
        except Exception:
            return default

    rule_w = _f(
        blend_cfg.get("rule_weight", blend_cfg.get("rule", DEFAULT_RULE_SCORE_WEIGHT)),
        DEFAULT_RULE_SCORE_WEIGHT,
    )
    llm_w = _f(
        blend_cfg.get("llm_weight", blend_cfg.get("llm", DEFAULT_LLM_SCORE_WEIGHT)),
        DEFAULT_LLM_SCORE_WEIGHT,
    )
    delta_cap = _f(blend_cfg.get("llm_delta_cap", DEFAULT_LLM_DELTA_CAP), DEFAULT_LLM_DELTA_CAP)

    rule_w = max(0.0, rule_w)
    llm_w = max(0.0, llm_w)
    total = rule_w + llm_w
    if total <= 1e-9:
        rule_w, llm_w = DEFAULT_RULE_SCORE_WEIGHT, DEFAULT_LLM_SCORE_WEIGHT
        total = rule_w + llm_w
    rule_w /= total
    llm_w /= total
    delta_cap = max(0.0, delta_cap)
    return rule_w, llm_w, delta_cap


def _fuse_rule_and_llm_scores(
    *,
    rule_total: float,
    llm_total_raw: float,
    project: Dict[str, object],
) -> tuple[float, float, Dict[str, float]]:
    rule = _clip_score(rule_total)
    llm_raw = _clip_score(llm_total_raw)
    rule_w, llm_w, delta_cap = _resolve_score_blend_weights(project)
    llm_bounded = _clip_score(max(rule - delta_cap, min(rule + delta_cap, llm_raw)))
    fused = _clip_score(rule * rule_w + llm_bounded * llm_w)
    blend_info = {
        "rule_weight": round(rule_w, 4),
        "llm_weight": round(llm_w, 4),
        "llm_delta_cap": round(delta_cap, 2),
    }
    return round(fused, 2), round(llm_bounded, 2), blend_info


def _apply_prediction_to_report(
    report: Dict[str, object],
    *,
    submission_like: Dict[str, object],
    project: Dict[str, object],
) -> Optional[str]:
    model = _select_calibrator_model(project)
    if not model:
        report["pred_total_score"] = None
        report["llm_total_score"] = None
        report["pred_confidence"] = None
        report["pred_dim_scores"] = None
        report["score_blend"] = None
        # `total_score` is the primary score used by UI/sorting.
        report["total_score"] = float(
            report.get("rule_total_score", report.get("total_score", 0.0))
        )
        submission_like["total_score"] = float(report.get("total_score", 0.0))
        return None
    artifact = model.get("model_artifact") or model.get("artifact") or {}
    if not isinstance(artifact, dict):
        report["pred_total_score"] = None
        report["llm_total_score"] = None
        report["pred_confidence"] = None
        report["pred_dim_scores"] = None
        report["score_blend"] = None
        report["total_score"] = float(
            report.get("rule_total_score", report.get("total_score", 0.0))
        )
        submission_like["total_score"] = float(report.get("total_score", 0.0))
        return None
    row = build_feature_row(report, submission=submission_like)
    try:
        pred, conf = predict_with_model(artifact, row.get("x_features") or {})
    except Exception as e:
        # 预测模型不应影响主流程可用性；失败时保留 rule 分并显式记录错误。
        report["pred_total_score"] = None
        report["llm_total_score"] = None
        report["pred_confidence"] = None
        report["pred_dim_scores"] = None
        report["score_blend"] = None
        report["total_score"] = float(
            report.get("rule_total_score", report.get("total_score", 0.0))
        )
        submission_like["total_score"] = float(report.get("total_score", 0.0))
        report.setdefault("meta", {})
        report["meta"]["calibrator_version"] = model.get("calibrator_version")
        report["meta"]["calibrator_error"] = f"{type(e).__name__}: {e}"
        return str(model.get("calibrator_version") or "")

    rule_total = float(report.get("rule_total_score", report.get("total_score", 0.0)))
    fused_total, llm_total, blend_info = _fuse_rule_and_llm_scores(
        rule_total=rule_total,
        llm_total_raw=float(pred),
        project=project,
    )
    report["pred_total_score"] = fused_total
    report["llm_total_score"] = llm_total
    report["pred_confidence"] = {
        **conf,
        "raw_llm_score": float(pred),
        "bounded_llm_score": llm_total,
    }
    report["score_blend"] = blend_info
    report["pred_dim_scores"] = None
    report["total_score"] = float(fused_total)
    submission_like["total_score"] = float(fused_total)
    report.setdefault("meta", {})
    report["meta"]["calibrator_version"] = model.get("calibrator_version")
    return str(model.get("calibrator_version") or "")


def _to_float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_submission_score_fields(
    submission: Dict[str, object],
    *,
    allow_pred_score: bool = True,
    score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
) -> Dict[str, object]:
    report = submission.get("report")
    pred_total = None
    rule_total = None
    if isinstance(report, dict):
        pred_total = _to_float_or_none(report.get("pred_total_score"))
        if not allow_pred_score:
            pred_total = None
        rule_total = _to_float_or_none(report.get("rule_total_score"))
        if rule_total is None:
            rule_total = _to_float_or_none(report.get("total_score"))
    fallback_total = _to_float_or_none(submission.get("total_score"))
    if rule_total is None:
        rule_total = fallback_total
    primary_total = pred_total if pred_total is not None else rule_total
    if primary_total is None:
        primary_total = fallback_total if fallback_total is not None else 0.0
    total_display = _convert_score_from_100(primary_total, score_scale_max)
    pred_display = _convert_score_from_100(pred_total, score_scale_max)
    rule_display = _convert_score_from_100(rule_total, score_scale_max)
    if total_display is None:
        total_display = 0.0
    return {
        "total_score": round(float(total_display), 2),
        "pred_total_score": round(float(pred_display), 2) if pred_display is not None else None,
        "rule_total_score": round(float(rule_display), 2) if rule_display is not None else None,
        "score_source": "pred" if pred_total is not None else "rule",
    }


def _submission_is_scored(submission: Dict[str, object]) -> bool:
    report_obj = submission.get("report")
    if isinstance(report_obj, dict):
        status = str(report_obj.get("scoring_status") or "").strip().lower()
        if status == "pending":
            return False
        if status == "scored":
            return True
        if _to_float_or_none(report_obj.get("rule_total_score")) is not None:
            return True
        if _to_float_or_none(report_obj.get("pred_total_score")) is not None:
            return True
        if _to_float_or_none(report_obj.get("total_score")) is not None:
            return True
    return _to_float_or_none(submission.get("total_score")) is not None


def _mark_report_scored(report: Dict[str, object], *, trigger: str) -> None:
    report["scoring_status"] = "scored"
    report["scoring_trigger"] = trigger
    report["scored_at"] = _now_iso()


def _build_pending_submission_report(
    *,
    project: Dict[str, object],
    scoring_engine_version: str,
) -> Dict[str, object]:
    return {
        "scoring_status": "pending",
        "scoring_trigger": "upload_only",
        "queued_at": _now_iso(),
        "total_score": None,
        "rule_total_score": None,
        "pred_total_score": None,
        "llm_total_score": None,
        "pred_confidence": None,
        "score_blend": None,
        "dimension_scores": {},
        "rule_dim_scores": {},
        "pred_dim_scores": None,
        "penalties": [],
        "lint_findings": [],
        "suggestions": [],
        "requirement_hits": [],
        "mandatory_req_hit_rate": None,
        "evidence_units_count": 0,
        "meta": {
            "engine_version": _determine_engine_version(project, scoring_engine_version),
            "region": project.get("region", DEFAULT_REGION),
            "scoring_engine_version": scoring_engine_version,
            "queued_for_scoring": True,
        },
    }


def _score_submission_for_project(
    *,
    submission_id: str,
    text: str,
    project_id: str,
    project: Dict[str, object],
    config: object,
    multipliers: Dict[str, float],
    profile_snapshot: Optional[Dict[str, object]],
    scoring_engine_version: str,
    anchors: Optional[List[Dict[str, object]]] = None,
    requirements: Optional[List[Dict[str, object]]] = None,
) -> tuple[Dict[str, object], List[Dict[str, object]]]:
    engine_version = _determine_engine_version(project, scoring_engine_version)
    if engine_version == "v2":
        anchors = (
            anchors
            if anchors is not None
            else [a for a in load_project_anchors() if str(a.get("project_id")) == project_id]
        )
        requirements = (
            requirements
            if requirements is not None
            else [r for r in load_project_requirements() if str(r.get("project_id")) == project_id]
        )
        if not anchors or not requirements:
            anchors, requirements = _rebuild_project_anchors_and_requirements(project_id)

        weights_norm = (
            dict(profile_snapshot.get("weights_norm") or {})
            if profile_snapshot
            else _weights_from_multipliers(multipliers)
        )
        v2_result = score_text_v2(
            submission_id=submission_id,
            text=text,
            lexicon=config.lexicon,
            weights_norm=weights_norm,
            anchors=anchors,
            requirements=requirements,
        )
        report = _build_v2_report_payload(
            v2_result,
            text=text,
            project=project,
            profile_snapshot=profile_snapshot,
            scoring_engine_version=scoring_engine_version,
        )
        _apply_deployed_patch_to_report(project_id, report)
        submission_like = {"id": submission_id, "project_id": project_id, "text": text}
        _apply_prediction_to_report(report, submission_like=submission_like, project=project)
        _mark_report_scored(report, trigger="score_engine")
        return report, list(v2_result.get("evidence_units") or [])

    legacy = score_text(
        text,
        config.rubric,
        config.lexicon,
        dimension_multipliers=multipliers,
    ).model_dump()
    legacy.setdefault("rule_total_score", float(legacy.get("total_score", 0.0)))
    legacy.setdefault(
        "rule_dim_scores", _rule_dim_scores_from_legacy(legacy.get("dimension_scores", {}))
    )
    legacy.setdefault("pred_dim_scores", None)
    legacy.setdefault("pred_total_score", None)
    legacy.setdefault("pred_confidence", None)
    legacy.setdefault("lint_findings", [])
    legacy.setdefault("requirement_hits", [])
    legacy.setdefault("mandatory_req_hit_rate", None)
    legacy.setdefault("evidence_units_count", 0)
    legacy.setdefault("meta", {})
    legacy["meta"]["engine_version"] = "v1"
    legacy["meta"]["region"] = project.get("region", DEFAULT_REGION)
    legacy["meta"]["scoring_engine_version"] = scoring_engine_version
    if profile_snapshot:
        legacy["meta"]["expert_profile_snapshot"] = profile_snapshot
        legacy["meta"]["expert_profile_id"] = profile_snapshot.get("id")
    _apply_deployed_patch_to_report(project_id, legacy)
    _mark_report_scored(legacy, trigger="score_engine")
    return legacy, []


def _replace_submission_evidence_units(
    all_units: List[Dict[str, object]],
    *,
    submission_id: str,
    new_units: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    kept = [u for u in all_units if str(u.get("submission_id")) != submission_id]
    kept.extend(new_units)
    return kept


def _latest_records_by_submission(records: List[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    latest: Dict[str, Dict[str, object]] = {}
    for item in records:
        sid = str(item.get("submission_id") or "")
        if not sid:
            continue
        prev = latest.get(sid)
        if prev is None or str(item.get("created_at", "")) >= str(prev.get("created_at", "")):
            latest[sid] = item
    return latest


def _extract_auto_candidates(model_artifact: Dict[str, Any]) -> List[Dict[str, Any]]:
    best_selection = model_artifact.get("best_selection") or {}
    raw_candidates = best_selection.get("candidates") or []
    if not isinstance(raw_candidates, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") or {}
        cv_item = item.get("cv") or {}
        normalized.append(
            {
                "model_type": str(item.get("model_type") or ""),
                "ok": bool(item.get("ok")),
                "gate_passed": bool(item.get("gate_passed")),
                "cv_mae": metrics.get("cv_mae"),
                "cv_rmse": metrics.get("cv_rmse"),
                "cv_spearman": metrics.get("cv_spearman"),
                "cv_mode": cv_item.get("mode"),
                "cv_pred_count": cv_item.get("pred_count"),
            }
        )
    return normalized


def _build_calibrator_summary(
    *,
    model_type: Optional[str],
    calibrator_version: Optional[str],
    gate_passed: Optional[bool],
    cv_metrics: Optional[Dict[str, Any]] = None,
    baseline_metrics: Optional[Dict[str, Any]] = None,
    improve_threshold: Optional[float] = None,
    spearman_tolerance: Optional[float] = None,
    auto_candidates: Optional[List[Dict[str, Any]]] = None,
    sample_count: Optional[int] = None,
    skipped_reason: Optional[str] = None,
) -> Dict[str, Any]:
    gate_payload: Dict[str, Any] = {}
    if gate_passed is not None:
        gate_payload["passed"] = bool(gate_passed)
    if improve_threshold is not None:
        gate_payload["improve_threshold"] = round(float(improve_threshold), 4)
    if spearman_tolerance is not None:
        gate_payload["spearman_tolerance"] = float(spearman_tolerance)

    summary: Dict[str, Any] = {
        "calibrator_version": calibrator_version,
        "model_type": model_type,
        "gate_passed": gate_passed,
        "cv_metrics": cv_metrics or {},
        "baseline_metrics": baseline_metrics or {},
        "gate": gate_payload,
        "auto_candidates": auto_candidates or [],
    }
    if sample_count is not None:
        summary["sample_count"] = int(sample_count)
    if skipped_reason:
        summary["skipped_reason"] = skipped_reason
    return summary


def _refresh_project_reflection_objects(project_id: str) -> None:
    submissions = [s for s in load_submissions() if str(s.get("project_id")) == project_id]
    submissions_by_id = {str(s.get("id")): s for s in submissions}
    latest_reports = _latest_records_by_submission(
        [r for r in load_score_reports() if str(r.get("project_id")) == project_id]
    )
    latest_qt = _latest_records_by_submission(
        [q for q in load_qingtian_results() if str(q.get("submission_id")) in submissions_by_id]
    )

    delta_cases = build_delta_cases(
        project_id=project_id,
        latest_reports_by_submission=latest_reports,
        latest_qingtian_by_submission=latest_qt,
    )
    all_delta = [d for d in load_delta_cases() if str(d.get("project_id")) != project_id]
    all_delta.extend(delta_cases)
    save_delta_cases(all_delta)

    samples = build_calibration_samples(
        project_id=project_id,
        latest_reports_by_submission=latest_reports,
        latest_qingtian_by_submission=latest_qt,
        submissions_by_id=submissions_by_id,
    )
    all_samples = [s for s in load_calibration_samples() if str(s.get("project_id")) != project_id]
    all_samples.extend(samples)
    save_calibration_samples(all_samples)


def _auto_update_project_weights_from_delta_cases(project_id: str) -> Dict[str, object]:
    """
    基于 DELTA_CASE 的维度偏差自动微调 16 维关注度。
    规则：
    - rule_dim > qt_dim（正偏差） -> 关注度下调
    - rule_dim < qt_dim（负偏差） -> 关注度上调
    """
    projects = load_projects()
    project = next((p for p in projects if str(p.get("id")) == project_id), None)
    if project is None:
        return {"updated": False, "reason": "project_not_found"}

    delta_cases = [d for d in load_delta_cases() if str(d.get("project_id")) == project_id]
    if len(delta_cases) < 2:
        return {
            "updated": False,
            "reason": "insufficient_delta_cases",
            "sample_count": len(delta_cases),
        }

    dim_stats: Dict[str, Dict[str, float]] = {
        dim_id: {"sum": 0.0, "count": 0.0} for dim_id in DIMENSION_IDS
    }
    for case in delta_cases:
        dim_errors = case.get("dim_errors") or {}
        if not isinstance(dim_errors, dict):
            continue
        for dim_id, raw_err in dim_errors.items():
            did = _normalize_dimension_id(str(dim_id))
            try:
                err = float(raw_err)
            except Exception:
                continue
            dim_stats[did]["sum"] += err
            dim_stats[did]["count"] += 1.0

    candidates: List[Dict[str, object]] = []
    for dim_id in DIMENSION_IDS:
        count = int(dim_stats[dim_id]["count"])
        if count < 2:
            continue
        mean_error = dim_stats[dim_id]["sum"] / float(count)
        if abs(mean_error) < 0.8:
            continue
        candidates.append(
            {
                "dim_id": dim_id,
                "count": count,
                "mean_error": round(mean_error, 3),
                "abs_mean_error": abs(mean_error),
                "step": -1 if mean_error > 0 else 1,
            }
        )

    if not candidates:
        return {
            "updated": False,
            "reason": "no_adjustable_dimensions",
            "sample_count": len(delta_cases),
        }

    candidates.sort(key=lambda x: float(x.get("abs_mean_error") or 0.0), reverse=True)
    top_dims = candidates[:4]

    profiles = load_expert_profiles()
    profile, created = _ensure_project_expert_profile(project, profiles)
    if created:
        save_expert_profiles(profiles)
        save_projects(projects)

    weights_raw = _coerce_weights_raw(dict(profile.get("weights_raw") or _default_weights_raw()))
    new_weights_raw = dict(weights_raw)
    changed_dims: List[Dict[str, object]] = []
    for item in top_dims:
        dim_id = str(item.get("dim_id"))
        step = int(item.get("step") or 0)
        before = int(new_weights_raw.get(dim_id, 5))
        after = max(0, min(10, before + step))
        if after == before:
            continue
        new_weights_raw[dim_id] = after
        changed_dims.append(
            {
                "dim_id": dim_id,
                "before": before,
                "after": after,
                "mean_error": item.get("mean_error"),
                "count": item.get("count"),
            }
        )

    if not changed_dims:
        return {"updated": False, "reason": "weights_unchanged", "sample_count": len(delta_cases)}

    auto_name = (
        f"{project.get('name', '项目')}_auto_feedback_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    new_profile = _new_expert_profile(auto_name, new_weights_raw)
    profiles.append(new_profile)
    save_expert_profiles(profiles)

    project["expert_profile_id"] = new_profile["id"]
    project["updated_at"] = _now_iso()
    save_projects(projects)

    return {
        "updated": True,
        "sample_count": len(delta_cases),
        "changed_dims": changed_dims,
        "new_profile_id": new_profile["id"],
    }


def _run_feedback_closed_loop(project_id: str, *, locale: str, trigger: str) -> Dict[str, object]:
    """
    反馈信号闭环：刷新样本 -> 自动调权重 -> 自动反演校准。
    所有步骤为 best-effort，不影响主流程返回。
    """
    result: Dict[str, object] = {
        "ok": True,
        "project_id": project_id,
        "trigger": trigger,
        "weight_update": {"updated": False},
        "auto_run": None,
    }
    try:
        _refresh_project_reflection_objects(project_id)
    except Exception as exc:
        result["ok"] = False
        result["refresh_error"] = str(exc)
        return result

    try:
        result["weight_update"] = _auto_update_project_weights_from_delta_cases(project_id)
    except Exception as exc:
        result["weight_update"] = {"updated": False, "error": str(exc)}

    try:
        auto_resp = auto_run_reflection_pipeline(project_id=project_id, api_key=None, locale=locale)
        if hasattr(auto_resp, "model_dump"):
            result["auto_run"] = auto_resp.model_dump()
        else:
            result["auto_run"] = dict(auto_resp)
    except Exception as exc:
        result["auto_run"] = {"ok": False, "error": str(exc)}
        result["ok"] = False
    return result


def _sync_ground_truth_record_to_qingtian(project_id: str, gt_record: Dict[str, object]) -> None:
    projects = load_projects()
    project = _find_project(project_id, projects)
    config = load_config()
    multipliers, profile_snapshot, _ = _resolve_project_scoring_context(project_id)
    scoring_engine_version = str(project.get("scoring_engine_version_locked") or "v1")
    source_gt_id = str(gt_record.get("id") or "")
    gt_text = str(gt_record.get("shigong_text") or "")

    submissions = load_submissions()
    matched_submission = None
    for s in submissions:
        if str(s.get("project_id")) != project_id:
            continue
        if str(s.get("source_ground_truth_id") or "") == source_gt_id:
            matched_submission = s
            break
        if str(s.get("text") or "").strip() == gt_text.strip() and gt_text.strip():
            matched_submission = s
            break

    scored_submission = False
    submission_changed = False
    evidence_units_new: List[Dict[str, object]] = []
    now_iso = _now_iso()
    if matched_submission is None:
        matched_submission = {
            "id": str(uuid4()),
            "project_id": project_id,
            "filename": f"ground_truth_{source_gt_id[:8]}.txt",
            "total_score": 0.0,
            "report": _build_pending_submission_report(
                project=project,
                scoring_engine_version=scoring_engine_version,
            ),
            "text": gt_text,
            "created_at": now_iso,
            "updated_at": now_iso,
            "expert_profile_id_used": profile_snapshot.get("id") if profile_snapshot else None,
            "source_ground_truth_id": source_gt_id,
            "bidder_name": f"GT_{source_gt_id[:8]}",
        }
        submissions.append(matched_submission)
        submission_changed = True

    if str(matched_submission.get("source_ground_truth_id") or "") != source_gt_id:
        matched_submission["source_ground_truth_id"] = source_gt_id
        submission_changed = True
    if gt_text.strip() and str(matched_submission.get("text") or "").strip() != gt_text.strip():
        matched_submission["text"] = gt_text
        submission_changed = True

    if not _submission_is_scored(matched_submission):
        report, evidence_units_new = _score_submission_for_project(
            submission_id=str(matched_submission.get("id")),
            text=gt_text,
            project_id=project_id,
            project=project,
            config=config,
            multipliers=multipliers,
            profile_snapshot=profile_snapshot,
            scoring_engine_version=scoring_engine_version,
        )
        _mark_report_scored(report, trigger="ground_truth_sync")
        matched_submission["report"] = report
        matched_submission["total_score"] = float(
            report.get("total_score", report.get("rule_total_score", 0.0))
        )
        matched_submission["expert_profile_id_used"] = (
            profile_snapshot.get("id") if profile_snapshot else None
        )
        matched_submission["updated_at"] = _now_iso()
        scored_submission = True
        submission_changed = True

    if submission_changed:
        save_submissions(submissions)

    if scored_submission:
        snapshots = load_score_reports()
        snapshots.append(
            _build_score_report_snapshot(
                submission_id=str(matched_submission.get("id")),
                project=project,
                report=matched_submission.get("report") or {},
                profile_snapshot=profile_snapshot,
                scoring_engine_version=scoring_engine_version,
            )
        )
        save_score_reports(snapshots)
        if evidence_units_new:
            all_units = load_evidence_units()
            all_units = _replace_submission_evidence_units(
                all_units,
                submission_id=str(matched_submission.get("id")),
                new_units=evidence_units_new,
            )
            save_evidence_units(all_units)

        report = matched_submission.get("report") or {}
        dimension_scores = {
            dim_id: (dim.get("score", 0.0) if isinstance(dim, dict) else 0.0)
            for dim_id, dim in (report.get("dimension_scores") or {}).items()
        }
        penalty_count = len(report.get("penalties", []))
        record_history_score(
            project_id=project_id,
            submission_id=str(matched_submission.get("id")),
            filename=str(matched_submission.get("filename", "")),
            total_score=float(report.get("total_score", report.get("rule_total_score", 0.0))),
            dimension_scores=dimension_scores,
            penalty_count=penalty_count,
        )

    qt_results = load_qingtian_results()
    exists = any(
        str((r.get("raw_payload") or {}).get("ground_truth_record_id") or "") == source_gt_id
        for r in qt_results
    )
    if not exists:
        qt_results.append(
            {
                "id": str(uuid4()),
                "submission_id": str(matched_submission.get("id")),
                "qingtian_model_version": str(
                    project.get("qingtian_model_version") or DEFAULT_QINGTIAN_MODEL_VERSION
                ),
                "qt_total_score": float(gt_record.get("final_score", 0.0)),
                "qt_dim_scores": None,
                "qt_reasons": [
                    {
                        "kind": "ground_truth",
                        "text": f"评委分: {gt_record.get('judge_scores')}",
                    }
                ],
                "raw_payload": {
                    "ground_truth_record_id": source_gt_id,
                    "source": gt_record.get("source"),
                    "judge_scores": gt_record.get("judge_scores"),
                    "final_score": gt_record.get("final_score"),
                },
                "created_at": _now_iso(),
            }
        )
        save_qingtian_results(qt_results)

    if str(project.get("status") or "") == "scoring_preparation":
        project["status"] = "submitted_to_qingtian"
        project["updated_at"] = _now_iso()
        save_projects(projects)

    _refresh_project_reflection_objects(project_id)


def _rebuild_project_anchors_and_requirements(
    project_id: str,
) -> tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    merged_text = _merge_materials_text(project_id)
    project = next((p for p in load_projects() if str(p.get("id")) == project_id), {})
    region = str(project.get("region") or DEFAULT_REGION)
    scoring_engine_version = str(
        project.get("scoring_engine_version_locked") or DEFAULT_SCORING_ENGINE_LOCKED
    )
    anchors = extract_project_anchors_from_text(project_id, merged_text)
    requirements = build_project_requirements_from_anchors(
        project_id,
        anchors,
        region=region,
        scoring_engine_version=scoring_engine_version,
    )

    all_anchors = [a for a in load_project_anchors() if str(a.get("project_id")) != project_id]
    all_requirements = [
        r for r in load_project_requirements() if str(r.get("project_id")) != project_id
    ]
    all_anchors.extend(anchors)
    all_requirements.extend(requirements)
    save_project_anchors(all_anchors)
    save_project_requirements(all_requirements)
    return anchors, requirements


def _build_constraint_pack(project_id: str) -> Dict[str, object]:
    multipliers, profile, _ = _resolve_project_scoring_context(project_id)
    anchors = [a for a in load_project_anchors() if str(a.get("project_id")) == project_id]
    requirements = [
        r for r in load_project_requirements() if str(r.get("project_id")) == project_id
    ]
    pack_versions = sorted(
        {
            str(r.get("source_pack_version") or "").strip()
            for r in requirements
            if str(r.get("source_pack_version") or "").strip()
        }
    )
    mandatory = [r for r in requirements if bool(r.get("mandatory"))]
    priority_order = sorted(
        DIMENSION_IDS, key=lambda d: float(multipliers.get(d, 1.0)), reverse=True
    )

    # 基础阈值：高优先维度略提高闭环/落地阈值
    thresholds: Dict[str, Dict[str, float]] = {}
    for dim_id in DIMENSION_IDS:
        weight = float(multipliers.get(dim_id, 1.0))
        bump = 0.2 if weight >= 1.15 else 0.0
        thresholds[dim_id] = {
            "coverage_min": round(1.2 + bump, 2),
            "closure_min": round(1.2 + bump, 2),
            "landing_min": round(1.2 + bump, 2),
            "specificity_min": round(1.0 + bump, 2),
        }

    return {
        "project_id": project_id,
        "expert_profile_snapshot": profile,
        "anchors_required": [
            {
                "anchor_key": a.get("anchor_key"),
                "expectation": "must_appear_same_value"
                if a.get("value_num") is not None
                else "must_not_conflict",
            }
            for a in anchors
        ],
        "requirements_mandatory": [
            {
                "requirement_id": r.get("id"),
                "dimension_id": r.get("dimension_id"),
                "req_label": r.get("req_label"),
                "req_type": r.get("req_type"),
            }
            for r in mandatory
        ],
        "dimension_thresholds": thresholds,
        "priority_order": priority_order,
        "requirement_pack_versions": pack_versions,
        "generated_at": _now_iso(),
    }


app = FastAPI(
    title="青天评标系统 API",
    version="1.0.0",
    description="""
## 施工组织设计智能评审系统

本系统提供施工组织设计文档的自动化评分、对比分析和自适应优化功能。

### 核心功能

- **智能评分**: 基于多维度评分规则，自动分析施组文档质量
- **批量处理**: 支持多文档并行处理，提升效率
- **对比分析**: 多版本施组横向对比，发现改进点
- **自适应学习**: 根据历史数据自动优化评分参数
- **DOCX 导出**: 生成专业格式的评审报告

### 认证方式

部分端点需要 API Key 认证，通过 `X-API-Key` 请求头传递。

### 快速开始

1. 创建项目: `POST /api/v1/projects`
2. 上传施组: `POST /api/v1/projects/{project_id}/shigong`
3. 查看结果: `GET /api/v1/projects/{project_id}/submissions`
4. 对比分析: `GET /api/v1/projects/{project_id}/compare`

### API 版本

当前 API 版本: v1 (路径前缀: `/api/v1`)
""",
    openapi_tags=OPENAPI_TAGS,
    contact={
        "name": "智飞文档生成系统",
        "email": "support@zhifei.example.com",
    },
    license_info={
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT",
    },
)

# Setup rate limiting (infrastructure ready, decorators disabled due to compatibility)
setup_rate_limiting(app)


@app.exception_handler(StarletteHTTPException)
async def _web_405_fallback_handler(request: Request, exc: StarletteHTTPException):
    """
    将网页上传入口的 405 统一回退为首页提示，避免浏览器直接展示 JSON 报错页。
    """
    if exc.status_code == 405:
        path = (request.url.path or "").rstrip("/")
        if path in ("/web/upload_materials", "/web/upload_shigong"):
            project_id = request.query_params.get("project_id", "")
            message = "请在主页选择文件后点击“上传资料”提交。"
            anchor = "#section-materials"
            if path == "/web/upload_shigong":
                message = "请在主页选择文件后点击“上传施组”提交。"
                anchor = "#section-shigong"
            return RedirectResponse(
                url=_web_upload_redirect_url(project_id, message, anchor),
                status_code=303,
            )
    return await http_exception_handler(request, exc)


def parse_accept_language(accept_language: str | None) -> str:
    """
    解析 Accept-Language header，返回最佳匹配的语言代码。

    支持的格式：
    - "zh"
    - "zh-CN"
    - "en-US,en;q=0.9,zh;q=0.8"

    Args:
        accept_language: Accept-Language header 值

    Returns:
        匹配的语言代码 (zh/en)，默认返回 zh
    """
    if not accept_language:
        return DEFAULT_LOCALE

    # 解析语言优先级列表
    languages = []
    for part in accept_language.split(","):
        part = part.strip()
        if not part:
            continue

        # 解析 q 值
        if ";q=" in part:
            lang, q_str = part.split(";q=", 1)
            try:
                q = float(q_str)
            except ValueError:
                q = 1.0
        else:
            lang = part
            q = 1.0

        # 提取主语言代码 (zh-CN -> zh)
        lang = lang.split("-")[0].lower()
        languages.append((lang, q))

    # 按优先级排序
    languages.sort(key=lambda x: x[1], reverse=True)

    # 查找第一个支持的语言
    for lang, _ in languages:
        if lang in SUPPORTED_LOCALES:
            return lang

    return DEFAULT_LOCALE


def get_locale(
    accept_language: str | None = Header(None, alias="Accept-Language"),
) -> str:
    """
    FastAPI 依赖：从 Accept-Language header 获取语言代码。

    用法:
        @router.get("/endpoint")
        def endpoint(locale: str = Depends(get_locale)):
            message = t("api.some_key", locale=locale)
    """
    return parse_accept_language(accept_language)


def _run_system_self_check(project_id: Optional[str]) -> Dict[str, object]:
    items: List[Dict[str, object]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        items.append({"name": name, "ok": bool(ok), "detail": detail or None})

    # health (always true if request reaches server)
    add("health", True, "service reachable")

    # config
    try:
        load_config()
        add("config", True, "rubric/lexicon loaded")
    except Exception as e:
        add("config", False, str(e))

    # data dirs + writable test
    try:
        ensure_data_dirs()
        with tempfile.NamedTemporaryFile(
            prefix="selfcheck_", suffix=".tmp", dir="data", delete=True
        ) as _:
            pass
        add("data_dirs_writable", True, "data directory writable")
    except Exception as e:
        add("data_dirs_writable", False, str(e))

    # status providers
    try:
        s = get_auth_status()
        add("auth_status", True, f"enabled={bool(s.get('enabled'))}")
    except Exception as e:
        add("auth_status", False, str(e))
    try:
        s = get_rate_limit_status()
        add("rate_limit_status", True, f"enabled={bool(s.get('enabled'))}")
    except Exception as e:
        add("rate_limit_status", False, str(e))

    # project-specific checks
    if project_id:
        try:
            projects = load_projects()
            target = next((p for p in projects if str(p.get("id")) == project_id), None)
            if target is None:
                add("project_exists", False, f"project not found: {project_id}")
            else:
                add("project_exists", True, str(target.get("name") or project_id))
                try:
                    materials_count = len(
                        [m for m in load_materials() if str(m.get("project_id")) == project_id]
                    )
                    add("project_materials_listable", True, f"count={materials_count}")
                except Exception as e:
                    add("project_materials_listable", False, str(e))
                try:
                    submissions_count = len(
                        [s for s in load_submissions() if str(s.get("project_id")) == project_id]
                    )
                    add("project_submissions_listable", True, f"count={submissions_count}")
                except Exception as e:
                    add("project_submissions_listable", False, str(e))
        except Exception as e:
            add("project_exists", False, str(e))

    all_ok = all(bool(x.get("ok")) for x in items)
    return {
        "ok": all_ok,
        "checked_at": _now_iso(),
        "items": items,
    }


# ==================== 健康检查端点（根路径，便于容器编排系统访问） ====================


@app.get("/health", response_model=HealthResponse, tags=["健康检查"])
def health_check() -> HealthResponse:
    """
    健康检查（Liveness Probe）。

    返回服务存活状态。只要服务进程正常运行即返回 healthy。
    用于 Kubernetes liveness probe 或负载均衡健康检查。

    - 不检查外部依赖
    - 响应时间应小于 100ms
    """
    return HealthResponse(status="healthy", version="1.0.0")


@app.get("/metrics", tags=["监控指标"], include_in_schema=True)
def prometheus_metrics():
    """
    Prometheus 指标端点。

    返回 Prometheus 文本格式的运行时指标，包括：
    - HTTP 请求计数和延迟
    - 评分请求统计和分数分布
    - 项目和提交数量
    - 配置状态

    用于 Prometheus 服务器抓取（scrape）或 Grafana 可视化。
    """
    from fastapi.responses import Response

    # 更新项目统计指标
    try:
        ensure_data_dirs()
        projects = load_projects()
        submissions = load_submissions()
        update_project_stats(len(projects), len(submissions))
    except Exception:
        pass  # 指标端点不应因统计失败而报错

    content = get_metrics()
    return Response(content=content, media_type="text/plain; charset=utf-8")


@app.get("/ready", response_model=ReadyResponse, tags=["健康检查"])
def readiness_check() -> ReadyResponse:
    """
    就绪检查（Readiness Probe）。

    返回服务是否准备好处理请求。检查配置和数据目录是否可用。
    用于 Kubernetes readiness probe，决定是否将流量路由到此实例。

    检查项目：
    - config: 配置文件是否可加载
    - data_dirs: 数据目录是否存在且可访问
    """
    checks = {}

    # 检查配置是否可加载
    try:
        load_config()
        checks["config"] = True
    except Exception:
        checks["config"] = False

    # 检查数据目录是否可用
    try:
        ensure_data_dirs()
        checks["data_dirs"] = True
    except Exception:
        checks["data_dirs"] = False

    # 所有检查通过则就绪
    all_ready = all(checks.values())
    status = "ready" if all_ready else "not_ready"

    return ReadyResponse(status=status, checks=checks)


@app.get("/__ping__", include_in_schema=False)
def ui_click_ping(btn: str = "") -> dict:
    return {"ok": True, "btn": btn}


# API v1 路由
router = APIRouter(prefix="/api/v1")


@router.get("/auth/status", tags=["系统状态"])
def auth_status() -> dict:
    """
    获取 API 认证状态。

    返回当前系统的认证配置状态，包括是否启用认证、认证方式等信息。
    """
    return get_auth_status()


@router.get("/rate_limit/status", tags=["系统状态"])
def rate_limit_status() -> dict:
    """
    获取限流状态。

    返回当前系统的请求限流配置，包括限流策略、配额等信息。
    """
    return get_rate_limit_status()


@router.get("/cache/stats", response_model=CacheStatsResponse, tags=["系统状态"])
def cache_stats() -> CacheStatsResponse:
    """
    获取评分缓存统计。

    返回缓存的运行时统计信息，包括：
    - 总请求数
    - 缓存命中数与未命中数
    - 缓存命中率
    - 当前缓存条目数
    - 驱逐数量

    用于监控缓存效率和容量规划。
    """
    stats = get_cache_stats()
    return CacheStatsResponse(**stats)


@router.post(
    "/cache/clear",
    response_model=CacheClearResponse,
    tags=["系统状态"],
    responses=RESPONSES_401,
)
def cache_clear(
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> CacheClearResponse:
    """
    清空评分缓存。

    清除所有缓存的评分结果。在配置变更后或需要强制重新评分时使用。

    **需要 API Key 认证**

    ⚠️ 此操作会清除所有缓存，不可恢复

    支持 Accept-Language header 进行多语言响应。
    """
    count = clear_score_cache()
    return CacheClearResponse(
        cleared=True,
        count=count,
        message=t("api.cache_cleared", locale=locale)
        if count > 0
        else t("api.cache_empty", locale=locale),
    )


@router.get("/config/status", response_model=ConfigStatusResponse, tags=["系统状态"])
def config_status() -> ConfigStatusResponse:
    """
    获取配置加载状态。

    返回配置文件的缓存状态、文件路径和修改时间信息。
    用于监控配置热加载机制是否正常工作。
    """
    status = get_config_status()
    return ConfigStatusResponse(
        cached=status["cached"],
        rubric_path=status["rubric_path"],
        lexicon_path=status["lexicon_path"],
        needs_reload=status["needs_reload"],
        rubric_mtime=status["rubric_mtime"],
        lexicon_mtime=status["lexicon_mtime"],
    )


@router.get("/config/llm_status", response_model=LLMBackendStatus, tags=["系统状态"])
def llm_status() -> LLMBackendStatus:
    """
    获取进化 LLM 后端配置状态（不暴露密钥）。
    便于确认当前生效的后端及哪些 API 已配置。
    """
    s = get_llm_backend_status()
    return LLMBackendStatus(
        evolution_backend=s["evolution_backend"],
        spark_configured=s["spark_configured"],
        openai_configured=s["openai_configured"],
        gemini_configured=s["gemini_configured"],
    )


@router.get(
    "/scoring/factors",
    response_model=ScoringFactorsResponse,
    tags=["系统状态"],
    responses=RESPONSES_404,
)
def scoring_factors(
    project_id: Optional[str] = Query(None, description="可选：指定项目ID，返回该项目的编制要求"),
    locale: str = Depends(get_locale),
) -> ScoringFactorsResponse:
    """
    获取评分体系总览。

    返回当前系统已启用的维度评分因子、扣分规则、Lint问题码，以及章节完整性/图文/组织架构等编制要求。
    便于对外分析与审阅当前评分标准。
    """
    ensure_data_dirs()
    if project_id:
        projects = load_projects()
        if not any(str(p.get("id")) == project_id for p in projects):
            raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    return ScoringFactorsResponse(**_build_scoring_factors_overview(project_id))


@router.get(
    "/scoring/factors/markdown",
    response_model=ScoringFactorsMarkdownResponse,
    tags=["系统状态"],
    responses=RESPONSES_404,
)
def scoring_factors_markdown(
    project_id: Optional[str] = Query(None, description="可选：指定项目ID，返回该项目的编制要求"),
    locale: str = Depends(get_locale),
) -> ScoringFactorsMarkdownResponse:
    """导出评分体系总览 Markdown 文本，便于外部模型或文档系统直接使用。"""
    ensure_data_dirs()
    if project_id:
        projects = load_projects()
        if not any(str(p.get("id")) == project_id for p in projects):
            raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    payload = _build_scoring_factors_overview(project_id)
    return ScoringFactorsMarkdownResponse(
        project_id=project_id,
        markdown=_render_scoring_factors_markdown(payload),
    )


@router.get(
    "/projects/{project_id}/analysis_bundle",
    response_model=AnalysisBundleResponse,
    tags=["洞察与学习"],
    responses={**RESPONSES_404},
)
def project_analysis_bundle(
    project_id: str,
    locale: str = Depends(get_locale),
) -> AnalysisBundleResponse:
    """
    导出项目分析包（Markdown），包含：
    - 项目级 V1/V2/V2+Calib 指标
    - 当前评分体系与章节要求
    """
    ensure_data_dirs()
    projects = load_projects()
    project = next((p for p in projects if str(p.get("id")) == project_id), None)
    if project is None:
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    factors_payload = _build_scoring_factors_overview(project_id)
    eval_payload = evaluate_project_variants(
        project_id=project_id,
        submissions=load_submissions(),
        score_reports=load_score_reports(),
        qingtian_results=load_qingtian_results(),
    )
    markdown = _render_project_analysis_bundle_markdown(
        project=project,
        factors_payload=factors_payload,
        evaluation_payload=eval_payload,
    )
    return AnalysisBundleResponse(
        project_id=project_id,
        markdown=markdown,
        generated_at=_now_iso(),
    )


@router.get(
    "/projects/{project_id}/analysis_bundle.md",
    tags=["洞察与学习"],
    responses={**RESPONSES_404},
)
def project_analysis_bundle_markdown_file(
    project_id: str,
    locale: str = Depends(get_locale),
) -> Response:
    """下载项目分析包 Markdown 文件。"""
    bundle = project_analysis_bundle(project_id=project_id, locale=locale)
    if isinstance(bundle, dict):
        markdown = str(bundle.get("markdown") or "")
    else:
        markdown = str(bundle.markdown or "")
    filename = f"analysis_bundle_{project_id}.md"
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/config/reload",
    response_model=ConfigReloadResponse,
    tags=["系统状态"],
    responses=RESPONSES_401,
)
def config_reload_endpoint(
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> ConfigReloadResponse:
    """
    强制重新加载配置文件。

    立即从磁盘重新加载 rubric.yaml 和 lexicon.yaml 配置文件。
    用于在修改配置文件后立即生效，无需重启服务。

    **需要 API Key 认证**

    支持 Accept-Language header 进行多语言响应。
    """
    try:
        reload_config()
        return ConfigReloadResponse(reloaded=True, message=t("api.config_reloaded", locale=locale))
    except Exception as e:
        return ConfigReloadResponse(reloaded=False, message=f"重载失败: {str(e)}")


@router.get(
    "/system/self_check",
    response_model=SelfCheckResponse,
    tags=["系统状态"],
)
def system_self_check(
    project_id: Optional[str] = Query(None, description="可选：附带检查某项目读取能力"),
) -> SelfCheckResponse:
    """运行系统自检并返回结构化结果。"""
    ensure_data_dirs()
    return SelfCheckResponse(**_run_system_self_check(project_id))


@router.post(
    "/score",
    response_model=ScoreReport,
    tags=["评分"],
    responses={**RESPONSES_401, **RESPONSES_422},
)
def score_endpoint(
    payload: ScoreRequest,
    api_key: Optional[str] = Depends(verify_api_key),
) -> ScoreReport:
    """
    对施组文本进行评分。

    接收施工组织设计文本内容，返回完整的评分报告，包括：
    - 总分及各维度得分
    - 命中的关键词证据
    - 扣分项及原因
    - 改进建议

    支持评分结果缓存，相同文本重复评分时直接返回缓存结果。

    **需要 API Key 认证**
    """
    config = load_config()

    # 尝试从缓存获取结果
    cached_result = get_cached_score(payload.text)
    if cached_result is not None:
        # 记录评分指标（命中缓存）
        record_score(cached_result.get("total_score", 0.0))
        return ScoreReport(**cached_result)

    # 缓存未命中，执行评分
    result = score_text(payload.text, config.rubric, config.lexicon)
    result_dict = result.model_dump()

    # 缓存评分结果
    cache_score_result(payload.text, result_dict)

    # 记录评分指标
    record_score(result.total_score)
    return result


@router.post(
    "/projects",
    response_model=ProjectRecord,
    tags=["项目管理"],
    responses={**RESPONSES_401, **RESPONSES_422},
)
def create_project(
    payload: ProjectCreate,
    api_key: Optional[str] = Depends(verify_api_key),
) -> ProjectRecord:
    """
    创建新项目。

    项目是施组评分的容器，可以包含多个施组文档和材料文件。
    创建项目后，可以上传材料和施组进行评分。

    **需要 API Key 认证**
    """
    ensure_data_dirs()
    projects = load_projects()
    if any(p["name"] == payload.name for p in projects):
        raise HTTPException(status_code=422, detail="项目名称已存在，请更换名称")
    project_id = str(uuid4())
    record = {
        "id": project_id,
        "name": payload.name,
        "meta": payload.meta or {},
        "region": DEFAULT_REGION,
        "expert_profile_id": None,
        "qingtian_model_version": DEFAULT_QINGTIAN_MODEL_VERSION,
        "scoring_engine_version_locked": DEFAULT_SCORING_ENGINE_LOCKED,
        "calibrator_version_locked": DEFAULT_CALIBRATOR_LOCKED,
        "status": "scoring_preparation",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    projects.append(record)
    save_projects(projects)
    return ProjectRecord(**record)


@router.get("/projects", response_model=list[ProjectRecord], tags=["项目管理"])
def list_projects() -> list[ProjectRecord]:
    """
    获取所有项目列表。

    返回系统中已创建的所有项目记录。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        active_projects = [
            p
            for p in projects
            if str(p.get("id") or "") != "p1" and not str(p.get("name") or "").startswith("E2E_")
        ]
        if not active_projects:
            recovered = _recover_latest_orphan_project(projects)
            if recovered is not None:
                projects = load_projects()
    changed = False
    for p in projects:
        changed = _ensure_project_v2_fields(p) or changed
    if changed:
        save_projects(projects)
    return [ProjectRecord(**p) for p in projects]


@router.get(
    "/projects/{project_id}/expert-profile",
    response_model=ProjectExpertProfileResponse,
    tags=["项目管理"],
    responses={**RESPONSES_404},
)
def get_project_expert_profile(
    project_id: str, locale: str = Depends(get_locale)
) -> ProjectExpertProfileResponse:
    """获取项目当前生效的专家16维关注度配置。"""
    ensure_data_dirs()
    projects = load_projects()
    try:
        project = _find_project(project_id, projects)
    except HTTPException:
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    project_changed = _ensure_project_v2_fields(project)
    profiles = load_expert_profiles()
    profile, created = _ensure_project_expert_profile(project, profiles)
    if project_changed or created:
        save_projects(projects)
    if created:
        save_expert_profiles(profiles)
    return ProjectExpertProfileResponse(
        project=ProjectRecord(**project),
        expert_profile=ExpertProfileRecord(**profile),
    )


@router.put(
    "/projects/{project_id}/expert-profile",
    response_model=ProjectExpertProfileResponse,
    tags=["项目管理"],
    responses={**RESPONSES_401, **RESPONSES_404, **RESPONSES_409},
)
def update_project_expert_profile(
    project_id: str,
    payload: ExpertProfileUpdate,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> ProjectExpertProfileResponse:
    """保存新的专家关注度配置并绑定到项目。"""
    ensure_data_dirs()
    projects = load_projects()
    try:
        project = _find_project(project_id, projects)
    except HTTPException:
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    _ensure_project_v2_fields(project)
    _assert_project_profile_operation_unlocked(project, bool(payload.force_unlock))
    weights_raw = _coerce_weights_raw(payload.weights_raw)
    profile_name = (
        payload.name or ""
    ).strip() or f"{project.get('name', '项目')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    profile = _new_expert_profile(profile_name, weights_raw)

    profiles = load_expert_profiles()
    profiles.append(profile)
    save_expert_profiles(profiles)

    project["expert_profile_id"] = profile["id"]
    project["updated_at"] = _now_iso()
    save_projects(projects)

    return ProjectExpertProfileResponse(
        project=ProjectRecord(**project),
        expert_profile=ExpertProfileRecord(**profile),
    )


@router.post(
    "/projects/{project_id}/rescore",
    response_model=RescoreResponse,
    tags=["项目管理"],
    responses={**RESPONSES_401, **RESPONSES_404, **RESPONSES_409},
)
def rescore_project_submissions(
    project_id: str,
    payload: RescoreRequest,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> RescoreResponse:
    """按当前生效专家配置重算项目施组评分。"""
    ensure_data_dirs()
    started_at = _now_iso()
    projects = load_projects()
    try:
        project = _find_project(project_id, projects)
    except HTTPException:
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    project_changed = _ensure_project_v2_fields(project)
    _assert_project_profile_operation_unlocked(project, bool(payload.force_unlock))
    score_scale_max = _normalize_score_scale_max(
        payload.score_scale_max,
        default=_resolve_project_score_scale_max(project),
    )
    project_meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
    project_meta = dict(project_meta or {})
    if int(project_meta.get("score_scale_max", DEFAULT_SCORE_SCALE_MAX)) != score_scale_max:
        project_meta["score_scale_max"] = score_scale_max
        project["meta"] = project_meta
        project_changed = True
    profiles = load_expert_profiles()
    profile, created = _ensure_project_expert_profile(project, profiles)
    if created:
        save_expert_profiles(profiles)
    if project_changed or created:
        save_projects(projects)

    if payload.scope not in {"project", "submission"}:
        raise HTTPException(status_code=422, detail="scope 仅支持 project 或 submission")
    if payload.scope == "submission" and not payload.submission_id:
        raise HTTPException(status_code=422, detail="scope=submission 时必须传 submission_id")

    if str(payload.scoring_engine_version or "").strip():
        project["scoring_engine_version_locked"] = payload.scoring_engine_version
    # 使用 _resolve_project_scoring_context 获取 multipliers，使进化产出的 dimension_multipliers 在评分时生效
    multipliers, profile_snapshot, _ = _resolve_project_scoring_context(project_id)
    if not multipliers and profile:
        multipliers = _weights_norm_to_dimension_multipliers(profile.get("weights_norm", {}))
    profile_for_meta = profile_snapshot if profile_snapshot else profile
    config = load_config()
    submissions = load_submissions()

    anchors: Optional[List[Dict[str, object]]] = None
    requirements: Optional[List[Dict[str, object]]] = None
    if payload.rebuild_anchors or payload.rebuild_requirements:
        anchors, requirements = _rebuild_project_anchors_and_requirements(project_id)

    if payload.scope == "submission":
        targets = [
            s
            for s in submissions
            if s.get("project_id") == project_id and s.get("id") == payload.submission_id
        ]
    else:
        targets = [s for s in submissions if s.get("project_id") == project_id]

    if not targets:
        raise HTTPException(status_code=404, detail=t("api.no_submissions", locale=locale))

    score_reports = load_score_reports()
    all_evidence_units = load_evidence_units()
    generated = 0
    now = _now_iso()
    for submission in targets:
        text = submission.get("text") or ""
        if not text.strip():
            continue
        report, evidence_units = _score_submission_for_project(
            submission_id=str(submission.get("id")),
            text=text,
            project_id=project_id,
            project=project,
            config=config,
            multipliers=multipliers,
            profile_snapshot=profile_snapshot,
            scoring_engine_version=payload.scoring_engine_version,
            anchors=anchors,
            requirements=requirements,
        )
        _apply_evolution_total_scale(project_id, report)
        all_evidence_units = _replace_submission_evidence_units(
            all_evidence_units,
            submission_id=str(submission.get("id")),
            new_units=evidence_units,
        )
        _mark_report_scored(report, trigger="manual_rescore")
        report_meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
        report_meta = dict(report_meta or {})
        report_meta["score_scale_max"] = score_scale_max
        report_meta["score_scale_label"] = _score_scale_label(score_scale_max)
        report["meta"] = report_meta

        submission["report"] = report
        submission["total_score"] = float(
            report.get("total_score", report.get("rule_total_score", 0.0))
        )
        submission["updated_at"] = now
        submission["expert_profile_id_used"] = (
            profile_for_meta.get("id") if profile_for_meta else None
        )

        snapshot = _build_score_report_snapshot(
            submission_id=str(submission.get("id")),
            project=project,
            report=report,
            profile_snapshot=profile_for_meta,
            scoring_engine_version=payload.scoring_engine_version,
        )
        score_reports.append(snapshot)
        generated += 1

        dimension_scores = {
            dim_id: dim.get("score", 0.0)
            for dim_id, dim in report.get("dimension_scores", {}).items()
        }
        penalty_count = len(report.get("penalties", []))
        record_history_score(
            project_id=project_id,
            submission_id=str(submission.get("id")),
            filename=str(submission.get("filename", "")),
            total_score=report.get("total_score", 0.0),
            dimension_scores=dimension_scores,
            penalty_count=penalty_count,
        )

    save_submissions(submissions)
    save_score_reports(score_reports)
    save_evidence_units(all_evidence_units)
    project["updated_at"] = _now_iso()
    save_projects(projects)
    # 重评分属于有效反馈信号：自动刷新样本并触发校准/调权重闭环（best-effort）。
    try:
        _run_feedback_closed_loop(project_id, locale=locale, trigger="rescore")
    except Exception:
        pass

    return RescoreResponse(
        ok=True,
        project_id=project_id,
        scoring_engine_version=payload.scoring_engine_version,
        expert_profile_id_used=str(profile.get("id")),
        submission_count=len(targets),
        reports_generated=generated,
        score_scale_max=score_scale_max,
        score_scale_label=_score_scale_label(score_scale_max),
        started_at=started_at,
        finished_at=_now_iso(),
    )


def _delete_project_cascade(project_id: str, *, locale: str = "zh") -> Dict[str, object]:
    """
    删除项目及其关联数据。

    会删除该项目下的：
    - 项目记录
    - 资料记录与资料文件
    - 施组提交记录
    - 学习画像
    - 历史评分记录
    - 项目背景上下文
    - 真实评标记录
    - 进化报告

    **需要 API Key 认证**（未配置 API_KEYS 时无需）
    """
    ensure_data_dirs()
    projects = load_projects()
    target = next((p for p in projects if str(p.get("id")) == project_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    target_name = str(target.get("name") or project_id)
    target_profile_id = str(target.get("expert_profile_id") or "")

    removed_counts = {
        "materials": 0,
        "submissions": 0,
        "score_reports": 0,
        "ground_truth": 0,
        "delta_cases": 0,
        "calibration_samples": 0,
        "patch_packages": 0,
    }

    save_projects([p for p in projects if p.get("id") != project_id])

    materials = load_materials()
    project_materials = [m for m in materials if m.get("project_id") == project_id]
    removed_counts["materials"] = len(project_materials)
    for m in project_materials:
        path = Path(str(m.get("path") or ""))
        if path.exists() and path.is_file():
            try:
                path.unlink()
            except Exception:
                pass
    save_materials([m for m in materials if m.get("project_id") != project_id])

    project_dir = MATERIALS_DIR / project_id
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)

    submissions = load_submissions()
    project_submission_ids = {
        str(s.get("id")) for s in submissions if s.get("project_id") == project_id
    }
    removed_counts["submissions"] = len(project_submission_ids)
    save_submissions([s for s in submissions if s.get("project_id") != project_id])

    score_reports = load_score_reports()
    removed_counts["score_reports"] = sum(
        1 for r in score_reports if r.get("project_id") == project_id
    )
    save_score_reports([r for r in score_reports if r.get("project_id") != project_id])
    evidence_units = load_evidence_units()
    save_evidence_units(
        [u for u in evidence_units if str(u.get("submission_id")) not in project_submission_ids]
    )
    qingtian_results = load_qingtian_results()
    save_qingtian_results(
        [q for q in qingtian_results if str(q.get("submission_id")) not in project_submission_ids]
    )
    delta_cases = load_delta_cases()
    removed_counts["delta_cases"] = sum(
        1
        for d in delta_cases
        if str(d.get("project_id")) == project_id
        or str(d.get("submission_id")) in project_submission_ids
    )
    save_delta_cases(
        [
            d
            for d in delta_cases
            if str(d.get("project_id")) != project_id
            and str(d.get("submission_id")) not in project_submission_ids
        ]
    )
    calibration_samples = load_calibration_samples()
    removed_counts["calibration_samples"] = sum(
        1
        for s in calibration_samples
        if str(s.get("project_id")) == project_id
        or str(s.get("submission_id")) in project_submission_ids
    )
    save_calibration_samples(
        [
            s
            for s in calibration_samples
            if str(s.get("project_id")) != project_id
            and str(s.get("submission_id")) not in project_submission_ids
        ]
    )
    patch_packages = load_patch_packages()
    removed_patch_ids = {
        str(p.get("id")) for p in patch_packages if str(p.get("project_id")) == project_id
    }
    removed_counts["patch_packages"] = len(removed_patch_ids)
    save_patch_packages([p for p in patch_packages if str(p.get("project_id")) != project_id])
    patch_deployments = load_patch_deployments()
    save_patch_deployments(
        [
            d
            for d in patch_deployments
            if str(d.get("project_id")) != project_id
            and str(d.get("patch_id")) not in removed_patch_ids
        ]
    )

    anchors = load_project_anchors()
    save_project_anchors([a for a in anchors if a.get("project_id") != project_id])
    requirements = load_project_requirements()
    save_project_requirements([r for r in requirements if r.get("project_id") != project_id])

    learning_profiles = load_learning_profiles()
    save_learning_profiles([p for p in learning_profiles if p.get("project_id") != project_id])

    score_history = load_score_history()
    save_score_history([h for h in score_history if h.get("project_id") != project_id])

    context = load_project_context()
    if project_id in context:
        context.pop(project_id, None)
        save_project_context(context)

    ground_truth = load_ground_truth()
    removed_counts["ground_truth"] = sum(
        1 for r in ground_truth if r.get("project_id") == project_id
    )
    save_ground_truth([r for r in ground_truth if r.get("project_id") != project_id])

    reports = load_evolution_reports()
    if project_id in reports:
        reports.pop(project_id, None)
        save_evolution_reports(reports)

    # 清理未被其它项目引用的专家配置
    if target_profile_id:
        remaining_projects = load_projects()
        in_use = any(
            str(p.get("expert_profile_id") or "") == target_profile_id for p in remaining_projects
        )
        if not in_use:
            profiles = load_expert_profiles()
            profiles = [ep for ep in profiles if str(ep.get("id") or "") != target_profile_id]
            save_expert_profiles(profiles)

    return {
        "project_id": project_id,
        "project_name": target_name,
        "removed_counts": removed_counts,
    }


@router.delete(
    "/projects/{project_id}",
    status_code=204,
    tags=["项目管理"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def delete_project(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> None:
    _delete_project_cascade(project_id, locale=locale)


@router.post(
    "/projects/cleanup_e2e",
    tags=["项目管理"],
    responses={**RESPONSES_401},
)
def cleanup_e2e_projects(
    prefix: str = Query("E2E_"),
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> dict:
    ensure_data_dirs()
    projects = load_projects()
    targets = [
        {
            "id": str(p.get("id") or ""),
            "name": str(p.get("name") or ""),
        }
        for p in projects
        if str(p.get("name") or "").startswith(prefix)
    ]
    removed: List[Dict[str, str]] = []
    failed: List[Dict[str, str]] = []
    for item in targets:
        pid = item["id"]
        try:
            _delete_project_cascade(pid, locale=locale)
            removed.append(item)
        except Exception as exc:  # noqa: BLE001 - bulk cleanup should continue
            failed.append({"id": pid, "name": item["name"], "detail": str(exc)})

    return {
        "ok": len(failed) == 0,
        "prefix": prefix,
        "matched": len(targets),
        "removed_count": len(removed),
        "removed": removed,
        "failed_count": len(failed),
        "failed": failed,
    }


MATERIAL_ALLOWED_EXTS = (
    ".txt",
    ".pdf",
    ".doc",
    ".docx",
    ".docm",
    ".json",
    ".xlsx",
    ".xls",
    ".xlsm",
    ".csv",
)
MATERIAL_ALLOWED_MIME_TOKENS = (
    "text/plain",
    "application/pdf",
    "application/json",
    "application/msword",
    "wordprocessingml",
    "spreadsheetml",
    "ms-excel",
)


def _normalize_uploaded_filename(filename: str) -> str:
    raw = unicodedata.normalize("NFKC", str(filename or "")).replace("\u3000", " ").strip()
    base = Path(raw).name.strip()
    while base.endswith("."):
        base = base[:-1].rstrip()
    return base


def _is_allowed_material_upload(filename: str, content_type: str) -> bool:
    normalized = _normalize_uploaded_filename(filename).lower()
    if normalized and any(normalized.endswith(ext) for ext in MATERIAL_ALLOWED_EXTS):
        return True
    ctype = str(content_type or "").lower().strip()
    return any(token in ctype for token in MATERIAL_ALLOWED_MIME_TOKENS)


SUBMISSION_DUPLICATE_WINDOW_SECONDS = 15


def _parse_iso_datetime_utc(value: object) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _find_recent_duplicate_submission(
    submissions: List[dict],
    *,
    project_id: str,
    filename: str,
    text: str,
    now_utc: datetime,
) -> Optional[dict]:
    normalized_filename = _normalize_uploaded_filename(filename)
    for submission in reversed(submissions):
        if submission.get("project_id") != project_id:
            continue
        if _normalize_uploaded_filename(submission.get("filename", "")) != normalized_filename:
            continue
        if str(submission.get("text") or "") != text:
            continue
        created_at = _parse_iso_datetime_utc(submission.get("created_at"))
        if created_at is None:
            continue
        age_seconds = (now_utc - created_at).total_seconds()
        if 0 <= age_seconds <= SUBMISSION_DUPLICATE_WINDOW_SECONDS:
            return submission
    return None


@router.post(
    "/projects/{project_id}/materials",
    tags=["项目管理"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def upload_material(
    project_id: str,
    file: UploadFile = File(...),
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> dict:
    """
    上传项目材料文件。

    上传招标文件、清单等项目参考材料。支持 .txt、.pdf、.doc、.docx、.json、.xlsx/.xls。
    资料会持久保存，用于项目投喂包、学习与进化等。

    **需要 API Key 认证**（未配置 API_KEYS 时无需）

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    raw_name = file.filename or ""
    normalized_name = _normalize_uploaded_filename(raw_name)
    if not normalized_name:
        raise HTTPException(status_code=422, detail="资料文件名为空，请重试或重命名后上传。")
    if not _is_allowed_material_upload(normalized_name, file.content_type or ""):
        raise HTTPException(
            status_code=422, detail="资料支持 .txt、.pdf、.doc、.docx、.json、.xlsx/.xls 格式"
        )
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    project_dir = MATERIALS_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    target = project_dir / normalized_name
    content = file.file.read()
    target.write_bytes(content)

    materials = load_materials()
    existing_ids = [
        str(m.get("id"))
        for m in materials
        if m.get("project_id") == project_id
        and _normalize_uploaded_filename(m.get("filename", "")) == normalized_name
        and m.get("id")
    ]
    materials = [
        m
        for m in materials
        if not (
            m.get("project_id") == project_id
            and _normalize_uploaded_filename(m.get("filename", "")) == normalized_name
        )
    ]
    record = {
        "id": existing_ids[0] if existing_ids else str(uuid4()),
        "project_id": project_id,
        "filename": normalized_name,
        "path": str(target),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    materials.append(record)
    save_materials(materials)
    return {"status": "ok", "material": record}


@router.get(
    "/projects/{project_id}/materials",
    response_model=list[MaterialRecord],
    tags=["项目管理"],
    responses={**RESPONSES_404},
)
def list_materials(project_id: str, locale: str = Depends(get_locale)) -> list[MaterialRecord]:
    """获取指定项目下已上传的资料列表。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    materials = [m for m in load_materials() if m.get("project_id") == project_id]
    return [MaterialRecord(**m) for m in materials]


@router.get(
    "/projects/{project_id}/anchors",
    response_model=list[ProjectAnchorRecord],
    tags=["项目管理"],
    responses={**RESPONSES_404},
)
def list_project_anchors(
    project_id: str, locale: str = Depends(get_locale)
) -> list[ProjectAnchorRecord]:
    """获取项目锚点列表。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    anchors = [a for a in load_project_anchors() if a.get("project_id") == project_id]
    return [ProjectAnchorRecord(**a) for a in anchors]


@router.post(
    "/projects/{project_id}/anchors/rebuild",
    response_model=list[ProjectAnchorRecord],
    tags=["项目管理"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def rebuild_project_anchors(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> list[ProjectAnchorRecord]:
    """基于项目资料重建锚点。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    anchors, _ = _rebuild_project_anchors_and_requirements(project_id)
    return [ProjectAnchorRecord(**a) for a in anchors]


@router.get(
    "/projects/{project_id}/requirements",
    response_model=list[ProjectRequirementRecord],
    tags=["项目管理"],
    responses={**RESPONSES_404},
)
def list_project_requirements(
    project_id: str,
    locale: str = Depends(get_locale),
) -> list[ProjectRequirementRecord]:
    """获取项目要求矩阵。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    requirements = [r for r in load_project_requirements() if r.get("project_id") == project_id]
    return [ProjectRequirementRecord(**r) for r in requirements]


@router.post(
    "/projects/{project_id}/requirements/rebuild",
    response_model=list[ProjectRequirementRecord],
    tags=["项目管理"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def rebuild_project_requirements(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> list[ProjectRequirementRecord]:
    """基于项目资料重建要求矩阵。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    _, requirements = _rebuild_project_anchors_and_requirements(project_id)
    return [ProjectRequirementRecord(**r) for r in requirements]


@router.get(
    "/projects/{project_id}/constraint_pack",
    response_model=ConstraintPack,
    tags=["项目管理"],
    responses={**RESPONSES_404},
)
def get_project_constraint_pack(
    project_id: str, locale: str = Depends(get_locale)
) -> ConstraintPack:
    """生成项目级编制约束包（Constraint Pack）。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    anchors = [a for a in load_project_anchors() if a.get("project_id") == project_id]
    requirements = [r for r in load_project_requirements() if r.get("project_id") == project_id]
    if not anchors or not requirements:
        _rebuild_project_anchors_and_requirements(project_id)
    pack = _build_constraint_pack(project_id)
    return ConstraintPack(**pack)


@router.delete(
    "/projects/{project_id}/materials/{material_id}",
    tags=["项目管理"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def delete_material(
    project_id: str,
    material_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> dict:
    """删除指定项目下的一条资料记录及对应文件。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    materials = load_materials()
    found = None
    for m in materials:
        if m.get("id") == material_id and m.get("project_id") == project_id:
            found = m
            break
    if not found:
        raise HTTPException(status_code=404, detail="资料记录不存在")
    path = Path(found["path"])
    if path.exists():
        path.unlink()
    materials = [m for m in materials if m.get("id") != material_id]
    save_materials(materials)
    return {"ok": True, "id": material_id}


def _read_uploaded_file_content(content: bytes, filename: str) -> str:
    """根据文件名解析上传文件为文本，支持 .txt、.docx、.pdf、.json、.xlsx"""
    name = filename.lower()
    if name.endswith(".txt"):
        return content.decode("utf-8", errors="ignore")
    if name.endswith(".docx"):
        if Document is None:
            raise ValueError("DOCX 解析不可用：请安装与当前系统架构兼容的 python-docx/lxml。")
        doc = Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs)
    if name.endswith(".pdf"):
        if pymupdf is None:
            raise ValueError("PDF 解析不可用：请安装与当前系统架构兼容的 PyMuPDF。")
        doc = pymupdf.open(stream=content, filetype="pdf")
        try:
            parts: List[str] = []
            for idx, page in enumerate(doc, start=1):
                # Embed stable page markers so downstream diagnostics can map evidence to pages.
                page_text = page.get_text() or ""
                parts.append(f"[PAGE:{idx}]\n{page_text}")
            return "\n\n".join(parts).strip()
        finally:
            doc.close()
    if name.endswith(".json"):
        return content.decode("utf-8", errors="ignore")
    if name.endswith(".xlsx") or name.endswith(".xls"):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            parts = []
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    parts.append("\t".join(str(c) if c is not None else "" for c in row))
            wb.close()
            return "\n".join(parts)
        except Exception as e:
            raise ValueError(f"Excel 解析失败: {e}") from e
    raise ValueError("仅支持 .txt、.docx、.pdf、.json、.xlsx/.xls")


def _merge_materials_text(project_id: str) -> str:
    """将本项目已上传的资料文件内容合并为一段文本，供学习进化使用。"""
    materials = [m for m in load_materials() if m.get("project_id") == project_id]
    parts = []
    for m in materials:
        path = m.get("path")
        if not path:
            continue
        p = Path(path)
        if not p.exists():
            continue
        try:
            content = p.read_bytes()
            text = _read_uploaded_file_content(content, p.name)
            if text.strip():
                parts.append(f"--- {p.name} ---\n{text.strip()}")
        except Exception:
            continue
    return "\n\n".join(parts) if parts else ""


def _compute_multipliers_hash(multipliers: dict) -> str:
    """计算 multipliers 的 hash，用于缓存 key"""
    import hashlib
    import json

    content = json.dumps(multipliers, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _get_effective_dimension_multipliers(project_id: str) -> Dict[str, float]:
    multipliers, _, _ = _resolve_project_scoring_context(project_id)
    return multipliers


def _get_evolution_total_score_scale(project_id: str) -> float | None:
    """进化报告中的总分缩放因子，用于使本系统总分贴近青天平均分。"""
    reports = load_evolution_reports()
    evo = reports.get(project_id) or {}
    se = evo.get("scoring_evolution") or {}
    scale = se.get("total_score_scale")
    if scale is not None and isinstance(scale, (int, float)):
        return float(scale)
    return None


def _apply_evolution_total_scale(project_id: str, report: Dict[str, object]) -> None:
    """若进化报告有 total_score_scale，对总分进行缩放（原地修改 report）。"""
    scale = _get_evolution_total_score_scale(project_id)
    if scale is None or abs(scale - 1.0) < 1e-6:
        return
    for key in ("total_score", "rule_total_score", "pred_total_score", "llm_total_score"):
        v = report.get(key)
        if v is not None:
            try:
                report[key] = round(min(100.0, max(0.0, float(v) * scale)), 2)
            except (TypeError, ValueError):
                pass
    # 若存在 pred_total_score，则保持 total_score 与展示/排序主分一致。
    pred_total = _to_float_or_none(report.get("pred_total_score"))
    rule_total = _to_float_or_none(report.get("rule_total_score"))
    if pred_total is not None:
        report["total_score"] = pred_total
    elif rule_total is not None:
        report["total_score"] = rule_total


@router.post(
    "/projects/{project_id}/shigong",
    response_model=SubmissionRecord,
    tags=["施组提交"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def upload_shigong(
    project_id: str,
    file: UploadFile = File(...),
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> SubmissionRecord:
    """
    上传施工组织设计文档（仅上传，不自动评分）。

    上传 TXT / DOCX / PDF / JSON / XLSX 格式施组文档并保存解析文本。
    上传后提交会进入“待评分”状态，需手动调用“评分施组”才会产生分数。

    **需要 API Key 认证**

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    raw_filename = file.filename or ""
    normalized_filename = _normalize_uploaded_filename(raw_filename)
    if not normalized_filename:
        raise HTTPException(status_code=422, detail="施组文件名为空，请重试或重命名后上传。")
    content = file.file.read()
    try:
        text = _read_uploaded_file_content(content, normalized_filename)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    submissions = load_submissions()
    now_utc = datetime.now(timezone.utc)
    duplicate = _find_recent_duplicate_submission(
        submissions,
        project_id=project_id,
        filename=normalized_filename,
        text=text,
        now_utc=now_utc,
    )
    if duplicate is not None:
        return SubmissionRecord(**duplicate)

    _, profile_snapshot, project = _resolve_project_scoring_context(project_id)
    scoring_engine_version = str(project.get("scoring_engine_version_locked") or "v1")
    submission_id = str(uuid4())

    report = _build_pending_submission_report(
        project=project,
        scoring_engine_version=scoring_engine_version,
    )
    if profile_snapshot:
        report_meta = report.get("meta")
        report_meta = report_meta if isinstance(report_meta, dict) else {}
        report_meta["expert_profile_snapshot"] = profile_snapshot
        report_meta["expert_profile_id"] = profile_snapshot.get("id")
        report["meta"] = report_meta

    record = {
        "id": submission_id,
        "project_id": project_id,
        "filename": normalized_filename,
        "total_score": 0.0,
        "report": report,
        "text": text,
        "created_at": now_utc.isoformat(),
        "updated_at": now_utc.isoformat(),
        "expert_profile_id_used": profile_snapshot.get("id") if profile_snapshot else None,
    }
    submissions.append(record)
    save_submissions(submissions)

    return SubmissionRecord(**record)


@router.post(
    "/projects/{project_id}/score",
    response_model=SubmissionRecord,
    tags=["施组提交"],
    responses={**RESPONSES_401, **RESPONSES_404, **RESPONSES_422},
)
def score_text_for_project(
    project_id: str,
    payload: ScoreRequest,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> SubmissionRecord:
    """
    对文本内容进行项目级评分。

    与直接上传文件不同，此端点接收内联文本进行评分。
    适用于已解析的文本内容或 API 集成场景。
    支持评分结果缓存，相同文本和配置重复评分时直接返回缓存结果。

    **需要 API Key 认证**

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    config = load_config()
    multipliers, profile_snapshot, project = _resolve_project_scoring_context(project_id)
    submission_id = str(uuid4())
    scoring_engine_version = str(project.get("scoring_engine_version_locked") or "v1")
    engine_version = _determine_engine_version(project, scoring_engine_version)

    if engine_version == "v1":
        config_hash = _compute_multipliers_hash(multipliers) if multipliers else None
        cached_result = get_cached_score(payload.text, config_hash)
        if cached_result is not None:
            report = dict(cached_result)
        else:
            raw_report, _ = _score_submission_for_project(
                submission_id=submission_id,
                text=payload.text,
                project_id=project_id,
                project=project,
                config=config,
                multipliers=multipliers,
                profile_snapshot=profile_snapshot,
                scoring_engine_version=scoring_engine_version,
            )
            # 缓存仅存“未缩放原始分”，避免后续读取时重复应用 total_score_scale。
            cache_score_result(payload.text, raw_report, config_hash)
            report = dict(raw_report)
        _apply_evolution_total_scale(project_id, report)
        evidence_units: List[Dict[str, object]] = []
    else:
        report, evidence_units = _score_submission_for_project(
            submission_id=submission_id,
            text=payload.text,
            project_id=project_id,
            project=project,
            config=config,
            multipliers=multipliers,
            profile_snapshot=profile_snapshot,
            scoring_engine_version=scoring_engine_version,
        )
        _apply_evolution_total_scale(project_id, report)

    record = {
        "id": submission_id,
        "project_id": project_id,
        "filename": "inline",
        "total_score": float(report.get("total_score", report.get("rule_total_score", 0.0))),
        "report": report,
        "text": payload.text,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expert_profile_id_used": profile_snapshot.get("id") if profile_snapshot else None,
    }
    submissions = load_submissions()
    submissions.append(record)
    save_submissions(submissions)

    snapshots = load_score_reports()
    snapshots.append(
        _build_score_report_snapshot(
            submission_id=submission_id,
            project=project,
            report=report,
            profile_snapshot=profile_snapshot,
            scoring_engine_version=scoring_engine_version,
        )
    )
    save_score_reports(snapshots)
    if evidence_units:
        all_units = load_evidence_units()
        all_units = _replace_submission_evidence_units(
            all_units,
            submission_id=submission_id,
            new_units=evidence_units,
        )
        save_evidence_units(all_units)

    # 记录评分历史
    dimension_scores = {
        dim_id: dim.get("score", 0.0) for dim_id, dim in report.get("dimension_scores", {}).items()
    }
    penalty_count = len(report.get("penalties", []))
    record_history_score(
        project_id=project_id,
        submission_id=submission_id,
        filename="inline",
        total_score=float(report.get("total_score", report.get("rule_total_score", 0.0))),
        dimension_scores=dimension_scores,
        penalty_count=penalty_count,
    )

    return SubmissionRecord(**record)


@router.get(
    "/projects/{project_id}/submissions",
    response_model=list[SubmissionRecord] | ProjectPreScoreListResponse,
    tags=["施组提交"],
)
def list_submissions(
    project_id: str,
    with_: Optional[str] = Query(None, alias="with"),
):
    """
    获取项目的所有施组提交记录。

    返回指定项目下的所有历史评分记录，包括评分报告详情。
    """
    ensure_data_dirs()
    projects = load_projects()
    project = next((p for p in projects if str(p.get("id")) == project_id), {"id": project_id})
    allow_pred_score = _select_calibrator_model(project) is not None
    score_scale_max = _resolve_project_score_scale_max(project)
    score_scale_max = _resolve_project_score_scale_max(project)

    submissions = [s for s in load_submissions() if s["project_id"] == project_id]

    def _view_submission(item: Dict[str, object]) -> Dict[str, object]:
        view = dict(item)
        report_obj = item.get("report")
        if not isinstance(report_obj, dict):
            total_display = _convert_score_from_100(item.get("total_score"), score_scale_max)
            if total_display is not None:
                view["total_score"] = total_display
            return view
        report = dict(report_obj)
        rule_total = _to_float_or_none(report.get("rule_total_score"))
        if rule_total is None:
            rule_total = _to_float_or_none(report.get("total_score"))
        if rule_total is None:
            rule_total = _to_float_or_none(item.get("total_score"))
        if rule_total is None:
            rule_total = 0.0
        if not allow_pred_score:
            report["pred_total_score"] = None
            report["llm_total_score"] = None
            report["pred_confidence"] = None
            report["score_blend"] = None
            report["total_score"] = round(float(rule_total), 2)
            view["total_score"] = round(float(rule_total), 2)
        raw_total = _to_float_or_none(report.get("total_score"))
        raw_rule = _to_float_or_none(report.get("rule_total_score"))
        raw_pred = _to_float_or_none(report.get("pred_total_score"))
        raw_llm = _to_float_or_none(report.get("llm_total_score"))
        report["raw_total_score_100"] = raw_total
        report["raw_rule_total_score_100"] = raw_rule
        report["raw_pred_total_score_100"] = raw_pred
        report["raw_llm_total_score_100"] = raw_llm
        report["score_scale_max"] = score_scale_max
        report["score_scale_label"] = _score_scale_label(score_scale_max)
        display_pred = _convert_score_from_100(raw_pred, score_scale_max)
        display_rule = _convert_score_from_100(raw_rule, score_scale_max)
        display_llm = _convert_score_from_100(raw_llm, score_scale_max)
        display_total = _convert_score_from_100(raw_total, score_scale_max)
        if display_total is None:
            display_total = _convert_score_from_100(item.get("total_score"), score_scale_max)
        report["pred_total_score"] = display_pred
        report["rule_total_score"] = display_rule
        report["llm_total_score"] = display_llm
        report["total_score"] = display_total
        if display_total is not None:
            view["total_score"] = display_total
        view["report"] = report
        return view

    submissions_view = [_view_submission(s) for s in submissions]
    if with_ != "latest_report":
        return [SubmissionRecord(**s) for s in submissions_view]

    latest_reports = _latest_records_by_submission(
        [r for r in load_score_reports() if str(r.get("project_id")) == project_id]
    )

    rows: List[Dict[str, object]] = []
    for s in submissions_view:
        sid = str(s.get("id"))
        latest = latest_reports.get(sid, {})
        suggestions = latest.get("suggestions") or []
        top_gain = 0.0
        if suggestions and isinstance(suggestions[0], dict):
            top_gain = float(suggestions[0].get("expected_gain", 0.0))
        pred_total_raw = latest.get("pred_total_score")
        if not allow_pred_score:
            pred_total_raw = None
        rule_total_raw = float(latest.get("rule_total_score", s.get("total_score", 0.0)))
        pred_total = _convert_score_from_100(pred_total_raw, score_scale_max)
        rule_total = _convert_score_from_100(rule_total_raw, score_scale_max)
        top_gain_display = _convert_score_from_100(top_gain, score_scale_max)
        rows.append(
            {
                "submission_id": sid,
                "bidder_name": s.get("bidder_name") or s.get("filename") or sid,
                "latest_report": {
                    "report_id": latest.get("id"),
                    "rule_total_score": float(rule_total if rule_total is not None else 0.0),
                    "pred_total_score": pred_total,
                    "rank_by_pred": None,
                    "rank_by_rule": None,
                    "top_expected_gain": round(
                        float(top_gain_display if top_gain_display is not None else 0.0), 2
                    ),
                    "updated_at": latest.get("created_at") or s.get("created_at"),
                },
            }
        )

    pred_sorted = sorted(
        rows,
        key=lambda x: (
            -float(x["latest_report"]["pred_total_score"])
            if x["latest_report"]["pred_total_score"] is not None
            else float("inf")
        ),
    )
    for idx, row in enumerate(pred_sorted, start=1):
        if row["latest_report"]["pred_total_score"] is not None:
            row["latest_report"]["rank_by_pred"] = idx

    rule_sorted = sorted(rows, key=lambda x: -float(x["latest_report"]["rule_total_score"]))
    for idx, row in enumerate(rule_sorted, start=1):
        row["latest_report"]["rank_by_rule"] = idx

    return ProjectPreScoreListResponse(
        project_id=project_id,
        expert_profile_id=project.get("expert_profile_id"),
        submissions=rows,
    )


def _delete_submission_record(project_id: str, submission_id: str, locale: str) -> None:
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    submissions = load_submissions()
    found = next(
        (
            s
            for s in submissions
            if s.get("id") == submission_id and s.get("project_id") == project_id
        ),
        None,
    )
    if not found:
        raise HTTPException(status_code=404, detail="施组提交记录不存在")
    raw_path = str(found.get("path") or "").strip()
    if raw_path:
        p = Path(raw_path)
        if p.exists():
            p.unlink()
    submissions = [
        s
        for s in submissions
        if not (s.get("id") == submission_id and s.get("project_id") == project_id)
    ]
    save_submissions(submissions)
    snapshots = load_score_reports()
    snapshots = [
        r
        for r in snapshots
        if not (r.get("submission_id") == submission_id and r.get("project_id") == project_id)
    ]
    save_score_reports(snapshots)
    evidence_units = load_evidence_units()
    evidence_units = [u for u in evidence_units if str(u.get("submission_id")) != submission_id]
    save_evidence_units(evidence_units)
    qingtian_results = load_qingtian_results()
    qingtian_results = [q for q in qingtian_results if str(q.get("submission_id")) != submission_id]
    save_qingtian_results(qingtian_results)
    delta_cases = load_delta_cases()
    delta_cases = [d for d in delta_cases if str(d.get("submission_id")) != submission_id]
    save_delta_cases(delta_cases)
    calibration_samples = load_calibration_samples()
    calibration_samples = [
        s for s in calibration_samples if str(s.get("submission_id")) != submission_id
    ]
    save_calibration_samples(calibration_samples)
    # 删除属于显式反馈信号：自动刷新样本并触发校准/调权重闭环（best-effort）。
    try:
        _run_feedback_closed_loop(project_id, locale=locale, trigger="delete_submission")
    except Exception:
        pass


@router.delete(
    "/projects/{project_id}/submissions/{submission_id}",
    tags=["施组提交"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def delete_submission(
    project_id: str,
    submission_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> dict:
    """删除指定项目下的一条施组提交记录。"""
    _delete_submission_record(project_id=project_id, submission_id=submission_id, locale=locale)
    return {"ok": True, "id": submission_id}


@router.delete(
    "/projects/{project_id}/shigong/{file_id}",
    tags=["施组提交"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def delete_shigong_file(
    project_id: str,
    file_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> dict:
    """删除指定项目下的一条施组记录（shigong 别名路径）。"""
    _delete_submission_record(project_id=project_id, submission_id=file_id, locale=locale)
    return {"ok": True, "id": file_id}


@router.get(
    "/submissions/{submission_id}/reports/latest",
    response_model=LatestReportResponse,
    tags=["施组提交"],
    responses={**RESPONSES_404},
)
def get_latest_submission_report(submission_id: str) -> LatestReportResponse:
    """获取某个提交的最新评分报告（含UI摘要）。"""
    ensure_data_dirs()
    reports = [r for r in load_score_reports() if str(r.get("submission_id")) == submission_id]
    reports.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)

    report_obj: Dict[str, object]
    if reports:
        latest = reports[0]
        report_obj = {
            "id": latest.get("id"),
            "submission_id": latest.get("submission_id"),
            "scoring_engine_version": latest.get("scoring_engine_version"),
            "rule_total_score": latest.get("rule_total_score"),
            "pred_total_score": latest.get("pred_total_score"),
            "llm_total_score": latest.get("llm_total_score"),
            "pred_confidence": latest.get("pred_confidence"),
            "score_blend": latest.get("score_blend"),
            "rule_dim_scores": latest.get("rule_dim_scores", {}),
            "pred_dim_scores": latest.get("pred_dim_scores"),
            "penalties": latest.get("penalties", []),
            "lint_findings": latest.get("lint_findings", []),
            "suggestions": latest.get("suggestions", []),
            "expert_profile_snapshot": latest.get("expert_profile_snapshot", {}),
            "created_at": latest.get("created_at"),
        }
    else:
        submissions = load_submissions()
        try:
            submission = _find_submission(submission_id, submissions)
        except HTTPException:
            raise HTTPException(status_code=404, detail="评分报告不存在")
        report_obj = dict(submission.get("report") or {})
        if not report_obj:
            raise HTTPException(status_code=404, detail="评分报告不存在")
        report_obj.setdefault("submission_id", submission_id)
        report_obj.setdefault("rule_total_score", report_obj.get("total_score", 0.0))
        report_obj.setdefault("pred_total_score", report_obj.get("pred_total_score"))
        report_obj.setdefault("llm_total_score", report_obj.get("llm_total_score"))
        report_obj.setdefault("pred_confidence", report_obj.get("pred_confidence"))
        report_obj.setdefault("score_blend", report_obj.get("score_blend"))
        report_obj.setdefault("rule_dim_scores", report_obj.get("rule_dim_scores", {}))
        report_obj.setdefault("penalties", report_obj.get("penalties", []))
        report_obj.setdefault("lint_findings", report_obj.get("lint_findings", []))
        report_obj.setdefault("suggestions", report_obj.get("suggestions", []))

    penalties = report_obj.get("penalties") or []
    lint_findings = report_obj.get("lint_findings") or []
    suggestions = report_obj.get("suggestions") or []

    top_conflicts = [p for p in penalties if str(p.get("code") or "") == "P-CONSIST-001"][:10]
    top_missing_requirements = [
        f for f in lint_findings if str(f.get("issue_code") or "") == "MissingRequirement"
    ][:10]

    ui_summary = {
        "pred_total_score": report_obj.get("pred_total_score"),
        "llm_total_score": report_obj.get("llm_total_score"),
        "pred_confidence": report_obj.get("pred_confidence"),
        "score_blend": report_obj.get("score_blend"),
        "rule_total_score": report_obj.get("rule_total_score", report_obj.get("total_score")),
        "top10_suggestions": suggestions[:10],
        "top_conflicts": top_conflicts,
        "top_missing_requirements": top_missing_requirements,
    }
    return LatestReportResponse(report=report_obj, ui_summary=ui_summary)


@router.post(
    "/submissions/{submission_id}/qingtian-results",
    response_model=QingTianResultRecord,
    tags=["施组提交"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def ingest_qingtian_result(
    submission_id: str,
    payload: QingTianResultCreate,
    api_key: Optional[str] = Depends(verify_api_key),
) -> QingTianResultRecord:
    """写入青天真实评标结果。"""
    ensure_data_dirs()
    submissions = load_submissions()
    submission = _find_submission(submission_id, submissions)
    project_id = str(submission.get("project_id") or "")
    projects = load_projects()
    project = _find_project(project_id, projects)

    model_version = str(
        payload.qingtian_model_version
        or project.get("qingtian_model_version")
        or DEFAULT_QINGTIAN_MODEL_VERSION
    )
    record = {
        "id": str(uuid4()),
        "submission_id": submission_id,
        "qingtian_model_version": model_version,
        "qt_total_score": float(payload.qt_total_score),
        "qt_dim_scores": payload.qt_dim_scores,
        "qt_reasons": payload.qt_reasons,
        "raw_payload": payload.raw_payload,
        "created_at": _now_iso(),
    }
    results = load_qingtian_results()
    results.append(record)
    save_qingtian_results(results)

    if str(project.get("status") or "") == "scoring_preparation":
        project["status"] = "submitted_to_qingtian"
        project["updated_at"] = _now_iso()
        save_projects(projects)

    return QingTianResultRecord(**record)


@router.get(
    "/submissions/{submission_id}/qingtian-results/latest",
    response_model=QingTianResultRecord,
    tags=["施组提交"],
    responses={**RESPONSES_404},
)
def get_latest_qingtian_result(submission_id: str) -> QingTianResultRecord:
    """获取某个提交最新的青天真实评标结果。"""
    ensure_data_dirs()
    results = [r for r in load_qingtian_results() if str(r.get("submission_id")) == submission_id]
    if not results:
        raise HTTPException(status_code=404, detail="暂无青天评标结果")
    latest = sorted(results, key=lambda x: str(x.get("created_at", "")), reverse=True)[0]
    return QingTianResultRecord(**latest)


@router.post(
    "/calibration/train",
    response_model=CalibratorModelRecord,
    tags=["洞察与学习"],
    responses={**RESPONSES_401, **RESPONSES_422},
)
def train_calibrator(
    payload: CalibratorTrainRequest,
    api_key: Optional[str] = Depends(verify_api_key),
) -> CalibratorModelRecord:
    """训练校准器（支持 auto / ridge / offset / linear1d / isotonic1d）。"""
    ensure_data_dirs()
    model_type = str(payload.model_type or "ridge").lower().strip()
    if model_type not in {"auto", "ridge", "offset", "linear1d", "isotonic1d"}:
        raise HTTPException(
            status_code=422, detail="model_type 仅支持 auto/ridge/offset/linear1d/isotonic1d"
        )

    # 优先使用已落库的 FEATURE_ROW 样本
    stored_samples = load_calibration_samples()
    if payload.project_id:
        stored_samples = [
            s for s in stored_samples if str(s.get("project_id")) == payload.project_id
        ]

    feature_rows: List[Dict[str, object]] = []
    for sample in stored_samples:
        feature_rows.append(
            {
                "feature_schema_version": sample.get("feature_schema_version", "v2"),
                "x_features": sample.get("x_features") or {},
                "y_label": sample.get("y_label"),
                "submission_id": sample.get("submission_id"),
            }
        )

    # 若样本不足，在线拼接一次并反写样本表
    if len(feature_rows) < 3:
        submissions = load_submissions()
        if payload.project_id:
            submissions = [s for s in submissions if str(s.get("project_id")) == payload.project_id]
        submission_map = {str(s.get("id")): s for s in submissions}

        reports = load_score_reports()
        if payload.project_id:
            reports = [r for r in reports if str(r.get("project_id")) == payload.project_id]
        latest_reports = _latest_records_by_submission(reports)

        qt_results = load_qingtian_results()
        latest_qt = _latest_records_by_submission(qt_results)

        rebuilt_samples = build_calibration_samples(
            project_id=str(payload.project_id or "__all__"),
            latest_reports_by_submission=latest_reports,
            latest_qingtian_by_submission=latest_qt,
            submissions_by_id=submission_map,
        )
        if rebuilt_samples:
            saved = load_calibration_samples()
            for row in rebuilt_samples:
                sid = str(row.get("submission_id"))
                saved = [x for x in saved if str(x.get("submission_id")) != sid]
                saved.append(row)
            save_calibration_samples(saved)
            feature_rows = [
                {
                    "feature_schema_version": s.get("feature_schema_version", "v2"),
                    "x_features": s.get("x_features") or {},
                    "y_label": s.get("y_label"),
                    "submission_id": s.get("submission_id"),
                }
                for s in rebuilt_samples
            ]

    try:
        if model_type == "auto":
            model_artifact = train_best_calibrator_auto(feature_rows, alpha=float(payload.alpha))
        elif model_type == "offset":
            model_artifact = train_offset_calibrator(feature_rows)
        elif model_type == "linear1d":
            model_artifact = train_linear1d_calibrator(feature_rows, alpha=float(payload.alpha))
        elif model_type == "isotonic1d":
            model_artifact = train_isotonic1d_calibrator(feature_rows)
        else:
            model_artifact = train_ridge_calibrator(feature_rows, alpha=float(payload.alpha))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    selected_type = str(model_artifact.get("model_type") or model_type or "ridge")
    # 为审计保留 auto 来源
    version_prefix = f"calib_{'auto_' if model_type == 'auto' else ''}{selected_type}"

    # 统一用 CV 口径做上线闸门（避免 in-sample 过拟合）
    cv = cross_validate_calibrator(
        model_type=selected_type,
        feature_rows=feature_rows,
        alpha=float(payload.alpha),
        seed=42,
    )
    # baseline: raw rule_total_score
    y_true = [float(r.get("y_label")) for r in feature_rows if r.get("y_label") is not None]
    baseline_pred = [
        max(0.0, min(100.0, float(((r.get("x_features") or {}).get("rule_total_score") or 0.0))))
        for r in feature_rows
        if r.get("y_label") is not None
    ]
    baseline_metrics = calc_metrics(y_true, baseline_pred)
    cv_metrics = (
        (cv.get("metrics") or {})
        if bool(cv.get("ok"))
        else {"mae": 0.0, "rmse": 0.0, "spearman": 0.0}
    )
    improve_threshold = max(0.2, float(baseline_metrics.get("mae") or 0.0) * 0.01)
    spearman_tolerance = 0.02
    gate_passed = (
        bool(cv.get("ok"))
        and float(cv_metrics.get("mae") or 0.0)
        <= float(baseline_metrics.get("mae") or 0.0) - improve_threshold
        and float(cv_metrics.get("spearman") or 0.0)
        >= float(baseline_metrics.get("spearman") or 0.0) - spearman_tolerance
    )
    model_artifact.setdefault("metrics", {})
    model_artifact["metrics"]["cv_mae"] = cv_metrics.get("mae")
    model_artifact["metrics"]["cv_rmse"] = cv_metrics.get("rmse")
    model_artifact["metrics"]["cv_spearman"] = cv_metrics.get("spearman")
    model_artifact["metrics"]["cv_mode"] = cv.get("mode")
    model_artifact["metrics"]["cv_pred_count"] = cv.get("pred_count")
    model_artifact["metrics"]["baseline_mae"] = baseline_metrics.get("mae")
    model_artifact["metrics"]["baseline_rmse"] = baseline_metrics.get("rmse")
    model_artifact["metrics"]["baseline_spearman"] = baseline_metrics.get("spearman")
    model_artifact["metrics"]["gate_improve_threshold"] = round(improve_threshold, 4)
    model_artifact["metrics"]["gate_spearman_tolerance"] = spearman_tolerance
    model_artifact["gate_passed"] = gate_passed

    auto_candidates = _extract_auto_candidates(model_artifact)
    version = f"{version_prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    calibrator_summary = _build_calibrator_summary(
        model_type=selected_type,
        calibrator_version=version,
        gate_passed=bool(gate_passed),
        cv_metrics={
            "mae": cv_metrics.get("mae"),
            "rmse": cv_metrics.get("rmse"),
            "spearman": cv_metrics.get("spearman"),
            "mode": cv.get("mode"),
            "pred_count": cv.get("pred_count"),
        },
        baseline_metrics={
            "mae": baseline_metrics.get("mae"),
            "rmse": baseline_metrics.get("rmse"),
            "spearman": baseline_metrics.get("spearman"),
        },
        improve_threshold=improve_threshold,
        spearman_tolerance=spearman_tolerance,
        auto_candidates=auto_candidates,
        sample_count=len(feature_rows),
    )
    record = {
        "calibrator_version": version,
        "model_type": selected_type,
        "feature_schema_version": str(model_artifact.get("feature_schema_version", "v2")),
        "train_filter": {"project_id": payload.project_id},
        "metrics": {
            **(model_artifact.get("metrics") or {}),
            "gate_passed": bool(gate_passed),
        },
        "calibrator_summary": calibrator_summary,
        "artifact_uri": f"json://calibration_models/{version}",
        "model_artifact": model_artifact,
        "deployed": False,
        "created_at": _now_iso(),
    }

    models = load_calibration_models()
    train_scope_project_id = str(payload.project_id or "").strip()
    if payload.auto_deploy and bool(gate_passed) and train_scope_project_id:
        for m in models:
            if (
                str(((m.get("train_filter") or {}).get("project_id") or ""))
                == train_scope_project_id
            ):
                m["deployed"] = False
        record["deployed"] = True
        projects = load_projects()
        for p in projects:
            if str(p.get("id")) == train_scope_project_id:
                p["calibrator_version_locked"] = version
                p["updated_at"] = _now_iso()
        save_projects(projects)
    models.append(record)
    save_calibration_models(models)
    return CalibratorModelRecord(**record)


@router.get(
    "/calibration/models",
    response_model=list[CalibratorModelRecord],
    tags=["洞察与学习"],
)
def list_calibration_models() -> list[CalibratorModelRecord]:
    """获取校准器版本列表。"""
    ensure_data_dirs()
    models = sorted(
        load_calibration_models(), key=lambda x: str(x.get("created_at", "")), reverse=True
    )
    return [CalibratorModelRecord(**m) for m in models]


@router.post(
    "/calibration/deploy",
    response_model=CalibratorModelRecord,
    tags=["洞察与学习"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def deploy_calibrator(
    payload: CalibratorDeployRequest,
    api_key: Optional[str] = Depends(verify_api_key),
) -> CalibratorModelRecord:
    """部署某个校准器版本。"""
    ensure_data_dirs()
    models = load_calibration_models()
    target = None
    for model in models:
        if str(model.get("calibrator_version")) == payload.calibrator_version:
            target = model
            break
    if target is None:
        raise HTTPException(status_code=404, detail="校准器版本不存在")

    target_scope = str(((target.get("train_filter") or {}).get("project_id") or "")).strip()
    bind_project_id = str(payload.project_id or "").strip()
    if bind_project_id:
        if target_scope and target_scope != bind_project_id:
            raise HTTPException(status_code=422, detail="校准器与目标项目不匹配，禁止跨项目部署")
        if not target_scope:
            target.setdefault("train_filter", {})
            target["train_filter"]["project_id"] = bind_project_id
            target_scope = bind_project_id

    for model in models:
        model_scope = str(((model.get("train_filter") or {}).get("project_id") or "")).strip()
        if target_scope and model_scope == target_scope:
            model["deployed"] = False
    target["deployed"] = True
    save_calibration_models(models)

    if payload.project_id:
        projects = load_projects()
        for project in projects:
            if str(project.get("id")) == payload.project_id:
                project["calibrator_version_locked"] = payload.calibrator_version
                project["updated_at"] = _now_iso()
        save_projects(projects)

    return CalibratorModelRecord(**target)


@router.post(
    "/projects/{project_id}/calibration/predict",
    response_model=CalibratorPredictResponse,
    tags=["洞察与学习"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def apply_calibration_prediction(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> CalibratorPredictResponse:
    """将已部署校准器应用到项目已有评分报告。"""
    ensure_data_dirs()
    projects = load_projects()
    try:
        project = _find_project(project_id, projects)
    except HTTPException:
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    model = _select_calibrator_model(project)
    if not model:
        return CalibratorPredictResponse(
            ok=True,
            project_id=project_id,
            model_version=None,
            updated_reports=0,
            updated_submissions=0,
        )

    submissions = load_submissions()
    submission_map = {
        str(s.get("id")): s for s in submissions if str(s.get("project_id")) == project_id
    }

    reports = load_score_reports()
    updated_reports = 0
    for report in reports:
        if str(report.get("project_id")) != project_id:
            continue
        sid = str(report.get("submission_id") or "")
        sub = submission_map.get(sid)
        if not sid or not sub:
            continue
        _apply_prediction_to_report(report, submission_like=sub, project=project)
        updated_reports += 1
    save_score_reports(reports)

    updated_submissions = 0
    for submission in submissions:
        if str(submission.get("project_id")) != project_id:
            continue
        report = submission.get("report")
        if not isinstance(report, dict):
            continue
        _apply_prediction_to_report(report, submission_like=submission, project=project)
        updated_submissions += 1
    save_submissions(submissions)

    return CalibratorPredictResponse(
        ok=True,
        project_id=project_id,
        model_version=str(model.get("calibrator_version") or ""),
        updated_reports=updated_reports,
        updated_submissions=updated_submissions,
    )


@router.post(
    "/projects/{project_id}/delta_cases/rebuild",
    response_model=list[DeltaCaseRecord],
    tags=["洞察与学习"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def rebuild_delta_cases(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> list[DeltaCaseRecord]:
    """重建项目 DELTA_CASE（反演误差案例）。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(str(p.get("id")) == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    reports = [r for r in load_score_reports() if str(r.get("project_id")) == project_id]
    latest_reports = _latest_records_by_submission(reports)
    qtrs = load_qingtian_results()
    latest_qt = _latest_records_by_submission(
        [q for q in qtrs if str(q.get("submission_id")) in latest_reports]
    )

    new_cases = build_delta_cases(
        project_id=project_id,
        latest_reports_by_submission=latest_reports,
        latest_qingtian_by_submission=latest_qt,
    )
    all_cases = [d for d in load_delta_cases() if str(d.get("project_id")) != project_id]
    all_cases.extend(new_cases)
    save_delta_cases(all_cases)
    return [DeltaCaseRecord(**d) for d in new_cases]


@router.get(
    "/projects/{project_id}/delta_cases",
    response_model=list[DeltaCaseRecord],
    tags=["洞察与学习"],
    responses={**RESPONSES_404},
)
def list_delta_cases(
    project_id: str,
    locale: str = Depends(get_locale),
) -> list[DeltaCaseRecord]:
    """查询项目 DELTA_CASE。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(str(p.get("id")) == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    rows = [d for d in load_delta_cases() if str(d.get("project_id")) == project_id]
    rows = sorted(rows, key=lambda x: str(x.get("created_at", "")), reverse=True)
    return [DeltaCaseRecord(**d) for d in rows]


@router.post(
    "/projects/{project_id}/calibration_samples/rebuild",
    response_model=list[CalibrationSampleRecord],
    tags=["洞察与学习"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def rebuild_calibration_samples(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> list[CalibrationSampleRecord]:
    """重建项目 FEATURE_ROW 校准样本。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(str(p.get("id")) == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    submissions = [s for s in load_submissions() if str(s.get("project_id")) == project_id]
    submissions_by_id = {str(s.get("id")): s for s in submissions}
    latest_reports = _latest_records_by_submission(
        [r for r in load_score_reports() if str(r.get("project_id")) == project_id]
    )
    latest_qt = _latest_records_by_submission(
        [q for q in load_qingtian_results() if str(q.get("submission_id")) in submissions_by_id]
    )

    samples = build_calibration_samples(
        project_id=project_id,
        latest_reports_by_submission=latest_reports,
        latest_qingtian_by_submission=latest_qt,
        submissions_by_id=submissions_by_id,
    )
    all_samples = [s for s in load_calibration_samples() if str(s.get("project_id")) != project_id]
    all_samples.extend(samples)
    save_calibration_samples(all_samples)
    return [CalibrationSampleRecord(**s) for s in samples]


@router.get(
    "/projects/{project_id}/calibration_samples",
    response_model=list[CalibrationSampleRecord],
    tags=["洞察与学习"],
    responses={**RESPONSES_404},
)
def list_calibration_samples(
    project_id: str,
    locale: str = Depends(get_locale),
) -> list[CalibrationSampleRecord]:
    """查询项目 FEATURE_ROW 样本。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(str(p.get("id")) == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    rows = [s for s in load_calibration_samples() if str(s.get("project_id")) == project_id]
    rows = sorted(rows, key=lambda x: str(x.get("created_at", "")), reverse=True)
    return [CalibrationSampleRecord(**s) for s in rows]


@router.post(
    "/projects/{project_id}/patches/mine",
    response_model=PatchPackageRecord,
    tags=["洞察与学习"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def mine_patch(
    project_id: str,
    payload: PatchMineRequest,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> PatchPackageRecord:
    """基于 DELTA_CASE 挖掘候选补丁包（PATCH_PACKAGE）。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(str(p.get("id")) == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    delta_cases = [d for d in load_delta_cases() if str(d.get("project_id")) == project_id]
    if not delta_cases:
        raise HTTPException(status_code=404, detail="暂无 DELTA_CASE，请先重建")

    packages = load_patch_packages()
    rollback_pointer = None
    deployed = [
        p
        for p in packages
        if str(p.get("project_id")) == project_id and str(p.get("status")) == "deployed"
    ]
    if deployed:
        deployed = sorted(deployed, key=lambda x: str(x.get("updated_at", "")), reverse=True)
        rollback_pointer = str(deployed[0].get("id") or "")

    package = mine_patch_package(
        project_id=project_id,
        delta_cases=delta_cases,
        patch_type=payload.patch_type,
        top_k=int(payload.top_k),
        rollback_pointer=rollback_pointer,
    )
    packages.append(package)
    save_patch_packages(packages)
    return PatchPackageRecord(**package)


@router.get(
    "/projects/{project_id}/patches",
    response_model=list[PatchPackageRecord],
    tags=["洞察与学习"],
    responses={**RESPONSES_404},
)
def list_patches(
    project_id: str,
    locale: str = Depends(get_locale),
) -> list[PatchPackageRecord]:
    """查询项目补丁包列表。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(str(p.get("id")) == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    rows = [p for p in load_patch_packages() if str(p.get("project_id")) == project_id]
    rows = sorted(rows, key=lambda x: str(x.get("updated_at", "")), reverse=True)
    return [PatchPackageRecord(**p) for p in rows]


@router.post(
    "/patches/{patch_id}/shadow_eval",
    response_model=PatchShadowEvalResponse,
    tags=["洞察与学习"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def shadow_eval_patch(
    patch_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
) -> PatchShadowEvalResponse:
    """对候选补丁做 shadow 评估并更新状态。"""
    ensure_data_dirs()
    packages = load_patch_packages()
    patch = next((p for p in packages if str(p.get("id")) == patch_id), None)
    if patch is None:
        raise HTTPException(status_code=404, detail="补丁包不存在")
    project_id = str(patch.get("project_id") or "")
    delta_cases = [d for d in load_delta_cases() if str(d.get("project_id")) == project_id]

    result = evaluate_patch_shadow(patch=patch, delta_cases=delta_cases)
    patch["shadow_metrics"] = result.get("metrics_before_after", {})
    patch["status"] = "shadow_pass" if bool(result.get("gate_passed")) else "candidate"
    patch["updated_at"] = _now_iso()
    save_patch_packages(packages)
    return PatchShadowEvalResponse(**result)


@router.post(
    "/patches/{patch_id}/deploy",
    response_model=PatchDeploymentRecord,
    tags=["洞察与学习"],
    responses={**RESPONSES_401, **RESPONSES_404, **RESPONSES_422},
)
def deploy_or_rollback_patch(
    patch_id: str,
    payload: PatchDeployRequest,
    api_key: Optional[str] = Depends(verify_api_key),
) -> PatchDeploymentRecord:
    """发布或回滚补丁。"""
    ensure_data_dirs()
    packages = load_patch_packages()
    patch = next((p for p in packages if str(p.get("id")) == patch_id), None)
    if patch is None:
        raise HTTPException(status_code=404, detail="补丁包不存在")

    action = str(payload.action or "deploy").lower()
    if action not in {"deploy", "rollback"}:
        raise HTTPException(status_code=422, detail="action 仅支持 deploy 或 rollback")

    project_id = str(patch.get("project_id") or "")
    deployed = action == "deploy"
    if deployed:
        for p in packages:
            if str(p.get("project_id")) == project_id and str(p.get("status")) == "deployed":
                p["status"] = "shadow_pass"
                p["updated_at"] = _now_iso()
        patch["status"] = "deployed"
    else:
        patch["status"] = "rolled_back"
    patch["updated_at"] = _now_iso()
    save_patch_packages(packages)

    rec = {
        "id": str(uuid4()),
        "patch_id": patch_id,
        "project_id": project_id,
        "action": action,
        "deployed": deployed,
        "metrics_before_after": patch.get("shadow_metrics") or {},
        "rollback_to_version": payload.rollback_to_version or patch.get("rollback_pointer"),
        "created_at": _now_iso(),
    }
    deploys = load_patch_deployments()
    deploys.append(rec)
    save_patch_deployments(deploys)
    return PatchDeploymentRecord(**rec)


@router.post(
    "/projects/{project_id}/reflection/auto_run",
    response_model=ReflectionAutoRunResponse,
    tags=["洞察与学习"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def auto_run_reflection_pipeline(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> ReflectionAutoRunResponse:
    """
    一键执行反演闭环：
    1) 刷新 DELTA_CASE + FEATURE_ROW
    2) 训练并自动部署校准器（闸门通过）
    3) 回填预测分
    4) 挖掘/影子评估/发布补丁（闸门通过）
    """
    ensure_data_dirs()
    projects = load_projects()
    project = None
    for p in projects:
        if str(p.get("id")) == project_id:
            project = p
            break
    if project is None:
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    _refresh_project_reflection_objects(project_id)
    delta_cases = [d for d in load_delta_cases() if str(d.get("project_id")) == project_id]
    samples = [s for s in load_calibration_samples() if str(s.get("project_id")) == project_id]

    calibrator_version = None
    calibrator_deployed = False
    calibrator_summary = _build_calibrator_summary(
        model_type=None,
        calibrator_version=None,
        gate_passed=None,
        sample_count=len(samples),
        skipped_reason="insufficient_samples" if len(samples) < 3 else None,
    )
    calibrator_model_type = calibrator_summary.get("model_type")
    calibrator_gate_passed = calibrator_summary.get("gate_passed")
    calibrator_cv_metrics: Dict[str, Any] = calibrator_summary.get("cv_metrics") or {}
    calibrator_baseline_metrics: Dict[str, Any] = calibrator_summary.get("baseline_metrics") or {}
    calibrator_gate: Dict[str, Any] = calibrator_summary.get("gate") or {}
    calibrator_auto_candidates: List[Dict[str, Any]] = (
        calibrator_summary.get("auto_candidates") or []
    )
    if len(samples) >= 3:
        feature_rows = [
            {
                "feature_schema_version": s.get("feature_schema_version", "v2"),
                "x_features": s.get("x_features") or {},
                "y_label": s.get("y_label"),
                "submission_id": s.get("submission_id"),
            }
            for s in samples
        ]
        # “最强自动校准”：多候选 + CV 闸门 + 自动选择最佳模型
        model_artifact = train_best_calibrator_auto(feature_rows, alpha=1.0)
        selected_type = str(model_artifact.get("model_type") or "ridge")
        calibrator_model_type = selected_type

        # 统一用 CV 口径做上线闸门（避免 in-sample 过拟合）
        cv = cross_validate_calibrator(
            model_type=selected_type,
            feature_rows=feature_rows,
            alpha=1.0,
            seed=42,
        )
        # baseline: raw rule_total_score
        y_true = [float(r.get("y_label")) for r in feature_rows if r.get("y_label") is not None]
        baseline_pred = [
            float(((r.get("x_features") or {}).get("rule_total_score") or 0.0))
            for r in feature_rows
            if r.get("y_label") is not None
        ]
        baseline_metrics = calc_metrics(y_true, baseline_pred)
        cv_metrics = (
            (cv.get("metrics") or {})
            if bool(cv.get("ok"))
            else {"mae": 0.0, "rmse": 0.0, "spearman": 0.0}
        )
        improve_threshold = max(0.2, float(baseline_metrics.get("mae") or 0.0) * 0.01)
        spearman_tolerance = 0.02
        gate_passed = (
            bool(cv.get("ok"))
            and float(cv_metrics.get("mae") or 0.0)
            <= float(baseline_metrics.get("mae") or 0.0) - improve_threshold
            and float(cv_metrics.get("spearman") or 0.0)
            >= float(baseline_metrics.get("spearman") or 0.0) - spearman_tolerance
        )
        model_artifact.setdefault("metrics", {})
        model_artifact["metrics"]["cv_mae"] = cv_metrics.get("mae")
        model_artifact["metrics"]["cv_rmse"] = cv_metrics.get("rmse")
        model_artifact["metrics"]["cv_spearman"] = cv_metrics.get("spearman")
        model_artifact["metrics"]["cv_mode"] = cv.get("mode")
        model_artifact["metrics"]["cv_pred_count"] = cv.get("pred_count")
        model_artifact["metrics"]["baseline_mae"] = baseline_metrics.get("mae")
        model_artifact["metrics"]["baseline_rmse"] = baseline_metrics.get("rmse")
        model_artifact["metrics"]["baseline_spearman"] = baseline_metrics.get("spearman")
        model_artifact["metrics"]["gate_improve_threshold"] = round(improve_threshold, 4)
        model_artifact["metrics"]["gate_spearman_tolerance"] = spearman_tolerance
        model_artifact["gate_passed"] = gate_passed

        auto_candidates = _extract_auto_candidates(model_artifact)
        calibrator_version = (
            f"calib_auto_{selected_type}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        )
        calibrator_summary = _build_calibrator_summary(
            model_type=selected_type,
            calibrator_version=calibrator_version,
            gate_passed=bool(gate_passed),
            cv_metrics={
                "mae": cv_metrics.get("mae"),
                "rmse": cv_metrics.get("rmse"),
                "spearman": cv_metrics.get("spearman"),
                "mode": cv.get("mode"),
                "pred_count": cv.get("pred_count"),
            },
            baseline_metrics={
                "mae": baseline_metrics.get("mae"),
                "rmse": baseline_metrics.get("rmse"),
                "spearman": baseline_metrics.get("spearman"),
            },
            improve_threshold=improve_threshold,
            spearman_tolerance=spearman_tolerance,
            auto_candidates=auto_candidates,
            sample_count=len(feature_rows),
        )
        calibrator_model_type = calibrator_summary.get("model_type")
        calibrator_gate_passed = calibrator_summary.get("gate_passed")
        calibrator_cv_metrics = calibrator_summary.get("cv_metrics") or {}
        calibrator_baseline_metrics = calibrator_summary.get("baseline_metrics") or {}
        calibrator_gate = calibrator_summary.get("gate") or {}
        calibrator_auto_candidates = calibrator_summary.get("auto_candidates") or []
        record = {
            "calibrator_version": calibrator_version,
            "model_type": selected_type,
            "feature_schema_version": str(model_artifact.get("feature_schema_version", "v2")),
            "train_filter": {"project_id": project_id, "mode": "auto_run"},
            "metrics": {
                **(model_artifact.get("metrics") or {}),
                "gate_passed": bool(gate_passed),
            },
            "calibrator_summary": calibrator_summary,
            "artifact_uri": f"json://calibration_models/{calibrator_version}",
            "model_artifact": model_artifact,
            "deployed": bool(gate_passed),
            "created_at": _now_iso(),
        }
        models = load_calibration_models()
        if record["deployed"]:
            for m in models:
                if str(((m.get("train_filter") or {}).get("project_id") or "")) == project_id:
                    m["deployed"] = False
            project["calibrator_version_locked"] = calibrator_version
            project["updated_at"] = _now_iso()
            save_projects(projects)
            calibrator_deployed = True
        models.append(record)
        save_calibration_models(models)

    updated_reports = 0
    updated_submissions = 0
    if calibrator_deployed:
        submissions = load_submissions()
        submission_map = {
            str(s.get("id")): s for s in submissions if str(s.get("project_id")) == project_id
        }
        reports = load_score_reports()
        for report in reports:
            if str(report.get("project_id")) != project_id:
                continue
            sid = str(report.get("submission_id") or "")
            sub = submission_map.get(sid)
            if not sub:
                continue
            _apply_prediction_to_report(report, submission_like=sub, project=project)
            updated_reports += 1
        save_score_reports(reports)

        for sub in submissions:
            if str(sub.get("project_id")) != project_id:
                continue
            rep = sub.get("report")
            if not isinstance(rep, dict):
                continue
            _apply_prediction_to_report(rep, submission_like=sub, project=project)
            updated_submissions += 1
        save_submissions(submissions)

    patch_id = None
    patch_gate_passed = None
    patch_deployed = False
    if delta_cases:
        packages = load_patch_packages()
        deployed = [
            p
            for p in packages
            if str(p.get("project_id")) == project_id and str(p.get("status")) == "deployed"
        ]
        rollback_pointer = str(deployed[0].get("id")) if deployed else None
        patch = mine_patch_package(
            project_id=project_id,
            delta_cases=delta_cases,
            patch_type="threshold",
            top_k=5,
            rollback_pointer=rollback_pointer,
        )
        patch_id = str(patch.get("id"))
        shadow = evaluate_patch_shadow(patch=patch, delta_cases=delta_cases)
        patch_gate_passed = bool(shadow.get("gate_passed"))
        patch["shadow_metrics"] = shadow.get("metrics_before_after", {})
        patch["status"] = "shadow_pass" if patch_gate_passed else "candidate"
        patch["updated_at"] = _now_iso()

        if patch_gate_passed:
            for p in packages:
                if str(p.get("project_id")) == project_id and str(p.get("status")) == "deployed":
                    p["status"] = "shadow_pass"
            patch["status"] = "deployed"
            patch_deployed = True
            deploy_rec = {
                "id": str(uuid4()),
                "patch_id": patch_id,
                "project_id": project_id,
                "action": "deploy",
                "deployed": True,
                "metrics_before_after": patch.get("shadow_metrics") or {},
                "rollback_to_version": patch.get("rollback_pointer"),
                "created_at": _now_iso(),
            }
            deploys = load_patch_deployments()
            deploys.append(deploy_rec)
            save_patch_deployments(deploys)

        packages.append(patch)
        save_patch_packages(packages)

    return ReflectionAutoRunResponse(
        ok=True,
        project_id=project_id,
        delta_cases=len(delta_cases),
        calibration_samples=len(samples),
        calibrator_version=calibrator_version,
        calibrator_deployed=calibrator_deployed,
        calibrator_summary=calibrator_summary,
        calibrator_model_type=calibrator_model_type,
        calibrator_gate_passed=calibrator_gate_passed,
        calibrator_cv_metrics=calibrator_cv_metrics,
        calibrator_baseline_metrics=calibrator_baseline_metrics,
        calibrator_gate=calibrator_gate,
        calibrator_auto_candidates=calibrator_auto_candidates,
        prediction_updated_reports=updated_reports,
        prediction_updated_submissions=updated_submissions,
        patch_id=patch_id,
        patch_gate_passed=patch_gate_passed,
        patch_deployed=patch_deployed,
    )


@router.get(
    "/projects/{project_id}/evaluation",
    response_model=ProjectEvaluationResponse,
    tags=["洞察与学习"],
    responses={**RESPONSES_404},
)
def get_project_evaluation(
    project_id: str,
    locale: str = Depends(get_locale),
) -> ProjectEvaluationResponse:
    """输出项目级 V1/V2/V2+Calib 对比指标。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(str(p.get("id")) == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    data = evaluate_project_variants(
        project_id=project_id,
        submissions=load_submissions(),
        score_reports=load_score_reports(),
        qingtian_results=load_qingtian_results(),
    )
    return ProjectEvaluationResponse(**data)


@router.get(
    "/evaluation/summary",
    response_model=EvaluationSummaryResponse,
    tags=["洞察与学习"],
)
def get_evaluation_summary() -> EvaluationSummaryResponse:
    """跨项目汇总 V1/V2/V2+Calib 验收指标。"""
    ensure_data_dirs()
    projects = load_projects()
    submissions = load_submissions()
    reports = load_score_reports()
    qts = load_qingtian_results()

    project_ids = [str(p.get("id")) for p in projects if str(p.get("id") or "")]
    project_metrics = []
    for pid in project_ids:
        project_metrics.append(
            evaluate_project_variants(
                project_id=pid,
                submissions=submissions,
                score_reports=reports,
                qingtian_results=qts,
            )
        )

    def _avg(values: List[float]) -> float:
        if not values:
            return 0.0
        return round(sum(values) / len(values), 4)

    agg: Dict[str, Dict[str, Any]] = {}
    for variant in ("v1", "v2", "v2_calib"):
        maes, rmses, cors = [], [], []
        profile_sims, hit_rates, sample_counts = [], [], []
        for item in project_metrics:
            v = (item.get("variants") or {}).get(variant) or {}
            if int(v.get("sample_count") or 0) <= 0:
                continue
            maes.append(float(v.get("mae") or 0.0))
            rmses.append(float(v.get("rmse") or 0.0))
            cors.append(float(v.get("spearman") or 0.0))
            sample_counts.append(int(v.get("sample_count") or 0))
            if v.get("profile_similarity") is not None:
                profile_sims.append(float(v.get("profile_similarity")))
            if v.get("penalty_hit_rate") is not None:
                hit_rates.append(float(v.get("penalty_hit_rate")))
        agg[variant] = {
            "project_count": len(sample_counts),
            "sample_count_total": sum(sample_counts),
            "mae_avg": _avg(maes),
            "rmse_avg": _avg(rmses),
            "spearman_avg": _avg(cors),
            "profile_similarity_avg": _avg(profile_sims) if profile_sims else None,
            "penalty_hit_rate_avg": _avg(hit_rates) if hit_rates else None,
        }

    pass_count = {
        "mae_rmse_improved_vs_v1": 0,
        "rank_corr_not_worse_vs_v1": 0,
        "profile_similarity_improved_v2_vs_v1": 0,
        "penalty_hit_rate_improved_v2_vs_v1": 0,
    }
    for item in project_metrics:
        acc = item.get("acceptance") or {}
        for key in pass_count:
            if bool(acc.get(key)):
                pass_count[key] += 1

    return EvaluationSummaryResponse(
        project_count=len(project_ids),
        project_ids=project_ids,
        aggregate=agg,
        acceptance_pass_count=pass_count,
        computed_at=_now_iso(),
    )


@router.get(
    "/projects/{project_id}/compare",
    response_model=CompareReport,
    tags=["对比分析"],
    responses=RESPONSES_NO_SUBMISSIONS,
)
def compare_submissions(
    project_id: str,
    locale: str = Depends(get_locale),
) -> CompareReport:
    """
    对比项目的多次施组提交。

    返回排名、各维度平均分、常见扣分项统计等对比分析数据。

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    projects = load_projects()
    project = next((p for p in projects if str(p.get("id")) == project_id), {"id": project_id})
    allow_pred_score = _select_calibrator_model(project) is not None
    score_scale_max = _resolve_project_score_scale_max(project)
    submissions_all = [s for s in load_submissions() if s["project_id"] == project_id]
    if not submissions_all:
        raise HTTPException(status_code=404, detail=t("api.no_submissions", locale=locale))
    submissions = [s for s in submissions_all if _submission_is_scored(s)]
    if not submissions:
        raise HTTPException(status_code=404, detail="暂无已评分施组，请先点击“评分施组”。")
    rankings = []
    for s in submissions:
        score_fields = _resolve_submission_score_fields(
            s,
            allow_pred_score=allow_pred_score,
            score_scale_max=score_scale_max,
        )
        rankings.append(
            {
                "submission_id": s["id"],
                "filename": s["filename"],
                "total_score": score_fields["total_score"],
                "pred_total_score": score_fields["pred_total_score"],
                "rule_total_score": score_fields["rule_total_score"],
                "score_source": score_fields["score_source"],
                "created_at": s["created_at"],
            }
        )
    rankings = sorted(rankings, key=lambda x: float(x["total_score"]), reverse=True)
    dimension_totals: dict[str, float] = {}
    dimension_counts: dict[str, int] = {}
    penalty_stats: dict[str, int] = {}
    for s in submissions:
        report = s.get("report") if isinstance(s.get("report"), dict) else {}
        for dim_id, dim in (report.get("dimension_scores") or {}).items():
            dimension_totals[dim_id] = dimension_totals.get(dim_id, 0.0) + float(
                dim.get("score", 0.0)
            )
            dimension_counts[dim_id] = dimension_counts.get(dim_id, 0) + 1
        for p in report.get("penalties") or []:
            if not isinstance(p, dict):
                continue
            code = p.get("code", "UNKNOWN")
            penalty_stats[code] = penalty_stats.get(code, 0) + 1

    dimension_avg = {
        dim_id: round(dimension_totals[dim_id] / dimension_counts[dim_id], 2)
        for dim_id in dimension_totals
    }
    return CompareReport(
        project_id=project_id,
        rankings=rankings,
        dimension_avg=dimension_avg,
        penalty_stats=penalty_stats,
    )


@router.get(
    "/projects/{project_id}/compare_report",
    response_model=CompareNarrative,
    tags=["对比分析"],
    responses=RESPONSES_NO_SUBMISSIONS,
)
def compare_report(
    project_id: str,
    locale: str = Depends(get_locale),
) -> CompareNarrative:
    """
    生成对比分析叙述报告。

    返回自然语言格式的对比分析报告，包含趋势分析和改进建议。

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    projects = load_projects()
    project = next((p for p in projects if str(p.get("id")) == project_id), {"id": project_id})
    allow_pred_score = _select_calibrator_model(project) is not None
    score_scale_max = _resolve_project_score_scale_max(project)
    submissions_all = [s for s in load_submissions() if s["project_id"] == project_id]
    if not submissions_all:
        raise HTTPException(status_code=404, detail=t("api.no_submissions", locale=locale))
    submissions = [s for s in submissions_all if _submission_is_scored(s)]
    if not submissions:
        raise HTTPException(status_code=404, detail="暂无已评分施组，请先点击“评分施组”。")
    submissions_for_compare = []
    by_id: Dict[str, Dict[str, object]] = {}
    for s in submissions:
        score_fields_display = _resolve_submission_score_fields(
            s,
            allow_pred_score=allow_pred_score,
            score_scale_max=score_scale_max,
        )
        score_fields_raw = _resolve_submission_score_fields(
            s,
            allow_pred_score=allow_pred_score,
            score_scale_max=100,
        )
        item = dict(s)
        item["total_score"] = float(score_fields_raw["total_score"])
        report = item.get("report")
        report = dict(report) if isinstance(report, dict) else {}
        report["pred_total_score"] = score_fields_raw["pred_total_score"]
        report["rule_total_score"] = score_fields_raw["rule_total_score"]
        item["report"] = report
        item["score_source"] = score_fields_display["score_source"]
        submissions_for_compare.append(item)
        by_id[str(item.get("id") or "")] = item

    narrative = build_compare_narrative(submissions_for_compare)
    for key in ("top_submission", "bottom_submission"):
        row = narrative.get(key)
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "")
        source_submission = by_id.get(sid)
        if not source_submission:
            continue
        score_fields = _resolve_submission_score_fields(
            source_submission,
            allow_pred_score=allow_pred_score,
            score_scale_max=score_scale_max,
        )
        row["pred_total_score"] = score_fields["pred_total_score"]
        row["rule_total_score"] = score_fields["rule_total_score"]
        row["score_source"] = score_fields["score_source"]
    return CompareNarrative(project_id=project_id, **narrative)


@router.get(
    "/projects/{project_id}/adaptive",
    response_model=AdaptiveSuggestions,
    tags=["自适应优化"],
    responses=RESPONSES_NO_SUBMISSIONS,
)
def adaptive_suggestions(
    project_id: str,
    locale: str = Depends(get_locale),
) -> AdaptiveSuggestions:
    """
    获取自适应优化建议。

    基于项目历史评分数据，分析常见扣分模式，提供评分规则优化建议。

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    submissions_all = [s for s in load_submissions() if s["project_id"] == project_id]
    if not submissions_all:
        raise HTTPException(status_code=404, detail=t("api.no_submissions", locale=locale))
    submissions = [s for s in submissions_all if _submission_is_scored(s)]
    if not submissions:
        raise HTTPException(status_code=404, detail="暂无已评分施组，请先点击“评分施组”。")
    config = load_config()
    result = build_adaptive_suggestions(submissions, config.lexicon)
    return AdaptiveSuggestions(project_id=project_id, **result)


@router.get(
    "/projects/{project_id}/adaptive_patch",
    response_model=AdaptivePatch,
    tags=["自适应优化"],
    responses=RESPONSES_NO_SUBMISSIONS,
)
def adaptive_patch(
    project_id: str,
    locale: str = Depends(get_locale),
) -> AdaptivePatch:
    """
    获取自适应优化补丁。

    根据扣分统计生成词库调整补丁，包含建议的阈值修改。

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    submissions = [s for s in load_submissions() if s["project_id"] == project_id]
    if not submissions:
        raise HTTPException(status_code=404, detail=t("api.no_submissions", locale=locale))
    config = load_config()
    stats_result = build_adaptive_suggestions(submissions, config.lexicon)
    stats = stats_result["penalty_stats"]
    patch = build_adaptive_patch(config.lexicon, stats)
    return AdaptivePatch(project_id=project_id, source=stats_result.get("source") or {}, **patch)


@router.post(
    "/projects/{project_id}/adaptive_apply",
    response_model=AdaptiveApplyResult,
    tags=["自适应优化"],
    responses={**RESPONSES_401, **RESPONSES_NO_SUBMISSIONS},
)
def adaptive_apply(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> AdaptiveApplyResult:
    """
    应用自适应优化补丁。

    将优化补丁应用到词库与评分规则配置，自动备份原配置文件。

    **需要 API Key 认证**

    ⚠️ 此操作会修改系统配置文件（lexicon.yaml、rubric.yaml）

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    submissions = [s for s in load_submissions() if s["project_id"] == project_id]
    if not submissions:
        raise HTTPException(status_code=404, detail=t("api.no_submissions", locale=locale))
    config = load_config()
    stats_result = build_adaptive_suggestions(submissions, config.lexicon)
    stats = stats_result["penalty_stats"]
    patch = build_adaptive_patch(config.lexicon, stats)

    from pathlib import Path

    import yaml

    res_dir = Path(__file__).resolve().parent / "resources"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    all_changes: list[str] = []

    # 词库补丁：备份并写回
    lexicon_path = res_dir / "lexicon.yaml"
    lex_backup = lexicon_path.with_name(f"lexicon.yaml.bak_{ts}")
    lex_backup.write_text(lexicon_path.read_text(encoding="utf-8"), encoding="utf-8")
    updated_lexicon, lex_changes = apply_adaptive_patch(config.lexicon, patch)
    lexicon_path.write_text(yaml.safe_dump(updated_lexicon, allow_unicode=True), encoding="utf-8")
    all_changes.extend(lex_changes)

    # 规则补丁：备份并写回
    rubric_path = res_dir / "rubric.yaml"
    rubric_backup = rubric_path.with_name(f"rubric.yaml.bak_{ts}")
    rubric_backup.write_text(rubric_path.read_text(encoding="utf-8"), encoding="utf-8")
    updated_rubric, rubric_changes = apply_rubric_patch(
        config.rubric, patch.get("rubric_adjustments", {})
    )
    rubric_path.write_text(yaml.safe_dump(updated_rubric, allow_unicode=True), encoding="utf-8")
    all_changes.extend(rubric_changes)

    # 使内存中的配置失效，下次 load_config 会重新读盘
    reload_config()

    return AdaptiveApplyResult(
        project_id=project_id,
        applied=True,
        changes=all_changes,
        backup_path=str(lex_backup),
        source=stats_result.get("source") or {},
    )


@router.get(
    "/projects/{project_id}/adaptive_validate",
    response_model=AdaptiveValidation,
    tags=["自适应优化"],
    responses=RESPONSES_NO_SUBMISSIONS,
)
def adaptive_validate(
    project_id: str,
    locale: str = Depends(get_locale),
) -> AdaptiveValidation:
    """
    验证自适应优化效果。

    使用当前配置重新评分历史提交，对比新旧分数差异，验证优化效果。

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    submissions = [s for s in load_submissions() if s["project_id"] == project_id]
    if not submissions:
        raise HTTPException(status_code=404, detail=t("api.no_submissions", locale=locale))
    config = load_config()
    try:
        multipliers, _, _ = _resolve_project_scoring_context(project_id)
    except HTTPException as exc:
        # 兼容历史/清理场景：项目记录缺失时，仍允许按默认权重验证当前施组数据。
        if exc.status_code == 404:
            multipliers = {}
        else:
            raise
    comparisons = []
    deltas = []
    for s in submissions:
        text = s.get("text")
        if not text:
            continue
        new_report = score_text(
            text,
            config.rubric,
            config.lexicon,
            dimension_multipliers=multipliers,
        ).model_dump()
        old_score = float(s.get("total_score", 0.0))
        new_score = float(new_report.get("total_score", 0.0))
        delta = round(new_score - old_score, 2)
        comparisons.append(
            {
                "submission_id": s.get("id"),
                "filename": s.get("filename"),
                "old_score": old_score,
                "new_score": new_score,
                "delta": delta,
            }
        )
        deltas.append(delta)
    avg_delta = round(sum(deltas) / len(deltas), 2) if deltas else 0.0
    return AdaptiveValidation(project_id=project_id, avg_delta=avg_delta, comparisons=comparisons)


@router.get(
    "/projects/{project_id}/insights",
    response_model=InsightsReport,
    tags=["洞察与学习"],
    responses=RESPONSES_NO_SUBMISSIONS,
)
def project_insights(
    project_id: str,
    locale: str = Depends(get_locale),
) -> InsightsReport:
    """
    获取项目洞察报告。

    分析项目历史评分数据，识别强项、弱项和改进机会。

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    submissions_all = [s for s in load_submissions() if s["project_id"] == project_id]
    if not submissions_all:
        raise HTTPException(status_code=404, detail=t("api.no_submissions", locale=locale))
    submissions = [s for s in submissions_all if _submission_is_scored(s)]
    if not submissions:
        raise HTTPException(status_code=404, detail="暂无已评分施组，请先点击“评分施组”。")
    insights = build_project_insights(submissions)
    return InsightsReport(project_id=project_id, **insights)


@router.post(
    "/projects/{project_id}/learning",
    response_model=LearningProfile,
    tags=["洞察与学习"],
    responses={**RESPONSES_401, **RESPONSES_NO_SUBMISSIONS},
)
def update_learning_profile(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> LearningProfile:
    """
    生成/更新项目学习画像。

    基于历史评分模式，生成项目特定的维度权重调整系数。
    后续评分将应用这些调整以提供更精准的评估。

    **需要 API Key 认证**

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    submissions_all = [s for s in load_submissions() if s["project_id"] == project_id]
    if not submissions_all:
        raise HTTPException(status_code=404, detail=t("api.no_submissions", locale=locale))
    submissions = [s for s in submissions_all if _submission_is_scored(s)]
    if not submissions:
        raise HTTPException(status_code=404, detail="暂无已评分施组，请先点击“评分施组”。")
    profile = build_learning_profile(submissions)
    profiles = load_learning_profiles()
    record = {
        "project_id": project_id,
        "dimension_multipliers": profile["dimension_multipliers"],
        "rationale": profile["rationale"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    profiles = [p for p in profiles if p.get("project_id") != project_id]
    profiles.append(record)
    save_learning_profiles(profiles)
    return LearningProfile(**record)


@router.get(
    "/projects/{project_id}/learning",
    response_model=LearningProfile,
    tags=["洞察与学习"],
    responses=RESPONSES_NO_PROFILE,
)
def get_learning_profile(
    project_id: str,
    locale: str = Depends(get_locale),
) -> LearningProfile:
    """
    获取项目学习画像。

    返回项目已保存的学习画像，包含维度权重调整系数和分析依据。

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    profiles = load_learning_profiles()
    for p in profiles:
        if p.get("project_id") == project_id:
            return LearningProfile(**p)
    raise HTTPException(status_code=404, detail=t("api.no_profile", locale=locale))


# ==================== 历史记录与趋势分析端点 ====================


@router.get(
    "/projects/{project_id}/history",
    response_model=ProjectScoreHistory,
    tags=["历史与趋势"],
    responses=RESPONSES_404,
)
def get_project_history(
    project_id: str,
    locale: str = Depends(get_locale),
) -> ProjectScoreHistory:
    """
    获取项目评分历史记录。

    返回项目的所有评分历史，按时间顺序排列。
    历史记录包含每次评分的总分、各维度得分和扣分项数量。

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    history = get_history(project_id)
    return ProjectScoreHistory(**history)


@router.get(
    "/projects/{project_id}/trend",
    response_model=TrendAnalysis,
    tags=["历史与趋势"],
    responses=RESPONSES_404,
)
def get_project_trend(
    project_id: str,
    locale: str = Depends(get_locale),
) -> TrendAnalysis:
    """
    获取项目评分趋势分析。

    基于历史评分数据进行趋势分析，包括：
    - 整体评分趋势（上升/下降/稳定）
    - 各维度得分趋势
    - 扣分项数量变化
    - 基于趋势的改进建议

    支持 Accept-Language header 进行多语言响应。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    # 加载配置获取维度名称
    config = load_config()
    dimension_names = {}
    for dim_id, dim_config in config.rubric.get("dimensions", {}).items():
        dimension_names[dim_id] = dim_config.get("name", dim_id)

    trend = analyze_trend(project_id, dimension_names)
    return TrendAnalysis(**trend)


# ==================== 自我学习与进化 ====================


@router.put(
    "/projects/{project_id}/context",
    response_model=ProjectContextOut,
    tags=["自我学习与进化"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def set_project_context(
    project_id: str,
    payload: ProjectContextIn,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> ProjectContextOut:
    """
    设置项目投喂包/项目背景文本（招标文件、清单、图纸、设计等合并内容）。
    用于自我学习时结合项目信息分析高分逻辑。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    ctx = load_project_context()
    ctx[project_id] = {
        "text": payload.text,
        "filename": payload.filename,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_project_context(ctx)
    return ProjectContextOut(
        project_id=project_id,
        text=ctx[project_id]["text"],
        filename=ctx[project_id].get("filename"),
        updated_at=ctx[project_id].get("updated_at"),
    )


@router.get(
    "/projects/{project_id}/context",
    response_model=ProjectContextOut,
    tags=["自我学习与进化"],
    responses=RESPONSES_404,
)
def get_project_context_endpoint(
    project_id: str,
    locale: str = Depends(get_locale),
) -> ProjectContextOut:
    """获取项目投喂包/项目背景文本。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    ctx = load_project_context()
    data = ctx.get(project_id)
    if not data:
        return ProjectContextOut(project_id=project_id, text="", filename=None, updated_at=None)
    return ProjectContextOut(
        project_id=project_id,
        text=data.get("text", ""),
        filename=data.get("filename"),
        updated_at=data.get("updated_at"),
    )


def _parse_judge_scores_form(judge_scores: str) -> List[float]:
    try:
        scores = json.loads(judge_scores)
        if not isinstance(scores, list) or len(scores) != 5:
            raise ValueError("judge_scores 必须为长度为 5 的数组")
        return [float(x) for x in scores]
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"评委得分格式错误：{e}")


def _assert_valid_final_score(final_score: float) -> None:
    if not (0 <= final_score <= 100):
        raise HTTPException(status_code=422, detail="最终得分应在 0～100 之间。")


def _new_ground_truth_record(
    project_id: str,
    shigong_text: str,
    judge_scores: List[float],
    final_score: float,
    source: str,
    judge_weights: Optional[List[float]] = None,
) -> Dict[str, object]:
    return {
        "id": str(uuid4()),
        "project_id": project_id,
        "shigong_text": shigong_text,
        "judge_scores": judge_scores,
        "final_score": final_score,
        "judge_weights": judge_weights,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post(
    "/projects/{project_id}/ground_truth",
    response_model=GroundTruthRecord,
    tags=["自我学习与进化"],
    responses={**RESPONSES_401, **RESPONSES_404, **RESPONSES_422},
)
def add_ground_truth(
    project_id: str,
    payload: GroundTruthCreate,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> GroundTruthRecord:
    """
    录入真实评标结果（如青天大模型在交易中心评标后的施组+5评委得分+最终得分）。
    用于系统学习高分逻辑并进化。
    """
    ensure_data_dirs()
    if len((payload.shigong_text or "").strip()) < 50:
        raise HTTPException(
            status_code=422,
            detail="施组全文过短，至少 50 字以便学习分析。",
        )
    _assert_valid_final_score(payload.final_score)
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    records = load_ground_truth()
    record = _new_ground_truth_record(
        project_id=project_id,
        shigong_text=payload.shigong_text,
        judge_scores=payload.judge_scores,
        final_score=payload.final_score,
        source=payload.source,
        judge_weights=payload.judge_weights,
    )
    records.append(record)
    save_ground_truth(records)
    _sync_ground_truth_record_to_qingtian(project_id, record)
    try:
        _run_feedback_closed_loop(project_id, locale=locale, trigger="ground_truth_add")
    except Exception:
        pass
    return GroundTruthRecord(**record)


@router.post(
    "/projects/{project_id}/ground_truth/from_submission",
    response_model=GroundTruthRecord,
    tags=["自我学习与进化"],
    responses={**RESPONSES_401, **RESPONSES_404, **RESPONSES_422},
)
def add_ground_truth_from_submission(
    project_id: str,
    payload: GroundTruthFromSubmissionCreate,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> GroundTruthRecord:
    """从“步骤4已上传施组”中选择一份文件录入真实评标结果，避免重复上传。"""
    ensure_data_dirs()
    _assert_valid_final_score(payload.final_score)
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))

    submission_id = str(payload.submission_id or "").strip()
    submissions = load_submissions()
    submission = next(
        (
            s
            for s in submissions
            if str(s.get("id")) == submission_id and str(s.get("project_id")) == project_id
        ),
        None,
    )
    if not submission:
        raise HTTPException(status_code=404, detail="未找到对应施组，请先在步骤4上传施组。")

    shigong_text = str(submission.get("text") or "").strip()
    if len(shigong_text) < 50:
        raise HTTPException(status_code=422, detail="该施组文本过短，暂不支持录入真实评标。")

    record = _new_ground_truth_record(
        project_id=project_id,
        shigong_text=shigong_text,
        judge_scores=[float(x) for x in payload.judge_scores],
        final_score=float(payload.final_score),
        source=payload.source,
        judge_weights=None,
    )
    record["source_submission_id"] = submission_id
    record["source_submission_filename"] = submission.get("filename")

    records = load_ground_truth()
    records.append(record)
    save_ground_truth(records)
    _sync_ground_truth_record_to_qingtian(project_id, record)
    try:
        _run_feedback_closed_loop(project_id, locale=locale, trigger="ground_truth_add")
    except Exception:
        pass
    return GroundTruthRecord(**record)


@router.post(
    "/projects/{project_id}/ground_truth/from_file",
    response_model=GroundTruthRecord,
    tags=["自我学习与进化"],
    responses={**RESPONSES_401, **RESPONSES_404, **RESPONSES_422},
)
async def add_ground_truth_from_file(
    project_id: str,
    file: UploadFile = File(...),
    judge_scores: str = Form(..., description="5个评委得分，JSON 数组如 [1,2,3,4,5]"),
    final_score: float = Form(...),
    source: str = Form("青天大模型"),
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> GroundTruthRecord:
    """
    通过上传施组文件录入一条真实评标。后端解析文件为文本后保存。
    用于界面简洁录入：选文件 + 评委分 + 最终分即可。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    judge_scores_list = _parse_judge_scores_form(judge_scores)
    _assert_valid_final_score(final_score)
    content = await file.read()
    try:
        shigong_text = _read_uploaded_file_content(content, file.filename or "")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if len(shigong_text.strip()) < 50:
        raise HTTPException(status_code=422, detail="施组全文过短，至少 50 字以便学习分析。")
    records = load_ground_truth()
    record = _new_ground_truth_record(
        project_id=project_id,
        shigong_text=shigong_text,
        judge_scores=judge_scores_list,
        final_score=final_score,
        source=source,
        judge_weights=None,
    )
    records.append(record)
    save_ground_truth(records)
    _sync_ground_truth_record_to_qingtian(project_id, record)
    try:
        _run_feedback_closed_loop(project_id, locale=locale, trigger="ground_truth_add")
    except Exception:
        pass
    return GroundTruthRecord(**record)


@router.post(
    "/projects/{project_id}/ground_truth/from_files",
    response_model=GroundTruthBatchResponse,
    tags=["自我学习与进化"],
    responses={**RESPONSES_401, **RESPONSES_404, **RESPONSES_422},
)
async def add_ground_truth_from_files(
    project_id: str,
    files: List[UploadFile] = File(...),
    judge_scores: str = Form(..., description="5个评委得分，JSON 数组如 [1,2,3,4,5]"),
    final_score: float = Form(...),
    source: str = Form("青天大模型"),
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> GroundTruthBatchResponse:
    """
    通过上传多个施组文件批量录入真实评标。后端将逐个解析并保存。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    judge_scores_list = _parse_judge_scores_form(judge_scores)
    _assert_valid_final_score(final_score)

    items: List[Dict[str, object]] = []
    success_records: List[Dict[str, object]] = []
    for file in files:
        filename = file.filename or "unknown"
        content = await file.read()
        try:
            shigong_text = _read_uploaded_file_content(content, filename)
            if len(shigong_text.strip()) < 50:
                raise ValueError("施组全文过短，至少 50 字以便学习分析。")
            record = _new_ground_truth_record(
                project_id=project_id,
                shigong_text=shigong_text,
                judge_scores=judge_scores_list,
                final_score=final_score,
                source=source,
                judge_weights=None,
            )
            success_records.append(record)
            items.append(
                {
                    "filename": filename,
                    "ok": True,
                    "record": record,
                    "detail": None,
                }
            )
        except Exception as e:
            items.append(
                {
                    "filename": filename,
                    "ok": False,
                    "record": None,
                    "detail": str(e),
                }
            )

    if success_records:
        records = load_ground_truth()
        records.extend(success_records)
        save_ground_truth(records)
        for item in items:
            record = item.get("record")
            if item.get("ok") and isinstance(record, dict):
                try:
                    _sync_ground_truth_record_to_qingtian(project_id, record)
                except Exception as e:
                    item["detail"] = f"已保存，但同步青天失败：{e}"
        try:
            _run_feedback_closed_loop(project_id, locale=locale, trigger="ground_truth_batch_add")
        except Exception:
            pass

    success_count = sum(1 for item in items if item.get("ok"))
    failed_count = len(items) - success_count
    return GroundTruthBatchResponse(
        project_id=project_id,
        total_files=len(items),
        success_count=success_count,
        failed_count=failed_count,
        items=items,
    )


@router.get(
    "/projects/{project_id}/ground_truth",
    response_model=List[GroundTruthRecord],
    tags=["自我学习与进化"],
    responses=RESPONSES_404,
)
def list_ground_truth(
    project_id: str,
    locale: str = Depends(get_locale),
) -> List[GroundTruthRecord]:
    """列出本项目的所有真实评标记录。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    records = [r for r in load_ground_truth() if r.get("project_id") == project_id]
    return [GroundTruthRecord(**r) for r in records]


@router.delete(
    "/projects/{project_id}/ground_truth/{record_id}",
    status_code=204,
    tags=["自我学习与进化"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def delete_ground_truth(
    project_id: str,
    record_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> None:
    """删除指定项目下的一条真实评标记录。"""
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    records = load_ground_truth()
    if not any(r.get("id") == record_id and r.get("project_id") == project_id for r in records):
        raise HTTPException(status_code=404, detail="真实评标记录不存在")
    removed = next(
        (r for r in records if r.get("id") == record_id and r.get("project_id") == project_id),
        None,
    )
    records = [
        r for r in records if not (r.get("id") == record_id and r.get("project_id") == project_id)
    ]
    save_ground_truth(records)
    if removed is not None:
        gt_id = str(removed.get("id") or "")
        qtrs = load_qingtian_results()
        linked_submission_ids = {
            str(q.get("submission_id") or "")
            for q in qtrs
            if str((q.get("raw_payload") or {}).get("ground_truth_record_id") or "") == gt_id
        }
        qtrs = [
            q
            for q in qtrs
            if str((q.get("raw_payload") or {}).get("ground_truth_record_id") or "") != gt_id
        ]
        save_qingtian_results(qtrs)

        submissions = load_submissions()
        auto_submission_ids = {
            str(s.get("id") or "")
            for s in submissions
            if str(s.get("source_ground_truth_id") or "") == gt_id
            and str(s.get("project_id")) == project_id
        }
        remove_submission_ids = linked_submission_ids.union(auto_submission_ids)
        if remove_submission_ids:
            submissions = [
                s for s in submissions if str(s.get("id") or "") not in remove_submission_ids
            ]
            save_submissions(submissions)
            reports = load_score_reports()
            reports = [
                r for r in reports if str(r.get("submission_id") or "") not in remove_submission_ids
            ]
            save_score_reports(reports)
            units = load_evidence_units()
            units = [
                u for u in units if str(u.get("submission_id") or "") not in remove_submission_ids
            ]
            save_evidence_units(units)
        _refresh_project_reflection_objects(project_id)


@router.post(
    "/projects/{project_id}/evolve",
    response_model=EvolutionReport,
    tags=["自我学习与进化"],
    responses={**RESPONSES_401, **RESPONSES_404},
)
def evolve_project(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> EvolutionReport:
    """
    根据已录入的真实评标结果进行学习，生成高分逻辑与编制指导并保存。
    执行后可通过「编制指导」接口或页面查看。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    records = [r for r in load_ground_truth() if r.get("project_id") == project_id]
    ctx_data = load_project_context().get(project_id) or {}
    project_context = (ctx_data.get("text") or "").strip()
    materials_text = _merge_materials_text(project_id)
    if materials_text:
        project_context = (
            (project_context + "\n\n" + materials_text) if project_context else materials_text
        )
    report = build_evolution_report(project_id, records, project_context)
    enhanced = enhance_evolution_report_with_llm(project_id, report, records, project_context)
    if enhanced is not None:
        report["high_score_logic"] = enhanced.get("high_score_logic", report["high_score_logic"])
        report["writing_guidance"] = enhanced.get("writing_guidance", report["writing_guidance"])
        report["sample_count"] = enhanced.get("sample_count", report["sample_count"])
        report["updated_at"] = enhanced.get("updated_at", report["updated_at"])
        report["enhanced_by"] = enhanced.get("enhanced_by")  # 可追溯：spark | openai | gemini
        # 保留规则版产出的 scoring_evolution、compilation_instructions（LLM 仅增强文字部分）
    reports = load_evolution_reports()
    reports[project_id] = report
    save_evolution_reports(reports)
    return EvolutionReport(**report)


@router.get(
    "/projects/{project_id}/writing_guidance",
    response_model=WritingGuidance,
    tags=["自我学习与进化"],
    responses=RESPONSES_404,
)
def get_writing_guidance(
    project_id: str,
    locale: str = Depends(get_locale),
) -> WritingGuidance:
    """
    获取编制指导。若已执行过「学习进化」则返回学习结果；否则返回引导说明。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    reports = load_evolution_reports()
    data = reports.get(project_id)
    if data:
        return WritingGuidance(
            project_id=project_id,
            guidance=data.get("writing_guidance", []),
            high_score_logic=data.get("high_score_logic", []),
            sample_count=data.get("sample_count", 0),
            updated_at=data.get("updated_at"),
        )
    return WritingGuidance(
        project_id=project_id,
        guidance=[
            "请先录入「真实评标结果」（施组+5评委得分+最终得分），再点击「学习进化」生成编制指导。"
        ],
        high_score_logic=[],
        sample_count=0,
        updated_at=None,
    )


@router.get(
    "/projects/{project_id}/scoring_context",
    tags=["自我学习与进化"],
    responses=RESPONSES_404,
)
def get_scoring_context(project_id: str) -> Dict[str, object]:
    """
    返回当前项目评分生效的维度权重与总分缩放，用于诊断进化是否生效。
    source: evolution | expert_profile | learning_profile | none
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail="项目不存在")
    multipliers, profile_snapshot, _ = _resolve_project_scoring_context(project_id)
    scale = _get_evolution_total_score_scale(project_id)
    source = "none"
    if profile_snapshot:
        source = "expert_profile"
    elif (
        load_evolution_reports()
        .get(project_id, {})
        .get("scoring_evolution", {})
        .get("dimension_multipliers")
    ):
        source = "evolution"
    elif any(p.get("project_id") == project_id for p in load_learning_profiles()):
        source = "learning_profile"
    return {
        "project_id": project_id,
        "source": source,
        "dimension_multipliers": multipliers,
        "total_score_scale": scale,
        "has_non_default_multipliers": any(
            abs(float(multipliers.get(d, 1.0)) - 1.0) > 0.01 for d in DIMENSION_IDS
        ),
    }


@router.get(
    "/projects/{project_id}/compilation_instructions",
    response_model=CompilationInstructions,
    tags=["自我学习与进化"],
    responses=RESPONSES_404,
)
def get_compilation_instructions(
    project_id: str,
    locale: str = Depends(get_locale),
) -> CompilationInstructions:
    """
    获取编制系统指令。基于学习进化结果，用于约束施组编制输出（必备章节、图表、要素及禁止表述）。
    可导出为系统指令，强制要求按此输出内容和图表。
    """
    ensure_data_dirs()
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        raise HTTPException(status_code=404, detail=t("api.project_not_found", locale=locale))
    reports = load_evolution_reports()
    data = reports.get(project_id)
    ci = data.get("compilation_instructions") if data else None
    if ci:
        return CompilationInstructions(
            project_id=project_id,
            required_sections=ci.get("required_sections", []),
            required_charts_images=ci.get("required_charts_images", []),
            mandatory_elements=ci.get("mandatory_elements", []),
            forbidden_patterns=ci.get("forbidden_patterns", []),
            guidance_items=ci.get("guidance_items", []),
            high_score_summary=ci.get("high_score_summary", []),
        )
    return CompilationInstructions(
        project_id=project_id,
        required_sections=[],
        required_charts_images=[],
        mandatory_elements=[],
        forbidden_patterns=[],
        guidance_items=["请先录入真实评标并执行「学习进化」后，编制系统指令将基于学习结果生成。"],
        high_score_summary=[],
    )


@router.post(
    "/tools/parse_text",
    tags=["工具"],
    include_in_schema=False,
)
async def parse_file_to_text(file: UploadFile = File(...)) -> Dict[str, str]:
    """解析上传文件为纯文本。支持 .txt、.docx、.pdf、.json、.xlsx/.xls"""
    content = await file.read()
    try:
        text = _read_uploaded_file_content(content, file.filename or "")
        return {"text": text}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# API 兼容路由（与执行文档中的 /api/projects/... 路径保持一致）
compat_router = APIRouter(prefix="/api")


@compat_router.get("/scoring/factors", response_model=ScoringFactorsResponse, tags=["系统状态"])
def compat_scoring_factors(
    project_id: Optional[str] = Query(None),
    locale: str = Depends(get_locale),
) -> ScoringFactorsResponse:
    return scoring_factors(project_id=project_id, locale=locale)


@compat_router.get(
    "/scoring/factors/markdown", response_model=ScoringFactorsMarkdownResponse, tags=["系统状态"]
)
def compat_scoring_factors_markdown(
    project_id: Optional[str] = Query(None),
    locale: str = Depends(get_locale),
) -> ScoringFactorsMarkdownResponse:
    return scoring_factors_markdown(project_id=project_id, locale=locale)


@compat_router.get("/system/self_check", response_model=SelfCheckResponse, tags=["系统状态"])
def compat_system_self_check(
    project_id: Optional[str] = Query(None),
) -> SelfCheckResponse:
    return system_self_check(project_id=project_id)


@compat_router.get(
    "/projects/{project_id}/analysis_bundle",
    response_model=AnalysisBundleResponse,
    tags=["洞察与学习"],
)
def compat_project_analysis_bundle(
    project_id: str,
    locale: str = Depends(get_locale),
) -> AnalysisBundleResponse:
    return project_analysis_bundle(project_id=project_id, locale=locale)


@compat_router.get("/projects/{project_id}/analysis_bundle.md", tags=["洞察与学习"])
def compat_project_analysis_bundle_markdown_file(
    project_id: str,
    locale: str = Depends(get_locale),
) -> Response:
    return project_analysis_bundle_markdown_file(project_id=project_id, locale=locale)


@compat_router.get(
    "/projects/{project_id}/expert-profile",
    response_model=ProjectExpertProfileResponse,
    tags=["项目管理"],
)
def compat_get_project_expert_profile(
    project_id: str, locale: str = Depends(get_locale)
) -> ProjectExpertProfileResponse:
    return get_project_expert_profile(project_id=project_id, locale=locale)


@compat_router.put(
    "/projects/{project_id}/expert-profile",
    response_model=ProjectExpertProfileResponse,
    tags=["项目管理"],
)
def compat_update_project_expert_profile(
    project_id: str,
    payload: ExpertProfileUpdate,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> ProjectExpertProfileResponse:
    return update_project_expert_profile(
        project_id=project_id,
        payload=payload,
        api_key=api_key,
        locale=locale,
    )


@compat_router.post(
    "/projects/{project_id}/rescore", response_model=RescoreResponse, tags=["项目管理"]
)
def compat_rescore_project_submissions(
    project_id: str,
    payload: RescoreRequest,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> RescoreResponse:
    return rescore_project_submissions(
        project_id=project_id,
        payload=payload,
        api_key=api_key,
        locale=locale,
    )


@compat_router.get(
    "/projects/{project_id}/submissions",
    response_model=list[SubmissionRecord] | ProjectPreScoreListResponse,
    tags=["施组提交"],
)
def compat_list_submissions(
    project_id: str,
    with_: Optional[str] = Query(None, alias="with"),
):
    return list_submissions(project_id=project_id, with_=with_)


@compat_router.get(
    "/submissions/{submission_id}/reports/latest",
    response_model=LatestReportResponse,
    tags=["施组提交"],
)
def compat_latest_submission_report(submission_id: str) -> LatestReportResponse:
    return get_latest_submission_report(submission_id=submission_id)


@compat_router.post(
    "/submissions/{submission_id}/qingtian-results",
    response_model=QingTianResultRecord,
    tags=["施组提交"],
)
def compat_ingest_qingtian_result(
    submission_id: str,
    payload: QingTianResultCreate,
    api_key: Optional[str] = Depends(verify_api_key),
) -> QingTianResultRecord:
    return ingest_qingtian_result(submission_id=submission_id, payload=payload, api_key=api_key)


@compat_router.get(
    "/submissions/{submission_id}/qingtian-results/latest",
    response_model=QingTianResultRecord,
    tags=["施组提交"],
)
def compat_latest_qingtian_result(submission_id: str) -> QingTianResultRecord:
    return get_latest_qingtian_result(submission_id=submission_id)


@compat_router.post(
    "/projects/{project_id}/reflection/auto_run",
    response_model=ReflectionAutoRunResponse,
    tags=["洞察与学习"],
)
def compat_auto_run_reflection(
    project_id: str,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> ReflectionAutoRunResponse:
    return auto_run_reflection_pipeline(project_id=project_id, api_key=api_key, locale=locale)


@compat_router.post(
    "/projects/{project_id}/ground_truth/from_submission",
    response_model=GroundTruthRecord,
    tags=["自我学习与进化"],
)
def compat_add_ground_truth_from_submission(
    project_id: str,
    payload: GroundTruthFromSubmissionCreate,
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> GroundTruthRecord:
    return add_ground_truth_from_submission(
        project_id=project_id,
        payload=payload,
        api_key=api_key,
        locale=locale,
    )


@compat_router.post(
    "/projects/{project_id}/ground_truth/from_files",
    response_model=GroundTruthBatchResponse,
    tags=["自我学习与进化"],
)
async def compat_add_ground_truth_from_files(
    project_id: str,
    files: List[UploadFile] = File(...),
    judge_scores: str = Form(...),
    final_score: float = Form(...),
    source: str = Form("青天大模型"),
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
) -> GroundTruthBatchResponse:
    return await add_ground_truth_from_files(
        project_id=project_id,
        files=files,
        judge_scores=judge_scores,
        final_score=final_score,
        source=source,
        api_key=api_key,
        locale=locale,
    )


@compat_router.get(
    "/projects/{project_id}/evaluation",
    response_model=ProjectEvaluationResponse,
    tags=["洞察与学习"],
)
def compat_project_evaluation(
    project_id: str,
    locale: str = Depends(get_locale),
) -> ProjectEvaluationResponse:
    return get_project_evaluation(project_id=project_id, locale=locale)


@compat_router.get(
    "/evaluation/summary", response_model=EvaluationSummaryResponse, tags=["洞察与学习"]
)
def compat_evaluation_summary() -> EvaluationSummaryResponse:
    return get_evaluation_summary()


# 注册 API v1 路由
app.include_router(router)
app.include_router(compat_router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
async def apple_touch_icon():
    return Response(status_code=204)


@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon_alt():
    return Response(status_code=204)


@app.post("/web/create_project", include_in_schema=False)
def web_create_project(
    name: str = Form(...),
    api_key: Optional[str] = Depends(verify_api_key),
):
    clean_name = (name or "").strip()
    if not clean_name:
        return RedirectResponse(
            url="/?create_error=" + quote_plus("项目名称不能为空"), status_code=303
        )
    try:
        rec = create_project(ProjectCreate(name=clean_name), api_key=api_key)
    except HTTPException as exc:
        detail = str(getattr(exc, "detail", "创建失败"))
        return RedirectResponse(url="/?create_error=" + quote_plus(detail), status_code=303)
    project_id_value = str(getattr(rec, "id", "") or "")
    return RedirectResponse(
        url="/?create_ok=" + quote_plus(clean_name) + "&project_id=" + quote_plus(project_id_value),
        status_code=303,
    )


@app.post("/web/delete_project", include_in_schema=False)
def web_delete_project(
    project_id: str = Form(""),
    api_key: Optional[str] = Depends(verify_api_key),
):
    pid = (project_id or "").strip()
    if not pid:
        return RedirectResponse(
            url="/?msg_type=error&msg=" + quote_plus("删除失败：请先选择项目"),
            status_code=303,
        )
    try:
        result = _delete_project_cascade(pid, locale="zh")
    except HTTPException as exc:
        detail = str(getattr(exc, "detail", "删除失败"))
        return RedirectResponse(
            url="/?msg_type=error&msg=" + quote_plus("删除失败：" + detail),
            status_code=303,
        )
    return RedirectResponse(
        url="/?msg_type=success&msg="
        + quote_plus("项目已删除：" + str(result.get("project_name") or pid)),
        status_code=303,
    )


@app.post("/web/upload_materials", include_in_schema=False)
def web_upload_materials(
    project_id: str = Form(""),
    file: List[UploadFile] = File(default=[]),
    api_key: Optional[str] = Depends(verify_api_key),
):
    pid = (project_id or "").strip()
    files = file or []
    if not pid:
        return RedirectResponse(
            url="/?msg_type=error&msg="
            + quote_plus("上传资料失败：请先选择项目")
            + "#section-materials",
            status_code=303,
        )
    if not files:
        return RedirectResponse(
            url="/?project_id="
            + quote_plus(pid)
            + "&msg_type=error&msg="
            + quote_plus("上传资料失败：未选择文件")
            + "#section-materials",
            status_code=303,
        )
    ok_count = 0
    fail_count = 0
    first_error = ""
    for f in files:
        try:
            upload_material(project_id=pid, file=f, api_key=api_key, locale="zh")
            ok_count += 1
        except Exception as exc:  # noqa: BLE001 - web fallback should keep processing
            fail_count += 1
            if not first_error:
                first_error = str(exc)
    msg = f"资料上传完成：成功 {ok_count}，失败 {fail_count}"
    if fail_count > 0 and first_error:
        msg += f"；首个错误：{first_error}"
    return RedirectResponse(
        url="/?project_id="
        + quote_plus(pid)
        + "&msg_type="
        + ("error" if fail_count > 0 else "success")
        + "&msg="
        + quote_plus(msg)
        + "#section-materials",
        status_code=303,
    )


def _web_upload_redirect_url(project_id: str, message: str, anchor: str = "") -> str:
    pid = (project_id or "").strip()
    base = "/?msg_type=error&msg=" + quote_plus(message)
    if pid:
        base = "/?project_id=" + quote_plus(pid) + "&msg_type=error&msg=" + quote_plus(message)
    anchor_part = str(anchor or "").strip()
    if anchor_part and not anchor_part.startswith("#"):
        anchor_part = "#" + anchor_part
    return base + anchor_part


@app.api_route(
    "/web/upload_materials",
    methods=["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
def web_upload_materials_get_fallback(project_id: str = ""):
    """
    兼容误触发非 POST 的场景，避免直接 405 中断用户流程。
    """
    return RedirectResponse(
        url=_web_upload_redirect_url(
            project_id, "请在主页选择文件后点击“上传资料”提交。", "#section-materials"
        ),
        status_code=303,
    )


@app.post("/web/upload_shigong", include_in_schema=False)
def web_upload_shigong(
    project_id: str = Form(""),
    file: List[UploadFile] = File(default=[]),
    api_key: Optional[str] = Depends(verify_api_key),
):
    pid = (project_id or "").strip()
    files = file or []
    if not pid:
        return RedirectResponse(
            url="/?msg_type=error&msg="
            + quote_plus("上传施组失败：请先选择项目")
            + "#section-shigong",
            status_code=303,
        )
    if not files:
        return RedirectResponse(
            url="/?project_id="
            + quote_plus(pid)
            + "&msg_type=error&msg="
            + quote_plus("上传施组失败：未选择文件")
            + "#section-shigong",
            status_code=303,
        )
    ok_count = 0
    fail_count = 0
    first_error = ""
    for f in files:
        try:
            upload_shigong(project_id=pid, file=f, api_key=api_key, locale="zh")
            ok_count += 1
        except Exception as exc:  # noqa: BLE001 - web fallback should keep processing
            fail_count += 1
            if not first_error:
                first_error = str(exc)
    msg = f"施组上传完成：成功 {ok_count}，失败 {fail_count}"
    if fail_count > 0 and first_error:
        msg += f"；首个错误：{first_error}"
    return RedirectResponse(
        url="/?project_id="
        + quote_plus(pid)
        + "&msg_type="
        + ("error" if fail_count > 0 else "success")
        + "&msg="
        + quote_plus(msg)
        + "#section-shigong",
        status_code=303,
    )


@app.post("/web/score_shigong", include_in_schema=False)
def web_score_shigong(
    project_id: str = Form(""),
    score_scale_max: int = Form(DEFAULT_SCORE_SCALE_MAX),
    api_key: Optional[str] = Depends(verify_api_key),
    locale: str = Depends(get_locale),
):
    pid = (project_id or "").strip()
    if not pid:
        return RedirectResponse(
            url="/?msg_type=error&msg="
            + quote_plus("施组评分失败：请先选择项目")
            + "#section-shigong",
            status_code=303,
        )
    try:
        result = rescore_project_submissions(
            project_id=pid,
            payload=RescoreRequest(
                scoring_engine_version="v2",
                scope="project",
                score_scale_max=score_scale_max,
                rebuild_anchors=False,
                rebuild_requirements=False,
                retrain_calibrator=False,
                force_unlock=False,
            ),
            api_key=api_key,
            locale=locale,
        )
        updated = int(
            getattr(
                result,
                "updated_submissions",
                getattr(result, "reports_generated", getattr(result, "submission_count", 0)),
            )
        )
        msg = f"施组评分完成：已重算 {updated} 份"
        return RedirectResponse(
            url="/?project_id="
            + quote_plus(pid)
            + "&msg_type=success&msg="
            + quote_plus(msg)
            + "#section-shigong",
            status_code=303,
        )
    except Exception as exc:  # noqa: BLE001 - web fallback should keep processing
        return RedirectResponse(
            url="/?project_id="
            + quote_plus(pid)
            + "&msg_type=error&msg="
            + quote_plus("施组评分失败：" + str(exc))
            + "#section-shigong",
            status_code=303,
        )


@app.api_route(
    "/web/upload_shigong",
    methods=["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
def web_upload_shigong_get_fallback(project_id: str = ""):
    return RedirectResponse(
        url=_web_upload_redirect_url(
            project_id, "请在主页选择文件后点击“上传施组”提交。", "#section-shigong"
        ),
        status_code=303,
    )


@app.api_route(
    "/web/score_shigong",
    methods=["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
def web_score_shigong_get_fallback(project_id: str = ""):
    return RedirectResponse(
        url=_web_upload_redirect_url(
            project_id, "请在主页选择项目后点击“评分施组”提交。", "#section-shigong"
        ),
        status_code=303,
    )


@app.head("/", include_in_schema=False)
def index_head() -> Response:
    """
    某些浏览器会先发 HEAD 检测主页可达性，避免返回 405 干扰用户判断。
    """
    return Response(status_code=200)


@app.get("/", tags=["系统状态"], include_in_schema=False)
def index(
    create_ok: Optional[str] = Query(None),
    create_error: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    msg: Optional[str] = Query(None),
    msg_type: Optional[str] = Query(None),
) -> Response:
    ensure_data_dirs()
    projects = load_projects()
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        active_projects = [
            p
            for p in projects
            if str(p.get("id") or "") != "p1" and not str(p.get("name") or "").startswith("E2E_")
        ]
        if not active_projects:
            recovered = _recover_latest_orphan_project(projects)
            if recovered is not None:
                projects = load_projects()
    project_ids = [str(p.get("id", "")) for p in projects]
    if (not os.environ.get("PYTEST_CURRENT_TEST")) and project_id and project_id not in project_ids:
        recovered = _recover_missing_project_from_artifacts(project_id, projects)
        if recovered is not None:
            projects = load_projects()
            project_ids = [str(p.get("id", "")) for p in projects]
    selected_project_id = ""
    if project_id and project_id in project_ids:
        selected_project_id = project_id
    elif project_ids:
        # Default to latest created project for better usability.
        selected_project_id = project_ids[-1]
    project_options = []
    for p in projects:
        pid_raw = str(p.get("id", ""))
        pid = html_lib.escape(pid_raw)
        pname = html_lib.escape(str(p.get("name", p.get("id", ""))))
        short_id = html_lib.escape(str(p.get("id", ""))[:8])
        selected_attr = " selected" if pid_raw == selected_project_id else ""
        project_options.append(
            f'<option value="{pid}"{selected_attr}>{pname} ({short_id}…)</option>'
        )
    project_options_html = "".join(project_options)
    create_notice_html = ""
    if create_ok:
        create_notice_html = (
            '<p style="margin:6px 0 0 0;font-size:13px;color:#15803d">'
            "创建成功（表单模式）：" + html_lib.escape(create_ok) + "</p>"
        )
    elif create_error:
        create_notice_html = (
            '<p style="margin:6px 0 0 0;font-size:13px;color:#b91c1c">'
            "创建失败：" + html_lib.escape(create_error) + "</p>"
        )

    global_notice_html = ""
    if msg:
        is_error = str(msg_type or "").lower() == "error"
        notice_color = "#b91c1c" if is_error else "#15803d"
        notice_bg = "#fef2f2" if is_error else "#ecfdf5"
        global_notice_html = (
            f'<div style="margin:0 0 16px 0;padding:10px 12px;border-radius:8px;background:{notice_bg};color:{notice_color};font-size:13px">'
            + html_lib.escape(msg)
            + "</div>"
        )

    ui_dimension_labels = {
        "01": "01 工程项目整体理解与实施路径",
        "02": "02 安全生产管理体系与控制措施",
        "03": "03 文明施工管理体系与实施措施",
        "04": "04 材料与部品采购及管理机制",
        "05": "05 四新技术的应用与实施方案",
        "06": "06 工程关键工序识别与控制措施",
        "07": "07 工程重难点及危险性较大工程管控",
        "08": "08 工程质量管理体系与保证措施",
        "09": "09 工期目标保障与进度控制措施",
        "10": "10 专项施工工艺与技术方案",
        "11": "11 人力资源配置与管理方案",
        "12": "12 总体施工工艺流程与组织逻辑",
        "13": "13 物资与施工设备配置方案",
        "14": "14 设计协调与深化实施能力",
        "15": "15 总体资源配置与实施计划",
        "16": "16 技术措施的可行性与落地性",
    }
    initial_weights_raw: Dict[str, int] = _default_weights_raw()
    initial_weights_norm: Dict[str, float] = _normalize_weights(initial_weights_raw)
    initial_profile_status = "请先选择项目并加载配置。"
    if selected_project_id:
        try:
            project = _find_project(selected_project_id, projects)
            profiles = load_expert_profiles()
            profile: Optional[Dict[str, object]] = None
            profile_id = str(project.get("expert_profile_id") or "")
            if profile_id:
                for item in profiles:
                    if str(item.get("id") or "") == profile_id:
                        profile = item
                        break
            if profile and isinstance(profile.get("weights_raw"), dict):
                initial_weights_raw = _coerce_weights_raw(profile.get("weights_raw", {}))
                initial_weights_norm = _normalize_weights(initial_weights_raw)
                profile_name = str(profile.get("name") or "项目默认配置")
                profile_id_text = str(profile.get("id") or "-")
                updated_at_text = (
                    str(project.get("updated_at") or profile.get("updated_at") or "")[:19] or "-"
                )
                initial_profile_status = (
                    "当前生效配置："
                    + html_lib.escape(profile_name)
                    + "（ID: "
                    + html_lib.escape(profile_id_text)
                    + "，更新时间: "
                    + html_lib.escape(updated_at_text)
                    + "）"
                )
            else:
                profile_name = str(project.get("name") or "项目") + " 默认配置"
                updated_at_text = str(project.get("updated_at") or "")[:19] or "-"
                initial_profile_status = (
                    "当前生效配置："
                    + html_lib.escape(profile_name)
                    + "（ID: 未绑定，更新时间: "
                    + html_lib.escape(updated_at_text)
                    + "）"
                )
        except Exception:
            initial_profile_status = "请先选择项目并加载配置。"

    initial_weights_rows = []
    for dim_id in DIMENSION_IDS:
        label = ui_dimension_labels.get(dim_id, f"{dim_id} {_normalize_dimension_id(dim_id)}")
        raw = int(initial_weights_raw.get(dim_id, 5))
        norm_pct = float(initial_weights_norm.get(dim_id, 0.0) * 100.0)
        initial_weights_rows.append(
            '<div class="weight-row">'
            + f'<label for="w_{dim_id}">{html_lib.escape(label)}</label>'
            + f'<input id="w_{dim_id}" data-dim="{html_lib.escape(dim_id)}" type="range" min="0" max="10" step="1" value="{raw}" />'
            + f'<span class="raw-value" id="w_raw_{dim_id}">{raw}</span>'
            + f'<span class="norm-value" id="w_norm_{dim_id}">{norm_pct:.2f}%</span>'
            + "</div>"
        )
    initial_weights_rows_html = "".join(initial_weights_rows)
    initial_weights_summary = " | ".join(
        [
            f"{dim_id}:{(float(initial_weights_norm.get(dim_id, 0.0)) * 100.0):.2f}%"
            for dim_id in DIMENSION_IDS
        ]
    )
    selected_project_for_view = (
        next((p for p in projects if str(p.get("id", "")) == selected_project_id), {})
        if selected_project_id
        else {}
    )
    score_scale_initial = (
        _resolve_project_score_scale_max(selected_project_for_view)
        if selected_project_for_view
        else DEFAULT_SCORE_SCALE_MAX
    )
    allow_pred_initial = bool(selected_project_for_view) and (
        _select_calibrator_model(selected_project_for_view) is not None
    )
    initial_material_rows: List[str] = []
    initial_submission_rows: List[str] = []
    if selected_project_id:
        try:
            materials_all = load_materials()
            selected_materials = [
                m for m in materials_all if str(m.get("project_id", "")) == selected_project_id
            ]
            selected_materials.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
            for m in selected_materials:
                material_id = html_lib.escape(str(m.get("id", "")))
                filename_raw = str(m.get("filename", ""))
                filename = html_lib.escape(filename_raw)
                created_at = html_lib.escape(str(m.get("created_at", ""))[:19])
                initial_material_rows.append(
                    "<tr>"
                    + f"<td>{filename}</td>"
                    + f"<td>{created_at}</td>"
                    + (
                        "<td>"
                        + f'<button type="button" class="btn-danger js-delete-material" data-material-id="{material_id}" data-filename="{html_lib.escape(filename_raw)}" onclick="return window.__zhifeiFallbackDelete(event, \'material\', this.getAttribute(\'data-material-id\'), this.getAttribute(\'data-filename\'))">删除</button>'
                        + "</td>"
                    )
                    + "</tr>"
                )
        except Exception:
            initial_material_rows = []
        try:
            submissions_all = load_submissions()
            selected_submissions = [
                s for s in submissions_all if str(s.get("project_id", "")) == selected_project_id
            ]
            selected_submissions.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
            for s in selected_submissions:
                submission_id = html_lib.escape(str(s.get("id", "")))
                filename_raw = str(s.get("filename", ""))
                filename = html_lib.escape(filename_raw)
                report_obj = s.get("report")
                report = report_obj if isinstance(report_obj, dict) else {}
                pred_total_raw = report.get("pred_total_score")
                rule_total_raw = report.get("rule_total_score")
                if not allow_pred_initial:
                    pred_total_raw = None
                llm_total_raw = report.get("llm_total_score")
                pred_total = _convert_score_from_100(pred_total_raw, score_scale_initial)
                rule_total = _convert_score_from_100(rule_total_raw, score_scale_initial)
                llm_total = _convert_score_from_100(llm_total_raw, score_scale_initial)
                scoring_status = str(report.get("scoring_status") or "").strip().lower()
                is_pending = scoring_status == "pending"
                primary_total = (
                    pred_total
                    if pred_total is not None
                    else _convert_score_from_100(s.get("total_score"), score_scale_initial)
                )
                if is_pending:
                    score_cell = '<span class="note">待评分</span>'
                elif pred_total is not None:
                    score_cell = html_lib.escape(str(pred_total))
                    note_items: List[str] = []
                    if rule_total is not None:
                        note_items.append("规则: " + html_lib.escape(str(rule_total)))
                    if llm_total is not None:
                        note_items.append("LLM: " + html_lib.escape(str(llm_total)))
                    if note_items:
                        score_cell += '<div class="note">' + " / ".join(note_items) + "</div>"
                else:
                    score_cell = (
                        "-" if primary_total is None else html_lib.escape(str(primary_total))
                    )
                created_at = html_lib.escape(str(s.get("created_at", ""))[:19])
                initial_submission_rows.append(
                    "<tr>"
                    + f"<td>{filename}</td>"
                    + f"<td>{score_cell}</td>"
                    + f"<td>{created_at}</td>"
                    + (
                        "<td>"
                        + f'<button type="button" class="btn-danger js-delete-submission" data-submission-id="{submission_id}" data-filename="{html_lib.escape(filename_raw)}" onclick="return window.__zhifeiFallbackDelete(event, \'submission\', this.getAttribute(\'data-submission-id\'), this.getAttribute(\'data-filename\'))">删除</button>'
                        + "</td>"
                    )
                    + "</tr>"
                )
        except Exception:
            initial_submission_rows = []
    initial_material_rows_html = "".join(initial_material_rows)
    initial_submission_rows_html = "".join(initial_submission_rows)
    initial_materials_empty_display = "none" if initial_material_rows else "block"
    initial_submissions_empty_display = "none" if initial_submission_rows else "block"
    html = """
    <html>
    <head>
      <meta charset="utf-8">
      <title>青天评标系统</title>
      <style>
        :root { --bg:#f4f6fb; --card:#fff; --border:#dbe2ef; --primary:#2563eb; --text:#1e293b; }
        body { font-family: system-ui, sans-serif; margin: 0 auto; max-width: 1680px; padding: 20px; background: var(--bg); color: var(--text); line-height: 1.5; }
        h2 { margin-top: 12px; font-size: 1.8rem; color: var(--primary); }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px; margin-bottom: 18px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); }
        input[type="text"], select { padding: 8px 10px; margin-right: 8px; min-width: 200px; border:1px solid #cbd5e1; border-radius:8px; background:#f8fafc; }
        button { padding: 10px 16px; background: var(--primary); color: #fff; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; }
        button:hover { opacity: 0.9; }
        button:disabled { opacity: 0.45; cursor: not-allowed; }
        button.secondary { background: #64748b; }
        button.btn-danger { background: #dc2626; color: #fff; position:relative; z-index:2; pointer-events:auto; }
        pre { white-space: pre-wrap; font-size: 12px; margin: 0; line-height: 1.45; }
        #output { font-size: 13px; }
        .section { margin-bottom: 24px; }
        .toolbar { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
        .inline-form { display:inline-flex; flex-wrap:wrap; align-items:center; gap:10px; margin:0; }
        .action-row { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin: 8px 0; }
        .action-row button { position:relative; z-index:2; pointer-events:auto; }
        .upload-box { margin-top:14px; padding:12px 12px 10px 12px; border-top:1px solid var(--border); border-radius:10px; background:#fbfdff; }
        .field-group { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-bottom:8px; }
        .field-group p { flex-basis:100%; margin:6px 0 0 0; }
        .note { font-size:12px; color:#64748b; }
        .muted { margin:4px 0 0 0; font-size:13px; color:#64748b; }
        .result-block { margin-top: 10px; padding: 12px; background: #f8fafc; border-radius: 8px; border-left: 3px solid var(--primary); overflow-x:auto; }
        table { border-collapse: collapse; width: 100%; font-size: 14px; margin-top: 6px; }
        th, td { border: 1px solid var(--border); padding: 10px 12px; text-align: left; vertical-align: top; }
        th { background: #e2e8f0; }
        tbody tr:nth-child(even) { background: #f8fafc; }
        .error { color: #b91c1c; }
        .success { color: #15803d; }
        details { margin-top: 8px; }
        summary { cursor: pointer; font-weight: 600; }
        .weight-row { display:grid; grid-template-columns: 280px 1fr 48px 78px; gap:10px; align-items:center; }
        .weight-row label { font-size:13px; color:#1e293b; }
        .weight-row input[type="range"] { width:100%; }
        .weight-row .raw-value { font-weight:600; color:#0f172a; text-align:right; }
        .weight-row .norm-value { color:#334155; text-align:right; font-size:12px; }
      </style>
    </head>
    <body>
      <h1>青天评标系统 - 上传与对比 (v2)</h1>
      <p style="margin:-8px 0 16px 0;padding:10px;background:#e0f2fe;border-radius:6px;font-size:14px;">
        <strong>首次使用：</strong>① 创建项目 → ② 刷新并选择项目 → ③ 上传施组文件 → ④ 点击“评分施组”出分。数据保存在本机，无需额外配置。
      </p>
      __GLOBAL_NOTICE_HTML__
      <script>
        (function () {
          // Early fallback shim: guarantees visible response even if later scripts crash.
          if (window.__zhifeiEarlyFallbackReady) return;
          window.__zhifeiEarlyFallbackReady = true;

          function pickProjectId() {
            const sel = document.getElementById('projectSelect');
            return (sel && sel.value) ? String(sel.value) : '';
          }
          function apiHeaders(isJson) {
            const h = {};
            try {
              const k = localStorage.getItem('api_key') || '';
              if (k) h['X-API-Key'] = k;
            } catch (_) {}
            if (isJson) h['Content-Type'] = 'application/json';
            return h;
          }
          function esc(v) {
            return String(v == null ? '' : v)
              .replace(/&/g, '&amp;')
              .replace(/</g, '&lt;')
              .replace(/>/g, '&gt;')
              .replace(/"/g, '&quot;')
              .replace(/'/g, '&#39;');
          }
          function setResult(resultId, text, isError) {
            const el = resultId ? document.getElementById(resultId) : null;
            if (!el) return;
            el.style.display = 'block';
            el.innerHTML = '<span class="' + (isError ? 'error' : 'success') + '">' + esc(text || '') + '</span>';
          }
          function setResultHtml(resultId, html) {
            const el = resultId ? document.getElementById(resultId) : null;
            if (!el) return;
            el.style.display = 'block';
            el.innerHTML = html || '';
          }
          function setOutput(text) {
            const out = document.getElementById('output');
            if (out) out.textContent = String(text || '');
          }
          function parseJson(text) {
            try { return JSON.parse(text || '{}'); } catch (_) { return {}; }
          }
          function selectedScoreScaleMax() {
            const el = document.getElementById('scoreScaleSelect');
            const raw = (el && el.value) ? String(el.value).trim() : '100';
            return raw === '5' ? 5 : 100;
          }

          const EARLY_ACTIONS = {
            btnCompare: { resultId: 'compareResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/compare', loading: '对比排名加载中...' },
            btnCompareReport: { resultId: 'compareReportResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/compare_report', loading: '对比报告生成中...' },
            btnInsights: { resultId: 'insightsResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/insights', loading: '洞察分析中...' },
            btnLearning: { resultId: 'learningResult', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/learning', loading: '学习画像生成中...' },
            btnAdaptive: { resultId: 'adaptiveResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/adaptive', loading: '自适应建议生成中...' },
            btnAdaptivePatch: { resultId: 'adaptivePatchResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/adaptive_patch', loading: '补丁生成中...' },
            btnAdaptiveValidate: { resultId: 'adaptiveValidateResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/adaptive_validate', loading: '验证效果计算中...' },
            btnAdaptiveApply: { resultId: 'adaptiveApplyResult', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/adaptive_apply', loading: '应用补丁中...' },
            btnRefreshGroundTruth: { resultId: 'evolveResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/ground_truth', loading: '真实评标列表刷新中...' },
            btnRefreshGroundTruthSubmissionOptions: { resultId: 'evolveResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/submissions', loading: '施组选项刷新中...' },
            btnEvolve: { resultId: 'evolveResult', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/evolve', loading: '学习进化执行中...' },
            btnWritingGuidance: { resultId: 'guidanceResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/writing_guidance', loading: '正在生成编制指导...' },
            btnCompilationInstructions: { resultId: 'compilationInstructionsResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/compilation_instructions', loading: '正在生成编制系统指令...' },
            btnScoreShigong: { resultId: 'shigongActionStatus', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/rescore', loading: '施组评分中...' },
          };
          window.__ZHIFEI_FALLBACK_ACTIONS = window.__ZHIFEI_FALLBACK_ACTIONS || {};
          Object.keys(EARLY_ACTIONS).forEach((k) => { window.__ZHIFEI_FALLBACK_ACTIONS[k] = EARLY_ACTIONS[k]; });

          async function runEarlyAction(actionId) {
            const cfg = EARLY_ACTIONS[String(actionId || '')];
            if (!cfg) return true;
            const projectId = pickProjectId();
            if (!projectId) {
              setResult(cfg.resultId, '请先在「2) 选择项目」中选择项目', true);
              setOutput('[' + actionId + '] 缺少项目ID');
              return false;
            }
            setResult(cfg.resultId, cfg.loading || '处理中...', false);
            let url = cfg.path(projectId);
            let method = cfg.method || 'GET';
            let headers = apiHeaders(false);
            let body;
            if (actionId === 'btnScoreShigong') {
              headers = apiHeaders(true);
              body = JSON.stringify({
                scope: 'project',
                scoring_engine_version: 'v2',
                score_scale_max: selectedScoreScaleMax(),
              });
            }
            let res;
            let text = '';
            try {
              res = await fetch(url, { method, headers, body });
              text = await res.text();
            } catch (err) {
              setResult(cfg.resultId, '网络异常：' + String((err && err.message) || err || 'unknown'), true);
              setOutput('[' + actionId + '] 网络异常');
              return false;
            }
            const data = parseJson(text);
            setOutput('[' + actionId + '] HTTP ' + String(res.status || 0) + '\\n' + (text || ''));
            if (!res.ok) {
              setResult(cfg.resultId, '[' + actionId + '] 请求失败：' + String((data && data.detail) || ('HTTP ' + res.status)), true);
              return false;
            }
            if (actionId === 'btnCompare') {
              const rows = Array.isArray(data.rankings) ? data.rankings : [];
              const html = '<strong>排名</strong><table><tr><th>文件名</th><th>总分</th><th>时间</th></tr>' +
                (rows.length
                  ? rows.map((r) => '<tr><td>' + esc(r.filename || '') + '</td><td>' + esc(r.total_score) + '</td><td>' + esc(r.created_at || '') + '</td></tr>').join('')
                  : '<tr><td colspan="3">暂无施组评分数据</td></tr>') +
                '</table>';
              setResultHtml(cfg.resultId, html);
              return true;
            }
            if (actionId === 'btnRefreshGroundTruthSubmissionOptions') {
              const sel = document.getElementById('groundTruthSubmissionSelect');
              const subs = Array.isArray(data) ? data : [];
              if (sel) {
                sel.innerHTML = '';
                const lead = document.createElement('option');
                lead.value = '';
                lead.textContent = subs.length ? '-- 请选择步骤4已上传施组文件 --' : '-- 暂无施组，请先在步骤4上传 --';
                sel.appendChild(lead);
                subs.forEach((s) => {
                  const opt = document.createElement('option');
                  opt.value = String((s && s.id) || '');
                  opt.textContent = String((s && s.filename) || '未命名施组');
                  sel.appendChild(opt);
                });
              }
              setResult(cfg.resultId, '施组选项刷新完成：共 ' + subs.length + ' 份。', false);
              return true;
            }
            setResultHtml(cfg.resultId, '<pre>' + esc(text || '{}') + '</pre>');
            return true;
          }

          window.__zhifeiFallbackClick = function (ev, actionId) {
            if (ev) {
              if (typeof ev.preventDefault === 'function') ev.preventDefault();
              if (typeof ev.stopPropagation === 'function') ev.stopPropagation();
              if (typeof ev.stopImmediatePropagation === 'function') ev.stopImmediatePropagation();
            }
            runEarlyAction(actionId);
            return false;
          };
        })();
      </script>

      <div class="section card">
        <h2>1) 创建项目</h2>
        <form id="createProject" method="post" action="/web/create_project">
          项目名称：<input name="name" placeholder="例如：XX标段施组评审" />
          <button type="submit" id="btnCreateProject">创建</button>
        </form>
        __CREATE_NOTICE_HTML__
        <p id="createProjectMessage" style="margin:8px 0 0 0;font-size:13px;min-height:1.2em"></p>
        <p style="margin:4px 0 0 0;font-size:13px;color:#64748b">创建后可从下方下拉选择项目，或复制返回的 id 使用。</p>
      </div>

      <div class="section card">
        <h2>2) 选择项目</h2>
        <div class="toolbar">
          <button type="button" id="refreshProjects">刷新项目列表</button>
          <span style="margin-left:4px">项目：</span>
          <select id="projectSelect">
            <option value="">-- 请先刷新并选择项目 --</option>
            __PROJECT_OPTIONS__
          </select>
          <span id="currentProjectTag" style="margin-left:4px;font-size:12px;color:#475569"></span>
          <form id="deleteProjectForm" method="post" action="/web/delete_project" class="inline-form">
            <input type="hidden" name="project_id" id="deleteProjectId" value="__SELECTED_PROJECT_ID__" />
            <button type="submit" id="deleteCurrentProject" class="secondary" style="background:#dc2626">删除当前项目</button>
          </form>
        </div>
        <details style="margin-top:8px">
          <summary style="cursor:pointer;color:#334155;font-size:13px">高级工具（系统诊断 / 评分体系 / 分析包）</summary>
          <div class="toolbar" style="margin-top:8px">
            <button type="button" id="btnSelfCheck" class="secondary">系统自检</button>
            <button type="button" id="btnScoringFactors" class="secondary">评分体系一览</button>
            <button type="button" id="btnScoringFactorsMd" class="secondary">评分体系Markdown</button>
            <button type="button" id="btnAnalysisBundle" class="secondary">项目分析包</button>
            <button type="button" id="btnAnalysisBundleDownload" class="secondary">下载分析包(.md)</button>
            <button type="button" id="btnCleanupE2EProjects" class="secondary" style="background:#b45309">清理E2E测试项目</button>
          </div>
        </details>
        <p id="selectProjectMessage" style="margin:8px 0 0 0;font-size:13px;min-height:1.2em"></p>
        <div id="selfCheckResult" class="result-block" style="display:none"></div>
        <div id="scoringFactorsResult" class="result-block" style="display:none"></div>
        <p class="muted">下方所有操作将使用选中的项目。选择项目后建议先上传项目资料（招标、清单等），再上传施组进行评分。删除项目会同时删除该项目全部资料与记录。</p>
      </div>

      <div class="section card" id="section-materials">
        <h2>3) 项目资料</h2>
        <p style="font-size:13px;color:#64748b;margin:-8px 0 8px 0">支持 .txt、.pdf、.doc、.docx、.json、.xlsx/.xls。用于项目投喂包与学习进化。创建项目后建议先上传招标/清单等资料。</p>
        <div style="margin-bottom:10px">
          <strong>本项目资料列表</strong>
          <button type="button" id="btnRefreshMaterials" class="secondary" style="margin-left:8px">刷新</button>
        </div>
        <table id="materialsTable"><thead><tr><th>文件名</th><th>上传时间</th><th>操作</th></tr></thead><tbody>__MATERIAL_ROWS__</tbody></table>
        <p id="materialsEmpty" style="font-size:13px;color:#64748b;margin:6px 0 0 0;display:__MATERIALS_EMPTY_DISPLAY__">暂无资料，请下方添加。</p>
        <div class="upload-box">
          <form id="uploadMaterial" method="post" action="/web/upload_materials" enctype="multipart/form-data" class="inline-form">
            <strong>添加资料：</strong>
            <input type="hidden" name="project_id" id="uploadMaterialProjectId" value="__SELECTED_PROJECT_ID__" />
            <input type="file" name="file" accept=".txt,.pdf,.doc,.docx,.json,.xlsx,.xls" multiple />
            <button type="submit" id="btnUploadMaterials" onclick="if (window.__zhifeiFallbackClick) { return window.__zhifeiFallbackClick(event, 'btnUploadMaterials'); } return true;">上传资料</button>
            <span class="note">支持一次选择多个文件（Mac 按 Command，Windows 按 Ctrl）。</span>
          </form>
          <p id="materialsActionStatus" style="margin:6px 0 0 0;font-size:12px;color:#475569;min-height:1.2em"></p>
        </div>
      </div>

      <div class="section card">
        <h2>2.5) 青天评标关注度（16维）</h2>
        <p style="font-size:12px;color:#64748b;margin:-4px 0 10px 0">先设置16维关注度，再点击“应用到本项目并重算”。同一项目内所有施组将统一按该配置重算，历史快照会保留。</p>
        <div id="expertProfileStatus" style="font-size:13px;color:#334155;margin-bottom:8px">__EXPERT_PROFILE_STATUS__</div>
        <div id="expertWeightsPanel" style="display:grid;grid-template-columns:1fr;gap:8px;margin-bottom:10px">__EXPERT_WEIGHTS_ROWS__</div>
        <div style="font-size:13px;color:#0f172a;margin-bottom:8px">
          当前归一化权重（%）：<span id="expertWeightsSummary">__EXPERT_WEIGHTS_SUMMARY__</span>
        </div>
        <div>
          <button type="button" id="btnWeightsReset" class="secondary" onclick="return window.__zhifeiFallbackClick(event, 'btnWeightsReset')">重置默认(全部=5)</button>
          <button type="button" id="btnWeightsSave" class="secondary" onclick="return window.__zhifeiFallbackClick(event, 'btnWeightsSave')">保存为专家配置</button>
          <button type="button" id="btnWeightsApply" onclick="return window.__zhifeiFallbackClick(event, 'btnWeightsApply')">应用到本项目并重算所有施组</button>
        </div>
      </div>

      <div class="section card" id="section-shigong">
        <h2>4) 项目施组</h2>
        <p style="font-size:13px;color:#64748b;margin:-8px 0 8px 0">每份施组单独打分。支持 .txt、.docx、.pdf、.json、.xlsx/.xls。基于下方列表进行对比与洞察。</p>
        <div style="margin-bottom:10px">
          <strong>本项目施组列表</strong>
          <button type="button" id="btnRefreshSubmissions" class="secondary" style="margin-left:8px">刷新</button>
        </div>
        <table id="submissionsTable"><thead><tr><th>文件名</th><th>总分</th><th>上传时间</th><th>操作</th></tr></thead><tbody>__SUBMISSION_ROWS__</tbody></table>
        <p id="submissionsEmpty" style="font-size:13px;color:#64748b;margin:6px 0 0 0;display:__SUBMISSIONS_EMPTY_DISPLAY__">暂无施组，请下方添加。</p>
        <div class="upload-box">
          <form id="uploadShigong" method="post" action="/web/upload_shigong" enctype="multipart/form-data" class="inline-form">
            <strong>添加施组：</strong>
            <input type="hidden" name="project_id" id="uploadShigongProjectId" value="__SELECTED_PROJECT_ID__" />
            <input type="file" name="file" accept=".txt,.docx,.pdf,.json,.xlsx,.xls" multiple />
            <button type="submit" id="btnUploadShigong" name="submit_action" value="upload" onclick="if (window.__zhifeiFallbackClick) { return window.__zhifeiFallbackClick(event, 'btnUploadShigong'); } return true;">上传施组</button>
            <button type="submit" id="btnScoreShigong" class="secondary" formaction="/web/score_shigong" name="submit_action" value="score" onclick="if (window.__zhifeiFallbackClick) { return window.__zhifeiFallbackClick(event, 'btnScoreShigong'); } return true;">评分施组</button>
            <span style="margin-left:8px;color:#334155;font-size:13px">满分标准：</span>
            <select id="scoreScaleSelect" name="score_scale_max" style="margin-left:4px">
              <option value="100">100分制</option>
              <option value="5">5分制</option>
            </select>
            <span class="note">支持一次选择多个文件（Mac 按 Command，Windows 按 Ctrl）。</span>
          </form>
          <p id="shigongActionStatus" style="margin:6px 0 0 0;font-size:12px;color:#475569;min-height:1.2em"></p>
        </div>
      </div>

      <div class="section card">
        <h2>5) 对比与洞察</h2>
        <p style="font-size:12px;color:#64748b;margin:-4px 0 8px 0">对比排名：看多份施组分数排序；对比报告：看叙述性差异；洞察：看弱项与扣分建议；学习画像：生成维度权重供后续评分参考。</p>
        <div class="action-row">
          <button type="button" id="btnCompare" onclick="return window.__zhifeiFallbackClick(event, 'btnCompare')">对比排名</button>
          <button type="button" id="btnCompareReport" class="secondary" onclick="return window.__zhifeiFallbackClick(event, 'btnCompareReport')">对比报告（叙述）</button>
          <button type="button" id="btnInsights" class="secondary" onclick="return window.__zhifeiFallbackClick(event, 'btnInsights')">洞察</button>
          <button type="button" id="btnLearning" class="secondary" onclick="return window.__zhifeiFallbackClick(event, 'btnLearning')">生成学习画像</button>
        </div>
        <div id="compareResult" class="result-block" style="display:none"></div>
        <div id="compareReportResult" class="result-block" style="display:none"></div>
        <div id="insightsResult" class="result-block" style="display:none"></div>
        <div id="learningResult" class="result-block" style="display:none"></div>
      </div>

      <div class="section card">
        <h2>6) 自适应优化</h2>
        <p style="font-size:12px;color:#64748b;margin:-4px 0 8px 0">基于本项目施组扣分统计给出词库/规则优化建议 → 生成补丁 → 验证效果 → 应用补丁（需 API Key）。</p>
        <div class="action-row" style="margin-bottom:6px">
          <button type="button" id="btnAdaptive" onclick="return window.__zhifeiFallbackClick(event, 'btnAdaptive')">自适应建议</button>
          <button type="button" id="btnAdaptivePatch" class="secondary" onclick="return window.__zhifeiFallbackClick(event, 'btnAdaptivePatch')">生成补丁</button>
        </div>
        <div class="action-row">
          <button type="button" id="btnAdaptiveValidate" class="secondary" onclick="return window.__zhifeiFallbackClick(event, 'btnAdaptiveValidate')">验证效果</button>
          <button type="button" id="btnAdaptiveApply" class="secondary" onclick="return window.__zhifeiFallbackClick(event, 'btnAdaptiveApply')">应用补丁（需 API Key）</button>
        </div>
        <div id="adaptiveResult" class="result-block" style="display:none"></div>
        <div id="adaptivePatchResult" class="result-block" style="display:none"></div>
        <div id="adaptiveValidateResult" class="result-block" style="display:none"></div>
        <div id="adaptiveApplyResult" class="result-block" style="display:none"></div>
      </div>

      <div class="section card">
        <h2>7) 自我学习与进化</h2>
        <p style="font-size:13px;color:#64748b;margin:0 0 6px 0">上传项目投喂包（招标/清单/图纸等合并文本），录入交易中心真实评标结果（5评委+最终得分），系统学习高分逻辑并生成编制指导。</p>
        <p style="font-size:12px;color:#475569;margin:0 0 10px 0">系统会将学习到的高分逻辑与编制指导持久保存，并用于本项目的预评分权重与编制系统指令；再次执行学习进化可基于新录入的真实评标升级这些经验。</p>
        <div style="margin-bottom:10px">
          <strong>真实评标列表（本项目 / 其它项目）</strong>
          <span style="margin-left:8px">查看范围：</span>
          <select id="groundTruthScope">
            <option value="current">本项目</option>
            <option value="other">其它项目</option>
          </select>
          <select id="groundTruthOtherProject" style="margin-left:8px;display:none">
            <option value="">-- 选择要查看的项目 --</option>
          </select>
          <button type="button" id="btnRefreshGroundTruth" class="secondary" style="margin-left:8px" onclick="return window.__zhifeiFallbackClick(event, 'btnRefreshGroundTruth')">刷新</button>
        </div>
        <table id="groundTruthTable"><thead><tr><th>序号</th><th>施组摘要</th><th>评委1–5分</th><th>最终分</th><th>来源</th><th>操作</th></tr></thead><tbody></tbody></table>
        <p id="groundTruthEmpty" style="font-size:13px;color:#64748b;margin:6px 0 10px 0;display:none">暂无真实评标，请下方录入。</p>
        <div style="margin-bottom:10px">
          <strong>投喂包（即本项目资料）：</strong>上传后可在下方查看文件名与上传时间。
        </div>
        <div class="field-group">
          <label>上传文件：</label>
          <input type="file" id="feedFile" accept=".txt,.pdf,.doc,.docx,.json,.xlsx,.xls" multiple style="margin-left:8px" />
          <button type="button" id="btnUploadFeed" onclick="return window.__zhifeiFallbackClick(event, 'btnUploadFeed')">上传并保存投喂包</button>
          <span class="note">支持一次选择多个文件。</span>
          <p id="feedActionStatus" style="margin:6px 0 0 0;font-size:12px;color:#475569;min-height:1.2em"></p>
        </div>
        <div style="margin-bottom:10px">
          <strong>已上传投喂包（项目资料）</strong>
          <button type="button" id="btnRefreshFeedMaterials" class="secondary" style="margin-left:8px" onclick="return window.__zhifeiFallbackClick(event, 'btnRefreshFeedMaterials')">刷新</button>
        </div>
        <table id="feedMaterialsTable"><thead><tr><th>文件名</th><th>上传时间</th><th>操作</th></tr></thead><tbody></tbody></table>
        <p id="feedMaterialsEmpty" style="font-size:13px;color:#64748b;margin:6px 0 10px 0;display:none">暂无投喂包，请在上方或「3) 项目资料」上传。</p>
        <div style="margin-bottom:10px">
          <strong>真实评标录入：</strong>从步骤4已上传施组中选择 + 评委分 + 最终分 →
          <button type="button" id="btnAddGroundTruth" onclick="return window.__zhifeiFallbackClick(event, 'btnAddGroundTruth')">录入所选文件</button>
        </div>
        <div class="field-group">
          <label>施组文件：</label>
          <select id="groundTruthSubmissionSelect" style="margin-left:8px;min-width:360px">
            <option value="">-- 请选择步骤4已上传施组文件 --</option>
          </select>
          <button type="button" id="btnRefreshGroundTruthSubmissionOptions" class="secondary" style="margin-left:8px" onclick="return window.__zhifeiFallbackClick(event, 'btnRefreshGroundTruthSubmissionOptions')">刷新施组选项</button>
          <span class="note">无需重复上传，直接复用「4) 项目施组」已上传文件。</span>
        </div>
        <div class="field-group">
          评委1：<input type="number" id="gtJ1" step="0.01" style="width:70px" />
          评委2：<input type="number" id="gtJ2" step="0.01" style="width:70px" />
          评委3：<input type="number" id="gtJ3" step="0.01" style="width:70px" />
          评委4：<input type="number" id="gtJ4" step="0.01" style="width:70px" />
          评委5：<input type="number" id="gtJ5" step="0.01" style="width:70px" />
          最终得分：<input type="number" id="gtFinal" step="0.01" style="width:70px" />
        </div>
        <div class="action-row" style="margin-bottom:10px">
          <button type="button" id="btnEvolve" onclick="return window.__zhifeiFallbackClick(event, 'btnEvolve')">学习进化（根据已录入真实评标生成高分逻辑与编制指导）</button>
          <button type="button" id="btnWritingGuidance" class="secondary" onclick="return window.__zhifeiFallbackClick(event, 'btnWritingGuidance')">查看编制指导</button>
          <button type="button" id="btnCompilationInstructions" class="secondary" onclick="return window.__zhifeiFallbackClick(event, 'btnCompilationInstructions')">编制系统指令（可导出为编制约束）</button>
        </div>
        <details style="margin:12px 0 8px 0;padding:10px;border:1px dashed #cbd5e1;border-radius:8px;background:#f8fafc">
          <summary style="cursor:pointer"><strong>V2 反演校准闭环（高级，可忽略）</strong></summary>
          <div style="margin-top:8px">
            <button type="button" id="btnRebuildDelta" class="secondary">重建 DELTA_CASE</button>
            <button type="button" id="btnRebuildSamples" class="secondary">重建 FEATURE_ROW</button>
            <button type="button" id="btnTrainCalibratorV2" class="secondary">训练并部署校准器</button>
            <button type="button" id="btnApplyCalibPredict" class="secondary">回填预测分</button>
            <button type="button" id="btnAutoRunReflection">一键闭环执行</button>
            <button type="button" id="btnEvalMetricsV2" class="secondary">评估 V1/V2/校准</button>
            <button type="button" id="btnEvalSummaryV2" class="secondary">跨项目汇总评估</button>
          </div>
          <div style="margin-top:8px">
            补丁类型：
            <select id="patchType" style="margin:0 8px">
              <option value="threshold">threshold</option>
              <option value="requirement">requirement</option>
              <option value="keywords">keywords</option>
            </select>
            <button type="button" id="btnMinePatchV2" class="secondary">挖掘 PATCH_PACKAGE</button>
            <input id="patchIdInput" placeholder="补丁ID（可留空自动取最新）" style="width:260px;margin-left:8px" />
            <button type="button" id="btnShadowPatchV2" class="secondary">影子评估</button>
            <button type="button" id="btnDeployPatchV2" class="secondary">发布补丁</button>
            <button type="button" id="btnRollbackPatchV2" class="secondary">回滚补丁</button>
          </div>
        </details>
        <div id="evolveResult" class="result-block" style="display:none"></div>
        <div id="compilationInstructionsResult" class="result-block" style="display:none"></div>
        <div id="guidanceResult" class="result-block" style="display:none"></div>
        <div id="deltaResult" class="result-block" style="display:none"></div>
        <div id="sampleResult" class="result-block" style="display:none"></div>
        <div id="calibTrainResult" class="result-block" style="display:none"></div>
        <div id="patchResult" class="result-block" style="display:none"></div>
        <div id="patchShadowResult" class="result-block" style="display:none"></div>
        <div id="patchDeployResult" class="result-block" style="display:none"></div>
        <div id="evalResult" class="result-block" style="display:none"></div>
      </div>

      <div class="section card">
        <h2>原始输出（最后一次请求）</h2>
        <pre id="output">（操作后这里显示原始 JSON）</pre>
      </div>

      <script>
        (function () {
          const BOOTSTRAP_SCORE_SCALE_MAX = "__PROJECT_SCORE_SCALE_MAX__";
          const bootScoreScaleEl = document.getElementById('scoreScaleSelect');
          if (bootScoreScaleEl) {
            bootScoreScaleEl.value = BOOTSTRAP_SCORE_SCALE_MAX === '5' ? '5' : '100';
          }
          const FALLBACK_ACTIONS = Object.assign(window.__ZHIFEI_FALLBACK_ACTIONS || {}, {
            btnWeightsSave: { resultId: 'output', method: 'PUT', path: (pid) => '/api/v1/projects/' + pid + '/expert-profile', loading: '专家配置保存中...' },
            btnWeightsApply: { resultId: 'output', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/rescore', loading: '按当前关注度重算中...' },
            btnUploadMaterials: { resultId: 'materialsActionStatus', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/materials', loading: '资料上传中...' },
            btnUploadShigong: { resultId: 'shigongActionStatus', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/shigong', loading: '施组上传中...' },
            btnScoreShigong: { resultId: 'shigongActionStatus', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/rescore', loading: '施组评分中...' },
            btnCompare: { resultId: 'compareResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/compare', loading: '对比排名加载中...' },
            btnCompareReport: { resultId: 'compareReportResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/compare_report', loading: '对比报告生成中...' },
            btnInsights: { resultId: 'insightsResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/insights', loading: '洞察分析中...' },
            btnLearning: { resultId: 'learningResult', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/learning', loading: '学习画像生成中...' },
            btnAdaptive: { resultId: 'adaptiveResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/adaptive', loading: '自适应建议生成中...' },
            btnAdaptivePatch: { resultId: 'adaptivePatchResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/adaptive_patch', loading: '补丁生成中...' },
            btnAdaptiveValidate: { resultId: 'adaptiveValidateResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/adaptive_validate', loading: '验证效果计算中...' },
            btnAdaptiveApply: { resultId: 'adaptiveApplyResult', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/adaptive_apply', loading: '应用补丁中...' },
            btnRefreshGroundTruth: { resultId: 'evolveResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/ground_truth', loading: '真实评标列表刷新中...' },
            btnRefreshGroundTruthSubmissionOptions: { resultId: 'evolveResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/submissions', loading: '施组选项刷新中...' },
            btnRefreshFeedMaterials: { resultId: 'evolveResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/materials', loading: '投喂包列表刷新中...' },
            btnUploadFeed: { resultId: 'evolveResult', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/materials', loading: '投喂包上传中...' },
            btnAddGroundTruth: { resultId: 'evolveResult', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/ground_truth/from_submission', loading: '真实评标录入中...' },
            btnEvolve: { resultId: 'evolveResult', method: 'POST', path: (pid) => '/api/v1/projects/' + pid + '/evolve', loading: '学习进化执行中...' },
            btnWritingGuidance: { resultId: 'guidanceResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/writing_guidance', loading: '正在生成编制指导...' },
            btnCompilationInstructions: { resultId: 'compilationInstructionsResult', method: 'GET', path: (pid) => '/api/v1/projects/' + pid + '/compilation_instructions', loading: '正在生成编制系统指令...' },
          });
          window.__ZHIFEI_FALLBACK_ACTIONS = FALLBACK_ACTIONS;

          function fallbackGetProjectId() {
            const sel = document.getElementById('projectSelect');
            return (sel && sel.value) ? sel.value : '';
          }
          function fallbackApiKey() {
            try { return localStorage.getItem('api_key') || ''; } catch (_) { return ''; }
          }
          function fallbackCollectWeightsRaw() {
            const m = {};
            const sliders = Array.from(document.querySelectorAll('input[type="range"][data-dim]') || []);
            sliders.forEach((el) => {
              const dim = String(el.getAttribute('data-dim') || '');
              if (!dim) return;
              const v = parseInt(String(el.value || '5'), 10);
              m[dim] = Number.isFinite(v) ? Math.max(0, Math.min(10, v)) : 5;
            });
            return m;
          }
          function fallbackApplyWeightUi(rawMap) {
            const dims = Array.from({ length: 16 }, (_, i) => String(i + 1).padStart(2, '0'));
            const effective = {};
            let sum = 0;
            dims.forEach((d) => {
              const v = Number(rawMap && rawMap[d] != null ? rawMap[d] : 5);
              const vv = Number.isFinite(v) ? Math.max(0, Math.min(10, v)) : 5;
              effective[d] = vv;
              const slider = document.getElementById('w_' + d);
              if (slider) slider.value = String(vv);
              const rawEl = document.getElementById('w_raw_' + d);
              if (rawEl) rawEl.textContent = String(vv);
              sum += (0.5 + vv / 10.0);
            });
            const safeSum = sum > 0 ? sum : (dims.length * 1.0);
            dims.forEach((d) => {
              const mv = 0.5 + (effective[d] / 10.0);
              const pct = mv / safeSum * 100.0;
              const normEl = document.getElementById('w_norm_' + d);
              if (normEl) normEl.textContent = pct.toFixed(2) + '%';
            });
            const summary = dims.map((d) => {
              const mv = 0.5 + (effective[d] / 10.0);
              return d + ':' + (mv / safeSum * 100.0).toFixed(2) + '%';
            }).join(' | ');
            const summaryEl = document.getElementById('expertWeightsSummary');
            if (summaryEl) summaryEl.textContent = summary;
          }
          function fallbackSetOutput(text) {
            const out = document.getElementById('output');
            if (out) out.textContent = String(text || '');
          }
          function fallbackSetResult(resultId, msg, isError) {
            const el = resultId ? document.getElementById(resultId) : null;
            if (!el) return;
            el.style.display = 'block';
            const klass = isError ? 'error' : 'success';
            el.innerHTML = '<span class="' + klass + '">' + String(msg || '').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</span>';
          }
          function fallbackSetLoading(resultId, msg) {
            const el = resultId ? document.getElementById(resultId) : null;
            if (!el) return;
            el.style.display = 'block';
            el.innerHTML = '<span style="color:#334155">' + String(msg || '处理中...').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</span>';
          }
          function fallbackEscapeHtml(v) {
            return String(v == null ? '' : v)
              .replace(/&/g, '&amp;')
              .replace(/</g, '&lt;')
              .replace(/>/g, '&gt;')
              .replace(/"/g, '&quot;')
              .replace(/'/g, '&#39;');
          }
          function fallbackSetResultHtml(resultId, html) {
            const el = resultId ? document.getElementById(resultId) : null;
            if (!el) return;
            el.style.display = 'block';
            el.innerHTML = html || '';
          }
          function fallbackParseJson(text) {
            try { return JSON.parse(text || '{}'); } catch (_) { return {}; }
          }
          function fallbackFormatPageHint(raw) {
            const s = String(raw == null ? '' : raw).trim();
            if (!s) return '页码待核对';
            const m = s.match(/(\\d+)/);
            if (m && m[1]) return '第' + m[1] + '页';
            return s;
          }
          function fallbackRenderActionSuccess(actionId, resultId, status, text) {
            const aid = String(actionId || '').trim();
            const data = fallbackParseJson(text);
            if (aid === 'btnCompare') {
              const rows = Array.isArray(data.rankings) ? data.rankings : [];
              const html = '<strong>排名</strong><table><tr><th>文件名</th><th>总分</th><th>时间</th></tr>' +
                (rows.length
                  ? rows.map((r) => '<tr><td>' + fallbackEscapeHtml(r.filename || '') + '</td><td>' + fallbackEscapeHtml(r.total_score) + '</td><td>' + fallbackEscapeHtml(r.created_at || '') + '</td></tr>').join('')
                  : '<tr><td colspan="3">暂无施组评分数据</td></tr>') +
                '</table>';
              fallbackSetResultHtml(resultId, html);
              return true;
            }
            if (aid === 'btnCompareReport') {
              const summary = fallbackEscapeHtml(data.summary || '');
              const top = data.top_submission || {};
              const bottom = data.bottom_submission || {};
              const keyDiffs = (Array.isArray(data.key_diffs) ? data.key_diffs : []).slice(0, 5);
              const rankings = Array.isArray(data.rankings) ? data.rankings : [];
              const sourceCards = Array.isArray(data.submission_optimization_cards) ? data.submission_optimization_cards : [];
              const cards = sourceCards.slice(0, 5);
              // 若后端仅返回部分优化卡片，按排名补齐到 5 份，避免“只看到 3 份”
              if (cards.length < 5 && rankings.length > cards.length) {
                const existing = new Set(cards.map((c) => String((c && c.filename) || '')));
                rankings.forEach((r) => {
                  if (cards.length >= 5) return;
                  const fn = String((r && r.filename) || '');
                  if (!fn || existing.has(fn)) return;
                  cards.push({
                    filename: fn,
                    total_score: r && r.total_score,
                    target_score: top && top.total_score,
                    recommendations: [],
                  });
                  existing.add(fn);
                });
              }
              const cardHtml = cards.map((c) => {
                const recs = (Array.isArray(c.recommendations) ? c.recommendations : []).slice(0, 2);
                return '<div style="margin:8px 0;padding:8px;border:1px solid #e2e8f0;border-radius:6px">'
                  + '<div><strong>' + fallbackEscapeHtml(c.filename || '-') + '</strong>（当前 ' + fallbackEscapeHtml(c.total_score) + '，目标 ' + fallbackEscapeHtml(c.target_score) + '）</div>'
                  + (recs.length
                    ? '<ol style="margin:6px 0 0 18px">' + recs.map((r) => '<li>' + fallbackEscapeHtml(fallbackFormatPageHint(r.page_hint || '')) + '：'
                      + fallbackEscapeHtml(r.issue || '需补充可量化执行要素')
                      + '；优化建议：' + fallbackEscapeHtml(r.rewrite_instruction || r.action || '按“责任人-频次-阈值-验收”四段式补齐。')
                      + '</li>').join('') + '</ol>'
                    : '<div style="color:#64748b;margin-top:6px">当前未提取到该文件的具体优化动作，建议先补充证据片段后再生成。</div>')
                  + '</div>';
              }).join('');
              const html = ''
                + '<p><strong>摘要</strong>：' + (summary || '无') + '</p>'
                + '<p><strong>最高分</strong>：' + fallbackEscapeHtml(top.filename || '-') + '（' + fallbackEscapeHtml(top.total_score) + '）'
                + '，<strong>最低分</strong>：' + fallbackEscapeHtml(bottom.filename || '-') + '（' + fallbackEscapeHtml(bottom.total_score) + '）</p>'
                + (keyDiffs.length
                  ? '<strong>关键差距维度（Top5）</strong><table><tr><th>维度</th><th>分差</th></tr>'
                    + keyDiffs.map((d) => '<tr><td>' + fallbackEscapeHtml((d.dimension || d.dim_id || '-') + ' ' + (d.dimension_name || '')) + '</td><td>' + fallbackEscapeHtml(d.delta) + '</td></tr>').join('')
                    + '</table>'
                  : '')
                + '<strong>逐文件优化清单（精简执行版）</strong>'
                + (cardHtml || '<div style="color:#64748b">暂无优化清单。</div>');
              fallbackSetResultHtml(resultId, html);
              return true;
            }
            if (aid === 'btnInsights') {
              const weak = Array.isArray(data.weakest_dims) ? data.weakest_dims : [];
              const recs = Array.isArray(data.recommendations) ? data.recommendations : [];
              const html = '<strong>洞察结果</strong>'
                + (weak.length ? ('<ul>' + weak.map((d) => '<li>' + fallbackEscapeHtml((d.dimension || d.dimension_id || '') + '：' + (d.avg_score || d.avg || '-')) + '</li>').join('') + '</ul>') : '<p>暂无弱项维度。</p>')
                + (recs.length ? ('<strong>建议</strong><ul>' + recs.map((r) => '<li>' + fallbackEscapeHtml((r.reason || '') + ' — ' + (r.action || '')) + '</li>').join('') + '</ul>') : '');
              fallbackSetResultHtml(resultId, html);
              return true;
            }
            if (aid === 'btnAdaptive' || aid === 'btnAdaptivePatch' || aid === 'btnAdaptiveValidate' || aid === 'btnAdaptiveApply') {
              fallbackSetResultHtml(resultId, '<strong>自适应结果</strong><pre>' + fallbackEscapeHtml(text || '{}') + '</pre>');
              return true;
            }
            if (aid === 'btnScoreShigong') {
              const updated = Number(
                (data && (data.updated_submissions ?? data.reports_generated ?? data.submission_count)) || 0
              );
              const scaleLabel = (data && data.score_scale_label) ? String(data.score_scale_label) : selectedScoreScaleLabel();
              fallbackSetResult(resultId, '评分完成（' + scaleLabel + '）：已重算 ' + updated + ' 份。', false);
              return true;
            }
            if (aid === 'btnUploadMaterials' || aid === 'btnUploadShigong' || aid === 'btnScoreShigong' || aid === 'btnLearning' || aid === 'btnEvolve' || aid === 'btnRefreshGroundTruth' || aid === 'btnUploadFeed' || aid === 'btnAddGroundTruth' || aid === 'btnRefreshFeedMaterials' || aid === 'btnWritingGuidance' || aid === 'btnCompilationInstructions') {
              fallbackSetResultHtml(resultId, '<span class="success">[' + fallbackEscapeHtml(aid) + '] 请求成功 (HTTP ' + fallbackEscapeHtml(status) + ')</span><pre>' + fallbackEscapeHtml(text || '{}') + '</pre>');
              return true;
            }
            return false;
          }
          function fallbackStopEvent(ev) {
            if (!ev) return;
            if (typeof ev.preventDefault === 'function') ev.preventDefault();
            if (typeof ev.stopPropagation === 'function') ev.stopPropagation();
            if (typeof ev.stopImmediatePropagation === 'function') ev.stopImmediatePropagation();
          }
          function fallbackJsonBodyHeaders() {
            const headers = { 'Content-Type': 'application/json' };
            const key = fallbackApiKey();
            if (key) headers['X-API-Key'] = key;
            return headers;
          }
          function fallbackAuthHeaders() {
            const headers = {};
            const key = fallbackApiKey();
            if (key) headers['X-API-Key'] = key;
            return headers;
          }
          async function fallbackRefreshMaterialsTable(projectId) {
            const pid = String(projectId || '').trim();
            if (!pid) return;
            const table = document.getElementById('materialsTable');
            const tbody = table ? table.querySelector('tbody') : null;
            const emptyEl = document.getElementById('materialsEmpty');
            if (tbody) tbody.innerHTML = '';
            let res;
            let text = '';
            let rows = [];
            try {
              res = await fetch('/api/v1/projects/' + encodeURIComponent(pid) + '/materials?t=' + Date.now(), {
                method: 'GET',
                headers: fallbackAuthHeaders(),
                cache: 'no-store',
              });
              text = await res.text();
              rows = fallbackParseJson(text);
            } catch (_) {
              if (emptyEl) {
                emptyEl.textContent = '资料列表加载失败，请稍后重试。';
                emptyEl.style.display = 'block';
              }
              return;
            }
            if (!res.ok || !Array.isArray(rows)) {
              if (emptyEl) {
                emptyEl.textContent = '资料列表加载失败（HTTP ' + String((res && res.status) || 0) + '）';
                emptyEl.style.display = 'block';
              }
              return;
            }
            if (!rows.length) {
              if (emptyEl) {
                emptyEl.textContent = '暂无资料，请下方添加。';
                emptyEl.style.display = 'block';
              }
              return;
            }
            if (emptyEl) emptyEl.style.display = 'none';
            rows.forEach((m) => {
              const tr = document.createElement('tr');
              const mid = fallbackEscapeHtml(String((m && m.id) || ''));
              const fn = fallbackEscapeHtml(String((m && m.filename) || ''));
              const createdAt = fallbackEscapeHtml(String((m && m.created_at) || '').slice(0, 19));
              tr.innerHTML =
                '<td>' + fn + '</td>'
                + '<td>' + createdAt + '</td>'
                + '<td><button type="button" class="btn-danger js-delete-material" data-material-id="' + mid + '" data-filename="' + fn + '">删除</button></td>';
              if (tbody) tbody.appendChild(tr);
            });
          }
          async function fallbackRefreshSubmissionsTable(projectId) {
            const pid = String(projectId || '').trim();
            if (!pid) return;
            const table = document.getElementById('submissionsTable');
            const tbody = table ? table.querySelector('tbody') : null;
            const emptyEl = document.getElementById('submissionsEmpty');
            if (tbody) tbody.innerHTML = '';
            let res;
            let text = '';
            let rows = [];
            try {
              res = await fetch('/api/v1/projects/' + encodeURIComponent(pid) + '/submissions?t=' + Date.now(), {
                method: 'GET',
                headers: fallbackAuthHeaders(),
                cache: 'no-store',
              });
              text = await res.text();
              rows = fallbackParseJson(text);
            } catch (_) {
              if (emptyEl) {
                emptyEl.textContent = '施组列表加载失败，请稍后重试。';
                emptyEl.style.display = 'block';
              }
              return;
            }
            if (!res.ok || !Array.isArray(rows)) {
              if (emptyEl) {
                emptyEl.textContent = '施组列表加载失败（HTTP ' + String((res && res.status) || 0) + '）';
                emptyEl.style.display = 'block';
              }
              return;
            }
            if (!rows.length) {
              if (emptyEl) {
                emptyEl.textContent = '暂无施组，请下方添加。';
                emptyEl.style.display = 'block';
              }
              return;
            }
            if (emptyEl) emptyEl.style.display = 'none';
            rows.forEach((s) => {
              const tr = document.createElement('tr');
              const sid = fallbackEscapeHtml(String((s && s.id) || ''));
              const fn = fallbackEscapeHtml(String((s && s.filename) || ''));
              const createdAt = fallbackEscapeHtml(String((s && s.created_at) || '').slice(0, 19));
              const report = (s && s.report) || {};
              const pred = report && report.pred_total_score != null ? report.pred_total_score : null;
              const rule = report && report.rule_total_score != null ? report.rule_total_score : null;
              const llm = report && report.llm_total_score != null ? report.llm_total_score : null;
              const scoringStatus = String((report && report.scoring_status) || '').toLowerCase();
              const isPending = scoringStatus === 'pending';
              let scoreHtml = '-';
              if (isPending) {
                scoreHtml = '<span class="note">待评分</span>';
              } else if (pred != null) {
                scoreHtml = fallbackEscapeHtml(String(pred));
                const notes = [];
                if (rule != null) notes.push('规则: ' + fallbackEscapeHtml(String(rule)));
                if (llm != null) notes.push('LLM: ' + fallbackEscapeHtml(String(llm)));
                if (notes.length) scoreHtml += '<div class="note">' + notes.join(' / ') + '</div>';
              } else if (s && s.total_score != null) {
                scoreHtml = fallbackEscapeHtml(String(s.total_score));
              }
              tr.innerHTML =
                '<td>' + fn + '</td>'
                + '<td>' + scoreHtml + '</td>'
                + '<td>' + createdAt + '</td>'
                + '<td><button type="button" class="btn-danger js-delete-submission" data-submission-id="' + sid + '" data-filename="' + fn + '">删除</button></td>';
              if (tbody) tbody.appendChild(tr);
            });
          }
          async function fallbackRefreshAfter(actionId) {
            const projectId = fallbackGetProjectId();
            if (actionId === 'btnUploadMaterials') {
              if (typeof refreshMaterials === 'function') await Promise.resolve(refreshMaterials(projectId));
              else await fallbackRefreshMaterialsTable(projectId);
              if (typeof refreshFeedMaterials === 'function') await Promise.resolve(refreshFeedMaterials(projectId));
              else await fallbackRefreshMaterialsTable(projectId);
              return;
            }
            if (actionId === 'btnUploadShigong' || actionId === 'btnScoreShigong') {
              if (typeof refreshSubmissions === 'function') await Promise.resolve(refreshSubmissions(projectId));
              else await fallbackRefreshSubmissionsTable(projectId);
              if (typeof refreshGroundTruthSubmissionOptions === 'function') {
                await Promise.resolve(refreshGroundTruthSubmissionOptions(projectId));
              }
              return;
            }
            if (actionId === 'btnUploadFeed') {
              if (typeof refreshMaterials === 'function') await Promise.resolve(refreshMaterials(projectId));
              else await fallbackRefreshMaterialsTable(projectId);
              if (typeof refreshFeedMaterials === 'function') await Promise.resolve(refreshFeedMaterials(projectId));
              else await fallbackRefreshMaterialsTable(projectId);
              return;
            }
            if (actionId === 'btnRefreshFeedMaterials') {
              if (typeof refreshFeedMaterials === 'function') await Promise.resolve(refreshFeedMaterials(projectId));
              else await fallbackRefreshMaterialsTable(projectId);
              return;
            }
            if (actionId === 'btnAddGroundTruth' || actionId === 'btnRefreshGroundTruth') {
              if (typeof refreshGroundTruth === 'function') await Promise.resolve(refreshGroundTruth(projectId));
              return;
            }
            if (actionId === 'btnRefreshGroundTruthSubmissionOptions') {
              if (typeof refreshGroundTruthSubmissionOptions === 'function') {
                await Promise.resolve(refreshGroundTruthSubmissionOptions(projectId));
              }
              return;
            }
            if (actionId === 'btnEvolve') {
              if (typeof refreshGroundTruth === 'function') await Promise.resolve(refreshGroundTruth(projectId));
            }
          }
          function fallbackRenderPayloadForAction(actionId, projectId) {
            if (actionId === 'btnWeightsSave') {
              const payload = { name: '', weights_raw: fallbackCollectWeightsRaw(), force_unlock: false };
              return { body: JSON.stringify(payload), headers: fallbackJsonBodyHeaders() };
            }
            if (actionId === 'btnWeightsApply') {
              const payload = {
                scoring_engine_version: 'v2',
                scope: 'project',
                score_scale_max: selectedScoreScaleMax(),
                rebuild_anchors: false,
                rebuild_requirements: false,
                retrain_calibrator: false,
                force_unlock: false,
              };
              return { body: JSON.stringify(payload), headers: fallbackJsonBodyHeaders() };
            }
            if (actionId === 'btnUploadFeed') {
              const fd = new FormData();
              const files = Array.from(((document.getElementById('feedFile') || {}).files) || []);
              files.forEach((f) => fd.append('file', f));
              return { body: fd, headers: fallbackAuthHeaders() };
            }
            if (actionId === 'btnUploadMaterials') {
              const form = document.getElementById('uploadMaterial');
              const fileInput = form && form.querySelector ? form.querySelector('input[name="file"]') : null;
              const files = Array.from((fileInput && fileInput.files) || []);
              const fd = new FormData();
              files.forEach((f) => fd.append('file', f));
              return { body: fd, headers: fallbackAuthHeaders() };
            }
            if (actionId === 'btnUploadShigong') {
              const form = document.getElementById('uploadShigong');
              const fileInput = form && form.querySelector ? form.querySelector('input[name="file"]') : null;
              const files = Array.from((fileInput && fileInput.files) || []);
              const fd = new FormData();
              files.forEach((f) => fd.append('file', f));
              return { body: fd, headers: fallbackAuthHeaders() };
            }
            if (actionId === 'btnScoreShigong') {
              const payload = {
                scoring_engine_version: 'v2',
                scope: 'project',
                score_scale_max: selectedScoreScaleMax(),
                rebuild_anchors: false,
                rebuild_requirements: false,
                retrain_calibrator: false,
                force_unlock: false,
              };
              return { body: JSON.stringify(payload), headers: fallbackJsonBodyHeaders() };
            }
            if (actionId === 'btnAddGroundTruth') {
              const selectedSubmissionId = String(((document.getElementById('groundTruthSubmissionSelect') || {}).value) || '').trim();
              const j1 = parseFloat(((document.getElementById('gtJ1') || {}).value || '0')) || 0;
              const j2 = parseFloat(((document.getElementById('gtJ2') || {}).value || '0')) || 0;
              const j3 = parseFloat(((document.getElementById('gtJ3') || {}).value || '0')) || 0;
              const j4 = parseFloat(((document.getElementById('gtJ4') || {}).value || '0')) || 0;
              const j5 = parseFloat(((document.getElementById('gtJ5') || {}).value || '0')) || 0;
              const finalScore = parseFloat(((document.getElementById('gtFinal') || {}).value || '0')) || 0;
              const payload = {
                submission_id: selectedSubmissionId,
                judge_scores: [j1, j2, j3, j4, j5],
                final_score: finalScore,
                source: '青天大模型',
              };
              return { body: JSON.stringify(payload), headers: fallbackJsonBodyHeaders() };
            }
            if (actionId === 'btnAdaptiveApply') {
              let key = fallbackApiKey();
              if (!key) {
                const prompted = prompt('应用补丁将修改 lexicon 配置，需要 API Key。请输入 X-API-Key（无则留空）：');
                key = prompted == null ? '' : prompted;
              }
              const headers = {};
              if (key) headers['X-API-Key'] = key;
              return { body: null, headers };
            }
            if (actionId === 'btnLearning' || actionId === 'btnEvolve') {
              return { body: null, headers: fallbackAuthHeaders() };
            }
            return { body: null, headers: fallbackAuthHeaders() };
          }
          async function fallbackRunAction(actionId) {
            actionId = String(actionId || '').trim();
            if (actionId === 'btnWeightsReset') {
              fallbackApplyWeightUi({});
              fallbackSetOutput('[btnWeightsReset] 已重置16维关注度到默认值(全部=5)');
              return true;
            }
            const cfg = FALLBACK_ACTIONS[actionId];
            if (!cfg) return false;
            const projectId = fallbackGetProjectId();
            if (!projectId) {
              fallbackSetResult(cfg.resultId, '请先在「2) 选择项目」中选择项目', true);
              fallbackSetOutput('[' + actionId + '] 缺少项目ID');
              return false;
            }
            fallbackSetLoading(cfg.resultId, cfg.loading);
            if (actionId === 'btnWeightsApply') {
              const saved = await fallbackRunAction('btnWeightsSave');
              if (!saved) return false;
            }
            if (actionId === 'btnUploadMaterials' || actionId === 'btnUploadShigong') {
              const formId = actionId === 'btnUploadMaterials' ? 'uploadMaterial' : 'uploadShigong';
              const typeLabel = actionId === 'btnUploadMaterials' ? '资料' : '施组';
              const form = document.getElementById(formId);
              const fileInput = form && form.querySelector ? form.querySelector('input[name="file"]') : null;
              const files = Array.from((fileInput && fileInput.files) || []);
              if (!files.length) {
                fallbackSetResult(cfg.resultId, '请先选择至少 1 个' + typeLabel + '文件。', true);
                fallbackSetOutput('[' + actionId + '] 未选择文件');
                return false;
              }
              let okCount = 0;
              let failCount = 0;
              const details = [];
              const headers = fallbackAuthHeaders();
              for (const f of files) {
                const fd = new FormData();
                fd.append('file', f);
                try {
                  const res = await fetch(cfg.path(projectId), {
                    method: cfg.method || 'POST',
                    headers,
                    body: fd,
                  });
                  const text = await res.text();
                  if (res.ok) {
                    okCount += 1;
                    details.push('[成功] ' + String((f && f.name) || ''));
                  } else {
                    failCount += 1;
                    let detail = text || '';
                    try {
                      const j = JSON.parse(text || '{}');
                      detail = (j && j.detail) || detail;
                    } catch (_) {}
                    details.push('[失败] ' + String((f && f.name) || '') + ' -> HTTP ' + String(res.status || 0) + ' ' + String(detail).slice(0, 120));
                  }
                } catch (err) {
                  failCount += 1;
                  details.push('[失败] ' + String((f && f.name) || '') + ' -> ' + String((err && err.message) || err || '网络异常'));
                }
              }
              const summary = typeLabel + '上传完成：成功 ' + okCount + '，失败 ' + failCount;
              fallbackSetResult(cfg.resultId, summary, failCount > 0);
              fallbackSetOutput('[' + actionId + '] ' + summary + '\\n' + details.join('\\n'));
              if (okCount > 0) await fallbackRefreshAfter(actionId);
              if (fileInput && failCount === 0) fileInput.value = '';
              return failCount === 0;
            }
            if (actionId === 'btnAddGroundTruth') {
              const selectedSubmissionId = String(((document.getElementById('groundTruthSubmissionSelect') || {}).value) || '').trim();
              if (!selectedSubmissionId) {
                fallbackSetResult(cfg.resultId, '请先在“施组文件”下拉框选择步骤4已上传施组。', true);
                fallbackSetOutput('[' + actionId + '] 未选择施组文件');
                return false;
              }
            }
            const req = fallbackRenderPayloadForAction(actionId, projectId);
            let res;
            let text = '';
            try {
              res = await fetch(cfg.path(projectId), {
                method: cfg.method || 'GET',
                headers: req.headers || {},
                body: req.body || undefined,
              });
              text = await res.text();
            } catch (err) {
              const msg = '[' + actionId + '] 网络异常：' + String((err && err.message) || err || 'unknown');
              fallbackSetResult(cfg.resultId, msg, true);
              fallbackSetOutput(msg);
              return false;
            }
            let detail = '';
            try {
              const j = JSON.parse(text || '{}');
              detail = String((j && j.detail) || '');
            } catch (_) {
              detail = '';
            }
            if (res.ok) {
              const rendered = fallbackRenderActionSuccess(actionId, cfg.resultId, res.status, text);
              if (!rendered) fallbackSetResult(cfg.resultId, '[' + actionId + '] 请求成功 (HTTP ' + String(res.status) + ')', false);
              await fallbackRefreshAfter(actionId);
              if (actionId === 'btnWeightsSave') {
                try {
                  const parsed = JSON.parse(text || '{}');
                  const profile = (parsed && parsed.expert_profile) || {};
                  fallbackApplyWeightUi(profile.weights_raw || {});
                  const statusEl = document.getElementById('expertProfileStatus');
                  if (statusEl) statusEl.textContent = '当前生效配置：' + String(profile.name || '项目默认配置') + '（ID: ' + String(profile.id || '-') + '）';
                } catch (_) {}
              }
            } else {
              fallbackSetResult(
                cfg.resultId,
                '[' + actionId + '] 请求失败 (HTTP ' + String(res.status) + ')' + (detail ? '：' + detail : ''),
                true
              );
            }
            if (actionId === 'btnCompareReport') {
              fallbackSetOutput('[' + actionId + '] HTTP ' + String(res.status) + '\\n已渲染精简版对比报告（详情已显示在页面，不再输出完整JSON）');
            } else {
              fallbackSetOutput('[' + actionId + '] HTTP ' + String(res.status) + '\\n' + String(text || ''));
            }
            return !!res.ok;
          }
          async function fallbackDelete(kind, fileId, filename, rowEl) {
            const projectId = fallbackGetProjectId();
            if (!projectId) { alert('请先选择项目'); return false; }
            if (!fileId) { alert('删除失败：记录ID为空'); return false; }
            if (!confirm('确认删除该文件？')) return false;
            const encodedPid = encodeURIComponent(String(projectId || ''));
            const encodedFid = encodeURIComponent(String(fileId || ''));
            let path = '/api/v1/projects/' + encodedPid + '/materials/' + encodedFid;
            if (kind === 'submission') path = '/api/v1/projects/' + encodedPid + '/shigong/' + encodedFid;
            if (kind === 'ground_truth') path = '/api/v1/projects/' + encodedPid + '/ground_truth/' + encodedFid;
            let res;
            let text = '';
            try {
              res = await fetch(path, { method: 'DELETE', headers: fallbackAuthHeaders() });
              text = await res.text();
            } catch (err) {
              alert('删除失败：' + String((err && err.message) || err || '网络异常'));
              return false;
            }
            if (!res.ok) {
              let detail = text || '';
              try {
                const j = JSON.parse(text || '{}');
                detail = (j && j.detail) || detail;
              } catch (_) {}
              alert('删除失败：HTTP ' + String(res.status) + ' ' + String(detail || '').slice(0, 180));
              fallbackSetOutput('删除失败：' + text);
              return false;
            }
            if (rowEl && typeof rowEl.remove === 'function') rowEl.remove();
            fallbackSetOutput(JSON.stringify({ ok: true, kind, id: fileId, filename: filename || '' }, null, 2));
            if (kind === 'material') {
              if (typeof refreshMaterials === 'function') refreshMaterials();
              if (typeof refreshFeedMaterials === 'function') refreshFeedMaterials();
            } else if (kind === 'submission' && typeof refreshSubmissions === 'function') {
              refreshSubmissions();
            } else if (kind === 'ground_truth' && typeof refreshGroundTruth === 'function') {
              refreshGroundTruth();
            }
            return true;
          }
          window.__zhifeiFallbackClick = function (ev, actionId) {
            fallbackStopEvent(ev);
            fallbackRunAction(actionId);
            return false;
          };
          window.__zhifeiFallbackDelete = function (ev, kind, fileId, filename) {
            const rowEl = ev && ev.target && ev.target.closest ? ev.target.closest('tr') : null;
            fallbackStopEvent(ev);
            fallbackDelete(kind, fileId, filename, rowEl);
            return false;
          };
          document.addEventListener('click', function (ev) {
            const btn = ev && ev.target && ev.target.closest
              ? ev.target.closest('.js-delete-material,.js-delete-submission,.js-delete-ground-truth')
              : null;
            if (!btn) return;
            let kind = 'material';
            if (btn.classList.contains('js-delete-submission')) kind = 'submission';
            if (btn.classList.contains('js-delete-ground-truth')) kind = 'ground_truth';
            let fileId = '';
            if (kind === 'submission') fileId = btn.getAttribute('data-submission-id') || '';
            else if (kind === 'ground_truth') fileId = btn.getAttribute('data-gt-id') || '';
            else fileId = btn.getAttribute('data-material-id') || '';
            const filename = btn.getAttribute('data-filename') || '';
            window.__zhifeiFallbackDelete(ev, kind, fileId, filename);
          }, true);
        })();
      </script>
      <script>
        function reportClientError(prefix, err) {
          const msg = prefix + ': ' + String((err && (err.message || err.reason || err)) || '未知错误');
          console.error(msg, err);
          const out = document.getElementById('output');
          if (out) out.textContent = msg;
        }
        window.addEventListener('error', function (e) {
          reportClientError('前端脚本错误', e && (e.error || e.message || e));
        });
        window.addEventListener('unhandledrejection', function (e) {
          reportClientError('前端异步错误', e && (e.reason || e));
        });
        const SAFE_CLICK_HANDLERS = {};
        function safeClick(id, fn) {
          const el = document.getElementById(id);
          if (!el) return;
          const wrapped = async (ev) => {
            if (ev) ev.preventDefault();
            try {
              await fn(ev);
            } catch (err) {
              const cfg = (window.__ZHIFEI_FALLBACK_ACTIONS && window.__ZHIFEI_FALLBACK_ACTIONS[id]) || {};
              const msg = '执行失败：' + String((err && err.message) || err || '未知错误');
              if (cfg.resultId && typeof setResultError === 'function') {
                setResultError(cfg.resultId, msg);
              }
              reportClientError('按钮[' + id + ']执行失败', err);
            }
          };
          if (SAFE_CLICK_HANDLERS[id]) {
            el.removeEventListener('click', SAFE_CLICK_HANDLERS[id]);
          }
          SAFE_CLICK_HANDLERS[id] = wrapped;
          el.onclick = null;
          el.addEventListener('click', wrapped);
          // 确保按钮本身可点击，避免被潜在覆盖层吞掉点击事件
          el.style.pointerEvents = 'auto';
          if (!el.style.position) el.style.position = 'relative';
          if (!el.style.zIndex || Number(el.style.zIndex) < 2) el.style.zIndex = '2';
        }
        // 兼容旧版内联 onclick：若已绑定 safeClick，则不再走旧兜底逻辑，避免覆盖详细渲染结果。
        const LEGACY_FALLBACK_CLICK = window.__zhifeiFallbackClick;
        window.__zhifeiFallbackClick = function (ev, actionId) {
          if (SAFE_CLICK_HANDLERS[actionId]) {
            if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
            return true;
          }
          if (typeof LEGACY_FALLBACK_CLICK === 'function') return LEGACY_FALLBACK_CLICK(ev, actionId);
          return true;
        };
        function safeChange(id, fn) { const el = document.getElementById(id); if (el) el.onchange = fn; }
        function storageGet(key) {
          try { return localStorage.getItem(key) || ''; } catch (_) { return ''; }
        }
        function storageSet(key, value) {
          try { localStorage.setItem(key, value); } catch (_) {}
        }
        function storageRemove(key) {
          try { localStorage.removeItem(key); } catch (_) {}
        }
        function pickProjectFromSelect(sel) {
          if (!sel) return '';
          if (sel.value) return sel.value;
          const remembered = storageGet('selected_project_id');
          if (remembered) {
            for (let i = 0; i < sel.options.length; i += 1) {
              if (String(sel.options[i].value || '') === remembered) {
                sel.value = remembered;
                break;
              }
            }
          }
          if (!sel.value && sel.options && sel.options.length > 1) {
            sel.selectedIndex = sel.options.length - 1;
          }
          if (sel.value) storageSet('selected_project_id', sel.value);
          return sel.value || '';
        }
        function pid() {
          const sel = document.getElementById('projectSelect');
          return pickProjectFromSelect(sel);
        }
        function selectedScoreScaleMax() {
          const el = document.getElementById('scoreScaleSelect');
          const raw = (el && el.value) ? String(el.value).trim() : '100';
          return raw === '5' ? 5 : 100;
        }
        function selectedScoreScaleLabel() {
          return selectedScoreScaleMax() === 5 ? '5分制' : '100分制';
        }
        function applyProjectScoreScale(projectId) {
          const el = document.getElementById('scoreScaleSelect');
          if (!el) return;
          const meta = projectMetaById[String(projectId || '')] || {};
          const raw = String((meta && meta.score_scale_max) != null ? meta.score_scale_max : '100');
          el.value = (raw === '5') ? '5' : '100';
        }
        function apiHeaders(isJson=true) {
          const k = storageGet('api_key');
          const h = {};
          if (k) h['X-API-Key'] = k;
          if (isJson) h['Content-Type'] = 'application/json';
          return h;
        }
        const NL = String.fromCharCode(10);
        const DIMENSION_LABELS = {
          "01": "01 工程项目整体理解与实施路径",
          "02": "02 安全生产管理体系与控制措施",
          "03": "03 文明施工管理体系与实施措施",
          "04": "04 材料与部品采购及管理机制",
          "05": "05 四新技术的应用与实施方案",
          "06": "06 工程关键工序识别与控制措施",
          "07": "07 工程重难点及危险性较大工程管控",
          "08": "08 工程质量管理体系与保证措施",
          "09": "09 工期目标保障与进度控制措施",
          "10": "10 专项施工工艺与技术方案",
          "11": "11 人力资源配置与管理方案",
          "12": "12 总体施工工艺流程与组织逻辑",
          "13": "13 物资与施工设备配置方案",
          "14": "14 设计协调与深化实施能力",
          "15": "15 总体资源配置与实施计划",
          "16": "16 技术措施的可行性与落地性"
        };
        const DIM_IDS = Array.from({ length: 16 }, (_, i) => String(i + 1).padStart(2, '0'));
        let expertProfileLocked = false;
        let projectMetaById = {};
        const PROJECT_REQUIRED_BUTTON_IDS = [
          'deleteCurrentProject', 'btnWeightsReset', 'btnWeightsSave', 'btnWeightsApply',
          'btnRefreshGroundTruth', 'btnRefreshGroundTruthSubmissionOptions', 'btnUploadFeed', 'btnRefreshFeedMaterials', 'btnAddGroundTruth',
          'btnEvolve', 'btnWritingGuidance', 'btnCompilationInstructions',
          'btnRebuildDelta', 'btnRebuildSamples', 'btnTrainCalibratorV2', 'btnApplyCalibPredict',
          'btnAutoRunReflection', 'btnEvalMetricsV2', 'btnEvalSummaryV2',
          'btnMinePatchV2', 'btnShadowPatchV2', 'btnDeployPatchV2', 'btnRollbackPatchV2',
        ];
        const NON_BLOCKING_ACTION_BUTTON_IDS = [
          'btnUploadMaterials', 'btnRefreshMaterials', 'btnUploadShigong', 'btnScoreShigong', 'btnRefreshSubmissions',
          'btnCompare', 'btnCompareReport', 'btnInsights', 'btnLearning',
          'btnAdaptive', 'btnAdaptivePatch', 'btnAdaptiveValidate', 'btnAdaptiveApply',
          'btnRefreshGroundTruth', 'btnRefreshGroundTruthSubmissionOptions', 'btnUploadFeed', 'btnRefreshFeedMaterials', 'btnAddGroundTruth',
          'btnEvolve', 'btnWritingGuidance', 'btnCompilationInstructions',
          'btnRebuildDelta', 'btnRebuildSamples', 'btnTrainCalibratorV2', 'btnApplyCalibPredict',
          'btnAutoRunReflection', 'btnEvalMetricsV2', 'btnEvalSummaryV2',
          'btnMinePatchV2', 'btnShadowPatchV2', 'btnDeployPatchV2', 'btnRollbackPatchV2',
        ];
        const PROJECT_REQUIRED_INPUT_IDS = [
          'scoreScaleSelect',
          'feedFile', 'groundTruthSubmissionSelect', 'groundTruthScope', 'groundTruthOtherProject',
          'gtJ1', 'gtJ2', 'gtJ3', 'gtJ4', 'gtJ5', 'gtFinal', 'patchType', 'patchIdInput',
        ];
        function setActionStatus(id, msg, isError=false) {
          const el = document.getElementById(id);
          if (!el) return;
          el.textContent = msg || '';
          el.style.color = isError ? '#b91c1c' : '#475569';
        }
        function syncProjectHiddenInputs(projectId) {
          const value = projectId || '';
          ['deleteProjectId', 'uploadMaterialProjectId', 'uploadShigongProjectId'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.value = value;
          });
        }
        function updateCurrentProjectTag(projectId) {
          const tag = document.getElementById('currentProjectTag');
          syncProjectHiddenInputs(projectId);
          if (!tag) return;
          if (!projectId) {
            tag.textContent = '当前项目：未选择';
            return;
          }
          const sel = document.getElementById('projectSelect');
          const opt = sel && sel.options && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;
          const label = opt && opt.textContent ? opt.textContent : projectId;
          tag.textContent = '当前项目：' + label;
        }

        function updateProjectBoundControlsState() {
          const projectId = pid();
          const hasProject = !!projectId;
          updateCurrentProjectTag(projectId);
          const title = hasProject ? '' : '请先选择项目';
          PROJECT_REQUIRED_BUTTON_IDS.forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.disabled = !hasProject;
            el.title = title;
          });
          PROJECT_REQUIRED_INPUT_IDS.forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.disabled = !hasProject;
            if (!hasProject && (el.tagName === 'INPUT' || el.tagName === 'SELECT')) {
              el.title = '请先选择项目';
            } else {
              el.title = '';
            }
          });
          // 对比/自适应按钮保持可点击：未选项目时也要有可见提示，避免“点击无反应”
          NON_BLOCKING_ACTION_BUTTON_IDS.forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.disabled = false;
            el.title = hasProject ? '' : '请先选择项目后执行（点击会显示提示）';
          });
          if (!hasProject) {
            setActionStatus('materialsActionStatus', '请先在「2) 选择项目」中选择项目。', true);
            setActionStatus('shigongActionStatus', '请先在「2) 选择项目」中选择项目。', true);
            setActionStatus('feedActionStatus', '请先在「2) 选择项目」中选择项目。', true);
          }
        }

        let projectSwitchSeq = 0;
        function selectedProjectIdStrict() {
          const sel = document.getElementById('projectSelect');
          return (sel && sel.value) ? String(sel.value) : '';
        }
        function isStaleProjectResponse(expectedProjectId, seq) {
          if (!expectedProjectId) return false;
          if (typeof seq === 'number' && seq !== projectSwitchSeq) return true;
          return selectedProjectIdStrict() !== String(expectedProjectId);
        }
        function setTableStandby(tableId, emptyId, message) {
          const table = document.getElementById(tableId);
          const tbody = table ? table.querySelector('tbody') : null;
          if (tbody) tbody.innerHTML = '';
          const emptyEl = document.getElementById(emptyId);
          if (emptyEl) {
            emptyEl.textContent = message || '';
            emptyEl.style.display = 'block';
          }
        }
        function clearResultBlock(resultId) {
          const el = document.getElementById(resultId);
          if (!el) return;
          el.style.display = 'none';
          el.innerHTML = '';
        }
        function resetProjectPanelsToStandby(projectId) {
          const hasProject = !!projectId;
          setTableStandby(
            'materialsTable',
            'materialsEmpty',
            hasProject ? '待机：正在加载当前项目资料…' : '暂无资料，请先选择项目。'
          );
          setTableStandby(
            'submissionsTable',
            'submissionsEmpty',
            hasProject ? '待机：正在加载当前项目施组…' : '暂无施组，请先选择项目。'
          );
          setTableStandby(
            'feedMaterialsTable',
            'feedMaterialsEmpty',
            hasProject ? '待机：正在加载当前项目投喂包…' : '暂无投喂包，请先选择项目。'
          );
          setTableStandby(
            'groundTruthTable',
            'groundTruthEmpty',
            hasProject ? '待机：正在加载当前项目真实评标记录…' : '暂无真实评标，请先选择项目。'
          );
          [
            'compareResult', 'compareReportResult', 'insightsResult', 'learningResult',
            'adaptiveResult', 'adaptivePatchResult', 'adaptiveValidateResult', 'adaptiveApplyResult',
            'evolveResult', 'guidanceResult', 'compilationInstructionsResult',
            'deltaResult', 'sampleResult', 'calibTrainResult', 'patchResult',
            'patchShadowResult', 'patchDeployResult', 'evalResult'
          ].forEach(clearResultBlock);
          const gtScope = document.getElementById('groundTruthScope');
          if (gtScope) gtScope.value = 'current';
          const gtOther = document.getElementById('groundTruthOtherProject');
          if (gtOther) {
            gtOther.style.display = 'none';
            gtOther.value = '';
          }
          ['gtJ1', 'gtJ2', 'gtJ3', 'gtJ4', 'gtJ5', 'gtFinal', 'patchIdInput'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.value = '';
          });
          if (!hasProject) {
            const scaleSel = document.getElementById('scoreScaleSelect');
            if (scaleSel) scaleSel.value = '100';
          }
          const gtSubmissionSel = document.getElementById('groundTruthSubmissionSelect');
          if (gtSubmissionSel) {
            gtSubmissionSel.innerHTML = '';
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = hasProject ? '-- 待加载步骤4施组文件 --' : '-- 请先选择项目 --';
            gtSubmissionSel.appendChild(opt);
            gtSubmissionSel.value = '';
          }
          document.querySelectorAll(
            '#uploadMaterial input[type="file"], #uploadShigong input[type="file"], #feedFile'
          ).forEach((el) => { if (el) el.value = ''; });
          setActionStatus(
            'materialsActionStatus',
            hasProject ? '待机：可上传资料或点击“刷新”。' : '请先在「2) 选择项目」中选择项目。',
            !hasProject
          );
          setActionStatus(
            'shigongActionStatus',
            hasProject ? '待机：可上传施组或点击“评分施组”。' : '请先在「2) 选择项目」中选择项目。',
            !hasProject
          );
          setActionStatus(
            'feedActionStatus',
            hasProject ? '待机：可上传投喂包或录入真实评标。' : '请先在「2) 选择项目」中选择项目。',
            !hasProject
          );
          if (hasProject) {
            setExpertProfileStatus('待机：正在加载当前项目的16维关注度配置...');
            applyWeightsRaw({});
          } else {
            setExpertProfileStatus('请先选择项目。');
            applyWeightsRaw({});
            expertProfileLocked = false;
          }
          const out = document.getElementById('output');
          if (out) {
            out.textContent = hasProject
              ? ('已切换项目，正在自动刷新 2.5/3/4/5/6/7 区域数据…')
              : '请选择项目后开始操作。';
          }
        }

        function valueOrDefault(v, fallback) {
          return (v === undefined || v === null) ? fallback : v;
        }
        function normalizeWeightsRaw(raw) {
          const m = {};
          let sum = 0;
          DIM_IDS.forEach(id => {
            const v = Number(valueOrDefault(raw[id], 5));
            const mv = 0.5 + (v / 10);
            m[id] = mv;
            sum += mv;
          });
          const norm = {};
          DIM_IDS.forEach(id => { norm[id] = sum > 0 ? (m[id] / sum) : 0; });
          return norm;
        }
        function buildWeightsPanel() {
          const panel = document.getElementById('expertWeightsPanel');
          if (!panel) return;
          panel.innerHTML = '';
          DIM_IDS.forEach(id => {
            const row = document.createElement('div');
            row.className = 'weight-row';
            row.innerHTML =
              '<label for="w_' + id + '">' + DIMENSION_LABELS[id] + '</label>' +
              '<input id="w_' + id + '" data-dim="' + id + '" type="range" min="0" max="10" step="1" value="5" />' +
              '<span class="raw-value" id="w_raw_' + id + '">5</span>' +
              '<span class="norm-value" id="w_norm_' + id + '">6.25%</span>';
            panel.appendChild(row);
          });
          const sliders = panel.querySelectorAll('input[type="range"]');
          for (let i = 0; i < sliders.length; i += 1) {
            sliders[i].addEventListener('input', renderWeightSummary);
          }
          renderWeightSummary();
        }
        function collectWeightsRaw() {
          const raw = {};
          DIM_IDS.forEach(id => {
            const el = document.getElementById('w_' + id);
            raw[id] = Number((el && el.value) || 5);
          });
          return raw;
        }
        function applyWeightsRaw(raw) {
          DIM_IDS.forEach(id => {
            const el = document.getElementById('w_' + id);
            if (!el) return;
            el.value = String(valueOrDefault(raw[id], 5));
          });
          renderWeightSummary();
        }
        function renderWeightSummary() {
          const raw = collectWeightsRaw();
          const norm = normalizeWeightsRaw(raw);
          DIM_IDS.forEach(id => {
            const rawEl = document.getElementById('w_raw_' + id);
            if (rawEl) rawEl.textContent = String(raw[id]);
            const normEl = document.getElementById('w_norm_' + id);
            if (normEl) normEl.textContent = (norm[id] * 100).toFixed(2) + '%';
          });
          const summaryEl = document.getElementById('expertWeightsSummary');
          if (summaryEl) {
            summaryEl.textContent = DIM_IDS.map(id => id + ':' + (norm[id] * 100).toFixed(2) + '%').join(' | ');
          }
        }
        function setExpertProfileStatus(text, isError=false) {
          const el = document.getElementById('expertProfileStatus');
          if (!el) return;
          el.textContent = text || '';
          el.style.color = isError ? '#b91c1c' : '#334155';
        }
        async function loadExpertProfile(expectedProjectId=null, switchSeq=null) {
          const projectId = expectedProjectId || pid();
          if (!projectId) {
            setExpertProfileStatus('请先选择项目。');
            applyWeightsRaw({});
            expertProfileLocked = false;
            return;
          }
          setExpertProfileStatus('正在加载当前生效配置...');
          let res, data;
          try {
            res = await fetch('/api/v1/projects/' + projectId + '/expert-profile');
            data = await res.json().catch(() => ({}));
          } catch (err) {
            if (isStaleProjectResponse(projectId, switchSeq)) return;
            setExpertProfileStatus('加载失败：' + String((err && err.message) || err), true);
            return;
          }
          if (isStaleProjectResponse(projectId, switchSeq)) return;
          if (!res.ok) {
            setExpertProfileStatus('加载失败：' + (data.detail || ('HTTP ' + res.status)), true);
            return;
          }
          const profile = (data && data.expert_profile) || {};
          applyWeightsRaw(profile.weights_raw || {});
          const project = (data && data.project) || {};
          expertProfileLocked = String(project.status || '') === 'submitted_to_qingtian';
          let statusText =
            '当前生效配置：' + (profile.name || '-') +
            '（ID: ' + (profile.id || '-') + '，更新时间: ' + ((project.updated_at || profile.updated_at || '').slice(0, 19) || '-') + '）';
          if (expertProfileLocked) {
            statusText += '；当前项目已进入青天评标阶段，保存/重算需要二次确认解锁。';
          }
          setExpertProfileStatus(statusText);
        }
        async function saveExpertProfile(askName=true, forceUnlock=false) {
          const projectId = pid();
          if (!projectId) { setExpertProfileStatus('请先选择项目。', true); return false; }
          if (expertProfileLocked && !forceUnlock) {
            const unlockOk = confirm('当前项目已进入青天评标阶段。是否解锁并继续保存专家配置？');
            if (!unlockOk) return false;
            forceUnlock = true;
          }
          const customName = askName ? (prompt('请输入专家配置名称（可留空自动命名）：') || '') : '';
          const payload = { name: customName, weights_raw: collectWeightsRaw(), force_unlock: !!forceUnlock };
          setExpertProfileStatus('正在保存配置...');
          let res, data;
          try {
            res = await fetch('/api/v1/projects/' + projectId + '/expert-profile', {
              method: 'PUT',
              headers: apiHeaders(),
              body: JSON.stringify(payload),
            });
            data = await res.json().catch(() => ({}));
          } catch (err) {
            setExpertProfileStatus('保存失败：' + String((err && err.message) || err), true);
            return false;
          }
          if (!res.ok) {
            if (res.status === 409 && !forceUnlock) {
              setExpertProfileStatus('保存被锁定：项目处于青天评标阶段，请解锁后重试。', true);
              return false;
            }
            setExpertProfileStatus('保存失败：' + (data.detail || ('HTTP ' + res.status)), true);
            return false;
          }
          const profile = (data && data.expert_profile) || {};
          setExpertProfileStatus('已保存并绑定配置：' + (profile.name || '-') + '（' + (profile.id || '-') + '）');
          applyWeightsRaw(profile.weights_raw || {});
          const out = document.getElementById('output');
          if (out) out.textContent = JSON.stringify(data, null, 2);
          return true;
        }
        async function applyExpertProfileAndRescore() {
          const projectId = pid();
          if (!projectId) { setExpertProfileStatus('请先选择项目。', true); return; }
          let forceUnlock = false;
          if (expertProfileLocked) {
            const unlockOk = confirm('当前项目已进入青天评标阶段。是否解锁并执行“保存+全项目重算”？');
            if (!unlockOk) return;
            forceUnlock = true;
          }
          const ok = confirm('将保存当前16维关注度，并重算本项目全部施组。是否继续？');
          if (!ok) return;
          const saved = await saveExpertProfile(false, forceUnlock);
          if (!saved) return;
          setExpertProfileStatus('正在按当前配置重算全部施组...');
          let res, data;
          try {
            res = await fetch('/api/v1/projects/' + projectId + '/rescore', {
              method: 'POST',
              headers: apiHeaders(),
              body: JSON.stringify({
                scoring_engine_version: 'v2',
                scope: 'project',
                score_scale_max: selectedScoreScaleMax(),
                rebuild_anchors: false,
                rebuild_requirements: false,
                retrain_calibrator: false,
                force_unlock: !!forceUnlock
              }),
            });
            data = await res.json().catch(() => ({}));
          } catch (err) {
            setExpertProfileStatus('重算失败：' + String((err && err.message) || err), true);
            return;
          }
          if (!res.ok) {
            if (res.status === 409) {
              setExpertProfileStatus('重算被锁定：项目处于青天评标阶段，请解锁后重试。', true);
              return;
            }
            setExpertProfileStatus('重算失败：' + (data.detail || ('HTTP ' + res.status)), true);
            return;
          }
          setExpertProfileStatus(
            '重算完成（' + ((data && data.score_scale_label) ? data.score_scale_label : selectedScoreScaleLabel()) + '）：共处理 ' + (data.submission_count || 0) + ' 份，生成 ' + (data.reports_generated || 0) + ' 份报告。'
          );
          const out = document.getElementById('output');
          if (out) out.textContent = JSON.stringify(data, null, 2);
          if (typeof refreshSubmissions === 'function') await refreshSubmissions();
          if (typeof refreshMaterials === 'function') await refreshMaterials();
        }
        function initWeightsSection() {
          try {
            buildWeightsPanel();
          } catch (err) {
            reportClientError('权重面板初始化失败', err);
          }
        }
        safeClick('btnWeightsReset', () => applyWeightsRaw({}));
        safeClick('btnWeightsSave', saveExpertProfile);
        safeClick('btnWeightsApply', applyExpertProfileAndRescore);

        function setCreateMsg(msg, isError) {
          const el = document.getElementById('createProjectMessage');
          if (!el) { alert(msg); return; }
          el.textContent = msg || '';
          el.style.color = isError ? '#b91c1c' : '#15803d';
        }
        function setSelectMsg(msg, isError) {
          const el = document.getElementById('selectProjectMessage');
          if (!el) { alert(msg); return; }
          el.textContent = msg || '';
          el.style.color = isError ? '#b91c1c' : '#15803d';
        }
        function setSelfCheckResult(summary, details, isError) {
          const el = document.getElementById('selfCheckResult');
          if (!el) return;
          el.style.display = 'block';
          el.style.borderLeftColor = isError ? '#b91c1c' : '#15803d';
          el.innerHTML = '<strong></strong><pre style="margin-top:6px"></pre>';
          const strong = el.querySelector('strong');
          const pre = el.querySelector('pre');
          if (strong) strong.textContent = summary || '';
          if (pre) pre.textContent = details || '';
        }
        function setScoringFactorsResult(summary, details, isError) {
          const el = document.getElementById('scoringFactorsResult');
          if (!el) return;
          el.style.display = 'block';
          el.style.borderLeftColor = isError ? '#b91c1c' : '#2563eb';
          el.innerHTML = '<strong></strong><pre style="margin-top:6px"></pre>';
          const strong = el.querySelector('strong');
          const pre = el.querySelector('pre');
          if (strong) strong.textContent = summary || '';
          if (pre) pre.textContent = details || '';
        }
        async function probeEndpoint(url, options) {
          try {
            const res = await fetch(url, options);
            const text = await res.text();
            let data = null;
            try { data = JSON.parse(text); } catch (_) {}
            return { ok: res.ok, status: res.status, text, data };
          } catch (err) {
            return { ok: false, status: 0, error: String((err && err.message) || err || '网络异常') };
          }
        }
        async function runSystemSelfCheck() {
          setSelfCheckResult('正在执行系统自检…', '', false);
          const currentId = pid();
          const url = currentId
            ? ('/api/v1/system/self_check?project_id=' + encodeURIComponent(currentId))
            : '/api/v1/system/self_check';
          const r = await probeEndpoint(url);
          if (r.error) {
            setSelfCheckResult('系统自检失败', r.error, true);
            const out = document.getElementById('output');
            if (out) out.textContent = r.error;
            return;
          }
          if (!r.ok) {
            const detail = (r.data && r.data.detail) ? String(r.data.detail) : String(r.text || '').slice(0, 200);
            setSelfCheckResult('系统自检失败', 'HTTP ' + r.status + ' ' + detail, true);
            const out = document.getElementById('output');
            if (out) out.textContent = detail;
            return;
          }
          const data = r.data || {};
          const items = data.items || [];
          const failed = items.filter(x => !x.ok).length;
          const lines = items.map(x => (x.ok ? '[ OK ] ' : '[FAIL] ') + x.name + (x.detail ? (' -> ' + x.detail) : ''));
          const summary = failed === 0 ? '系统自检通过（全部正常）' : ('系统自检完成：发现 ' + failed + ' 项异常');
          const details = lines.join(NL);
          setSelfCheckResult(summary, details, failed > 0);
          const out = document.getElementById('output');
          if (out) out.textContent = JSON.stringify(data, null, 2);
        }
        async function loadScoringFactorsOverview() {
          const currentId = pid();
          const url = currentId
            ? ('/api/v1/scoring/factors?project_id=' + encodeURIComponent(currentId))
            : '/api/v1/scoring/factors';
          setScoringFactorsResult('正在加载评分体系总览…', '', false);
          const r = await probeEndpoint(url);
          if (r.error) {
            setScoringFactorsResult('评分体系加载失败', r.error, true);
            const out = document.getElementById('output');
            if (out) out.textContent = r.error;
            return;
          }
          if (!r.ok) {
            const detail = (r.data && r.data.detail) ? String(r.data.detail) : String(r.text || '').slice(0, 200);
            setScoringFactorsResult('评分体系加载失败', 'HTTP ' + r.status + ' ' + detail, true);
            const out = document.getElementById('output');
            if (out) out.textContent = detail;
            return;
          }
          const d = r.data || {};
          const flags = d.capability_flags || {};
          const summary =
            '评分体系已加载：维度 ' + (d.dimension_count || 0) + '，扣分规则 ' + ((d.penalty_rules || []).length) +
            '；组织机构要求=' + (flags.organization_structure_required ? '是' : '否') +
            '，章节完整性=' + (flags.chapter_content_completeness_required ? '是' : '否') +
            '，重难点要求=' + (flags.key_difficult_points_required ? '是' : '否') +
            '，图文要求=' + (flags.graphic_content_required ? '是' : '否');
          const details = JSON.stringify(d, null, 2);
          setScoringFactorsResult(summary, details, false);
          const out = document.getElementById('output');
          if (out) out.textContent = details;
        }
        async function loadScoringFactorsMarkdown() {
          const currentId = pid();
          const url = currentId
            ? ('/api/v1/scoring/factors/markdown?project_id=' + encodeURIComponent(currentId))
            : '/api/v1/scoring/factors/markdown';
          setScoringFactorsResult('正在生成评分体系 Markdown…', '', false);
          const r = await probeEndpoint(url);
          if (r.error) {
            setScoringFactorsResult('Markdown 生成失败', r.error, true);
            const out = document.getElementById('output');
            if (out) out.textContent = r.error;
            return;
          }
          if (!r.ok) {
            const detail = (r.data && r.data.detail) ? String(r.data.detail) : String(r.text || '').slice(0, 200);
            setScoringFactorsResult('Markdown 生成失败', 'HTTP ' + r.status + ' ' + detail, true);
            const out = document.getElementById('output');
            if (out) out.textContent = detail;
            return;
          }
          const markdown = String((r.data && r.data.markdown) || '');
          setScoringFactorsResult('评分体系 Markdown 已生成（可直接复制到 ChatGPT）', markdown.slice(0, 600) + (markdown.length > 600 ? '\\n...（已截断预览）' : ''), false);
          const out = document.getElementById('output');
          if (out) out.textContent = markdown;
        }
        async function loadProjectAnalysisBundle() {
          const currentId = pid();
          if (!currentId) {
            setScoringFactorsResult('请先选择项目', '项目分析包需要 project_id', true);
            return;
          }
          const url = '/api/v1/projects/' + encodeURIComponent(currentId) + '/analysis_bundle';
          setScoringFactorsResult('正在生成项目分析包…', '', false);
          const r = await probeEndpoint(url);
          if (r.error) {
            setScoringFactorsResult('项目分析包生成失败', r.error, true);
            const out = document.getElementById('output');
            if (out) out.textContent = r.error;
            return;
          }
          if (!r.ok) {
            const detail = (r.data && r.data.detail) ? String(r.data.detail) : String(r.text || '').slice(0, 200);
            setScoringFactorsResult('项目分析包生成失败', 'HTTP ' + r.status + ' ' + detail, true);
            const out = document.getElementById('output');
            if (out) out.textContent = detail;
            return;
          }
          const markdown = String((r.data && r.data.markdown) || '');
          setScoringFactorsResult('项目分析包已生成（可直接复制给 ChatGPT）', markdown.slice(0, 600) + (markdown.length > 600 ? '\\n...（已截断预览）' : ''), false);
          const out = document.getElementById('output');
          if (out) out.textContent = markdown;
        }
        async function downloadProjectAnalysisBundle() {
          const currentId = pid();
          if (!currentId) {
            setScoringFactorsResult('请先选择项目', '下载分析包需要 project_id', true);
            return;
          }
          const url = '/api/v1/projects/' + encodeURIComponent(currentId) + '/analysis_bundle.md';
          setScoringFactorsResult('正在准备下载…', '若浏览器未自动下载，请检查弹窗或下载权限设置。', false);
          const a = document.createElement('a');
          a.href = url;
          a.download = 'analysis_bundle_' + currentId + '.md';
          document.body.appendChild(a);
          a.click();
          a.remove();
        }
        async function refreshProjects() {
          setSelectMsg('正在加载…', false);
          const current = pid() || storageGet('selected_project_id') || '';
          let res, text;
          try {
            res = await fetch('/api/v1/projects');
            text = await res.text();
          } catch (err) {
            setSelectMsg('网络错误，请确认服务已启动（如 make run）', true);
            const out = document.getElementById('output');
            if (out) { out.textContent = '请求失败: ' + (err.message || err); out.scrollIntoView({ behavior: 'smooth' }); }
            return;
          }
          let list = [];
          try { list = JSON.parse(text); } catch (e) { list = []; }
          if (!Array.isArray(list)) list = [];
          projectMetaById = {};
          list.forEach((p) => {
            if (p && p.id) projectMetaById[String(p.id)] = (p.meta && typeof p.meta === 'object') ? p.meta : {};
          });
          list = list.slice().sort((a, b) => {
            const an = String((a && a.name) || '');
            const bn = String((b && b.name) || '');
            const ae = an.startsWith('E2E_') ? 1 : 0;
            const be = bn.startsWith('E2E_') ? 1 : 0;
            if (ae !== be) return ae - be;
            const at = String((a && (a.updated_at || a.created_at)) || '');
            const bt = String((b && (b.updated_at || b.created_at)) || '');
            return at.localeCompare(bt);
          });
          if (!res.ok) {
            const errMsg = (typeof list === 'object' && list && list.detail) ? String(list.detail) : (text || '').slice(0, 200);
            setSelectMsg('刷新失败: ' + res.status + ' ' + errMsg, true);
            const out = document.getElementById('output');
            if (out) { out.textContent = '刷新失败: ' + res.status + '\\n' + text; out.scrollIntoView({ behavior: 'smooth' }); }
            return;
          }
          setSelectMsg(list.length ? '已加载 ' + list.length + ' 个项目，请在上方下拉框选择' : '暂无项目，请先在「1) 创建项目」中创建', false);
          const sel = document.getElementById('projectSelect');
          sel.innerHTML = '<option value="">-- 选择项目 --</option>';
          list.forEach(p => {
            const o = document.createElement('option');
            o.value = p.id;
            o.textContent = (p.name || p.id) + ' (' + (p.id || '').slice(0,8) + '…)';
            sel.appendChild(o);
          });
          if (current && list.some(p => p.id === current)) {
            sel.value = current;
          } else if (list.length > 0) {
            sel.value = list[list.length - 1].id;
          }
          if (sel.value) {
            storageSet('selected_project_id', sel.value);
          } else {
            storageRemove('selected_project_id');
          }
          applyProjectScoreScale(sel.value || '');
          await onProjectChanged();
        }
        async function onProjectChanged() {
          const selectedId = selectedProjectIdStrict() || pid();
          projectSwitchSeq += 1;
          const switchSeq = projectSwitchSeq;
          if (selectedId) storageSet('selected_project_id', selectedId);
          else storageRemove('selected_project_id');
          applyProjectScoreScale(selectedId);
          updateProjectBoundControlsState();
          resetProjectPanelsToStandby(selectedId);
          if (!selectedId) {
            setSelectMsg('请先在上方选择项目。', true);
            return;
          }
          await Promise.all([
            (typeof loadExpertProfile === 'function') ? loadExpertProfile(selectedId, switchSeq) : Promise.resolve(),
            (typeof refreshSubmissions === 'function') ? refreshSubmissions(selectedId, switchSeq) : Promise.resolve(),
            (typeof refreshMaterials === 'function') ? refreshMaterials(selectedId, switchSeq) : Promise.resolve(),
            (typeof refreshFeedMaterials === 'function') ? refreshFeedMaterials(selectedId, switchSeq) : Promise.resolve(),
            (typeof refreshGroundTruth === 'function') ? refreshGroundTruth(selectedId, switchSeq) : Promise.resolve(),
            (typeof refreshGroundTruthSubmissionOptions === 'function') ? refreshGroundTruthSubmissionOptions(selectedId, switchSeq) : Promise.resolve(),
          ]);
          if (isStaleProjectResponse(selectedId, switchSeq)) return;
          setSelectMsg('已切换项目并自动刷新下方所有区域。', false);
        }
        const elRefresh = document.getElementById('refreshProjects');
        if (elRefresh) elRefresh.onclick = refreshProjects;
        safeChange('projectSelect', onProjectChanged);
        const btnSelfCheck = document.getElementById('btnSelfCheck');
        if (btnSelfCheck) btnSelfCheck.onclick = runSystemSelfCheck;
        const btnScoringFactors = document.getElementById('btnScoringFactors');
        if (btnScoringFactors) btnScoringFactors.onclick = loadScoringFactorsOverview;
        const btnScoringFactorsMd = document.getElementById('btnScoringFactorsMd');
        if (btnScoringFactorsMd) btnScoringFactorsMd.onclick = loadScoringFactorsMarkdown;
        const btnAnalysisBundle = document.getElementById('btnAnalysisBundle');
        if (btnAnalysisBundle) btnAnalysisBundle.onclick = loadProjectAnalysisBundle;
        const btnAnalysisBundleDownload = document.getElementById('btnAnalysisBundleDownload');
        if (btnAnalysisBundleDownload) btnAnalysisBundleDownload.onclick = downloadProjectAnalysisBundle;
        const deleteProjectForm = document.getElementById('deleteProjectForm');
        if (deleteProjectForm) {
          deleteProjectForm.onsubmit = async (e) => {
            e.preventDefault();
            const id = pid();
            if (!id) { setSelectMsg('请先选择要删除的项目', true); return; }
            const sel = document.getElementById('projectSelect');
            const opt = sel && sel.options && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;
            const label = (opt && opt.textContent) ? opt.textContent : id;
            const ok = confirm('确认删除项目「' + label + '」？\\n此操作不可恢复，并会删除该项目全部资料和评分记录。');
            if (!ok) return;
            setSelectMsg('正在删除项目…', false);
            let res, text;
            try {
              res = await fetch('/api/v1/projects/' + id, { method: 'DELETE', headers: apiHeaders(false) });
              text = await res.text();
            } catch (err) {
              setSelectMsg('删除失败: ' + (err.message || err), true);
              return;
            }
            if (res.status === 204) {
              setSelectMsg('项目已删除', false);
              const out = document.getElementById('output');
              if (out) out.textContent = '已删除项目：' + label;
              await refreshProjects();
              if (typeof refreshSubmissions === 'function') refreshSubmissions();
              if (typeof refreshMaterials === 'function') refreshMaterials();
              if (typeof refreshFeedMaterials === 'function') refreshFeedMaterials();
              if (typeof refreshGroundTruth === 'function') refreshGroundTruth();
            } else {
              let detail = text || '';
              try { const j = JSON.parse(text || '{}'); detail = (j && j.detail) || detail; } catch (_) {}
              setSelectMsg('删除失败: ' + res.status + ' ' + String(detail || '').slice(0, 120), true);
            }
          };
        }
        safeClick('btnCleanupE2EProjects', async () => {
          const ok = confirm('将批量删除名称以 E2E_ 开头的测试项目及其资料/施组记录。是否继续？');
          if (!ok) return;
          const res = await fetch('/api/v1/projects/cleanup_e2e?prefix=E2E_', { method: 'POST', headers: apiHeaders(false) });
          const text = await res.text();
          let data = {};
          try { data = JSON.parse(text || '{}'); } catch (_) {}
          const out = document.getElementById('output');
          if (out) out.textContent = res.ok ? JSON.stringify(data, null, 2) : text;
          if (res.ok) {
            setSelectMsg('E2E 清理完成：删除 ' + (data.removed_count || 0) + ' 个项目。', false);
            await refreshProjects();
          } else {
            setSelectMsg('E2E 清理失败：' + (data.detail || ('HTTP ' + res.status)), true);
          }
        });

        const formCreate = document.getElementById('createProject');
        if (formCreate) {
          formCreate.onsubmit = async (e) => {
            e.preventDefault();
            const name = (formCreate.elements.name && formCreate.elements.name.value || '').trim();
            if (!name) { setCreateMsg('请填写项目名称', true); return; }
            setCreateMsg('正在创建…', false);
            let res, text;
            try {
              res = await fetch('/api/v1/projects', { method: 'POST', headers: apiHeaders(), body: JSON.stringify({ name }) });
              text = await res.text();
            } catch (err) {
              setCreateMsg('网络错误: ' + (err.message || err), true);
              const out = document.getElementById('output');
              if (out) { out.textContent = '请求失败: ' + (err.message || err); out.scrollIntoView({ behavior: 'smooth' }); }
              return;
            }
            const outEl = document.getElementById('output');
            if (outEl) outEl.textContent = text;
            if (res && res.ok) {
              try {
                const created = JSON.parse(text || '{}');
                if (created && created.id) storageSet('selected_project_id', created.id);
              } catch (_) {}
              setCreateMsg('创建成功，已刷新下方列表，请选择项目', false);
              await refreshProjects();
            } else {
              let detail = text;
              try { const j = JSON.parse(text); detail = (j && j.detail) || text; } catch (_) {}
              setCreateMsg('创建失败: ' + res.status + ' ' + (detail || '').slice(0, 100), true);
              const outSc = document.getElementById('output');
              if (outSc) outSc.scrollIntoView({ behavior: 'smooth' });
            }
          };
        } else {
          console.error('createProject form not found');
        }

        function setFormPid(form) {
          const fid = form.querySelector('input[name="project_id"]');
          if (fid) return fid.value;
          const hidden = document.createElement('input');
          hidden.name = 'project_id';
          hidden.value = pid();
          form.appendChild(hidden);
          return pid();
        }

        const formMaterial = document.getElementById('uploadMaterial');
        let uploadMaterialsInFlight = false;
        if (formMaterial) {
          formMaterial.onsubmit = async (e) => {
            e.preventDefault();
            e.stopPropagation();
            await uploadMaterialsAction();
            return false;
          };
        }
        async function uploadMaterialsAction() {
          if (uploadMaterialsInFlight) {
            setActionStatus('materialsActionStatus', '资料上传进行中，请稍候…', false);
            return;
          }
          uploadMaterialsInFlight = true;
          try {
            const projectId = pid();
            if (!projectId) {
              const o = document.getElementById('output');
              if (o) o.textContent = '请先选择项目';
              setActionStatus('materialsActionStatus', '上传失败：请先选择项目。', true);
              updateProjectBoundControlsState();
              return;
            }
            const fileInput = formMaterial && formMaterial.querySelector ? formMaterial.querySelector('input[name="file"]') : null;
            const files = Array.from((fileInput && fileInput.files) || []);
            if (!files.length) {
              const o = document.getElementById('output');
              if (o) o.textContent = '请先选择要上传的文件';
              setActionStatus('materialsActionStatus', '请先选择至少 1 个资料文件。', true);
              return;
            }
            const headers = {};
            const apiKey = storageGet('api_key');
            if (apiKey) headers['X-API-Key'] = apiKey;
            const out = document.getElementById('output');
            if (out) out.textContent = '资料上传中（' + files.length + ' 个）...';
            setActionStatus('materialsActionStatus', '资料上传中（' + files.length + ' 个）...', false);
            let okCount = 0;
            let failCount = 0;
            const details = [];
            for (const f of files) {
              const fd = new FormData();
              fd.append('file', f);
              try {
                const res = await fetch('/api/v1/projects/' + projectId + '/materials', { method: 'POST', headers, body: fd });
                const text = await res.text();
                if (res.ok) {
                  okCount += 1;
                  details.push('[成功] ' + f.name);
                } else {
                  failCount += 1;
                  let detail = text || '';
                  try { const j = JSON.parse(text || '{}'); detail = (j && j.detail) || detail; } catch (_) {}
                  details.push('[失败] ' + f.name + ' -> HTTP ' + res.status + ' ' + String(detail).slice(0, 120));
                }
              } catch (err) {
                failCount += 1;
                details.push('[失败] ' + f.name + ' -> ' + String((err && err.message) || err || '网络异常'));
              }
            }
            if (out) out.textContent = '资料上传完成：成功 ' + okCount + '，失败 ' + failCount + NL + details.join(NL);
            setActionStatus(
              'materialsActionStatus',
              '上传完成：成功 ' + okCount + '，失败 ' + failCount + '。',
              failCount > 0
            );
            if (okCount > 0) {
              await refreshMaterials(projectId, projectSwitchSeq);
              if (typeof refreshFeedMaterials === 'function') await refreshFeedMaterials(projectId, projectSwitchSeq);
            }
            if (fileInput && failCount === 0) fileInput.value = '';
          } finally {
            uploadMaterialsInFlight = false;
          }
        }

        const formShigong = document.getElementById('uploadShigong');
        let uploadShigongInFlight = false;
        let scoreShigongInFlight = false;
        let shigongSubmitIntent = 'upload';
        const btnUploadShigong = document.getElementById('btnUploadShigong');
        const btnScoreShigong = document.getElementById('btnScoreShigong');
        if (btnUploadShigong) {
          btnUploadShigong.addEventListener('click', () => { shigongSubmitIntent = 'upload'; });
        }
        if (btnScoreShigong) {
          btnScoreShigong.addEventListener('click', () => { shigongSubmitIntent = 'score'; });
        }
        if (formShigong) {
          formShigong.onsubmit = async (e) => {
            e.preventDefault();
            e.stopPropagation();
            let sid = e && e.submitter ? String(e.submitter.id || '') : '';
            if (!sid) {
              const active = document.activeElement;
              sid = active && active.id ? String(active.id) : '';
            }
            const isScoreSubmit = sid === 'btnScoreShigong' || shigongSubmitIntent === 'score';
            shigongSubmitIntent = 'upload';
            if (isScoreSubmit) await scoreShigongAction();
            else await uploadShigongAction();
            return false;
          };
        }
        async function uploadShigongAction() {
          if (uploadShigongInFlight) {
            setActionStatus('shigongActionStatus', '施组上传进行中，请稍候…', false);
            return;
          }
          uploadShigongInFlight = true;
          try {
            const projectId = pid();
            if (!projectId) {
              const o = document.getElementById('output');
              if (o) o.textContent = '请先选择项目';
              setActionStatus('shigongActionStatus', '上传失败：请先选择项目。', true);
              updateProjectBoundControlsState();
              return;
            }
            const fileInput = formShigong && formShigong.querySelector ? formShigong.querySelector('input[name="file"]') : null;
            const files = Array.from((fileInput && fileInput.files) || []);
            if (!files.length) {
              const o = document.getElementById('output');
              if (o) o.textContent = '请先选择要上传的施组文件';
              setActionStatus('shigongActionStatus', '请先选择至少 1 个施组文件。', true);
              return;
            }
            const headers = {};
            const apiKey = storageGet('api_key');
            if (apiKey) headers['X-API-Key'] = apiKey;
            const o = document.getElementById('output');
            if (o) o.textContent = '施组上传中（' + files.length + ' 个）...';
            setActionStatus('shigongActionStatus', '施组上传中（' + files.length + ' 个）...', false);
            let okCount = 0;
            let failCount = 0;
            const details = [];
            for (const f of files) {
              const fd = new FormData();
              fd.append('file', f);
              try {
                const res = await fetch('/api/v1/projects/' + projectId + '/shigong', { method: 'POST', headers, body: fd });
                const text = await res.text();
                if (res.ok) {
                  okCount += 1;
                  details.push('[成功] ' + f.name);
                } else {
                  failCount += 1;
                  let detail = text || '';
                  try { const j = JSON.parse(text || '{}'); detail = (j && j.detail) || detail; } catch (_) {}
                  details.push('[失败] ' + f.name + ' -> HTTP ' + res.status + ' ' + String(detail).slice(0, 120));
                }
              } catch (err) {
                failCount += 1;
                details.push('[失败] ' + f.name + ' -> ' + String((err && err.message) || err || '网络异常'));
              }
            }
            if (o) o.textContent = '施组上传完成：成功 ' + okCount + '，失败 ' + failCount + '。成功文件已入库，待点击“评分施组”后出分。' + NL + details.join(NL);
            setActionStatus(
              'shigongActionStatus',
              '上传完成：成功 ' + okCount + '，失败 ' + failCount + '。成功文件待评分。',
              failCount > 0
            );
            if (okCount > 0) await refreshSubmissions(projectId, projectSwitchSeq);
            if (fileInput && failCount === 0) fileInput.value = '';
          } finally {
            uploadShigongInFlight = false;
          }
        }
        async function scoreShigongAction() {
          if (scoreShigongInFlight) {
            setActionStatus('shigongActionStatus', '施组评分进行中，请稍候…', false);
            return;
          }
          scoreShigongInFlight = true;
          try {
            const projectId = pid();
            if (!projectId) {
              const o = document.getElementById('output');
              if (o) o.textContent = '请先选择项目';
              setActionStatus('shigongActionStatus', '评分失败：请先选择项目。', true);
              return;
            }
            const o = document.getElementById('output');
            const scaleLabel = selectedScoreScaleLabel();
            if (o) o.textContent = '施组评分中（' + scaleLabel + '）...';
            setActionStatus('shigongActionStatus', '施组评分中（' + scaleLabel + '）...', false);
            let res;
            let data = {};
            try {
              res = await fetch('/api/v1/projects/' + projectId + '/rescore', {
                method: 'POST',
                headers: apiHeaders(true),
                body: JSON.stringify({
                  scope: 'project',
                  scoring_engine_version: 'v2',
                  score_scale_max: selectedScoreScaleMax(),
                }),
              });
              data = await res.json().catch(() => ({}));
            } catch (err) {
              const msg = '评分失败：' + String((err && err.message) || err || '网络异常');
              if (o) o.textContent = msg;
              setActionStatus('shigongActionStatus', msg, true);
              return;
            }
            if (!res.ok) {
              const detail = (data && data.detail) ? String(data.detail) : ('HTTP ' + String(res.status || 0));
              if (o) o.textContent = '评分失败：' + detail;
              setActionStatus('shigongActionStatus', '评分失败：' + detail, true);
              return;
            }
            const updated = Number(
              (data && (data.updated_submissions ?? data.reports_generated ?? data.submission_count)) || 0
            );
            const doneScaleLabel = (data && data.score_scale_label) ? String(data.score_scale_label) : scaleLabel;
            if (o) o.textContent = '施组评分完成（' + doneScaleLabel + '）：已重算 ' + updated + ' 份。';
            setActionStatus('shigongActionStatus', '评分完成（' + doneScaleLabel + '）：已重算 ' + updated + ' 份。', false);
            await refreshSubmissions(projectId, projectSwitchSeq);
          } finally {
            scoreShigongInFlight = false;
          }
        }
        function updateTableEmptyState(tableId, emptyId) {
          const table = document.getElementById(tableId);
          const tbody = table ? table.querySelector('tbody') : null;
          const emptyEl = document.getElementById(emptyId);
          if (!emptyEl) return;
          emptyEl.style.display = (tbody && tbody.querySelector('tr')) ? 'none' : 'block';
        }
        function extractApiErrorMessage(status, text, data) {
          const detail = (data && data.detail) ? String(data.detail) : String(text || '').trim();
          return '删除失败：HTTP ' + String(status || 0) + (detail ? (' ' + detail) : '');
        }
        async function deleteSubmissionRow(submissionId, rowEl, filename) {
          const id = pid();
          if (!id) { alert('删除失败：请先选择项目'); return; }
          if (!submissionId) { alert('删除失败：记录ID为空'); return; }
          const ok = confirm('确认删除该文件？');
          if (!ok) return;
          let res;
          let text = '';
          let data = {};
          try {
            const encodedPid = encodeURIComponent(String(id || ''));
            const encodedFid = encodeURIComponent(String(submissionId || ''));
            res = await fetch('/api/v1/projects/' + encodedPid + '/shigong/' + encodedFid, { method: 'DELETE', headers: apiHeaders(false) });
            text = await res.text();
            try { data = JSON.parse(text || '{}'); } catch (_) { data = {}; }
          } catch (err) {
            alert('删除失败：' + String((err && err.message) || err || '网络异常'));
            return;
          }
          if (!res.ok) {
            alert(extractApiErrorMessage(res.status, text, data));
            return;
          }
          if (rowEl) rowEl.remove();
          updateTableEmptyState('submissionsTable', 'submissionsEmpty');
          const out = document.getElementById('output');
          if (out) out.textContent = JSON.stringify({ ok: true, id: submissionId, filename: filename || '' }, null, 2);
        }
        async function deleteMaterialRow(materialId, rowEl, filename) {
          const id = pid();
          if (!id) { alert('删除失败：请先选择项目'); return; }
          if (!materialId) { alert('删除失败：记录ID为空'); return; }
          const ok = confirm('确认删除该文件？');
          if (!ok) return;
          let res;
          let text = '';
          let data = {};
          try {
            const encodedPid = encodeURIComponent(String(id || ''));
            const encodedFid = encodeURIComponent(String(materialId || ''));
            res = await fetch('/api/v1/projects/' + encodedPid + '/materials/' + encodedFid, { method: 'DELETE', headers: apiHeaders(false) });
            text = await res.text();
            try { data = JSON.parse(text || '{}'); } catch (_) { data = {}; }
          } catch (err) {
            alert('删除失败：' + String((err && err.message) || err || '网络异常'));
            return;
          }
          if (!res.ok) {
            alert(extractApiErrorMessage(res.status, text, data));
            return;
          }
          if (rowEl) rowEl.remove();
          updateTableEmptyState('materialsTable', 'materialsEmpty');
          if (typeof refreshFeedMaterials === 'function') refreshFeedMaterials();
          const out = document.getElementById('output');
          if (out) out.textContent = JSON.stringify({ ok: true, id: materialId, filename: filename || '' }, null, 2);
        }
        function bindDeleteRowHandlers() {
          const submissionsTable = document.getElementById('submissionsTable');
          if (submissionsTable && !submissionsTable.dataset.deleteBound) {
            submissionsTable.dataset.deleteBound = '1';
            submissionsTable.addEventListener('click', (ev) => {
              const btn = ev.target && ev.target.closest ? ev.target.closest('.js-delete-submission') : null;
              if (!btn) return;
              ev.preventDefault();
              deleteSubmissionRow(
                btn.getAttribute('data-submission-id') || '',
                btn.closest('tr'),
                btn.getAttribute('data-filename') || ''
              );
            });
          }
          const materialsTable = document.getElementById('materialsTable');
          if (materialsTable && !materialsTable.dataset.deleteBound) {
            materialsTable.dataset.deleteBound = '1';
            materialsTable.addEventListener('click', (ev) => {
              const btn = ev.target && ev.target.closest ? ev.target.closest('.js-delete-material') : null;
              if (!btn) return;
              ev.preventDefault();
              deleteMaterialRow(
                btn.getAttribute('data-material-id') || '',
                btn.closest('tr'),
                btn.getAttribute('data-filename') || ''
              );
            });
          }
        }
        async function refreshSubmissions(expectedProjectId=null, switchSeq=null) {
          const id = expectedProjectId || pid();
          const tbl = document.getElementById('submissionsTable');
          const tbody = tbl ? tbl.querySelector('tbody') : null;
          const emptyEl = document.getElementById('submissionsEmpty');
          if (tbody) tbody.innerHTML = '';
          if (!id) {
            if (emptyEl) {
              emptyEl.textContent = '暂无施组，请先选择项目。';
              emptyEl.style.display = 'block';
            }
            return;
          }
          let res;
          try {
            res = await fetch('/api/v1/projects/' + id + '/submissions?t=' + Date.now(), { cache: 'no-store' });
          } catch (err) {
            if (isStaleProjectResponse(id, switchSeq)) return;
            if (emptyEl) {
              emptyEl.textContent = '施组列表加载失败，请稍后重试。';
              emptyEl.style.display = 'block';
            }
            return;
          }
          if (isStaleProjectResponse(id, switchSeq)) return;
          const subs = await res.json().catch(() => []);
          if (!res.ok) {
            if (emptyEl) {
              emptyEl.textContent = '施组列表加载失败（HTTP ' + String(res.status || 0) + '）';
              emptyEl.style.display = 'block';
            }
            return;
          }
          if (!Array.isArray(subs) || subs.length === 0) {
            if (emptyEl) {
              emptyEl.textContent = '暂无施组，请下方添加。';
              emptyEl.style.display = 'block';
            }
            return;
          }
          if (emptyEl) emptyEl.style.display = 'none';
          subs.forEach(s => {
            const tr = document.createElement('tr');
            const rep = (s && typeof s === 'object') ? (s.report || {}) : {};
            const pred = (rep && rep.pred_total_score != null) ? rep.pred_total_score : null;
            const rule = (rep && rep.rule_total_score != null) ? rep.rule_total_score : null;
            const llm = (rep && rep.llm_total_score != null) ? rep.llm_total_score : null;
            const scoringStatus = String((rep && rep.scoring_status) || '').toLowerCase();
            const isPending = scoringStatus === 'pending';
            let scoreHtml = '-';
            if (isPending) {
              scoreHtml = '<span class="note">待评分</span>';
            } else if (pred != null) {
              scoreHtml = escapeHtmlText(String(pred));
              const notes = [];
              if (rule != null) notes.push('规则: ' + escapeHtmlText(String(rule)));
              if (llm != null) notes.push('LLM: ' + escapeHtmlText(String(llm)));
              if (notes.length) scoreHtml += '<div class="note">' + notes.join(' / ') + '</div>';
            } else if (s && s.total_score != null) {
              scoreHtml = escapeHtmlText(String(s.total_score));
            }
            tr.innerHTML =
              '<td>' + escapeHtmlText(s.filename || '') + '</td>' +
              '<td>' + scoreHtml + '</td>' +
              '<td>' + escapeHtmlText((s.created_at || '').slice(0,19)) + '</td>' +
              '<td><button type="button" class="btn-danger js-delete-submission" data-submission-id="' + escapeHtmlText(String(s.id || '')) + '" data-filename="' + escapeHtmlText(String(s.filename || '')) + '">删除</button></td>';
            if (tbody) tbody.appendChild(tr);
          });
          updateTableEmptyState('submissionsTable', 'submissionsEmpty');
          await refreshGroundTruthSubmissionOptions(id, switchSeq);
        }
        async function refreshGroundTruthSubmissionOptions(expectedProjectId=null, switchSeq=null) {
          const id = expectedProjectId || pid();
          const sel = document.getElementById('groundTruthSubmissionSelect');
          if (!sel) return;
          const prev = String(sel.value || '');
          sel.innerHTML = '';
          const pendingOpt = document.createElement('option');
          pendingOpt.value = '';
          if (!id) {
            pendingOpt.textContent = '-- 请先选择项目 --';
            sel.appendChild(pendingOpt);
            sel.value = '';
            return;
          }
          pendingOpt.textContent = '-- 加载步骤4施组中... --';
          sel.appendChild(pendingOpt);
          sel.disabled = true;
          let res;
          try {
            res = await fetch('/api/v1/projects/' + id + '/submissions?t=' + Date.now(), { cache: 'no-store' });
          } catch (_) {
            sel.innerHTML = '';
            const errOpt = document.createElement('option');
            errOpt.value = '';
            errOpt.textContent = '-- 施组列表加载失败，请稍后重试 --';
            sel.appendChild(errOpt);
            sel.value = '';
            sel.disabled = false;
            return;
          }
          if (isStaleProjectResponse(id, switchSeq)) {
            sel.disabled = false;
            return;
          }
          const subs = await res.json().catch(() => []);
          sel.innerHTML = '';
          const leadOpt = document.createElement('option');
          leadOpt.value = '';
          if (!res.ok || !Array.isArray(subs)) {
            leadOpt.textContent = '-- 施组列表加载失败，请稍后重试 --';
            sel.appendChild(leadOpt);
            sel.value = '';
            sel.disabled = false;
            return;
          }
          if (!subs.length) {
            leadOpt.textContent = '-- 暂无施组，请先在步骤4上传 --';
            sel.appendChild(leadOpt);
            sel.value = '';
            sel.disabled = false;
            return;
          }
          leadOpt.textContent = '-- 请选择步骤4已上传施组文件 --';
          sel.appendChild(leadOpt);
          subs.forEach((s) => {
            const opt = document.createElement('option');
            opt.value = String((s && s.id) || '');
            const report = (s && s.report) || {};
            const status = String((report && report.scoring_status) || '').toLowerCase();
            const statusLabel = status === 'pending' ? '待评分' : (status === 'scored' ? '已评分' : '');
            const createdAt = String((s && s.created_at) || '').slice(0, 19);
            const suffix = [createdAt, statusLabel].filter(Boolean).join(' / ');
            opt.textContent = String((s && s.filename) || '未命名施组') + (suffix ? ('（' + suffix + '）') : '');
            sel.appendChild(opt);
          });
          if (prev && subs.some((s) => String((s && s.id) || '') === prev)) {
            sel.value = prev;
          } else {
            sel.value = '';
          }
          sel.disabled = false;
        }
        async function refreshMaterials(expectedProjectId=null, switchSeq=null) {
          const id = expectedProjectId || pid();
          const tbl = document.getElementById('materialsTable');
          const tbody = tbl ? tbl.querySelector('tbody') : null;
          const emptyEl = document.getElementById('materialsEmpty');
          if (tbody) tbody.innerHTML = '';
          if (!id) {
            if (emptyEl) {
              emptyEl.textContent = '暂无资料，请先选择项目。';
              emptyEl.style.display = 'block';
            }
            return;
          }
          let res;
          try {
            res = await fetch('/api/v1/projects/' + id + '/materials?t=' + Date.now(), { cache: 'no-store' });
          } catch (err) {
            if (isStaleProjectResponse(id, switchSeq)) return;
            if (emptyEl) {
              emptyEl.textContent = '资料列表加载失败，请稍后重试。';
              emptyEl.style.display = 'block';
            }
            return;
          }
          if (isStaleProjectResponse(id, switchSeq)) return;
          const mats = await res.json().catch(() => []);
          if (!res.ok) {
            if (emptyEl) {
              emptyEl.textContent = '资料列表加载失败（HTTP ' + String(res.status || 0) + '）';
              emptyEl.style.display = 'block';
            }
            return;
          }
          if (!Array.isArray(mats) || mats.length === 0) {
            if (emptyEl) {
              emptyEl.textContent = '暂无资料，请下方添加。';
              emptyEl.style.display = 'block';
            }
            return;
          }
          if (emptyEl) emptyEl.style.display = 'none';
          mats.forEach(m => {
            const tr = document.createElement('tr');
            tr.innerHTML =
              '<td>' + escapeHtmlText(m.filename || '') + '</td>' +
              '<td>' + escapeHtmlText((m.created_at || '').slice(0,19)) + '</td>' +
              '<td><button type="button" class="btn-danger js-delete-material" data-material-id="' + escapeHtmlText(String(m.id || '')) + '" data-filename="' + escapeHtmlText(String(m.filename || '')) + '">删除</button></td>';
            if (tbody) tbody.appendChild(tr);
          });
          updateTableEmptyState('materialsTable', 'materialsEmpty');
        }
        bindDeleteRowHandlers();
        const btnRefSub = document.getElementById('btnRefreshSubmissions');
        if (btnRefSub) btnRefSub.onclick = refreshSubmissions;
        const btnRefMat = document.getElementById('btnRefreshMaterials');
        if (btnRefMat) btnRefMat.onclick = refreshMaterials;

        async function refreshFeedMaterials(expectedProjectId=null, switchSeq=null) {
          const id = expectedProjectId || pid();
          const tbl = document.getElementById('feedMaterialsTable');
          const tbody = tbl ? tbl.querySelector('tbody') : null;
          const emptyEl = document.getElementById('feedMaterialsEmpty');
          if (tbody) tbody.innerHTML = '';
          if (!id) {
            if (emptyEl) {
              emptyEl.textContent = '暂无投喂包，请先选择项目。';
              emptyEl.style.display = 'block';
            }
            return;
          }
          let res;
          try {
            res = await fetch('/api/v1/projects/' + id + '/materials');
          } catch (err) {
            if (isStaleProjectResponse(id, switchSeq)) return;
            if (emptyEl) {
              emptyEl.textContent = '投喂包列表加载失败，请稍后重试。';
              emptyEl.style.display = 'block';
            }
            return;
          }
          if (isStaleProjectResponse(id, switchSeq)) return;
          const mats = await res.json().catch(() => []);
          if (!res.ok) {
            if (emptyEl) {
              emptyEl.textContent = '投喂包列表加载失败（HTTP ' + String(res.status || 0) + '）';
              emptyEl.style.display = 'block';
            }
            return;
          }
          if (!Array.isArray(mats) || mats.length === 0) {
            if (emptyEl) {
              emptyEl.textContent = '暂无投喂包，请在上方或「3) 项目资料」上传。';
              emptyEl.style.display = 'block';
            }
            return;
          }
          if (emptyEl) emptyEl.style.display = 'none';
          mats.forEach(m => {
            const tr = document.createElement('tr');
            tr.innerHTML = '<td>' + m.filename + '</td><td>' + (m.created_at || '').slice(0,19) + '</td><td><button type="button" class="btn-danger js-delete-material" data-material-id="' + String(m.id || '') + '" data-filename="' + String(m.filename || '').replace(/"/g, '&quot;') + '">删除</button></td>';
            const btn = tr.querySelector('button');
            if (btn) btn.onclick = async () => {
              const r = await fetch('/api/v1/projects/' + id + '/materials/' + m.id, { method: 'DELETE', headers: apiHeaders() });
              if (r.ok) { refreshMaterials(); refreshFeedMaterials(); }
              else { const o = document.getElementById('output'); if (o) o.textContent = await r.text(); }
            };
            if (tbody) tbody.appendChild(tr);
          });
        }
        const btnRefFeed = document.getElementById('btnRefreshFeedMaterials');
        if (btnRefFeed) btnRefFeed.onclick = refreshFeedMaterials;

        function groundTruthListProjectId() {
          const scope = document.getElementById('groundTruthScope').value;
          if (scope === 'current') return pid();
          if (scope === 'other') return document.getElementById('groundTruthOtherProject').value || '';
          return '';
        }
        async function refreshGroundTruth(expectedProjectId=null, switchSeq=null) {
          const listId = expectedProjectId || groundTruthListProjectId();
          const tbl = document.getElementById('groundTruthTable');
          const tbody = tbl ? tbl.querySelector('tbody') : null;
          const emptyEl = document.getElementById('groundTruthEmpty');
          if (tbody) tbody.innerHTML = '';
          if (!listId) {
            if (emptyEl) {
              emptyEl.textContent = '暂无真实评标，请先选择项目。';
              emptyEl.style.display = 'block';
            }
            setResultError('evolveResult', '请先选择项目后再刷新真实评标列表');
            return;
          }
          setResultLoading('evolveResult', '正在刷新真实评标列表...');
          let res;
          try {
            res = await fetch('/api/v1/projects/' + listId + '/ground_truth');
          } catch (err) {
            if (isStaleProjectResponse(expectedProjectId || listId, switchSeq)) return;
            if (emptyEl) {
              emptyEl.textContent = '真实评标列表加载失败，请稍后重试。';
              emptyEl.style.display = 'block';
            }
            setResultError('evolveResult', '真实评标列表加载失败：' + String((err && err.message) || err || '网络异常'));
            return;
          }
          if (isStaleProjectResponse(expectedProjectId || listId, switchSeq)) return;
          const list = await res.json().catch(() => []);
          if (!res.ok) {
            if (emptyEl) {
              emptyEl.textContent = '真实评标列表加载失败（HTTP ' + String(res.status || 0) + '）';
              emptyEl.style.display = 'block';
            }
            setResultError('evolveResult', '真实评标列表加载失败（HTTP ' + String(res.status || 0) + '）');
            return;
          }
          const scope = document.getElementById('groundTruthScope').value;
          const isCurrent = scope === 'current' && listId === selectedProjectIdStrict();
          if (list.length === 0) {
            if (emptyEl) {
              emptyEl.textContent = '暂无真实评标，请下方录入。';
              emptyEl.style.display = 'block';
            }
            setResultSuccess('evolveResult', '刷新完成：当前范围暂无真实评标记录');
            return;
          }
          if (emptyEl) emptyEl.style.display = 'none';
          list.forEach((r, idx) => {
            const summary = (r.shigong_text || '').slice(0, 50);
            const scores = (r.judge_scores || []).slice(0, 5);
            const scoresStr = scores.length ? scores.map(s => s.toFixed(1)).join(', ') : '-';
            const tr = document.createElement('tr');
            const st = r.shigong_text || '';
            const actionCell = isCurrent
              ? '<td><button type="button" class="btn-danger js-delete-ground-truth" data-gt-id="' + escapeHtmlText(String(r.id || '')) + '">删除</button></td>'
              : '<td></td>';
            tr.innerHTML = '<td>' + (idx + 1) + '</td><td title="' + st.slice(0, 200).replace(/"/g, '&quot;') + '">' + (summary ? summary + (st.length > 50 ? '…' : '') : '-') + '</td><td>' + scoresStr + '</td><td>' + (r.final_score != null ? r.final_score : '-') + '</td><td>' + (r.source || '-') + '</td>' + actionCell;
            if (isCurrent) {
              const delBtn = tr.querySelector('button');
              if (delBtn) delBtn.onclick = async () => {
                const delRes = await fetch('/api/v1/projects/' + listId + '/ground_truth/' + r.id, { method: 'DELETE', headers: apiHeaders() });
                if (delRes.status === 204) refreshGroundTruth();
                else { const o = document.getElementById('output'); if (o) o.textContent = await delRes.text(); }
              };
            }
            if (tbody) tbody.appendChild(tr);
          });
          setResultSuccess('evolveResult', '刷新完成：共 ' + list.length + ' 条真实评标记录');
        }
        safeChange('groundTruthScope', function() {
          const otherSel = document.getElementById('groundTruthOtherProject');
          if (otherSel && this.value === 'other') {
            otherSel.style.display = 'inline';
            fetch('/api/v1/projects').then(r => r.json()).then(list => {
              const cur = pid();
              otherSel.innerHTML = '<option value="">-- 选择要查看的项目 --</option>';
              (list || []).forEach(p => {
                if (p.id === cur) return;
                const o = document.createElement('option');
                o.value = p.id;
                o.textContent = (p.name || p.id) + ' (' + (p.id || '').slice(0, 8) + '…)';
                otherSel.appendChild(o);
              });
            });
          } else if (otherSel) {
            otherSel.style.display = 'none';
            otherSel.value = '';
          }
          refreshGroundTruth();
        });
        safeChange('groundTruthOtherProject', refreshGroundTruth);
        safeClick('btnRefreshGroundTruth', refreshGroundTruth);
        safeClick('btnRefreshGroundTruthSubmissionOptions', async () => {
          if (!ensureProjectForAction('evolveResult')) return;
          setResultLoading('evolveResult', '施组选项刷新中...');
          await refreshGroundTruthSubmissionOptions();
          setResultSuccess('evolveResult', '施组选项已刷新：请在下拉框中选择步骤4已上传施组。');
        });

        function showJson(id, data) {
          const out = document.getElementById('output');
          if (out) out.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
        }
        function escapeHtmlText(v) {
          return String(v == null ? '' : v)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\"/g, '&quot;')
            .replace(/'/g, '&#39;');
        }
        function setResultLoading(resultId, label) {
          const el = document.getElementById(resultId);
          if (!el) return;
          el.style.display = 'block';
          el.innerHTML = '<span style="color:#334155">' + escapeHtmlText(label || '处理中，请稍候...') + '</span>';
        }
        function setResultError(resultId, msg) {
          const text = msg || '请求失败';
          const el = document.getElementById(resultId);
          if (el) {
            el.style.display = 'block';
            el.innerHTML = '<span class="error">' + escapeHtmlText(text) + '</span>';
          }
          const out = document.getElementById('output');
          if (out) out.textContent = text;
        }
        function ensureProjectForAction(resultId) {
          if (pid()) return true;
          setResultError(resultId, '请先在「2) 选择项目」中选择项目');
          return false;
        }
        function actionProjectId() {
          return pid() || '__NO_PROJECT__';
        }
        function setResultSuccess(resultId, msg) {
          const text = msg || '操作成功';
          const el = document.getElementById(resultId);
          if (el) {
            el.style.display = 'block';
            el.innerHTML = '<span class="success">' + escapeHtmlText(text) + '</span>';
          }
        }
        function formatApiOutput(res, data, fallback='请求失败') {
          if (res && res.ok) return data;
          if (data && typeof data === 'object') {
            if (Object.prototype.hasOwnProperty.call(data, 'detail')) return data;
            if (Object.keys(data).length > 0) return data;
          } else if (typeof data === 'string' && data.trim()) {
            return data;
          }
          const status = res && typeof res.status === 'number' ? res.status : 0;
          return { detail: (status ? ('HTTP ' + status + ' ') : '') + fallback };
        }

        safeClick('btnCompare', async () => {
          if (!ensureProjectForAction('compareResult')) return;
          setResultLoading('compareResult', '对比排名加载中...');
          const projectId = actionProjectId();
          const res = await fetch('/api/v1/projects/' + projectId + '/compare');
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          const el = document.getElementById('compareResult');
          el.style.display = 'block';
          if (res.ok && data.rankings) {
            const scoreSource = (s) => (s === 'pred' ? '预测' : '规则');
            el.innerHTML = '<strong>排名</strong><table><tr><th>文件名</th><th>总分(优先预测)</th><th>规则分(追溯)</th><th>来源</th><th>时间</th></tr>' +
              data.rankings.map(r => '<tr><td>' + r.filename + '</td><td>' + r.total_score + '</td><td>' + (r.rule_total_score ?? '-') + '</td><td>' + scoreSource(r.score_source) + '</td><td>' + r.created_at + '</td></tr>').join('') + '</table>';
          } else {
            el.innerHTML = '<span class="error">' + (data.detail || '请求失败') + '</span>';
          }
        });

        safeClick('btnCompareReport', async () => {
          if (!ensureProjectForAction('compareReportResult')) return;
          setResultLoading('compareReportResult', '对比报告生成中...');
          const projectId = actionProjectId();
          const res = await fetch('/api/v1/projects/' + projectId + '/compare_report');
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          const el = document.getElementById('compareReportResult');
          el.style.display = 'block';
          document.getElementById('insightsResult').style.display = 'none';
          document.getElementById('learningResult').style.display = 'none';
          if (res.ok) {
            const esc = (v) => String(v == null ? '' : v)
              .replace(/&/g, '&amp;')
              .replace(/</g, '&lt;')
              .replace(/>/g, '&gt;')
              .replace(/\"/g, '&quot;')
              .replace(/'/g, '&#39;');
            const escMultiline = (v) => esc(v).replace(/\\n/g, '<br/>');
            const scoreText = (row) => {
              if (!row) return '-';
              const source = row.score_source === 'pred' ? '预测' : '规则';
              const rule = row.rule_total_score == null ? '-' : row.rule_total_score;
              return esc(row.total_score) + ' 分（规则 ' + esc(rule) + '，来源 ' + source + '）';
            };
            let html = '<p><strong>摘要</strong>: ' + (data.summary || '') + '</p>';
            if (data.top_submission && data.top_submission.filename)
              html += '<p>最高: ' + data.top_submission.filename + ' — ' + scoreText(data.top_submission) + '</p>';
            if (data.bottom_submission && data.bottom_submission.filename)
              html += '<p>最低: ' + data.bottom_submission.filename + ' — ' + scoreText(data.bottom_submission) + '</p>';
            if (data.submission_scorecards && data.submission_scorecards.length) {
              html += '<strong>逐份施组得分项/失分项（按文件）</strong>' +
                data.submission_scorecards.map((card, idx) => {
                  const dimRows = Array.isArray(card.dimension_score_items) ? card.dimension_score_items : [];
                  const lossRows = Array.isArray(card.loss_items) ? card.loss_items : [];
                  const gainRows = Array.isArray(card.gain_items) ? card.gain_items : [];
                  const dedRows = Array.isArray(card.deduction_items) ? card.deduction_items : [];
                  const title = esc(card.filename || '') +
                    '（排名 ' + esc(card.rank_desc || '-') +
                    '，总分 ' + esc(card.total_score) +
                    '，距满分 ' + esc(card.gap_to_full_total) +
                    '，累计扣分 ' + esc(card.total_deduction_points) + '）';

                  const lossTable = '<strong>主要失分项（Top5）</strong><table><tr><th>维度</th><th>得分/满分</th><th>失分</th><th>定位页码</th><th>证据片段</th></tr>' +
                    (lossRows.length ? lossRows.map(r => '<tr>' +
                      '<td>' + esc((r.dimension || '') + ' ' + (r.dimension_name || '')) + '</td>' +
                      '<td>' + esc(r.score) + '/' + esc(r.max_score) + '</td>' +
                      '<td>' + esc(r.gap_to_full) + '</td>' +
                      '<td>' + esc(r.page_hint || '页码未知') + '</td>' +
                      '<td>' + esc(r.evidence || '') + '<br/><span style="color:#64748b;font-size:12px">' + escMultiline(r.evidence_context || '') + '</span></td>' +
                    '</tr>').join('') : '<tr><td colspan="5">暂无失分项。</td></tr>') +
                    '</table>';

                  const gainTable = '<strong>主要得分项（Top5）</strong><table><tr><th>维度</th><th>得分/满分</th><th>定位页码</th><th>证据片段</th></tr>' +
                    (gainRows.length ? gainRows.map(r => '<tr>' +
                      '<td>' + esc((r.dimension || '') + ' ' + (r.dimension_name || '')) + '</td>' +
                      '<td>' + esc(r.score) + '/' + esc(r.max_score) + '</td>' +
                      '<td>' + esc(r.page_hint || '页码未知') + '</td>' +
                      '<td>' + esc(r.evidence || '') + '<br/><span style="color:#64748b;font-size:12px">' + escMultiline(r.evidence_context || '') + '</span></td>' +
                    '</tr>').join('') : '<tr><td colspan="4">暂无得分项。</td></tr>') +
                    '</table>';

                  const deductionTable = '<strong>扣分项（按该施组）</strong><table><tr><th>扣分码</th><th>扣分</th><th>定位页码</th><th>原因</th><th>证据片段</th></tr>' +
                    (dedRows.length ? dedRows.map(d => '<tr>' +
                      '<td>' + esc(d.code || '') + '</td>' +
                      '<td>' + esc(d.points) + '</td>' +
                      '<td>' + esc(d.page_hint || '页码未知') + '</td>' +
                      '<td>' + esc(d.reason || '') + '</td>' +
                      '<td>' + esc(d.evidence || '') + '<br/><span style="color:#64748b;font-size:12px">' + escMultiline(d.evidence_context || '') + '</span></td>' +
                    '</tr>').join('') : '<tr><td colspan="5">该施组暂无扣分项。</td></tr>') +
                    '</table>';

                  const fullDimTable = '<details style="margin-top:6px"><summary>查看该施组16维完整得分表</summary><table><tr><th>维度</th><th>名称</th><th>模块</th><th>得分</th><th>满分</th><th>失分</th><th>定位页码</th></tr>' +
                    (dimRows.length ? dimRows.map(r => '<tr>' +
                      '<td>' + esc(r.dimension || '') + '</td>' +
                      '<td>' + esc(r.dimension_name || '') + '</td>' +
                      '<td>' + esc(r.module || '') + '</td>' +
                      '<td>' + esc(r.score) + '</td>' +
                      '<td>' + esc(r.max_score) + '</td>' +
                      '<td>' + esc(r.gap_to_full) + '</td>' +
                      '<td>' + esc(r.page_hint || '页码未知') + '</td>' +
                    '</tr>').join('') : '<tr><td colspan="7">暂无维度数据。</td></tr>') +
                    '</table></details>';

                  return '<details style="margin-top:8px"' + (idx === 0 ? ' open' : '') + '><summary>' + title + '</summary><div style="margin-top:8px">' + lossTable + gainTable + deductionTable + fullDimTable + '</div></details>';
                }).join('');
            }
            if (data.submission_optimization_cards && data.submission_optimization_cards.length) {
              html += '<strong>逐文件优化清单（你要的直接执行版）</strong>' +
                data.submission_optimization_cards.map(card => {
                  const rows = Array.isArray(card.recommendations) ? card.recommendations : [];
                  const table = '<table><tr><th>优先级</th><th>类别</th><th>建议章节</th><th>定位页码</th><th>预计提分</th><th>优先理由</th><th>问题</th><th>证据片段</th><th>证据窗口（前后文）</th><th>改写前后示例</th><th>建议改写（直接执行）</th><th>验收标准</th><th>执行检查表</th></tr>' +
                    rows.map(r => '<tr>' +
                      '<td>' + esc(r.priority || '') + '</td>' +
                      '<td>' + esc(r.category || '') + '</td>' +
                      '<td>' + esc(r.chapter_hint || '') + '</td>' +
                      '<td>' + esc(r.page_hint || '页码未知') + '</td>' +
                      '<td>' + esc(r.target_delta_reduction == null ? '' : r.target_delta_reduction) + '</td>' +
                      '<td>' + escMultiline(r.priority_reason || '') + '</td>' +
                      '<td>' + escMultiline(r.issue || '') + '</td>' +
                      '<td>' + escMultiline(r.evidence || '') + '</td>' +
                      '<td><span style="font-size:12px;color:#334155">' + escMultiline(r.evidence_context || '') + '</span></td>' +
                      '<td><details><summary>展开</summary><span style="font-size:12px;color:#0f172a">' + escMultiline(r.before_after_example || '') + '</span></details></td>' +
                      '<td><details open><summary>执行步骤</summary>' + escMultiline(r.rewrite_instruction || '') + '</details></td>' +
                      '<td><details><summary>验收标准</summary>' + escMultiline(r.acceptance_check || '') + '</details></td>' +
                      '<td><details><summary>检查表</summary>' + escMultiline(r.execution_checklist || '') + '</details></td>' +
                    '</tr>').join('') + '</table>';
                  const refTop = card.reference_top_score == null ? '' : ('，项目最高 ' + esc(card.reference_top_score) + ' 分');
                  const title = esc(card.filename || '') + '（当前 ' + esc(card.total_score) + ' 分，目标 ' + esc(card.target_score) + ' 分，差距 ' + esc(card.target_gap) + refTop + '）';
                  return '<details style="margin-top:8px" open><summary>' + title + '</summary><div style="margin-top:8px">' + table + '</div></details>';
                }).join('');
            }
            if (data.score_overview && Object.keys(data.score_overview).length) {
              html += '<strong>总体分布</strong><table><tr><th>施组数</th><th>最高分</th><th>最低分</th><th>分差</th><th>项目均分</th><th>波动(标准差)</th></tr><tr>' +
                '<td>' + esc(data.score_overview.submission_count) + '</td>' +
                '<td>' + esc(data.score_overview.top_score) + '</td>' +
                '<td>' + esc(data.score_overview.bottom_score) + '</td>' +
                '<td>' + esc(data.score_overview.score_gap) + '</td>' +
                '<td>' + esc(data.score_overview.project_avg_score) + '</td>' +
                '<td>' + esc(data.score_overview.project_std_score) + '</td>' +
                '</tr></table>';
            }
            if (data.key_diffs && data.key_diffs.length) {
              html += '<strong>主要差距维度</strong><table><tr><th>维度</th><th>名称</th><th>模块</th><th>最高分施组</th><th>最低分施组</th><th>分差</th></tr>' +
                data.key_diffs.map(d =>
                  '<tr><td>' + esc(d.dimension || d.dim_id) + '</td><td>' + esc(d.dimension_name || '') + '</td><td>' + esc(d.module || '') + '</td><td>' + esc((d.top_filename || '-') + '（' + (d.top_dimension_score == null ? '-' : d.top_dimension_score) + '）') + '</td><td>' + esc((d.bottom_filename || '-') + '（' + (d.bottom_dimension_score == null ? '-' : d.bottom_dimension_score) + '）') + '</td><td>' + esc(d.delta) + '</td></tr>'
                ).join('') + '</table>';
            }
            if (data.dimension_diagnostics && data.dimension_diagnostics.length) {
              html += '<strong>维度诊断（编制依据）</strong><table><tr><th>维度</th><th>均分</th><th>最高/最低</th><th>分差</th><th>定位页码</th><th>弱势文件（含该维得分）</th><th>建议动作</th></tr>' +
                data.dimension_diagnostics.map(d => {
                  const actions = Array.isArray(d.actions) ? d.actions.slice(0, 2).map(x => esc(x)).join('<br/>') : '';
                  const weakFiles = Array.isArray(d.weak_files_with_scores) && d.weak_files_with_scores.length
                    ? d.weak_files_with_scores.slice(0, 4).map(x => esc(x)).join('、')
                    : (Array.isArray(d.weak_filenames) && d.weak_filenames.length
                      ? d.weak_filenames.slice(0, 4).map(x => esc(x)).join('、')
                      : ('共 ' + esc(d.weak_file_count || 0) + ' 份'));
                  return '<tr><td>' + esc((d.dimension || '') + ' ' + (d.dimension_name || '')) + '</td><td>' + esc(d.project_avg) + '</td><td>' + esc(d.top_score) + ' / ' + esc(d.bottom_score) + '</td><td>' + esc(d.delta) + '</td><td>' + esc(d.bottom_page_hint || d.top_page_hint || '页码未知') + '</td><td>' + weakFiles + '</td><td>' + actions + '</td></tr>';
                }).join('') + '</table>';
              html += '<details style="margin-top:6px"><summary>查看维度证据与改写模板</summary>' +
                data.dimension_diagnostics.map(d => {
                  const topRow = Array.isArray(d.top_evidence_rows) && d.top_evidence_rows.length ? d.top_evidence_rows[0] : null;
                  const botRow = Array.isArray(d.bottom_evidence_rows) && d.bottom_evidence_rows.length ? d.bottom_evidence_rows[0] : null;
                  const te = topRow ? esc(topRow.snippet || '') : (Array.isArray(d.top_evidence) && d.top_evidence.length ? esc(d.top_evidence[0]) : '无');
                  const be = botRow ? esc(botRow.snippet || '') : (Array.isArray(d.bottom_evidence) && d.bottom_evidence.length ? esc(d.bottom_evidence[0]) : '无');
                  const tp = topRow ? esc(topRow.page_hint || '页码未知') : esc(d.top_page_hint || '页码未知');
                  const bp = botRow ? esc(botRow.page_hint || '页码未知') : esc(d.bottom_page_hint || '页码未知');
                  const tc = topRow ? escMultiline(topRow.context_window || '') : '无';
                  const bc = botRow ? escMultiline(botRow.context_window || '') : '无';
                  const topFile = esc(d.top_filename || '高分施组');
                  const bottomFile = esc(d.bottom_filename || '低分施组');
                  return '<div style="margin-top:8px;padding:8px;border:1px solid #e2e8f0;border-radius:6px"><strong>' + esc((d.dimension || '') + ' ' + (d.dimension_name || '')) + '</strong>' +
                    '<p style="margin:4px 0"><strong>' + topFile + ' 证据片段（' + tp + '）：</strong>' + te + '</p>' +
                    '<p style="margin:4px 0;color:#64748b;font-size:12px"><strong>高分证据窗口：</strong><br/>' + tc + '</p>' +
                    '<p style="margin:4px 0"><strong>' + bottomFile + ' 证据片段（' + bp + '）：</strong>' + be + '</p>' +
                    '<p style="margin:4px 0;color:#64748b;font-size:12px"><strong>低分证据窗口：</strong><br/>' + bc + '</p>' +
                    '<p style="margin:4px 0"><strong>建议改写模板：</strong>' + esc(d.rewrite_template || '') + '</p></div>';
                }).join('') + '</details>';
            }
            if (data.penalty_diagnostics && data.penalty_diagnostics.length) {
              html += '<strong>扣分项诊断（编制风险）</strong><table><tr><th>扣分码</th><th>出现次数</th><th>影响文件数</th><th>累计扣分</th><th>高风险页码</th><th>建议动作</th></tr>' +
                data.penalty_diagnostics.map(p => {
                  const actions = Array.isArray(p.actions) ? p.actions.slice(0, 2).map(x => esc(x)).join('<br/>') : '';
                  const pageHint = Array.isArray(p.page_hints) && p.page_hints.length ? esc(p.page_hints[0]) : '页码未知';
                  return '<tr><td>' + esc(p.code) + '</td><td>' + esc(p.count) + '</td><td>' + esc(p.affected_submission_count) + '</td><td>' + esc(p.total_points) + '</td><td>' + pageHint + '</td><td>' + actions + '</td></tr>';
                }).join('') + '</table>';
              html += '<details style="margin-top:6px"><summary>查看扣分原因样本与证据片段</summary>' +
                data.penalty_diagnostics.map(p => {
                  const reason = Array.isArray(p.reason_samples) && p.reason_samples.length ? esc(p.reason_samples[0]) : '无';
                  const ev = Array.isArray(p.evidence_samples) && p.evidence_samples.length ? esc(p.evidence_samples[0]) : '无';
                  const evCtx = Array.isArray(p.evidence_context_samples) && p.evidence_context_samples.length ? escMultiline(p.evidence_context_samples[0]) : '无';
                  const pageHint = Array.isArray(p.page_hints) && p.page_hints.length ? esc(p.page_hints[0]) : '页码未知';
                  return '<div style="margin-top:8px;padding:8px;border:1px solid #e2e8f0;border-radius:6px"><strong>' + esc(p.code) + '</strong>' +
                    '<p style="margin:4px 0"><strong>高频原因：</strong>' + reason + '</p>' +
                    '<p style="margin:4px 0"><strong>证据片段（' + pageHint + '）：</strong>' + ev + '</p>' +
                    '<p style="margin:4px 0;color:#64748b;font-size:12px"><strong>证据窗口：</strong><br/>' + evCtx + '</p></div>';
                }).join('') + '</details>';
            }
            if (data.priority_actions && data.priority_actions.length) {
              html += '<strong>优先优化动作清单（可直接下发编制）</strong><ol>' +
                data.priority_actions.map(a =>
                  '<li><strong>' + esc(a.priority) + ' - ' + esc(a.theme) + '</strong><br/>' +
                  '依据：' + esc(a.reason || '') + '<br/>' +
                  '证据：' + esc(a.evidence || '') + '（' + esc(a.page_hint || '页码未知') + '）<br/>' +
                  '动作：' + esc(a.action || '') + '<br/>' +
                  '预期效果：' + esc(a.expected_impact || '') + '</li>'
                ).join('') + '</ol>';
            }
            if (data.submission_diagnostics && data.submission_diagnostics.length) {
              html += '<details style="margin-top:6px"><summary>分文件诊断（逐份施组）</summary><table><tr><th>文件</th><th>总分</th><th>弱项维度</th><th>主要扣分</th><th>建议</th></tr>' +
                data.submission_diagnostics.map(r => {
                  const weak = Array.isArray(r.weakest_dimensions) ? r.weakest_dimensions.map(w => esc((w.dimension || '') + (w.dimension_name ? (' ' + w.dimension_name) : ''))).join('、') : '-';
                  const penalties = Array.isArray(r.major_penalties) ? r.major_penalties.map(p => esc(p.code + '×' + p.count)).join('，') : '-';
                  return '<tr><td>' + esc(r.filename) + '</td><td>' + esc(r.total_score) + '</td><td>' + weak + '</td><td>' + penalties + '</td><td>' + esc(r.actionable_summary || '') + '</td></tr>';
                }).join('') + '</table></details>';
            }
            el.innerHTML = html;
          } else {
            el.innerHTML = '<span class="error">' + (data.detail || '请求失败') + '</span>';
          }
        });

        safeClick('btnInsights', async () => {
          if (!ensureProjectForAction('insightsResult')) return;
          setResultLoading('insightsResult', '洞察分析中...');
          const projectId = actionProjectId();
          const res = await fetch('/api/v1/projects/' + projectId + '/insights');
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          const el = document.getElementById('insightsResult');
          el.style.display = 'block';
          document.getElementById('compareReportResult').style.display = 'none';
          document.getElementById('learningResult').style.display = 'none';
          if (res.ok) {
            let html = '';
            if (data.weakest_dims && data.weakest_dims.length)
              html += '<strong>弱项维度</strong><ul>' + data.weakest_dims.map(d => '<li>' + (d.dimension || d.dimension_id) + ': ' + valueOrDefault(d.avg_score, d.avg) + '</li>').join('') + '</ul>';
            if (data.frequent_penalties && data.frequent_penalties.length)
              html += '<strong>常见扣分</strong><ul>' + data.frequent_penalties.map(p => '<li>' + (p.code || '') + ': ' + (p.count || 0) + ' 次</li>').join('') + '</ul>';
            if (data.recommendations && data.recommendations.length)
              html += '<strong>建议</strong><ul>' + data.recommendations.map(r => '<li>' + (r.reason || '') + ' — ' + (r.action || '') + '</li>').join('') + '</ul>';
            el.innerHTML = html || '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
          } else {
            el.innerHTML = '<span class="error">' + (data.detail || '请求失败') + '</span>';
          }
        });

        safeClick('btnLearning', async () => {
          if (!ensureProjectForAction('learningResult')) return;
          setResultLoading('learningResult', '学习画像生成中...');
          const projectId = actionProjectId();
          const res = await fetch('/api/v1/projects/' + projectId + '/learning', { method: 'POST' });
          const text = await res.text();
          document.getElementById('output').textContent = text;
          const el = document.getElementById('learningResult');
          el.style.display = 'block';
          document.getElementById('compareReportResult').style.display = 'none';
          document.getElementById('insightsResult').style.display = 'none';
          try {
            const data = JSON.parse(text);
            el.innerHTML = '<p class="success">学习画像已生成/更新</p><pre>' + JSON.stringify(data.dimension_multipliers || {}, null, 2) + '</pre>';
          } catch (_) {
            el.innerHTML = '<pre>' + text + '</pre>';
          }
        });

        safeClick('btnAdaptive', async () => {
          if (!ensureProjectForAction('adaptiveResult')) return;
          setResultLoading('adaptiveResult', '自适应建议生成中...');
          const projectId = actionProjectId();
          const res = await fetch('/api/v1/projects/' + projectId + '/adaptive');
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          const el = document.getElementById('adaptiveResult');
          el.style.display = 'block';
          document.getElementById('adaptivePatchResult').style.display = 'none';
          document.getElementById('adaptiveValidateResult').style.display = 'none';
          document.getElementById('adaptiveApplyResult').style.display = 'none';
          if (res.ok) {
            let html = '';
            if (data.penalty_stats && Object.keys(data.penalty_stats).length)
              html += '<strong>扣分统计</strong><ul>' + Object.entries(data.penalty_stats).map(([k,v]) => '<li>' + k + ': ' + v + '</li>').join('') + '</ul>';
            if (data.suggestions && data.suggestions.length)
              html += '<strong>建议</strong><ul>' + data.suggestions.map(s => '<li>' + (s.message || JSON.stringify(s)) + '</li>').join('') + '</ul>';
            el.innerHTML = html || '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
          } else {
            el.innerHTML = '<span class="error">' + (data.detail || '请求失败') + '</span>';
          }
        });

        safeClick('btnAdaptivePatch', async () => {
          if (!ensureProjectForAction('adaptivePatchResult')) return;
          setResultLoading('adaptivePatchResult', '补丁生成中...');
          const projectId = actionProjectId();
          const res = await fetch('/api/v1/projects/' + projectId + '/adaptive_patch');
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          const el = document.getElementById('adaptivePatchResult');
          el.style.display = 'block';
          document.getElementById('adaptiveValidateResult').style.display = 'none';
          document.getElementById('adaptiveApplyResult').style.display = 'none';
          if (res.ok) {
            let html = '';
            if (data.lexicon_additions && Object.keys(data.lexicon_additions).length)
              html += '<strong>词库补丁</strong><details><summary>展开</summary><pre>' + JSON.stringify(data.lexicon_additions, null, 2) + '</pre></details>';
            if (data.rubric_adjustments && Object.keys(data.rubric_adjustments).length)
              html += '<strong>规则补丁</strong><details><summary>展开</summary><pre>' + JSON.stringify(data.rubric_adjustments, null, 2) + '</pre></details>';
            el.innerHTML = html || '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
          } else {
            el.innerHTML = '<span class="error">' + (data.detail || '请求失败') + '</span>';
          }
        });

        safeClick('btnAdaptiveValidate', async () => {
          if (!ensureProjectForAction('adaptiveValidateResult')) return;
          setResultLoading('adaptiveValidateResult', '验证效果计算中...');
          const projectId = actionProjectId();
          const res = await fetch('/api/v1/projects/' + projectId + '/adaptive_validate');
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          const el = document.getElementById('adaptiveValidateResult');
          el.style.display = 'block';
          document.getElementById('adaptiveApplyResult').style.display = 'none';
          if (res.ok && data.comparisons) {
            let html = '<p><strong>平均分差（新-旧）</strong>: ' + data.avg_delta + '</p>';
            html += '<table><tr><th>文件名</th><th>旧分</th><th>新分</th><th>变化</th></tr>' +
              data.comparisons.map(c => '<tr><td>' + c.filename + '</td><td>' + c.old_score + '</td><td>' + c.new_score + '</td><td>' + c.delta + '</td></tr>').join('') + '</table>';
            el.innerHTML = html;
          } else {
            el.innerHTML = res.ok ? '<pre>' + JSON.stringify(data, null, 2) + '</pre>' : '<span class="error">' + (data.detail || '请求失败') + '</span>';
          }
        });

        safeClick('btnAdaptiveApply', async () => {
          if (!ensureProjectForAction('adaptiveApplyResult')) return;
          setResultLoading('adaptiveApplyResult', '应用补丁中...');
          const projectId = actionProjectId();
          const storedApiKey = storageGet('api_key');
          let apiKey = storedApiKey || '';
          if (!apiKey) {
            const prompted = prompt('应用补丁将修改 lexicon 配置，需要 API Key。请输入 X-API-Key（无则留空）：');
            apiKey = prompted == null ? '' : prompted;
          }
          const headers = {};
          if (apiKey) headers['X-API-Key'] = apiKey;
          const res = await fetch('/api/v1/projects/' + projectId + '/adaptive_apply', { method: 'POST', headers });
          const text = await res.text();
          document.getElementById('output').textContent = text;
          const el = document.getElementById('adaptiveApplyResult');
          el.style.display = 'block';
          try {
            const data = JSON.parse(text);
            el.innerHTML = data.applied ? '<p class="success">已应用。变更: ' + (data.changes || []).join(', ') + '</p><p>备份: ' + (data.backup_path || '') + '</p>' : '<p class="error">' + (data.detail || text) + '</p>';
          } catch (_) {
            el.innerHTML = '<pre>' + text + '</pre>';
          }
        });

        safeClick('btnUploadFeed', async () => {
          if (!ensureProjectForAction('evolveResult')) return;
          const projectId = actionProjectId();
          const feedInput = document.getElementById('feedFile');
          const files = Array.from((feedInput && feedInput.files) || []);
          const headers = {};
          const apiKey = storageGet('api_key');
          if (apiKey) headers['X-API-Key'] = apiKey;
          const requestCount = files.length > 0 ? files.length : 1;
          setResultLoading('evolveResult', '投喂包上传中（请求 ' + requestCount + ' 次）...');
          document.getElementById('output').textContent = '投喂包上传中（请求 ' + requestCount + ' 次）...';
          setActionStatus('feedActionStatus', '投喂包上传中（请求 ' + requestCount + ' 次）...', false);
          let okCount = 0;
          let failCount = 0;
          const details = [];
          const uploadTargets = files.length > 0 ? files : [null];
          for (const f of uploadTargets) {
            const fd = new FormData();
            if (f) fd.append('file', f);
            const label = f ? f.name : '（空文件请求）';
            try {
              const res = await fetch('/api/v1/projects/' + projectId + '/materials', { method: 'POST', headers, body: fd });
              const text = await res.text();
              if (res.ok) {
                okCount += 1;
                details.push('[成功] ' + label);
              } else {
                failCount += 1;
                let detail = text || '';
                try { const j = JSON.parse(text || '{}'); detail = (j && j.detail) || detail; } catch (_) {}
                details.push('[失败] ' + label + ' -> HTTP ' + res.status + ' ' + String(detail).slice(0, 120));
              }
            } catch (err) {
              failCount += 1;
              details.push('[失败] ' + label + ' -> ' + String((err && err.message) || err || '网络异常'));
            }
          }
          document.getElementById('output').textContent = '投喂包上传完成：成功 ' + okCount + '，失败 ' + failCount + NL + details.join(NL);
          setActionStatus(
            'feedActionStatus',
            '上传完成：成功 ' + okCount + '，失败 ' + failCount + '。',
            failCount > 0
          );
          const evolveEl = document.getElementById('evolveResult');
          if (okCount > 0) {
            evolveEl.innerHTML = '<p class="success">投喂包已保存：成功 ' + okCount + ' 个，失败 ' + failCount + ' 个。</p>';
            evolveEl.style.display = 'block';
            refreshMaterials();
            refreshFeedMaterials();
          } else {
            evolveEl.innerHTML = '<p class="error">投喂包上传失败，请检查文件格式或网络。</p>';
            evolveEl.style.display = 'block';
          }
          if (feedInput && failCount === 0) feedInput.value = '';
        });
        safeClick('btnAddGroundTruth', async () => {
          if (!ensureProjectForAction('evolveResult')) return;
          const projectId = actionProjectId();
          const submissionSelect = document.getElementById('groundTruthSubmissionSelect');
          const submissionId = String((submissionSelect && submissionSelect.value) || '').trim();
          if (!submissionId) {
            setResultError('evolveResult', '请先在“施组文件”下拉框选择步骤4已上传施组。');
            return;
          }
          const j1 = parseFloat(document.getElementById('gtJ1').value) || 0, j2 = parseFloat(document.getElementById('gtJ2').value) || 0, j3 = parseFloat(document.getElementById('gtJ3').value) || 0, j4 = parseFloat(document.getElementById('gtJ4').value) || 0, j5 = parseFloat(document.getElementById('gtJ5').value) || 0;
          const finalScore = parseFloat(document.getElementById('gtFinal').value) || 0;
          setResultLoading('evolveResult', '真实评标录入中（基于步骤4已上传施组）...');
          document.getElementById('output').textContent = '真实评标录入中（基于步骤4已上传施组）...';
          const payload = {
            submission_id: submissionId,
            judge_scores: [j1, j2, j3, j4, j5],
            final_score: finalScore,
            source: '青天大模型',
          };
          let res, data;
          try {
            res = await fetch('/api/v1/projects/' + projectId + '/ground_truth/from_submission', {
              method: 'POST',
              headers: apiHeaders(true),
              body: JSON.stringify(payload),
            });
            data = await res.json().catch(() => ({}));
          } catch (err) {
            document.getElementById('output').textContent = '真实评标录入失败：' + String((err && err.message) || err || '网络异常');
            return;
          }
          showJson('output', res.ok ? data : (data.detail || data));
          if (!res.ok) {
            const evolveErr = document.getElementById('evolveResult');
            evolveErr.innerHTML = '<p class="error">真实评标录入失败：' + (data.detail || ('HTTP ' + res.status)) + '</p>';
            evolveErr.style.display = 'block';
            return;
          }
          const sourceName = String((submissionSelect && submissionSelect.options && submissionSelect.selectedIndex >= 0 && submissionSelect.options[submissionSelect.selectedIndex] && submissionSelect.options[submissionSelect.selectedIndex].textContent) || submissionId);
          const evolveEl = document.getElementById('evolveResult');
          evolveEl.innerHTML =
            '<p class="success">真实评标录入完成：已记录 1 条。</p>' +
            '<p style="margin:6px 0 0 0"><strong>施组：</strong>' + escapeHtmlText(sourceName) + '</p>' +
            '<p style="margin:4px 0 0 0"><strong>最终分：</strong>' + escapeHtmlText(String(data.final_score != null ? data.final_score : finalScore)) + '</p>';
          evolveEl.style.display = 'block';
          if (submissionSelect) submissionSelect.value = '';
          refreshGroundTruth();
        });
        safeClick('btnEvolve', async () => {
          if (!ensureProjectForAction('evolveResult')) return;
          const projectId = actionProjectId();
          setResultLoading('evolveResult', '学习进化执行中...');
          const res = await fetch('/api/v1/projects/' + projectId + '/evolve', { method: 'POST', headers: apiHeaders(false) });
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data, '请求失败，若需认证请设置 API Key'));
          const el = document.getElementById('evolveResult');
          el.style.display = 'block';
          if (res.ok) {
            let html = '<p class="success">学习完成（基于 ' + (data.sample_count || 0) + ' 条真实评标）</p>';
            if (data.high_score_logic && data.high_score_logic.length) html += '<strong>高分逻辑</strong><ul>' + data.high_score_logic.map(l => '<li>' + l + '</li>').join('') + '</ul>';
            if (data.writing_guidance && data.writing_guidance.length) html += '<strong>编制指导</strong><ul>' + data.writing_guidance.map(l => '<li>' + l + '</li>').join('') + '</ul>';
            if (data.scoring_evolution && data.scoring_evolution.dimension_multipliers && Object.keys(data.scoring_evolution.dimension_multipliers).length) {
              html += '<strong>评分进化（贴近青天）</strong><p style="font-size:12px;color:#64748b">' + (data.scoring_evolution.goal || '') + '</p>';
              html += '<details><summary>维度权重建议</summary><pre style="margin:4px 0">' + JSON.stringify(data.scoring_evolution.dimension_multipliers, null, 2) + '</pre></details>';
            }
            html += '<p style="font-size:12px;margin-top:8px">点击「编制系统指令」可查看/导出编制约束（必备章节、图表、禁止表述等）。</p>';
            el.innerHTML = html;
          } else { el.innerHTML = '<span class="error">' + (data.detail || '请求失败，若需认证请设置 API Key') + '</span>'; }
        });
        safeClick('btnWritingGuidance', async () => {
          if (!ensureProjectForAction('guidanceResult')) return;
          const projectId = actionProjectId();
          setResultLoading('guidanceResult', '正在生成编制指导...');
          const res = await fetch('/api/v1/projects/' + projectId + '/writing_guidance');
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          const el = document.getElementById('guidanceResult');
          el.style.display = 'block';
          if (res.ok) {
            let html = '';
            if (data.high_score_logic && data.high_score_logic.length) html += '<strong>高分逻辑</strong><ul>' + data.high_score_logic.map(l => '<li>' + l + '</li>').join('') + '</ul>';
            if (data.guidance && data.guidance.length) html += '<strong>编制指导</strong><ul>' + data.guidance.map(l => '<li>' + l + '</li>').join('') + '</ul>';
            el.innerHTML = html || '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
          } else { el.innerHTML = '<span class="error">' + (data.detail || '') + '</span>'; }
        });
        safeClick('btnCompilationInstructions', async () => {
          if (!ensureProjectForAction('compilationInstructionsResult')) return;
          const projectId = actionProjectId();
          setResultLoading('compilationInstructionsResult', '正在生成编制系统指令...');
          const res = await fetch('/api/v1/projects/' + projectId + '/compilation_instructions');
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          const el = document.getElementById('compilationInstructionsResult');
          el.style.display = 'block';
          if (res.ok) {
            let html = '<strong>编制系统指令</strong>（可导出为编制施组时的系统约束）<br>';
            if ((data.required_sections || []).length) html += '<p><b>必备章节：</b>' + data.required_sections.join('；') + '</p>';
            if ((data.required_charts_images || []).length) html += '<p><b>必备图表/图片：</b>' + data.required_charts_images.join('；') + '</p>';
            if ((data.mandatory_elements || []).length) html += '<p><b>必备要素：</b>' + data.mandatory_elements.join('；') + '</p>';
            if ((data.forbidden_patterns || []).length) html += '<p><b>禁止表述：</b>' + data.forbidden_patterns.join('；') + '</p>';
            if ((data.guidance_items || []).length) html += '<p><b>编制指导：</b><ul>' + data.guidance_items.map(l => '<li>' + l + '</li>').join('') + '</ul></p>';
            html += '<button type="button" class="secondary" id="btnExportInstructions" style="margin-top:8px">导出为文本（复制到剪贴板）</button>';
            el.innerHTML = html;
            (document.getElementById('btnExportInstructions')||{}).onclick = () => {
              const L = (arr, prefix) => (arr || []).map(s => prefix + s).join(String.fromCharCode(10));
              const lines = ['# 施组编制系统指令', '', '## 必备章节', L(data.required_sections || [], '- '), '', '## 必备图表/图片', L(data.required_charts_images || [], '- '), '', '## 必备要素', L(data.mandatory_elements || [], '- '), '', '## 禁止表述', L(data.forbidden_patterns || [], '- '), '', '## 编制指导', L(data.guidance_items || [], '- ')];
              const plain = lines.join(String.fromCharCode(10));
              navigator.clipboard.writeText(plain).then(() => alert('已复制到剪贴板')).catch(() => prompt('请手动复制：', plain));
            };
          } else { el.innerHTML = '<span class="error">' + (data.detail || '') + '</span>'; }
        });
        async function latestPatchId(projectId) {
          const res = await fetch('/api/v1/projects/' + projectId + '/patches');
          const data = await res.json().catch(() => ([]));
          if (!res.ok || !Array.isArray(data) || !data.length) return '';
          return data[0].id || '';
        }
        function showBlock(id, ok, title, payload) {
          const el = document.getElementById(id);
          if (!el) return;
          el.style.display = 'block';
          const klass = ok ? 'success' : 'error';
          el.innerHTML = '<p class="' + klass + '">' + title + '</p><pre style="margin:0">' + JSON.stringify(payload, null, 2) + '</pre>';
        }
        function fmtMetric(v) {
          if (typeof v !== 'number' || Number.isNaN(v)) return '-';
          return Number(v).toFixed(4);
        }
        function showCalibratorSummaryBlock(id, ok, title, payload) {
          const el = document.getElementById(id);
          if (!el) return;
          el.style.display = 'block';
          const klass = ok ? 'success' : 'error';
          let html = '<p class="' + klass + '">' + title + '</p>';
          const data = payload && typeof payload === 'object' ? payload : {};
          if (ok) {
            const summary = (data.calibrator_summary && typeof data.calibrator_summary === 'object') ? data.calibrator_summary : {};
            const metrics = data.metrics || {};
            const cv = summary.cv_metrics || data.calibrator_cv_metrics || {};
            const baseline = summary.baseline_metrics || data.calibrator_baseline_metrics || {};
            const gate = summary.gate || data.calibrator_gate || {};
            const modelType = summary.model_type || data.calibrator_model_type || data.model_type || '-';
            const version = summary.calibrator_version || data.calibrator_version || '-';
            const gatePassed = summary.gate_passed ?? data.calibrator_gate_passed ?? metrics.gate_passed;
            const sampleCount = summary.sample_count;
            const skippedReason = summary.skipped_reason || '';
            const cvMae = cv.mae ?? metrics.cv_mae;
            const cvRmse = cv.rmse ?? metrics.cv_rmse;
            const cvSpearman = cv.spearman ?? metrics.cv_spearman;
            const baselineMae = baseline.mae ?? metrics.baseline_mae;
            const baselineRmse = baseline.rmse ?? metrics.baseline_rmse;
            const baselineSpearman = baseline.spearman ?? metrics.baseline_spearman;
            const improveThreshold = gate.improve_threshold ?? metrics.gate_improve_threshold;
            const spearmanTolerance = gate.spearman_tolerance ?? metrics.gate_spearman_tolerance;
            const cvMode = cv.mode ?? metrics.cv_mode;
            const cvPredCount = cv.pred_count ?? metrics.cv_pred_count;
            const gateText = typeof gatePassed === 'boolean' ? (gatePassed ? '通过' : '未通过') : '-';
            html += '<p style="margin:4px 0"><b>校准摘要</b>：模型=' + modelType + '；版本=' + version + '；闸门=' + gateText + (sampleCount != null ? ('；样本=' + sampleCount) : '') + '</p>';
            if (skippedReason) {
              html += '<p style="margin:4px 0;color:#9a3412"><b>提示</b>：本次未训练校准器（' + skippedReason + '）。</p>';
            }
            if (cvMae !== undefined || baselineMae !== undefined) {
              html += '<p style="margin:4px 0"><b>CV</b> MAE=' + fmtMetric(cvMae) + ' RMSE=' + fmtMetric(cvRmse) + ' Spearman=' + fmtMetric(cvSpearman) + '（' + (cvMode || '-') + ', n=' + (cvPredCount || 0) + '）</p>';
              html += '<p style="margin:4px 0"><b>Baseline</b> MAE=' + fmtMetric(baselineMae) + ' RMSE=' + fmtMetric(baselineRmse) + ' Spearman=' + fmtMetric(baselineSpearman) + '</p>';
              html += '<p style="margin:4px 0"><b>闸门阈值</b> MAE改进≥' + fmtMetric(improveThreshold) + '，Spearman不下降超过' + fmtMetric(spearmanTolerance) + '</p>';
            }
            const candidates = Array.isArray(summary.auto_candidates) ? summary.auto_candidates : (Array.isArray(data.calibrator_auto_candidates) ? data.calibrator_auto_candidates : []);
            if (candidates.length) {
              const rows = candidates.map((c) => {
                const gt = c.gate_passed === true ? '通过' : '未通过';
                return '<tr><td style="padding:2px 8px;border:1px solid #dbe3ef">' + (c.model_type || '-') + '</td><td style="padding:2px 8px;border:1px solid #dbe3ef">' + gt + '</td><td style="padding:2px 8px;border:1px solid #dbe3ef">' + fmtMetric(c.cv_mae) + '</td><td style="padding:2px 8px;border:1px solid #dbe3ef">' + fmtMetric(c.cv_spearman) + '</td></tr>';
              }).join('');
              html += '<details style="margin:6px 0"><summary>候选模型对比（auto）</summary><table style="border-collapse:collapse;font-size:12px;margin-top:4px"><thead><tr><th style="padding:2px 8px;border:1px solid #dbe3ef">模型</th><th style="padding:2px 8px;border:1px solid #dbe3ef">闸门</th><th style="padding:2px 8px;border:1px solid #dbe3ef">CV MAE</th><th style="padding:2px 8px;border:1px solid #dbe3ef">CV Spearman</th></tr></thead><tbody>' + rows + '</tbody></table></details>';
            }
          }
          html += '<pre style="margin:0">' + JSON.stringify(payload, null, 2) + '</pre>';
          el.innerHTML = html;
        }
        safeClick('btnRebuildDelta', async () => {
          const projectId = actionProjectId();
          setResultLoading('deltaResult', '正在重建 DELTA_CASE...');
          const res = await fetch('/api/v1/projects/' + projectId + '/delta_cases/rebuild', { method: 'POST', headers: apiHeaders(false) });
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          showBlock('deltaResult', res.ok, res.ok ? 'DELTA_CASE 重建完成' : 'DELTA_CASE 重建失败', data);
        });
        safeClick('btnRebuildSamples', async () => {
          const projectId = actionProjectId();
          setResultLoading('sampleResult', '正在重建 FEATURE_ROW...');
          const res = await fetch('/api/v1/projects/' + projectId + '/calibration_samples/rebuild', { method: 'POST', headers: apiHeaders(false) });
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          showBlock('sampleResult', res.ok, res.ok ? 'FEATURE_ROW 重建完成' : 'FEATURE_ROW 重建失败', data);
        });
        safeClick('btnTrainCalibratorV2', async () => {
          const projectId = actionProjectId();
          setResultLoading('calibTrainResult', '正在训练校准器...');
          const body = { project_id: projectId, model_type: 'auto', alpha: 1.0, auto_deploy: true };
          const res = await fetch('/api/v1/calibration/train', { method: 'POST', headers: apiHeaders(true), body: JSON.stringify(body) });
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          showCalibratorSummaryBlock('calibTrainResult', res.ok, res.ok ? '校准器训练完成' : '校准器训练失败', data);
        });
        safeClick('btnApplyCalibPredict', async () => {
          const projectId = actionProjectId();
          setResultLoading('calibTrainResult', '正在回填预测分...');
          const res = await fetch('/api/v1/projects/' + projectId + '/calibration/predict', { method: 'POST', headers: apiHeaders(false) });
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          showBlock('calibTrainResult', res.ok, res.ok ? '预测分回填完成' : '预测分回填失败', data);
        });
        safeClick('btnAutoRunReflection', async () => {
          const projectId = actionProjectId();
          setResultLoading('calibTrainResult', '正在执行闭环流程...');
          const res = await fetch('/api/v1/projects/' + projectId + '/reflection/auto_run', { method: 'POST', headers: apiHeaders(false) });
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          showCalibratorSummaryBlock('calibTrainResult', res.ok, res.ok ? '一键闭环执行完成' : '一键闭环执行失败', data);
        });
        safeClick('btnEvalMetricsV2', async () => {
          const projectId = actionProjectId();
          setResultLoading('evalResult', '正在评估项目指标...');
          const res = await fetch('/api/v1/projects/' + projectId + '/evaluation');
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          if (!res.ok) { showBlock('evalResult', false, '指标评估失败', data); return; }
          const a = data.acceptance || {};
          const summary = {
            sample_count_qt: data.sample_count_qt,
            acceptance: a,
            variants: data.variants || {},
          };
          showBlock('evalResult', true, '项目指标评估完成', summary);
        });
        safeClick('btnEvalSummaryV2', async () => {
          const res = await fetch('/api/v1/evaluation/summary');
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          if (!res.ok) { showBlock('evalResult', false, '跨项目汇总评估失败', data); return; }
          showBlock('evalResult', true, '跨项目汇总评估完成', data);
        });
        safeClick('btnMinePatchV2', async () => {
          const projectId = actionProjectId();
          setResultLoading('patchResult', '正在挖掘补丁...');
          const patchType = (document.getElementById('patchType') || {}).value || 'threshold';
          const body = { patch_type: patchType, top_k: 5 };
          const res = await fetch('/api/v1/projects/' + projectId + '/patches/mine', { method: 'POST', headers: apiHeaders(true), body: JSON.stringify(body) });
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          if (res.ok && data && data.id) { const input = document.getElementById('patchIdInput'); if (input) input.value = data.id; }
          showBlock('patchResult', res.ok, res.ok ? 'PATCH_PACKAGE 挖掘完成' : 'PATCH_PACKAGE 挖掘失败', data);
        });
        safeClick('btnShadowPatchV2', async () => {
          const projectId = actionProjectId();
          setResultLoading('patchShadowResult', '正在执行影子评估...');
          let patchId = ((document.getElementById('patchIdInput') || {}).value || '').trim();
          if (!patchId) patchId = await latestPatchId(projectId);
          if (!patchId) patchId = '__NO_PATCH__';
          const res = await fetch('/api/v1/patches/' + patchId + '/shadow_eval', { method: 'POST', headers: apiHeaders(false) });
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          showBlock('patchShadowResult', res.ok, res.ok ? '补丁影子评估完成' : '补丁影子评估失败', data);
        });
        safeClick('btnDeployPatchV2', async () => {
          const projectId = actionProjectId();
          setResultLoading('patchDeployResult', '正在发布补丁...');
          let patchId = ((document.getElementById('patchIdInput') || {}).value || '').trim();
          if (!patchId) patchId = await latestPatchId(projectId);
          if (!patchId) patchId = '__NO_PATCH__';
          const res = await fetch('/api/v1/patches/' + patchId + '/deploy', { method: 'POST', headers: apiHeaders(true), body: JSON.stringify({ action: 'deploy' }) });
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          showBlock('patchDeployResult', res.ok, res.ok ? '补丁已发布' : '补丁发布失败', data);
        });
        safeClick('btnRollbackPatchV2', async () => {
          const projectId = actionProjectId();
          setResultLoading('patchDeployResult', '正在回滚补丁...');
          let patchId = ((document.getElementById('patchIdInput') || {}).value || '').trim();
          if (!patchId) patchId = await latestPatchId(projectId);
          if (!patchId) patchId = '__NO_PATCH__';
          const res = await fetch('/api/v1/patches/' + patchId + '/deploy', { method: 'POST', headers: apiHeaders(true), body: JSON.stringify({ action: 'rollback' }) });
          const data = await res.json().catch(() => ({}));
          showJson('output', formatApiOutput(res, data));
          showBlock('patchDeployResult', res.ok, res.ok ? '补丁已回滚' : '补丁回滚失败', data);
        });

        // 关闭“硬接管”兜底，避免覆盖 safeClick 的详细渲染结果。
        initWeightsSection();
        updateProjectBoundControlsState();
        refreshProjects();

      </script>
    </body>
    </html>
    """
    html = html.replace("__PROJECT_OPTIONS__", project_options_html)
    html = html.replace("__CREATE_NOTICE_HTML__", create_notice_html)
    html = html.replace("__GLOBAL_NOTICE_HTML__", global_notice_html)
    html = html.replace("__SELECTED_PROJECT_ID__", html_lib.escape(selected_project_id))
    html = html.replace("__EXPERT_PROFILE_STATUS__", initial_profile_status)
    html = html.replace("__EXPERT_WEIGHTS_ROWS__", initial_weights_rows_html)
    html = html.replace("__EXPERT_WEIGHTS_SUMMARY__", html_lib.escape(initial_weights_summary))
    html = html.replace("__MATERIAL_ROWS__", initial_material_rows_html)
    html = html.replace("__MATERIALS_EMPTY_DISPLAY__", initial_materials_empty_display)
    html = html.replace("__SUBMISSION_ROWS__", initial_submission_rows_html)
    html = html.replace("__SUBMISSIONS_EMPTY_DISPLAY__", initial_submissions_empty_display)
    html = html.replace("__PROJECT_SCORE_SCALE_MAX__", str(score_scale_initial))
    return Response(
        content=html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={
            # Prevent stale cached UI when frontend script is updated.
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def create_app() -> FastAPI:
    return app


if __name__ == "__main__":
    import os
    import sys
    import threading
    import time
    import webbrowser

    port = int(os.environ.get("PORT", "8000"))

    def _open_browser() -> None:
        time.sleep(2.5)
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        except Exception:
            pass

    if "--no-browser" not in sys.argv:
        threading.Thread(target=_open_browser, daemon=True).start()
    print(f"浏览器将自动打开: http://127.0.0.1:{port}/")
    print("按 Ctrl+C 停止")

    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
