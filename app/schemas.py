from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ==================== 健康检查模型 ====================


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = Field(..., description="服务状态：healthy/unhealthy")
    version: str = Field(..., description="API 版本号")

    model_config = {"json_schema_extra": {"examples": [{"status": "healthy", "version": "1.0.0"}]}}


class ReadyResponse(BaseModel):
    """就绪检查响应"""

    status: str = Field(..., description="就绪状态：ready/not_ready")
    checks: dict = Field(..., description="各组件检查结果")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "ready",
                    "checks": {
                        "config": True,
                        "data_dirs": True,
                    },
                }
            ]
        }
    }


class ConfigStatusResponse(BaseModel):
    """配置状态响应"""

    cached: bool = Field(..., description="配置是否已缓存")
    rubric_path: str = Field(..., description="评分规则文件路径")
    lexicon_path: str = Field(..., description="词库文件路径")
    needs_reload: bool = Field(..., description="配置文件是否已变更需要重载")
    rubric_mtime: float = Field(..., description="缓存的评分规则文件修改时间")
    lexicon_mtime: float = Field(..., description="缓存的词库文件修改时间")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "cached": True,
                    "rubric_path": "/app/resources/rubric.yaml",
                    "lexicon_path": "/app/resources/lexicon.yaml",
                    "needs_reload": False,
                    "rubric_mtime": 1738680000.0,
                    "lexicon_mtime": 1738680000.0,
                }
            ]
        }
    }


class LLMBackendStatus(BaseModel):
    """进化 LLM 后端配置状态（不暴露密钥）"""

    evolution_backend: str = Field(..., description="当前真实进化后端：rules | openai | gemini")
    requested_backend: Optional[str] = Field(
        None, description="环境变量中请求的原始后端；spark 会作为兼容别名映射到 openai"
    )
    backend_alias_applied: bool = Field(
        False, description="是否应用了历史 spark -> openai 的兼容映射"
    )
    auto_mode: bool = Field(
        False, description="是否启用了自动多 provider 编排（auto 或未显式指定时）"
    )
    spark_configured: bool = Field(
        ..., description="是否检测到历史 Spark 兼容配置；仅提示迁移，不代表真实 provider 为 Spark"
    )
    legacy_spark_env_keys: List[str] = Field(
        default_factory=list, description="检测到的历史 Spark 兼容环境变量键名"
    )
    openai_configured: bool = Field(..., description="是否已配置 OPENAI_API_KEY")
    openai_account_count: int = Field(
        0,
        description="当前 OpenAI 可用账号数（OPENAI_API_KEY + OPENAI_API_KEYS 汇总）",
    )
    openai_pool_health: Dict[str, int] = Field(
        default_factory=dict,
        description="OpenAI 账号池健康摘要：total_accounts / healthy_accounts / cooling_accounts",
    )
    openai_model: Optional[str] = Field(None, description="当前 OpenAI 模型")
    gemini_configured: bool = Field(..., description="是否已配置 GEMINI_API_KEY")
    gemini_account_count: int = Field(
        0,
        description="当前 Gemini 可用账号数（GEMINI_API_KEY + GEMINI_API_KEYS 汇总）",
    )
    gemini_pool_health: Dict[str, int] = Field(
        default_factory=dict,
        description="Gemini 账号池健康摘要：total_accounts / healthy_accounts / cooling_accounts",
    )
    provider_health: Dict[str, str] = Field(
        default_factory=dict,
        description="各 provider 当前健康状态：healthy | cooldown",
    )
    provider_quality: Dict[str, str] = Field(
        default_factory=dict,
        description="各 provider 当前质量状态：stable | degraded",
    )
    provider_review_stats: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="各 provider 的增强复核历史摘要：confirmed/diverged/unavailable/fallback_only 计数与最近状态",
    )
    primary_provider_reason: Optional[str] = Field(
        None,
        description="当前主 provider 选择原因",
    )
    provider_chain: List[str] = Field(
        default_factory=list,
        description="当前实际调用链路，按主后端到备用后端排序",
    )
    fallback_providers: List[str] = Field(
        default_factory=list,
        description="当前可用的备用 provider 列表",
    )


class ConfigReloadResponse(BaseModel):
    """配置重载响应"""

    reloaded: bool = Field(..., description="是否成功重载")
    message: str = Field(..., description="操作结果消息")

    model_config = {
        "json_schema_extra": {"examples": [{"reloaded": True, "message": "配置已重新加载"}]}
    }


# ==================== 错误响应模型 ====================


class ErrorDetail(BaseModel):
    """错误详情"""

    detail: str = Field(..., description="错误描述信息")

    model_config = {"json_schema_extra": {"examples": [{"detail": "项目不存在"}]}}


class ValidationErrorItem(BaseModel):
    """验证错误项"""

    loc: List[str] = Field(..., description="错误位置路径")
    msg: str = Field(..., description="错误信息")
    type: str = Field(..., description="错误类型")


class ValidationErrorResponse(BaseModel):
    """422 验证错误响应"""

    detail: List[ValidationErrorItem] = Field(..., description="验证错误列表")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "detail": [
                        {
                            "loc": ["body", "text"],
                            "msg": "field required",
                            "type": "value_error.missing",
                        }
                    ]
                }
            ]
        }
    }


# 常用错误响应定义，用于 OpenAPI responses 参数
RESPONSES_404 = {
    404: {
        "model": ErrorDetail,
        "description": "资源不存在",
        "content": {"application/json": {"example": {"detail": "项目不存在"}}},
    }
}

RESPONSES_401 = {
    401: {
        "model": ErrorDetail,
        "description": "认证失败",
        "content": {"application/json": {"example": {"detail": "API Key 无效或缺失"}}},
    }
}

RESPONSES_422 = {
    422: {
        "model": ValidationErrorResponse,
        "description": "请求参数验证失败",
    }
}

RESPONSES_409 = {
    409: {
        "model": ErrorDetail,
        "description": "资源状态冲突（需解锁后操作）",
        "content": {
            "application/json": {
                "example": {"detail": "项目已进入青天评标阶段，默认锁定配置变更与重算。"}
            }
        },
    }
}

RESPONSES_NO_SUBMISSIONS = {
    404: {
        "model": ErrorDetail,
        "description": "无施组记录",
        "content": {"application/json": {"example": {"detail": "暂无施组记录"}}},
    }
}

RESPONSES_NO_PROFILE = {
    404: {
        "model": ErrorDetail,
        "description": "无学习画像",
        "content": {"application/json": {"example": {"detail": "暂无学习画像"}}},
    }
}


# ==================== 业务模型 ====================


class ScoreRequest(BaseModel):
    """评分请求体"""

    text: str = Field(..., description="施工组织设计纯文本")
    project_type: Optional[str] = Field(None, description="项目类型，可选用于扩展规则")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "text": "一、工程概况\n本工程为某市政道路改造项目...\n\n二、施工部署\n本工程采用分段施工方式...",
                    "project_type": "市政道路",
                }
            ]
        }
    }


class ProjectCreate(BaseModel):
    """项目创建请求"""

    name: str = Field(..., description="项目名称")
    meta: Optional[Dict[str, Any]] = Field(None, description="项目元数据（可选）")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "XX市政道路改造工程",
                    "meta": {"location": "北京", "budget": "5000万"},
                }
            ]
        }
    }


class ProjectRename(BaseModel):
    """项目改名请求"""

    name: str = Field(..., description="新的项目名称")


class ProjectRecord(BaseModel):
    """项目记录"""

    id: str = Field(..., description="项目唯一标识符")
    name: str = Field(..., description="项目名称")
    meta: Optional[Dict[str, Any]] = Field(None, description="项目元数据")
    region: Optional[str] = Field(None, description="项目所属地区")
    expert_profile_id: Optional[str] = Field(None, description="生效专家配置ID")
    qingtian_model_version: Optional[str] = Field(None, description="青天模型版本")
    scoring_engine_version_locked: Optional[str] = Field(None, description="评分引擎锁定版本")
    calibrator_version_locked: Optional[str] = Field(None, description="校准器锁定版本")
    status: Optional[str] = Field(None, description="项目状态")
    created_at: str = Field(..., description="创建时间（ISO 8601格式）")
    updated_at: Optional[str] = Field(None, description="更新时间（ISO 8601格式）")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "name": "XX市政道路改造工程",
                    "meta": {"location": "北京", "budget": "5000万"},
                    "created_at": "2026-02-04T12:00:00+00:00",
                }
            ]
        }
    }


class MaterialRecord(BaseModel):
    id: str
    project_id: str
    material_type: str = "tender_qa"
    filename: str
    path: str
    created_at: str
    parse_status: Optional[str] = None
    parse_backend: Optional[str] = None
    parse_confidence: Optional[float] = None
    parse_error_class: Optional[str] = None
    parse_error_message: Optional[str] = None
    parse_started_at: Optional[str] = None
    parse_finished_at: Optional[str] = None
    parse_version: Optional[str] = None
    structured_summary: Optional[Dict[str, Any]] = None
    job_id: Optional[str] = None
    parse_effective_status: Optional[str] = None
    parse_stage_label: Optional[str] = None
    parse_route_label: Optional[str] = None
    parse_note: Optional[str] = None
    queue_position: Optional[int] = None


class ProjectCreateFromTenderResponse(BaseModel):
    """上传招标文件自动创建项目响应"""

    project: ProjectRecord = Field(..., description="自动创建或复用的项目")
    material: MaterialRecord = Field(..., description="已归档到项目下的招标资料")
    inferred_name: str = Field(..., description="从招标文件识别出的项目名称")
    created: bool = Field(..., description="是否创建了新项目")
    reused_existing: bool = Field(..., description="是否复用了已存在项目")


class ProjectInferTenderNameResponse(BaseModel):
    """上传招标文件后仅识别项目名称响应"""

    inferred_name: str = Field(..., description="从招标文件识别出的项目名称")
    filename: str = Field(..., description="归一化后的招标文件名")


class MaterialParseJobRecord(BaseModel):
    id: str
    material_id: str
    project_id: str
    material_type: str
    filename: str
    status: str
    attempt: int = 0
    parse_backend: Optional[str] = None
    next_retry_at: Optional[str] = None
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_class: Optional[str] = None
    error_message: Optional[str] = None
    parse_confidence: Optional[float] = None


class MaterialParseStatusResponse(BaseModel):
    project_id: str
    summary: Dict[str, Any] = Field(default_factory=dict)
    jobs: List[MaterialParseJobRecord] = Field(default_factory=list)
    materials: List[MaterialRecord] = Field(default_factory=list)
    generated_at: str


class ExpertProfileRecord(BaseModel):
    id: str = Field(..., description="专家配置ID")
    name: str = Field(..., description="配置名称")
    weights_raw: Dict[str, int] = Field(..., description="16维原始关注度(0..10)")
    weights_norm: Dict[str, float] = Field(..., description="16维归一化权重(sum=1)")
    norm_rule_version: str = Field(..., description="归一化规则版本")
    created_at: str = Field(..., description="创建时间（ISO 8601格式）")
    updated_at: str = Field(..., description="更新时间（ISO 8601格式）")


class ExpertProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, description="配置名称，不传时自动命名")
    weights_raw: Dict[str, int] = Field(..., description="16维原始关注度(0..10)")
    force_unlock: bool = Field(False, description="项目已锁定时是否强制解锁本次操作")


class ProjectExpertProfileResponse(BaseModel):
    project: ProjectRecord
    expert_profile: ExpertProfileRecord


class RescoreRequest(BaseModel):
    scoring_engine_version: str = Field(
        default="v2",
        description="评分引擎版本标识",
    )
    scope: str = Field(
        default="project",
        description="重算范围：project | submission",
    )
    submission_id: Optional[str] = Field(
        None,
        description="当 scope=submission 时指定提交ID",
    )
    score_scale_max: int = Field(
        default=100,
        description="总分进制满分（支持 100 或 5）",
    )
    rebuild_anchors: bool = Field(False, description="是否重建锚点（预留）")
    rebuild_requirements: bool = Field(False, description="是否重建要求矩阵（预留）")
    retrain_calibrator: bool = Field(False, description="是否重训校准器（预留）")
    force_unlock: bool = Field(False, description="项目已锁定时是否强制解锁本次重算")


class RescoreResponse(BaseModel):
    ok: bool
    project_id: str
    scoring_engine_version: str
    expert_profile_id_used: Optional[str] = None
    submission_count: int
    reports_generated: int
    score_scale_max: int = 100
    score_scale_label: str = "100分制"
    material_utilization: Dict[str, Any] = Field(default_factory=dict)
    material_utilization_alerts: List[str] = Field(default_factory=list)
    material_utilization_gate: Dict[str, Any] = Field(default_factory=dict)
    material_utilization_by_submission: List[Dict[str, Any]] = Field(default_factory=list)
    feedback_closed_loop: Dict[str, Any] = Field(default_factory=dict)
    started_at: str
    finished_at: str


class VersionedJsonSnapshotRecord(BaseModel):
    artifact: str
    version_id: str
    filename: str
    created_at: str
    size_bytes: int


class VersionedJsonHistoryResponse(BaseModel):
    artifact: str
    versions: List[VersionedJsonSnapshotRecord] = Field(default_factory=list)
    generated_at: str


class VersionedJsonRollbackRequest(BaseModel):
    version_id: str = Field(..., description="要回滚到的历史版本ID")


class VersionedJsonRollbackResponse(BaseModel):
    ok: bool
    artifact: str
    restored_version_id: str
    restored_at: str
    backup_version_id: Optional[str] = None


class ScoringReadinessResponse(BaseModel):
    project_id: str
    ready: bool = Field(..., description="是否满足评分前置条件")
    score_button_enabled: bool = Field(..., description="前端是否应允许点击评分")
    gate_passed: bool = Field(..., description="资料门禁是否通过")
    issues: List[str] = Field(default_factory=list, description="阻断原因列表")
    warnings: List[str] = Field(default_factory=list, description="提醒信息")
    material_quality: Dict[str, Any] = Field(default_factory=dict)
    material_gate: Dict[str, Any] = Field(default_factory=dict)
    material_depth_gate: Dict[str, Any] = Field(default_factory=dict)
    submissions: Dict[str, Any] = Field(default_factory=dict)
    retrieval_policy: Dict[str, Any] = Field(default_factory=dict)
    generated_at: str


class ProjectMeceAuditResponse(BaseModel):
    """项目级 MECE 审计（输入完整性 / 评分有效性 / 进化闭环 / 运行稳定性）"""

    project_id: str
    generated_at: str
    overall: Dict[str, Any] = Field(default_factory=dict)
    dimensions: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[str] = Field(default_factory=list)


class EvolutionHealthResponse(BaseModel):
    """项目进化健康度报告（误差趋势 / 漂移 / 样本时效）"""

    project_id: str
    generated_at: str
    summary: Dict[str, Any] = Field(default_factory=dict)
    windows: Dict[str, Any] = Field(default_factory=dict)
    drift: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[str] = Field(default_factory=list)


class FeedbackGovernanceResponse(BaseModel):
    """项目级反馈闭环治理报告（异常样本 / few-shot / 版本快照）"""

    project_id: str
    generated_at: str
    summary: Dict[str, Any] = Field(default_factory=dict)
    blocked_samples: List[Dict[str, Any]] = Field(default_factory=list)
    approved_samples: List[Dict[str, Any]] = Field(default_factory=list)
    few_shot_recent: List[Dict[str, Any]] = Field(default_factory=list)
    adopted_few_shot: List[Dict[str, Any]] = Field(default_factory=list)
    version_history: List[Dict[str, Any]] = Field(default_factory=list)
    artifact_impacts: List[Dict[str, Any]] = Field(default_factory=list)
    score_preview: Dict[str, Any] = Field(default_factory=dict)
    sandbox_preview: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[str] = Field(default_factory=list)


class FeedbackGovernanceVersionPreviewRequest(BaseModel):
    artifact: str = Field(
        ..., description="high_score_features|evolution_reports|calibration_models|expert_profiles"
    )
    version_id: str = Field(..., description="要只读预演的历史版本ID")


class FeedbackGovernanceVersionPreviewResponse(BaseModel):
    ok: bool
    project_id: str
    artifact: str
    version_id: str
    version_created_at: Optional[str] = None
    generated_at: str
    current_summary: Dict[str, Any] = Field(default_factory=dict)
    preview_summary: Dict[str, Any] = Field(default_factory=dict)
    delta_vs_current: Dict[str, Any] = Field(default_factory=dict)
    matches_current: bool = False
    governance: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[str] = Field(default_factory=list)


class FeedbackGovernanceActionPreviewResponse(BaseModel):
    ok: bool
    project_id: str
    record_id: str
    preview_type: str
    requested_action: str
    generated_at: str
    current_state: Dict[str, Any] = Field(default_factory=dict)
    preview_state: Dict[str, Any] = Field(default_factory=dict)
    governance: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[str] = Field(default_factory=list)


class FeedbackGuardrailReviewRequest(BaseModel):
    action: str = Field(..., description="approve|reject|reset")
    note: Optional[str] = Field(None, description="人工审核备注")
    rerun_closed_loop: bool = Field(default=False, description="审核后是否立即重跑反馈闭环")


class FeedbackGuardrailReviewResponse(BaseModel):
    ok: bool
    project_id: str
    record_id: str
    feedback_guardrail: Dict[str, Any] = Field(default_factory=dict)
    feedback_closed_loop: Dict[str, Any] = Field(default_factory=dict)
    updated_at: str


class FewShotReviewRequest(BaseModel):
    action: str = Field(..., description="adopt|ignore|reset")
    note: Optional[str] = Field(None, description="人工审核备注")


class FewShotReviewResponse(BaseModel):
    ok: bool
    project_id: str
    record_id: str
    few_shot_distillation: Dict[str, Any] = Field(default_factory=dict)
    updated_at: str


class QingTianResultCreate(BaseModel):
    qingtian_model_version: Optional[str] = Field(None, description="青天模型版本")
    qt_total_score: float = Field(..., description="青天总分(0..100)")
    qt_dim_scores: Optional[Dict[str, float]] = Field(None, description="青天16维分数，可空")
    qt_reasons: List[Dict[str, Any]] = Field(default_factory=list, description="青天扣分/加分原因")
    raw_payload: Dict[str, Any] = Field(default_factory=dict, description="青天原始响应")


class QingTianResultRecord(BaseModel):
    id: str
    submission_id: str
    qingtian_model_version: str
    qt_total_score: float
    qt_dim_scores: Optional[Dict[str, float]] = None
    qt_reasons: List[Dict[str, Any]] = Field(default_factory=list)
    raw_payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CalibratorTrainRequest(BaseModel):
    project_id: Optional[str] = Field(None, description="仅用某项目样本训练")
    model_type: str = Field(
        default="auto", description="支持 auto/ridge/offset/linear1d/isotonic1d"
    )
    alpha: float = Field(default=1.0, ge=0.0, description="ridge 正则强度")
    auto_deploy: bool = Field(default=False, description="闸门通过后是否自动上线")


class CalibratorSummary(BaseModel):
    calibrator_version: Optional[str] = None
    model_type: Optional[str] = None
    gate_passed: Optional[bool] = None
    cv_metrics: Dict[str, Any] = Field(default_factory=dict)
    baseline_metrics: Dict[str, Any] = Field(default_factory=dict)
    gate: Dict[str, Any] = Field(default_factory=dict)
    auto_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    sample_count: Optional[int] = None
    bootstrap_small_sample: bool = False
    full_validation_min_samples: Optional[int] = None
    deployment_mode: Optional[str] = None
    auto_review: Dict[str, Any] = Field(default_factory=dict)
    skipped_reason: Optional[str] = None


class CalibratorModelRecord(BaseModel):
    calibrator_version: str
    model_type: str
    feature_schema_version: str
    train_filter: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    calibrator_summary: CalibratorSummary = Field(default_factory=CalibratorSummary)
    artifact_uri: str
    deployed: bool = False
    created_at: str


class CalibratorDeployRequest(BaseModel):
    calibrator_version: str
    project_id: Optional[str] = Field(None, description="可选：绑定到某个项目")


class CalibratorPredictResponse(BaseModel):
    ok: bool
    project_id: str
    model_version: Optional[str] = None
    updated_reports: int = 0
    updated_submissions: int = 0


class LatestReportResponse(BaseModel):
    report: Dict[str, Any]
    ui_summary: Dict[str, Any]


class ProjectPreScoreListResponse(BaseModel):
    project_id: str
    expert_profile_id: Optional[str] = None
    submissions: List[Dict[str, Any]] = Field(default_factory=list)


class ScoringFactorsResponse(BaseModel):
    """评分体系总览（用于对外分析与复核）"""

    engine_version: str = Field(..., description="当前评分引擎版本")
    project_id: Optional[str] = Field(None, description="可选：按项目返回定制要求")
    dimension_count: int = Field(..., description="维度数量（固定16）")
    dimensions: List[Dict[str, Any]] = Field(default_factory=list, description="维度评分因子列表")
    penalty_rules: List[Dict[str, Any]] = Field(default_factory=list, description="扣分规则列表")
    lint_issue_codes: List[str] = Field(default_factory=list, description="静态检查问题码")
    consistency_anchors: List[str] = Field(default_factory=list, description="一致性校验锚点")
    chapter_requirements: Dict[str, List[str]] = Field(
        default_factory=dict, description="章节/图文/要素要求"
    )
    capability_flags: Dict[str, bool] = Field(default_factory=dict, description="能力覆盖标识")
    source: Dict[str, str] = Field(default_factory=dict, description="数据来源说明")
    updated_at: str = Field(..., description="生成时间")


class ScoringFactorsMarkdownResponse(BaseModel):
    """评分体系Markdown导出"""

    project_id: Optional[str] = None
    markdown: str


class AnalysisBundleResponse(BaseModel):
    """面向外部模型分析的一体化文本包"""

    project_id: str
    markdown: str
    generated_at: str


class MaterialDepthReportResponse(BaseModel):
    """项目资料深读体检报告（评分前）"""

    project_id: str
    generated_at: str
    ready_to_score: bool
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    gate: Dict[str, Any] = Field(default_factory=dict)
    depth_gate: Dict[str, Any] = Field(default_factory=dict)
    quality_summary: Dict[str, Any] = Field(default_factory=dict)
    by_type: List[Dict[str, Any]] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


class MaterialDepthReportMarkdownResponse(BaseModel):
    """项目资料深读体检报告 Markdown 导出"""

    project_id: str
    markdown: str
    generated_at: str


class MaterialKnowledgeProfileResponse(BaseModel):
    """项目资料知识画像（按维度/资料类型聚合）"""

    project_id: str
    generated_at: str
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    by_type: List[Dict[str, Any]] = Field(default_factory=list)
    by_dimension: List[Dict[str, Any]] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


class MaterialKnowledgeProfileMarkdownResponse(BaseModel):
    """项目资料知识画像 Markdown 导出"""

    project_id: str
    markdown: str
    generated_at: str


class SelfCheckItem(BaseModel):
    name: str
    ok: bool
    detail: Optional[str] = None
    category: Optional[str] = None
    required: bool = False


class SelfCheckResponse(BaseModel):
    ok: bool
    required_ok: bool = True
    degraded: bool = False
    failed_required_count: int = 0
    failed_optional_count: int = 0
    checked_at: str
    checks: Dict[str, bool] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    items: List[SelfCheckItem] = Field(default_factory=list)


class DataHygieneDataset(BaseModel):
    name: str
    total: int
    orphan_count: int
    cleaned_count: int = 0
    mode: str = "project_id"


class DataHygieneResponse(BaseModel):
    generated_at: str
    apply_mode: bool
    valid_project_count: int
    orphan_records_total: int
    cleaned_records_total: int
    datasets: List[DataHygieneDataset] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


class DeltaCaseRecord(BaseModel):
    id: str
    project_id: str
    submission_id: str
    report_id: Optional[str] = None
    qingtian_result_id: Optional[str] = None
    total_error: float
    dim_errors: Dict[str, float] = Field(default_factory=dict)
    reason_alignment: List[Dict[str, Any]] = Field(default_factory=list)
    miss_types: Dict[str, int] = Field(default_factory=dict)
    created_at: str


class CalibrationSampleRecord(BaseModel):
    id: str
    project_id: str
    submission_id: str
    report_id: Optional[str] = None
    qingtian_result_id: Optional[str] = None
    feature_schema_version: str
    x_features: Dict[str, float] = Field(default_factory=dict)
    y_label: float
    created_at: str


class PatchMineRequest(BaseModel):
    patch_type: str = Field(default="threshold", description="keywords|regex|requirement|threshold")
    top_k: int = Field(default=3, ge=1, le=20)


class PatchPackageRecord(BaseModel):
    id: str
    project_id: str
    patch_type: str
    patch_payload: Dict[str, Any] = Field(default_factory=dict)
    target_symptom: Dict[str, Any] = Field(default_factory=dict)
    rollback_pointer: Optional[str] = None
    status: str = Field(
        default="candidate", description="candidate|shadow_pass|deployed|rolled_back"
    )
    shadow_metrics: Optional[Dict[str, Any]] = None
    created_at: str
    updated_at: str


class PatchShadowEvalResponse(BaseModel):
    ok: bool
    patch_id: str
    gate_passed: bool
    metrics_before_after: Dict[str, Any] = Field(default_factory=dict)


class PatchDeployRequest(BaseModel):
    action: str = Field(default="deploy", description="deploy|rollback")
    rollback_to_version: Optional[str] = None


class PatchDeploymentRecord(BaseModel):
    id: str
    patch_id: str
    project_id: str
    action: str
    deployed: bool
    metrics_before_after: Dict[str, Any] = Field(default_factory=dict)
    rollback_to_version: Optional[str] = None
    created_at: str


class ReflectionAutoRunResponse(BaseModel):
    ok: bool
    project_id: str
    delta_cases: int
    calibration_samples: int
    calibrator_version: Optional[str] = None
    calibrator_deployed: bool = False
    calibrator_summary: CalibratorSummary = Field(default_factory=CalibratorSummary)
    calibrator_model_type: Optional[str] = None
    calibrator_gate_passed: Optional[bool] = None
    calibrator_cv_metrics: Dict[str, Any] = Field(default_factory=dict)
    calibrator_baseline_metrics: Dict[str, Any] = Field(default_factory=dict)
    calibrator_gate: Dict[str, Any] = Field(default_factory=dict)
    calibrator_auto_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    calibrator_auto_review: Dict[str, Any] = Field(default_factory=dict)
    prediction_updated_reports: int = 0
    prediction_updated_submissions: int = 0
    patch_id: Optional[str] = None
    patch_gate_passed: Optional[bool] = None
    patch_deployed: bool = False
    patch_auto_govern: Dict[str, Any] = Field(default_factory=dict)


class EvidenceTraceResponse(BaseModel):
    """单份施组的证据追溯报告"""

    project_id: str
    submission_id: str
    filename: Optional[str] = None
    generated_at: str
    summary: Dict[str, Any] = Field(default_factory=dict)
    by_dimension: List[Dict[str, Any]] = Field(default_factory=list)
    requirement_hits: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_units: List[Dict[str, Any]] = Field(default_factory=list)
    material_conflicts: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[str] = Field(default_factory=list)


class EvidenceTraceMarkdownResponse(BaseModel):
    """单份施组的证据追溯 Markdown 导出"""

    project_id: str
    submission_id: str
    markdown: str
    generated_at: str


class ScoringBasisResponse(BaseModel):
    """单份施组评分依据审计报告"""

    project_id: str
    submission_id: str
    filename: Optional[str] = None
    generated_at: str
    scoring_status: str = "unknown"
    mece_inputs: Dict[str, Any] = Field(default_factory=dict)
    material_quality: Dict[str, Any] = Field(default_factory=dict)
    material_retrieval: Dict[str, Any] = Field(default_factory=dict)
    material_utilization: Dict[str, Any] = Field(default_factory=dict)
    material_utilization_gate: Dict[str, Any] = Field(default_factory=dict)
    evidence_trace: Dict[str, Any] = Field(default_factory=dict)
    material_constraint_shaping: Dict[str, Any] = Field(default_factory=dict)
    current_runtime_constraints: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[str] = Field(default_factory=list)


class ProjectScoringDiagnosticResponse(BaseModel):
    """项目级评分证据链诊断（聚合资料体检/证据追溯/评分依据）"""

    project_id: str
    generated_at: str
    readiness: ScoringReadinessResponse
    material_depth: MaterialDepthReportResponse
    latest_submission: Dict[str, Any] = Field(default_factory=dict)
    evidence_trace: Optional[EvidenceTraceResponse] = None
    scoring_basis: Optional[ScoringBasisResponse] = None
    material_type_cards: List[Dict[str, Any]] = Field(default_factory=list)
    dimension_support_cards: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[str] = Field(default_factory=list)


class ProjectEvaluationResponse(BaseModel):
    project_id: str
    sample_count_qt: int
    variants: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    acceptance: Dict[str, bool] = Field(default_factory=dict)
    computed_at: str


class EvaluationSummaryResponse(BaseModel):
    project_count: int
    project_ids: List[str] = Field(default_factory=list)
    aggregate: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    acceptance_pass_count: Dict[str, int] = Field(default_factory=dict)
    computed_at: str


class ProjectAnchorRecord(BaseModel):
    id: str
    project_id: str
    anchor_key: str
    anchor_value: Any
    value_num: Optional[float] = None
    value_unit: Optional[str] = None
    source_doc_id: Optional[str] = None
    source_locator: str
    confidence: float
    created_at: str


class ProjectRequirementRecord(BaseModel):
    id: str
    project_id: str
    dimension_id: str
    req_label: str
    req_type: str
    patterns: Dict[str, Any]
    mandatory: bool
    weight: float
    source_anchor_id: Optional[str] = None
    source_pack_id: Optional[str] = None
    source_pack_version: Optional[str] = None
    priority: Optional[float] = None
    lint: Dict[str, Any] = Field(default_factory=dict)
    version_locked: Optional[str] = None
    created_at: str


class ConstraintPack(BaseModel):
    project_id: str
    expert_profile_snapshot: Optional[ExpertProfileRecord] = None
    anchors_required: List[Dict[str, Any]] = Field(default_factory=list)
    requirements_mandatory: List[Dict[str, Any]] = Field(default_factory=list)
    dimension_thresholds: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    priority_order: List[str] = Field(default_factory=list)
    requirement_pack_versions: List[str] = Field(default_factory=list)
    generated_at: str


class SubmissionRecord(BaseModel):
    """施组提交记录"""

    id: str = Field(..., description="提交记录唯一标识符")
    project_id: str = Field(..., description="所属项目ID")
    filename: str = Field(..., description="上传的文件名")
    total_score: float = Field(..., description="评分总分（0-100）")
    report: Dict[str, Any] = Field(..., description="完整评分报告")
    created_at: str = Field(..., description="提交时间（ISO 8601格式）")
    text: Optional[str] = Field(None, description="施组文本原文（可选返回）")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "660e8400-e29b-41d4-a716-446655440001",
                    "project_id": "550e8400-e29b-41d4-a716-446655440000",
                    "filename": "施工组织设计_v1.txt",
                    "total_score": 78.5,
                    "report": {
                        "total_score": 78.5,
                        "dimension_scores": {},
                        "penalties": [],
                        "suggestions": [],
                    },
                    "created_at": "2026-02-04T14:30:00+00:00",
                    "text": None,
                }
            ]
        }
    }


class CompareReport(BaseModel):
    """多次提交对比报告"""

    project_id: str = Field(..., description="项目ID")
    rankings: List[Dict[str, Any]] = Field(..., description="按总分排序的提交列表")
    dimension_avg: Dict[str, float] = Field(..., description="各维度平均分")
    penalty_stats: Dict[str, int] = Field(..., description="扣分项统计")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "project_id": "550e8400-e29b-41d4-a716-446655440000",
                    "rankings": [
                        {
                            "submission_id": "sub-001",
                            "filename": "施组_v2.txt",
                            "total_score": 85.0,
                        },
                        {
                            "submission_id": "sub-002",
                            "filename": "施组_v1.txt",
                            "total_score": 72.5,
                        },
                    ],
                    "dimension_avg": {"D01": 15.5, "D02": 12.0, "D03": 18.0},
                    "penalty_stats": {"EMPTY_PROMISE": 3, "ACTION_MISSING": 2},
                }
            ]
        }
    }


class InsightsReport(BaseModel):
    project_id: str
    dimension_avg: Dict[str, float]
    weakest_dims: List[Dict[str, Any]]
    frequent_penalties: List[Dict[str, Any]]
    recommendations: List[Dict[str, Any]]


class LearningProfile(BaseModel):
    project_id: str
    dimension_multipliers: Dict[str, float]
    rationale: Dict[str, str]
    updated_at: str


class CompareNarrative(BaseModel):
    project_id: str
    summary: str
    top_submission: Dict[str, Any]
    bottom_submission: Dict[str, Any]
    key_diffs: List[Dict[str, Any]]
    score_overview: Dict[str, Any] = Field(default_factory=dict, description="总体分数分布与波动")
    dimension_diagnostics: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="维度差距诊断（含证据与改进动作）",
    )
    penalty_diagnostics: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="扣分项诊断（频次、影响范围、原因样本、优化动作）",
    )
    submission_diagnostics: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="分文件诊断（强弱维度与重点扣分）",
    )
    priority_actions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="编制优化优先级动作清单",
    )
    submission_optimization_cards: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="逐文件优化卡片（文件级优先动作与页码定位）",
    )
    submission_scorecards: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="逐份施组得分/失分明细（按文件名列出维度得分与扣分项）",
    )


class AdaptiveSuggestions(BaseModel):
    project_id: str
    penalty_stats: Dict[str, int]
    suggestions: List[Dict[str, Any]]
    source: Dict[str, Any] = Field(default_factory=dict)


class AdaptivePatch(BaseModel):
    project_id: str
    lexicon_additions: Dict[str, Any]
    rubric_adjustments: Dict[str, Any]
    source: Dict[str, Any] = Field(default_factory=dict)


class AdaptiveApplyResult(BaseModel):
    project_id: str
    applied: bool
    changes: List[str]
    backup_path: str
    source: Dict[str, Any] = Field(default_factory=dict)


class AdaptiveValidation(BaseModel):
    project_id: str
    avg_delta: float
    comparisons: List[Dict[str, Any]]


# ==================== 自我学习与进化 ====================


class ProjectContextIn(BaseModel):
    """投喂包/项目背景文本（招标文件、清单、图纸、设计等合并后的内容）"""

    text: str = Field(..., description="项目背景文本内容")
    filename: Optional[str] = Field(None, description="来源文件名，如 投喂包.txt")


class ProjectContextOut(BaseModel):
    project_id: str
    text: str
    filename: Optional[str] = None
    updated_at: Optional[str] = None


class GroundTruthRecord(BaseModel):
    """单条真实评标记录（青天大模型等外部评标结果）"""

    id: str
    project_id: str
    shigong_text: str
    source_submission_id: Optional[str] = Field(None, description="关联施组ID（若来自步骤4）")
    source_submission_filename: Optional[str] = Field(
        None, description="关联施组文件名，优先用于界面展示"
    )
    source_submission_created_at: Optional[str] = Field(
        None, description="关联施组上传时间（若可追溯）"
    )
    judge_scores: List[float] = Field(..., description="5或7个评委得分")
    judge_count: Optional[int] = Field(None, description="评委人数（5或7）")
    score_scale_max: Optional[int] = Field(None, description="该条真实评分的原始满分制（5或100）")
    final_score: float = Field(..., description="最终得分")
    final_score_raw: Optional[float] = Field(None, description="原始录入最终分（按项目满分制）")
    final_score_100: Optional[float] = Field(None, description="归一化到100分制后的最终分")
    judge_weights: Optional[List[float]] = Field(None, description="5个评委关注度/权重")
    qualitative_tags_by_judge: Optional[List[List[str]]] = Field(
        None,
        description="每位评委的定性标签（例：[['扣了进度分'], ['重点表扬了BIM']...]）",
    )
    source: str = Field(default="青天大模型", description="来源")
    feedback_guardrail: Dict[str, Any] = Field(default_factory=dict, description="闭环守门诊断")
    learning_quality_gate: Dict[str, Any] = Field(
        default_factory=dict, description="学习样本质量闸门诊断"
    )
    feedback_closed_loop: Dict[str, Any] = Field(default_factory=dict, description="闭环执行结果")
    few_shot_distillation: Dict[str, Any] = Field(
        default_factory=dict, description="高分逻辑few-shot蒸馏结果"
    )
    created_at: str


class GroundTruthCreate(BaseModel):
    """录入真实评标结果"""

    shigong_text: str = Field(..., description="施组全文")
    judge_scores: List[float] = Field(..., description="5或7个评委得分")
    final_score: float = Field(..., description="最终得分")
    judge_weights: Optional[List[float]] = Field(
        None, description="评委关注度/权重（长度需与评委人数一致）"
    )
    qualitative_tags_by_judge: Optional[List[List[str]]] = Field(
        None,
        description="每位评委对应的定性标签数组（可选）",
    )
    source: str = Field(default="青天大模型", description="来源")


class GroundTruthFromSubmissionCreate(BaseModel):
    """从已上传施组中选择一份录入真实评标结果"""

    submission_id: str = Field(..., description="施组提交ID（来自步骤4已上传列表）")
    judge_scores: List[float] = Field(..., description="5或7个评委得分")
    final_score: float = Field(..., description="最终得分")
    qualitative_tags_by_judge: Optional[List[List[str]]] = Field(
        None,
        description="每位评委对应的定性标签数组（可选）",
    )
    source: str = Field(default="青天大模型", description="来源")


class JudgeFeedback(BaseModel):
    """单个评委反馈（用于定向反演）"""

    score: float = Field(..., description="评委给出的总分")
    qualitative_tags: List[str] = Field(
        default_factory=list,
        description="定性标签（例：扣了进度分、重点表扬了BIM）",
    )


class FeedbackRecord(BaseModel):
    """用于权重反演训练的反馈记录"""

    id: Optional[str] = None
    project_id: str
    submission_id: Optional[str] = None
    predicted_total_score: float = Field(..., description="系统预测总分")
    final_total_score: Optional[float] = Field(None, description="真实最终总分")
    judge_feedbacks: List[JudgeFeedback] = Field(default_factory=list)
    created_at: str = Field(..., description="反馈时间（ISO 8601）")


class ExtractedFeature(BaseModel):
    """语义解构后的高分逻辑骨架（防查重存储）"""

    feature_id: str = Field(..., description="特征唯一ID")
    dimension_id: str = Field(..., description="关联维度ID")
    logic_skeleton: List[str] = Field(
        default_factory=list,
        description="抽象逻辑骨架，禁止原文摘抄",
    )
    confidence_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="特征置信度，基于真实反馈自动更新",
    )
    usage_count: int = Field(default=0, ge=0, description="特征被系统采纳次数")
    active: bool = Field(default=True, description="是否活跃（软删除后为False）")
    retired_at: Optional[str] = Field(None, description="软删除时间")
    created_at: Optional[str] = Field(None, description="创建时间")
    updated_at: Optional[str] = Field(None, description="更新时间")


class ExtractedFeatureStore(BaseModel):
    """高分逻辑骨架特征库"""

    schema_version: str = Field(default="v1")
    features: List[ExtractedFeature] = Field(default_factory=list)


class GroundTruthBatchItem(BaseModel):
    """批量录入中的单文件结果"""

    filename: str = Field(..., description="上传文件名")
    ok: bool = Field(..., description="该文件是否录入成功")
    record: Optional[GroundTruthRecord] = Field(None, description="成功时返回的记录")
    detail: Optional[str] = Field(None, description="失败或告警信息")


class GroundTruthBatchResponse(BaseModel):
    """批量录入真实评标结果"""

    project_id: str
    total_files: int
    success_count: int
    failed_count: int
    items: List[GroundTruthBatchItem] = Field(default_factory=list)


class EvolutionReport(BaseModel):
    """进化报告：基于真实评标学习的高分逻辑与编制建议；含评分进化与编制系统指令。"""

    project_id: str
    high_score_logic: List[str] = Field(..., description="高分逻辑总结")
    writing_guidance: List[str] = Field(..., description="编制指导")
    sample_count: int = Field(0, description="参与学习的真实评标条数")
    updated_at: str
    scoring_evolution: Optional[Dict[str, Any]] = Field(
        None,
        description="评分系统进化建议（维度权重等），使预评分更贴近青天",
    )
    compilation_instructions: Optional[Dict[str, Any]] = Field(
        None,
        description="编制系统指令（必备章节/图表/要素），可导出为编制约束",
    )
    enhanced_by: Optional[str] = Field(
        None,
        description="若由 LLM 增强则标识真实后端：openai | gemini；仅规则时为 None",
    )
    enhancement_provider_chain: List[str] = Field(
        default_factory=list,
        description="本次增强尝试的 provider 链路",
    )
    enhancement_fallback_used: bool = Field(
        False,
        description="本次增强是否发生了 provider fallback",
    )
    enhancement_attempts: int = Field(
        0,
        description="本次增强总尝试次数（含同 provider 重试）",
    )
    enhancement_applied: bool = Field(
        True,
        description="本次 LLM 增强结果是否已实际应用到最终进化报告",
    )
    enhancement_governed: bool = Field(
        False,
        description="本次增强是否因治理规则被保守回退",
    )
    enhancement_governance_notes: List[str] = Field(
        default_factory=list,
        description="增强治理说明",
    )
    enhancement_review_provider: Optional[str] = Field(
        None,
        description="用于复核主增强结果的备用 provider：openai | gemini",
    )
    enhancement_review_status: str = Field(
        "not_run",
        description="增强复核状态：not_run | confirmed | diverged | unavailable | fallback_only",
    )
    enhancement_review_similarity: Optional[float] = Field(
        None,
        description="主结果与复核结果的相似度（0-1）",
    )
    enhancement_review_notes: List[str] = Field(
        default_factory=list,
        description="增强复核说明",
    )


class CompilationInstructions(BaseModel):
    """编制系统指令：用于约束施组编制输出（内容、图表、必备要素）。"""

    project_id: str
    required_sections: List[str] = Field(default_factory=list, description="必备章节/模块")
    required_charts_images: List[str] = Field(default_factory=list, description="必备图表或图片")
    mandatory_elements: List[str] = Field(default_factory=list, description="必备表述要素")
    forbidden_patterns: List[str] = Field(default_factory=list, description="禁止的表述模式")
    guidance_items: List[str] = Field(default_factory=list, description="编制指导条目")
    high_score_summary: List[str] = Field(default_factory=list, description="高分逻辑摘要")


class WritingGuidance(BaseModel):
    """编制指导（供前端/编制人使用）"""

    project_id: str
    guidance: List[str] = Field(..., description="编制建议条目")
    high_score_logic: List[str] = Field(default_factory=list, description="高分逻辑摘要")
    sample_count: int = 0
    updated_at: Optional[str] = None
    enhancement_applied: bool = True
    enhancement_governed: bool = False
    enhancement_governance_notes: List[str] = Field(default_factory=list)
    enhancement_review_provider: Optional[str] = None
    enhancement_review_status: str = "not_run"
    enhancement_review_similarity: Optional[float] = None
    enhancement_review_notes: List[str] = Field(default_factory=list)


# ==================== 历史记录与趋势分析模型 ====================


class ScoreHistoryEntry(BaseModel):
    """评分历史记录条目"""

    id: str = Field(..., description="记录唯一标识符")
    project_id: str = Field(..., description="项目ID")
    submission_id: str = Field(..., description="提交记录ID")
    filename: str = Field(..., description="文件名")
    total_score: float = Field(..., description="总分")
    dimension_scores: Dict[str, float] = Field(..., description="各维度得分")
    penalty_count: int = Field(..., description="扣分项数量")
    created_at: str = Field(..., description="创建时间（ISO 8601格式）")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "hist-001",
                    "project_id": "proj-001",
                    "submission_id": "sub-001",
                    "filename": "施组_v1.txt",
                    "total_score": 75.5,
                    "dimension_scores": {"D01": 15.0, "D02": 12.5, "D03": 18.0},
                    "penalty_count": 3,
                    "created_at": "2026-02-04T10:00:00+00:00",
                }
            ]
        }
    }


class TrendPoint(BaseModel):
    """趋势数据点"""

    submission_id: str = Field(..., description="提交记录ID")
    filename: str = Field(..., description="文件名")
    total_score: float = Field(..., description="总分")
    created_at: str = Field(..., description="时间点")


class DimensionTrend(BaseModel):
    """维度趋势"""

    dimension_id: str = Field(..., description="维度ID")
    dimension_name: str = Field(..., description="维度名称")
    scores: List[float] = Field(..., description="历史分数序列")
    trend: str = Field(..., description="趋势方向：improving/declining/stable")
    avg_score: float = Field(..., description="平均分")
    latest_score: float = Field(..., description="最新分数")


class TrendAnalysis(BaseModel):
    """趋势分析报告"""

    project_id: str = Field(..., description="项目ID")
    total_submissions: int = Field(..., description="总提交次数")
    score_history: List[TrendPoint] = Field(..., description="总分历史序列")
    overall_trend: str = Field(..., description="整体趋势：improving/declining/stable")
    avg_score: float = Field(..., description="平均总分")
    best_score: float = Field(..., description="最高分")
    worst_score: float = Field(..., description="最低分")
    latest_score: float = Field(..., description="最新分数")
    score_improvement: float = Field(..., description="分数提升（最新-首次）")
    dimension_trends: List[DimensionTrend] = Field(..., description="各维度趋势")
    penalty_trend: List[int] = Field(..., description="扣分项数量趋势")
    recommendations: List[str] = Field(..., description="基于趋势的改进建议")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "project_id": "proj-001",
                    "total_submissions": 5,
                    "score_history": [
                        {
                            "submission_id": "sub-001",
                            "filename": "v1.txt",
                            "total_score": 70.0,
                            "created_at": "2026-02-01T10:00:00",
                        },
                        {
                            "submission_id": "sub-002",
                            "filename": "v2.txt",
                            "total_score": 78.5,
                            "created_at": "2026-02-02T10:00:00",
                        },
                    ],
                    "overall_trend": "improving",
                    "avg_score": 74.25,
                    "best_score": 78.5,
                    "worst_score": 70.0,
                    "latest_score": 78.5,
                    "score_improvement": 8.5,
                    "dimension_trends": [],
                    "penalty_trend": [5, 3],
                    "recommendations": ["继续优化工程概况部分"],
                }
            ]
        }
    }


class ProjectScoreHistory(BaseModel):
    """项目评分历史"""

    project_id: str = Field(..., description="项目ID")
    entries: List[ScoreHistoryEntry] = Field(..., description="历史记录列表")
    total_count: int = Field(..., description="记录总数")


class EvidenceSpan(BaseModel):
    start_index: int
    end_index: int
    snippet: str
    anchor_label: Optional[str] = None
    quote: Optional[str] = None


class SubScore(BaseModel):
    name: str
    score: float
    hits: List[str]
    evidence: List[EvidenceSpan]


class DimensionScore(BaseModel):
    id: str
    name: str
    module: str
    score: float
    max_score: float
    hits: List[str]
    evidence: List[EvidenceSpan]
    sub_scores: Optional[List[SubScore]] = None


class LogicLockResult(BaseModel):
    definition_score: float
    analysis_score: float
    solution_score: float
    breaks: List[str]
    evidence: List[EvidenceSpan]


class Penalty(BaseModel):
    code: str
    message: str
    evidence_span: Optional[EvidenceSpan]
    deduct: Optional[float] = None
    tags: Optional[List[str]] = None


class Suggestion(BaseModel):
    dimension: str
    action: str
    expected_gain: float


class ScoreReport(BaseModel):
    """完整评分报告"""

    total_score: float = Field(..., description="总分（0-100）")
    dimension_scores: Dict[str, DimensionScore] = Field(..., description="各维度得分详情")
    logic_lock: LogicLockResult = Field(..., description="逻辑锁分析结果")
    penalties: List[Penalty] = Field(default_factory=list, description="扣分项列表")
    penalties_logic_lock: List[Penalty] = Field(default_factory=list, description="逻辑锁扣分")
    penalties_empty_promises: List[Penalty] = Field(default_factory=list, description="空承诺扣分")
    penalties_action_missing: List[Penalty] = Field(
        default_factory=list, description="缺少行动扣分"
    )
    suggestions: List[Suggestion] = Field(default_factory=list, description="改进建议")
    meta: Dict[str, Any] = Field(default_factory=dict, description="评分元数据")
    judge_mode: Optional[str] = Field(None, description="评判模式")
    judge_source: Optional[str] = Field(None, description="评判来源")
    spark_called: Optional[bool] = Field(None, description="是否调用外部LLM（兼容旧字段名）")
    fallback_reason: Optional[str] = Field(None, description="回退原因")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "total_score": 78.5,
                    "dimension_scores": {
                        "D01": {
                            "id": "D01",
                            "name": "工程概况",
                            "module": "overview",
                            "score": 15.0,
                            "max_score": 20.0,
                            "hits": ["工程概况", "项目背景"],
                            "evidence": [
                                {"start_index": 0, "end_index": 50, "snippet": "一、工程概况..."}
                            ],
                            "sub_scores": None,
                        }
                    },
                    "logic_lock": {
                        "definition_score": 8.0,
                        "analysis_score": 7.5,
                        "solution_score": 8.0,
                        "breaks": [],
                        "evidence": [],
                    },
                    "penalties": [
                        {
                            "code": "EMPTY_PROMISE",
                            "message": "缺少具体数据支撑",
                            "evidence_span": None,
                            "deduct": 2.0,
                            "tags": ["quality"],
                        }
                    ],
                    "penalties_logic_lock": [],
                    "penalties_empty_promises": [],
                    "penalties_action_missing": [],
                    "suggestions": [
                        {
                            "dimension": "D01",
                            "action": "补充项目规模数据",
                            "expected_gain": 3.0,
                        }
                    ],
                    "meta": {"text_length": 5000, "scored_at": "2026-02-04T12:00:00"},
                    "judge_mode": "local",
                    "judge_source": None,
                    "spark_called": False,
                    "fallback_reason": None,
                }
            ]
        }
    }


# ==================== 缓存相关模型 ====================


class CacheStatsResponse(BaseModel):
    """缓存统计响应"""

    total_requests: int = Field(..., description="总请求数")
    hits: int = Field(..., description="缓存命中数")
    misses: int = Field(..., description="缓存未命中数")
    evictions: int = Field(..., description="缓存驱逐数")
    size: int = Field(..., description="当前缓存条目数")
    hit_rate: float = Field(..., description="缓存命中率（0.0-1.0）")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "total_requests": 100,
                    "hits": 75,
                    "misses": 25,
                    "evictions": 5,
                    "size": 50,
                    "hit_rate": 0.75,
                }
            ]
        }
    }


class CacheClearResponse(BaseModel):
    """缓存清空响应"""

    cleared: bool = Field(..., description="是否成功清空")
    count: int = Field(..., description="清除的缓存条目数")
    message: str = Field(..., description="操作结果消息")

    model_config = {
        "json_schema_extra": {
            "examples": [{"cleared": True, "count": 50, "message": "已清空 50 条缓存"}]
        }
    }
