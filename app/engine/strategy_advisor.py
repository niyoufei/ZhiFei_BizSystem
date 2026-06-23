"""
战略判断层（Strategy Advisor）。

升级增量 3。回答两个真实数据逼出来的问题：
1) 施组含金量评估：在本标的评标办法 + 竞争分布下，施组到底是不是胜负手，
   决定要不要在施组上砸资源（decisive 决定性 / coupled 联动 / gate 门槛）。
2) 动态目标设定：目标不是「绝对满分」，而是「本标良好档·全场最前」。
   四标实测无人进优秀档，绝对满分不是合理目标。

纪律：纯新增、确定性、零外部依赖；不修改核心评分链。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.engine.compilation_advisor import (
    CompilationAdvisorError,
    build_compilation_advice,
    compilation_advisor_to_dict,
)
from app.engine.tender_profile import (
    TenderProfile,
    TenderProfileValidationError,
    TenderScoringProfile,
    field_target_score,
    load_tender_profile,
    summarize_field,
    validate_tender_profile,
)

# 含金量 -> 建议投入强度
_EFFORT_BY_LEVEL = {
    "decisive": "high",
    "coupled": "medium",
    "gate": "low-gate",
}

_DECIDED_BY = {
    "decisive": "价格普遍踩满、商务齐全后，施组分直接决定排名——施组是胜负手，值得重点投入。",
    "coupled": "施组与价格联动，差距常在小数点后，需施组与价格同时到位才稳。",
    "gate": "施组在此办法下是「门槛项」：主要决定能否进入报价评审，最终由价格定胜负——施组达标即可，过度投入边际收益低。",
}

# 施组分「极差/满分」低于该比例，视为高度聚集、几乎无区分度
_LOW_SPREAD_RATIO = 0.02


@dataclass(frozen=True)
class LeverageAssessment:
    tender_id: str
    eval_method: str
    leverage_level: str  # decisive / coupled / gate
    recommended_effort: str  # high / medium / low-gate
    decided_by: str
    rationale: Tuple[str, ...]
    field_signal: Optional[str]  # 由竞争分布 refine 出的信号（给了数据才有）

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TargetRecommendation:
    tender_id: str
    target_f: Optional[float]  # 动态目标 F 分（默认=全场最高）
    target_band: Optional[str]
    target_quantile: float  # 1.0 = 全场最前
    ceiling_note: str
    current_gap: Optional[float]  # 给了 current_f 时，距目标还差多少分

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def assess_shigong_leverage(
    profile: TenderScoringProfile,
    *,
    field_shigong_scores: Optional[Sequence[float]] = None,
    field_price_scores: Optional[Sequence[float]] = None,
) -> LeverageAssessment:
    """评估在本标里施组的胜负含金量。配置给出基线，竞争分布做 refine。"""
    level = profile.shigong_weight_type
    effort = _EFFORT_BY_LEVEL.get(level, "medium")
    decided = _DECIDED_BY.get(level, "")

    rationale = [
        f"评标办法：{profile.eval_method}；配置判定施组含金量为「{level}」。",
    ]

    field_signal: Optional[str] = None
    if field_shigong_scores:
        sm = summarize_field(field_shigong_scores)
        if sm:
            spread = sm["max"] - sm["min"]
            ratio = spread / float(profile.shigong_max_score) if profile.shigong_max_score else 0.0
            if ratio < _LOW_SPREAD_RATIO:
                field_signal = (
                    f"施组分高度聚集（极差 {spread:.2f}/{profile.shigong_max_score:g}），"
                    "几乎无区分度——靠施组拉开差距很难。"
                )
            else:
                field_signal = f"施组分有区分度（极差 {spread:.2f}），值得在施组上发力。"
            rationale.append(field_signal)

    if field_price_scores:
        pm = summarize_field(field_price_scores)
        if pm and (pm["max"] - pm["min"]) < 1e-9:
            rationale.append("报价分完全相同——价格已无区分度，施组/商务成为唯一变量。")

    return LeverageAssessment(
        tender_id=profile.tender_id,
        eval_method=profile.eval_method,
        leverage_level=level,
        recommended_effort=effort,
        decided_by=decided,
        rationale=tuple(rationale),
        field_signal=field_signal,
    )


def recommend_target(
    profile: TenderScoringProfile,
    *,
    field_shigong_scores: Optional[Sequence[float]] = None,
    current_f: Optional[float] = None,
    quantile: float = 1.0,
) -> TargetRecommendation:
    """给出本标的动态目标分。默认目标=全场最高（quantile=1.0，即冲到全场最前）。"""
    scores = list(field_shigong_scores or [])
    target_f = field_target_score(scores, quantile=quantile) if scores else None
    target_band = profile.band_of(target_f) if target_f is not None else None

    ceiling_note = "四标实测无人进入优秀档；现实目标为「本标良好档·全场最前」，不必追绝对满分。"
    current_gap: Optional[float] = None
    if current_f is not None and target_f is not None:
        current_gap = round(max(0.0, float(target_f) - float(current_f)), 4)

    return TargetRecommendation(
        tender_id=profile.tender_id,
        target_f=(round(float(target_f), 4) if target_f is not None else None),
        target_band=target_band,
        target_quantile=quantile,
        ceiling_note=ceiling_note,
        current_gap=current_gap,
    )


class StrategyAdvisorError(ValueError):
    """按标策略建议生成失败。"""


_STRATEGY_PRIORITIES = ("high", "medium", "low")
_STRATEGY_TYPES = (
    "mapping",
    "evidence",
    "redline",
    "document",
    "coverage",
    "quality",
    "scoring_response",
)


@dataclass(frozen=True)
class StrategyRecommendation:
    recommendation_id: str
    item_id: str
    title: str
    priority: str
    strategy_type: str
    rationale: str
    action: str
    expected_effect: str
    source_advice_ids: Tuple[str, ...] = ()
    source_issue_codes: Tuple[str, ...] = ()
    legacy_dimension_refs: Tuple[str, ...] = ()
    evidence_requirement: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.priority not in _STRATEGY_PRIORITIES:
            raise ValueError(f"非法 strategy priority: {self.priority}")
        if self.strategy_type not in _STRATEGY_TYPES:
            raise ValueError(f"非法 strategy type: {self.strategy_type}")

    def to_dict(self) -> Dict[str, object]:
        return {
            "recommendation_id": self.recommendation_id,
            "item_id": self.item_id,
            "title": self.title,
            "priority": self.priority,
            "strategy_type": self.strategy_type,
            "rationale": self.rationale,
            "action": self.action,
            "expected_effect": self.expected_effect,
            "source_advice_ids": list(self.source_advice_ids),
            "source_issue_codes": list(self.source_issue_codes),
            "legacy_dimension_refs": list(self.legacy_dimension_refs),
            "evidence_requirement": list(self.evidence_requirement),
        }


@dataclass(frozen=True)
class StrategyAdvisorReport:
    tender_id: str
    tender_name: str
    version: str
    status: str
    recommendations: Tuple[StrategyRecommendation, ...]
    compilation_advice: Dict[str, object]
    priority_counts: Dict[str, int]
    strategy_type_counts: Dict[str, int]
    focus_item_ids: Tuple[str, ...]
    unmapped_item_ids: Tuple[str, ...]
    missing_evidence_item_ids: Tuple[str, ...]
    hard_redline_count: int
    summary: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "tender_id": self.tender_id,
            "tender_name": self.tender_name,
            "version": self.version,
            "status": self.status,
            "recommendations": [item.to_dict() for item in self.recommendations],
            "compilation_advice": dict(self.compilation_advice),
            "priority_counts": dict(self.priority_counts),
            "strategy_type_counts": dict(self.strategy_type_counts),
            "focus_item_ids": list(self.focus_item_ids),
            "unmapped_item_ids": list(self.unmapped_item_ids),
            "missing_evidence_item_ids": list(self.missing_evidence_item_ids),
            "hard_redline_count": self.hard_redline_count,
            "summary": dict(self.summary),
        }


def _strategy_status(recommendations: Sequence[StrategyRecommendation]) -> str:
    if not recommendations:
        return "pass"
    if any(item.priority == "high" for item in recommendations):
        return "action_required"
    return "warning"


def _strategy_type_counts(recommendations: Sequence[StrategyRecommendation]) -> Dict[str, int]:
    return {
        strategy_type: sum(1 for item in recommendations if item.strategy_type == strategy_type)
        for strategy_type in _STRATEGY_TYPES
    }


def _stable_item_ids(recommendations: Sequence[StrategyRecommendation]) -> Tuple[str, ...]:
    seen = set()
    item_ids: List[str] = []
    for item in recommendations:
        item_id = str(item.item_id or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        item_ids.append(item_id)
    return tuple(item_ids)


def _strategy_type_for_advice(advice: object) -> str:
    category = str(getattr(advice, "category", ""))
    legacy_refs = tuple(str(ref) for ref in getattr(advice, "legacy_dimension_refs", ()))
    if category == "mapping":
        return "scoring_response" if legacy_refs else "mapping"
    if category in ("evidence", "redline", "document"):
        return category
    if category == "coverage":
        return "coverage"
    return "quality"


def _strategy_action_for_advice(advice: object, strategy_type: str) -> str:
    base_action = str(getattr(advice, "action", "")).strip()
    if strategy_type in ("mapping", "scoring_response"):
        return (
            f"{base_action} 同步补齐评审口径映射说明，明确该评分项如何承接既有维度、"
            "目标章节和高分响应口径。"
        )
    if strategy_type == "evidence":
        return (
            f"{base_action} 补充章节证据、量化指标、验收依据或图文锚点，并在对应章节形成"
            "可核查闭环。"
        )
    if strategy_type == "redline":
        return (
            f"{base_action} 前置核查与人工复核红线约束；本策略仅提示风险，"
            "不作否决、扣分、判废或裁决。"
        )
    if strategy_type == "document":
        return f"{base_action} 仅做施组正文落位复核和补写建议，不作判废或扣分判断。"
    return base_action or "按编制建议补齐高分响应策略。"


def _strategy_effect(strategy_type: str) -> str:
    effects = {
        "mapping": "提高评分项到既有评审口径的可追溯性，降低漏项和错配风险。",
        "scoring_response": "把评分项转换为可执行的章节响应动作，增强高分口径一致性。",
        "evidence": "增强支撑材料、量化指标和验收依据的可核查性。",
        "redline": "把 hard redline 前置为人工复核清单，避免误把提示项当作自动裁决。",
        "document": "提升施组正文对证据要求和章节锚点的覆盖度。",
        "coverage": "补齐覆盖缺口，减少评分项未响应风险。",
        "quality": "提升策略表达的针对性、可行性和可执行性。",
    }
    return effects.get(strategy_type, "提升按标策略响应质量。")


def _recommendation_from_advice(advice: object, index: int) -> StrategyRecommendation:
    strategy_type = _strategy_type_for_advice(advice)
    return StrategyRecommendation(
        recommendation_id=f"strategy-{index:03d}",
        item_id=str(getattr(advice, "item_id", "")),
        title=str(getattr(advice, "title", "")),
        priority=str(getattr(advice, "priority", "medium")),
        strategy_type=strategy_type,
        rationale=str(getattr(advice, "reason", "")),
        action=_strategy_action_for_advice(advice, strategy_type),
        expected_effect=_strategy_effect(strategy_type),
        source_advice_ids=(str(getattr(advice, "advice_id", "")),),
        source_issue_codes=tuple(str(code) for code in getattr(advice, "source_issue_codes", ())),
        legacy_dimension_refs=tuple(
            str(ref) for ref in getattr(advice, "legacy_dimension_refs", ())
        ),
        evidence_requirement=tuple(str(req) for req in getattr(advice, "evidence_requirement", ())),
    )


def _build_strategy_items(compilation_report: object) -> Tuple[StrategyRecommendation, ...]:
    return tuple(
        _recommendation_from_advice(advice, index)
        for index, advice in enumerate(getattr(compilation_report, "advice_items", ()), start=1)
    )


def build_strategy_recommendations(
    profile: TenderProfile,
    document_text: str = "",
    provided_evidence: dict | None = None,
) -> StrategyAdvisorReport:
    """把按标诊断与编制建议转换为结构化、可序列化的高分响应策略。"""
    try:
        validate_tender_profile(profile)
    except TenderProfileValidationError as exc:
        raise StrategyAdvisorError(f"TenderProfile 校验失败: {exc}") from exc

    try:
        compilation_report = build_compilation_advice(
            profile,
            document_text=document_text,
            provided_evidence=provided_evidence,
        )
    except CompilationAdvisorError as exc:
        raise StrategyAdvisorError(f"编制建议生成失败: {exc}") from exc

    recommendations = _build_strategy_items(compilation_report)
    strategy_type_counts = _strategy_type_counts(recommendations)
    priority_counts = dict(getattr(compilation_report, "priority_counts", {}))
    status = _strategy_status(recommendations)
    compilation_dict = compilation_advisor_to_dict(compilation_report)
    summary = {
        "status": status,
        "recommendation_count": len(recommendations),
        "high_priority_count": priority_counts.get("high", 0),
        "medium_priority_count": priority_counts.get("medium", 0),
        "low_priority_count": priority_counts.get("low", 0),
        "strategy_type_counts": dict(strategy_type_counts),
        "compilation_status": getattr(compilation_report, "status", ""),
        "diagnostics": dict(compilation_dict.get("diagnostics", {})),
        "coverage": dict(getattr(compilation_report, "coverage", {})),
        "unmapped_item_count": len(getattr(compilation_report, "unmapped_item_ids", ())),
        "missing_evidence_item_count": len(
            getattr(compilation_report, "missing_evidence_item_ids", ())
        ),
        "hard_redline_count": int(getattr(compilation_report, "hard_redline_count", 0)),
        "does_not_disqualify": True,
        "affects_score": False,
    }

    return StrategyAdvisorReport(
        tender_id=profile.tender_id,
        tender_name=profile.tender_name,
        version=profile.version,
        status=status,
        recommendations=recommendations,
        compilation_advice=compilation_dict,
        priority_counts=priority_counts,
        strategy_type_counts=strategy_type_counts,
        focus_item_ids=_stable_item_ids(recommendations),
        unmapped_item_ids=tuple(getattr(compilation_report, "unmapped_item_ids", ())),
        missing_evidence_item_ids=tuple(
            getattr(compilation_report, "missing_evidence_item_ids", ())
        ),
        hard_redline_count=int(getattr(compilation_report, "hard_redline_count", 0)),
        summary=summary,
    )


def build_strategy_recommendations_from_file(
    profile_path: str | Path,
    document_text: str = "",
    provided_evidence: dict | None = None,
) -> StrategyAdvisorReport:
    """从 TenderProfile JSON 加载并生成按标策略建议。"""
    try:
        profile = load_tender_profile(profile_path)
    except TenderProfileValidationError as exc:
        raise StrategyAdvisorError(f"TenderProfile 加载失败: {exc}") from exc
    return build_strategy_recommendations(
        profile,
        document_text=document_text,
        provided_evidence=provided_evidence,
    )


def strategy_advisor_to_dict(report: StrategyAdvisorReport) -> Dict[str, object]:
    """返回 JSON 友好的按标策略建议报告 dict。"""
    return report.to_dict()
