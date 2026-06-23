"""
编制建议层（Compilation Advisor）· LLM 解释 / 针对性改写。

升级增量 7（路线图收口）。消费增量 6 的诊断，产出两类自然语言编制建议：
1) 差异解释：给定"预测 F 分 vs 真实青天 F 分"，解释差距可能来自哪些诊断信号
   （用于反推青天评分特点）；
2) 针对性改写：把一条空泛措施改写成"针对本工程 + 落地"的建议。

严守既有本地 LLM 边界（对齐 llm_evolution.py 的 default-"rules" 与 preview 适配器契约）：
- default-off：后端默认 "rules"，纯确定性模板输出；
- preview-only / no-write / affects_score=False：本层永不参与评分、永不写库；
- LLM 仅作为可注入的可选增强（llm 回调）；不可用即退化为确定性模板。

真实后端（ollama/openai/...）的接线属单独授权步骤，复用既有 llm_evolution 后端，不在本层展开。
纯新增、确定性可自验；不接核心评分主链。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from app.engine.shigong_diagnostics import (
    ShigongDiagnosis,
    ShigongDiagnosticsError,
    run_shigong_diagnostics,
    shigong_diagnostics_to_dict,
)
from app.engine.tender_profile import (
    TenderProfile,
    TenderProfileValidationError,
    TenderScoringProfile,
    load_tender_profile,
    validate_tender_profile,
)

COMPILATION_LLM_BACKEND_ENV = "COMPILATION_LLM_BACKEND"

# 可注入的 LLM 回调：输入 prompt -> 输出文本
LLMFn = Callable[[str], str]

# 常见空泛套话（用于改写时识别并建议替换）
_GENERIC_TERMS = (
    "严格按照",
    "严格执行",
    "确保",
    "加强",
    "认真",
    "高度重视",
    "精心组织",
    "精心施工",
    "全面落实",
    "积极",
    "努力",
    "坚决",
    "务必",
    "切实",
    "科学组织",
    "合理安排",
)


def get_compilation_llm_backend() -> str:
    """默认 'rules'（不调用任何大模型），对齐 EVOLUTION_LLM_BACKEND 的默认行为。"""
    return (os.environ.get(COMPILATION_LLM_BACKEND_ENV) or "rules").strip().lower()


@dataclass(frozen=True)
class CompilationSuggestion:
    kind: str  # "gap_explanation" | "rewrite"
    text: str
    factors: Tuple[str, ...]
    source: str  # "rules" | "llm:<tag>"
    preview_only: bool = True
    affects_score: bool = False
    no_write: bool = True

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _axis_label(v: float) -> str:
    if v >= 0.66:
        return "高"
    if v >= 0.4:
        return "中"
    return "低"


# ==================== 差异解释 ====================


def build_gap_explanation_prompt(
    diagnosis: ShigongDiagnosis,
    predicted_f: float,
    real_f: float,
    profile: TenderScoringProfile,
) -> str:
    missing = [c.name for c in diagnosis.considerations if not c.addressed]
    return (
        "你是评标分析助手。请仅基于以下诊断信号，解释「预测分与真实青天分」的差距可能来自哪里，"
        "用于反推青天评分特点。不要给出分数，不要编造文中没有的信息。\n"
        f"评标办法：{profile.eval_method}；满分：{profile.shigong_max_score:g}\n"
        f"预测F分：{predicted_f}；真实青天F分：{real_f}\n"
        f"三轴：针对性{diagnosis.axes.specificity}、可行性{diagnosis.axes.landing}、"
        f"语言精练度{diagnosis.axes.conciseness}\n"
        f"考量项覆盖：{diagnosis.coverage_rate}；缺失项：{('、'.join(missing) or '无')}\n"
        f"硬要素{diagnosis.hard_element_count}处、空泛{diagnosis.generic_phrase_count}处。"
    )


def explain_gap(
    diagnosis: ShigongDiagnosis,
    predicted_f: float,
    real_f: float,
    profile: TenderScoringProfile,
    *,
    llm: Optional[LLMFn] = None,
) -> CompilationSuggestion:
    """解释预测分与真实分的差距。llm 给定则用之，否则确定性模板。永不影响评分。"""
    gap = round(float(real_f) - float(predicted_f), 3)
    missing = [c.name for c in diagnosis.considerations if not c.addressed]
    factors = [
        f"针对性{_axis_label(diagnosis.axes.specificity)}（{diagnosis.axes.specificity}）",
        f"可行性{_axis_label(diagnosis.axes.landing)}（{diagnosis.axes.landing}）",
        f"语言精练度{_axis_label(diagnosis.axes.conciseness)}（{diagnosis.axes.conciseness}）",
        f"考量项覆盖{diagnosis.coverage_rate}"
        + (f"，缺失：{'、'.join(missing)}" if missing else "（无缺失）"),
        f"硬要素{diagnosis.hard_element_count}处、空泛{diagnosis.generic_phrase_count}处",
    ]

    if llm is not None:
        prompt = build_gap_explanation_prompt(diagnosis, predicted_f, real_f, profile)
        text = str(llm(prompt))
        source = "llm:injected"
    else:
        if gap > 0:
            head = (
                f"系统低估 {gap}（真实{real_f} > 预测{predicted_f}）。真实评委可能更认可以下表现："
            )
        elif gap < 0:
            head = f"系统高估 {abs(gap)}（真实{real_f} < 预测{predicted_f}）。真实评委可能更在意以下短板："
        else:
            head = "预测与真实一致。关键信号："
        text = head + "；".join(factors) + "。"
        source = "rules"

    return CompilationSuggestion(
        kind="gap_explanation",
        text=text,
        factors=tuple(factors),
        source=source,
    )


# ==================== 针对性改写 ====================


def build_rewrite_prompt(
    generic_text: str,
    profile: TenderScoringProfile,
    *,
    project_terms: Optional[Sequence[str]] = None,
) -> str:
    terms = "、".join(project_terms or []) or "本工程"
    return (
        "你是施工组织设计编制助手。请把下面这条偏空泛的措施，改写成「针对本工程 + 可落地」的措施："
        "结合项目特点，明确量化参数、执行频次、责任岗位、验收动作；删除空泛套话。"
        "不要给出分数。\n"
        f"项目关键词：{terms}\n"
        f"三轴评审：{('、'.join(profile.quality_axes))}\n"
        f"原措施：{generic_text}"
    )


def suggest_rewrite(
    generic_text: str,
    profile: TenderScoringProfile,
    *,
    project_terms: Optional[Sequence[str]] = None,
    llm: Optional[LLMFn] = None,
) -> CompilationSuggestion:
    """把一条空泛措施改写为针对性 + 落地的建议。llm 给定则用之，否则确定性模板。"""
    term = (list(project_terms or []) or ["本工程"])[0]
    found = [g for g in _GENERIC_TERMS if g in str(generic_text or "")]
    factors = ("注入项目针对性", "补足参数/频次/责任/验收四要素", "删除空泛套话")

    if llm is not None:
        prompt = build_rewrite_prompt(generic_text, profile, project_terms=project_terms)
        text = str(llm(prompt))
        source = "llm:injected"
    else:
        text = (
            f"【针对性改写建议】结合【{term}】特点，由【责任岗位，如项目经理/质检员】负责，"
            "按【频次，如每日/每道工序】执行【具体动作+量化参数，如…mm/…次/…MPa】，"
            "并以【验收动作，如旁站/隐蔽验收/实测实量】闭环。"
        )
        if found:
            text += f" 建议删除空泛表述：{'、'.join(found)}。"
        source = "rules"

    return CompilationSuggestion(
        kind="rewrite",
        text=text,
        factors=factors,
        source=source,
    )


# ==================== 按标编制建议 ====================


class CompilationAdvisorError(ValueError):
    """按标编制建议生成失败。"""


_ADVICE_PRIORITIES = ("high", "medium", "low")


@dataclass(frozen=True)
class CompilationAdviceItem:
    advice_id: str
    item_id: str
    title: str
    priority: str
    category: str
    reason: str
    action: str
    evidence_requirement: Tuple[str, ...] = ()
    source_issue_codes: Tuple[str, ...] = ()
    legacy_dimension_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.priority not in _ADVICE_PRIORITIES:
            raise ValueError(f"非法 compilation advice priority: {self.priority}")

    def to_dict(self) -> Dict[str, object]:
        return {
            "advice_id": self.advice_id,
            "item_id": self.item_id,
            "title": self.title,
            "priority": self.priority,
            "category": self.category,
            "reason": self.reason,
            "action": self.action,
            "evidence_requirement": list(self.evidence_requirement),
            "source_issue_codes": list(self.source_issue_codes),
            "legacy_dimension_refs": list(self.legacy_dimension_refs),
        }


@dataclass(frozen=True)
class CompilationAdvisorReport:
    tender_id: str
    tender_name: str
    version: str
    status: str
    advice_items: Tuple[CompilationAdviceItem, ...]
    diagnostics: Dict[str, object]
    coverage: Dict[str, object]
    unmapped_item_ids: Tuple[str, ...]
    missing_evidence_item_ids: Tuple[str, ...]
    hard_redline_count: int
    priority_counts: Dict[str, int]
    summary: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "tender_id": self.tender_id,
            "tender_name": self.tender_name,
            "version": self.version,
            "status": self.status,
            "advice_items": [item.to_dict() for item in self.advice_items],
            "diagnostics": dict(self.diagnostics),
            "coverage": dict(self.coverage),
            "unmapped_item_ids": list(self.unmapped_item_ids),
            "missing_evidence_item_ids": list(self.missing_evidence_item_ids),
            "hard_redline_count": self.hard_redline_count,
            "priority_counts": dict(self.priority_counts),
            "summary": dict(self.summary),
        }


def _item_name(profile: TenderProfile, item_id: str) -> str:
    for item in profile.scoring_items:
        if item.item_id == item_id:
            return item.name
    return item_id


def _item_evidence_requirements(profile: TenderProfile, item_id: str) -> Tuple[str, ...]:
    for item in profile.scoring_items:
        if item.item_id == item_id:
            return tuple(str(req) for req in item.evidence_requirements)
    return ()


def _item_legacy_refs(profile: TenderProfile, item_id: str) -> Tuple[str, ...]:
    for item in profile.scoring_items:
        if item.item_id == item_id:
            return tuple(str(ref) for ref in item.legacy_dimension_refs)
    return ()


def _issue_missing_requirements(issue: object, profile: TenderProfile) -> Tuple[str, ...]:
    details = getattr(issue, "details", {})
    if isinstance(details, dict):
        missing = details.get("missing_requirements")
        if isinstance(missing, (list, tuple)):
            return tuple(str(req) for req in missing)
    return _item_evidence_requirements(profile, str(getattr(issue, "item_id", "")))


def _advice_status(advice_items: Sequence[CompilationAdviceItem]) -> str:
    if not advice_items:
        return "pass"
    if any(item.priority == "high" for item in advice_items):
        return "action_required"
    return "warning"


def _priority_counts(advice_items: Sequence[CompilationAdviceItem]) -> Dict[str, int]:
    return {
        priority: sum(1 for item in advice_items if item.priority == priority)
        for priority in _ADVICE_PRIORITIES
    }


def _advice_from_issue(
    issue: object,
    profile: TenderProfile,
    advice_id: str,
) -> Optional[CompilationAdviceItem]:
    code = str(getattr(issue, "code", ""))
    item_id = str(getattr(issue, "item_id", ""))
    item_name = _item_name(profile, item_id) if item_id else ""
    legacy_refs = _item_legacy_refs(profile, item_id)
    requirements = _issue_missing_requirements(issue, profile)

    if code == "UNMAPPED_SCORING_ITEM":
        return CompilationAdviceItem(
            advice_id=advice_id,
            item_id=item_id,
            title=f"补齐评分项目标映射: {item_name or item_id}",
            priority="high",
            category="mapping",
            reason="该评分项缺少 legacy_dimension_refs，无法稳定承接既有评分维度。",
            action="为该评分项补充可追溯的 legacy_dimension_refs 或明确等价目标映射后再编制对应章节。",
            evidence_requirement=requirements,
            source_issue_codes=(code,),
            legacy_dimension_refs=legacy_refs,
        )

    if code == "MISSING_EVIDENCE_REQUIREMENT":
        return CompilationAdviceItem(
            advice_id=advice_id,
            item_id=item_id,
            title=f"补齐评分项证据要求: {item_name or item_id}",
            priority="high",
            category="evidence",
            reason="该评分项未声明 evidence requirements，编制时无法形成可核查的支撑材料清单。",
            action="为该评分项补充明确的证明材料、现场数据、图表或验收记录要求，并在施组章节中逐项落位。",
            evidence_requirement=requirements,
            source_issue_codes=(code,),
            legacy_dimension_refs=legacy_refs,
        )

    if code == "EVIDENCE_NOT_PROVIDED":
        return CompilationAdviceItem(
            advice_id=advice_id,
            item_id=item_id,
            title=f"收集评分项支撑证据: {item_name or item_id}",
            priority="high",
            category="evidence",
            reason="该评分项已有 evidence requirements，但 provided_evidence 未覆盖。",
            action="先补齐缺失支撑材料，再把证据写入对应施工组织章节，避免只出现空泛承诺。",
            evidence_requirement=requirements,
            source_issue_codes=(code,),
            legacy_dimension_refs=legacy_refs,
        )

    if code == "HARD_REDLINE_DECLARED":
        return CompilationAdviceItem(
            advice_id=advice_id,
            item_id=item_id,
            title="复核 hard redline 编制约束",
            priority="medium",
            category="redline",
            reason="TenderProfile 已声明 hard redline，编制时需要作为风险提示项单独复核。",
            action="在编制清单中加入对应红线复核动作；本建议仅提示，不执行否决、扣分、判废或裁决。",
            evidence_requirement=requirements,
            source_issue_codes=(code,),
            legacy_dimension_refs=legacy_refs,
        )

    if code == "DOCUMENT_TEXT_EMPTY":
        return CompilationAdviceItem(
            advice_id=advice_id,
            item_id=item_id,
            title="提供施组正文后再做文本落位复核",
            priority="medium",
            category="document",
            reason="当前 document_text 为空，只能基于 profile / preflight / mapping 给出结构建议。",
            action="补充施组正文后复核 evidence requirements 是否落入具体章节；本建议不判废、不扣分。",
            evidence_requirement=requirements,
            source_issue_codes=(code,),
            legacy_dimension_refs=legacy_refs,
        )

    if code == "EVIDENCE_REQUIREMENT_NOT_IN_DOCUMENT":
        return CompilationAdviceItem(
            advice_id=advice_id,
            item_id=item_id,
            title=f"把证据要求写入施组正文: {item_name or item_id}",
            priority="low",
            category="document",
            reason="施组正文未直接包含部分 evidence requirement 关键词。",
            action="检查对应章节是否以等价表达覆盖证据要求；必要时补充可核查的材料名称、参数或验收动作。",
            evidence_requirement=requirements,
            source_issue_codes=(code,),
            legacy_dimension_refs=legacy_refs,
        )

    return None


def _build_advice_items(
    profile: TenderProfile,
    diagnostics: object,
) -> Tuple[CompilationAdviceItem, ...]:
    items: List[CompilationAdviceItem] = []
    for issue in getattr(diagnostics, "issues", ()):
        advice = _advice_from_issue(issue, profile, f"advice-{len(items) + 1:03d}")
        if advice is not None:
            items.append(advice)
    return tuple(items)


def build_compilation_advice(
    profile: TenderProfile,
    document_text: str = "",
    provided_evidence: dict | None = None,
) -> CompilationAdvisorReport:
    """把按标诊断结果转换为结构化、可序列化的施组编制建议。"""
    try:
        validate_tender_profile(profile)
    except TenderProfileValidationError as exc:
        raise CompilationAdvisorError(f"TenderProfile 校验失败: {exc}") from exc

    try:
        diagnostics = run_shigong_diagnostics(
            profile,
            document_text=document_text,
            provided_evidence=provided_evidence,
        )
    except ShigongDiagnosticsError as exc:
        raise CompilationAdvisorError(f"施组诊断失败: {exc}") from exc

    diagnostics_dict = shigong_diagnostics_to_dict(diagnostics)
    advice_items = _build_advice_items(profile, diagnostics)
    priority_counts = _priority_counts(advice_items)
    status = _advice_status(advice_items)
    summary = {
        "status": status,
        "advice_item_count": len(advice_items),
        "high_priority_count": priority_counts["high"],
        "medium_priority_count": priority_counts["medium"],
        "low_priority_count": priority_counts["low"],
        "diagnostics_status": diagnostics.status,
        "diagnostics_issue_count": len(diagnostics.issues),
        "coverage": dict(diagnostics.coverage),
        "hard_redline_count": diagnostics.hard_redline_count,
        "does_not_disqualify": True,
        "affects_score": False,
    }

    return CompilationAdvisorReport(
        tender_id=profile.tender_id,
        tender_name=profile.tender_name,
        version=profile.version,
        status=status,
        advice_items=advice_items,
        diagnostics=diagnostics_dict,
        coverage=dict(diagnostics.coverage),
        unmapped_item_ids=tuple(diagnostics.unmapped_item_ids),
        missing_evidence_item_ids=tuple(diagnostics.missing_evidence_item_ids),
        hard_redline_count=diagnostics.hard_redline_count,
        priority_counts=priority_counts,
        summary=summary,
    )


def build_compilation_advice_from_file(
    profile_path: str | Path,
    document_text: str = "",
    provided_evidence: dict | None = None,
) -> CompilationAdvisorReport:
    """从 TenderProfile JSON 加载并生成按标编制建议。"""
    try:
        profile = load_tender_profile(profile_path)
    except TenderProfileValidationError as exc:
        raise CompilationAdvisorError(f"TenderProfile 加载失败: {exc}") from exc
    return build_compilation_advice(
        profile,
        document_text=document_text,
        provided_evidence=provided_evidence,
    )


def compilation_advisor_to_dict(report: CompilationAdvisorReport) -> Dict[str, object]:
    """返回 JSON 友好的按标编制建议报告 dict。"""
    return report.to_dict()
