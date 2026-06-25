"""
统一离线分析器（Shigong Analyzer）。

升级增量 9 · 把增量 1–8 串成一条命令：给一份施组全文 + 招标编号，
输出一份完整的「按标分析报告」（Markdown），覆盖：
- 本标口径（评标办法/分制/分档）与施组含金量、动态目标；
- 硬红线预检（判废/0分/失分风险）；
- 三轴诊断（针对性/可行性/语言精练度）+ 考量项覆盖；
- 预测 F 分/档位/距下一档/同标百分位；
- ROI 优化清单（按预期提分排序、绑定考量项）；
- 针对性改写示例（确定性模板；LLM 改写见增量 7，default-off）。

纯新增、确定性、零外部依赖；不接核心评分主链；不修改 main.py。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from app.engine.compilation_advisor import (
    CompilationSuggestion,
    build_compilation_advice,
    compilation_advisor_to_dict,
    suggest_rewrite,
)
from app.engine.judge_aggregation import aggregate_judge_scores, judge_aggregation_to_dict
from app.engine.shigong_diagnostics import (
    OptimizationItem,
    ShigongDiagnosis,
    build_optimization_checklist,
    diagnose_shigong,
    run_shigong_diagnostics,
    shigong_diagnostics_to_dict,
)
from app.engine.strategy_advisor import (
    LeverageAssessment,
    TargetRecommendation,
    assess_shigong_leverage,
    build_strategy_recommendations,
    recommend_target,
    strategy_advisor_to_dict,
)
from app.engine.target_mapping import (
    TargetPrediction,
    build_target_mapping,
    map_internal_to_target,
    target_mapping_to_dict,
)
from app.engine.tender_preflight import (
    TenderPreflightResult,
    check_tender_hard_lines,
    run_tender_preflight,
    tender_preflight_to_dict,
)
from app.engine.tender_profile import (
    TenderProfile,
    TenderProfileValidationError,
    TenderScoringProfile,
    get_profile,
    load_tender_profile,
    tender_profile_to_dict,
    validate_tender_profile,
)
from app.engine.text_calibration import (
    calibrate_text_against_profile,
    composite_quality,
    text_calibration_to_dict,
)

_GENERIC_SAMPLE = ("确保", "严格", "加强", "认真", "精心", "全面落实", "高度重视")
_SHIGONG_ANALYSIS_SECTION_ORDER = (
    "项目基本信息",
    "按标评分项覆盖",
    "预检结论",
    "施工诊断",
    "编制建议",
    "高分策略",
    "评委评分聚合",
    "文本校准",
)


class ShigongAnalyzerError(ValueError):
    """按标施组总分析失败时抛出。"""


class AnalyzerError(ShigongAnalyzerError):
    """分析器输入非法（如找不到标书配置）时抛出。"""


def _first_generic_sentence(text: str) -> Optional[str]:
    for sent in re.split(r"[。\n；;]", str(text or "")):
        s = sent.strip()
        if len(s) >= 6 and any(g in s for g in _GENERIC_SAMPLE):
            return s
    return None


@dataclass(frozen=True)
class AnalysisReport:
    profile: TenderScoringProfile
    internal_composite: float
    prediction: TargetPrediction
    leverage: LeverageAssessment
    target: Optional[TargetRecommendation]
    preflight: TenderPreflightResult
    diagnosis: ShigongDiagnosis
    checklist: List[OptimizationItem]
    rewrite_example: Optional[CompilationSuggestion]

    def to_markdown(self) -> str:
        p = self.profile
        d = self.diagnosis
        pred = self.prediction
        lines: List[str] = []

        lines.append(f"# 施组分析报告 · {p.tender_name}")
        lines.append("")
        lines.append(
            f"- 招标编号：{p.tender_id}　|　评标办法：{p.eval_method}　|　施组满分：{p.shigong_max_score:g}"
        )
        lines.append(
            f"- 施组含金量：**{self.leverage.leverage_level}**（建议投入：{self.leverage.recommended_effort}）"
        )
        lines.append(f"  - {self.leverage.decided_by}")
        if self.target and self.target.target_f is not None:
            lines.append(
                f"- 动态目标：冲到全场最前 ≈ **{self.target.target_f}**（{self.target.target_band}）"
                + (
                    f"，当前距目标 {self.target.current_gap}"
                    if self.target.current_gap is not None
                    else ""
                )
            )
        lines.append(f"- {self.leverage.field_signal}" if self.leverage.field_signal else "")
        lines.append("")

        # 预测
        lines.append("## 预测（本标口径）")
        lines.append("")
        lines.append(
            f"- 预测 F 分：**{pred.f_score}**（{pred.band}）　归一化 {pred.normalized}"
            + (
                f"　同标百分位 {pred.percentile_in_field}"
                if pred.percentile_in_field is not None
                else ""
            )
        )
        if pred.next_band and pred.gap_to_next_band is not None:
            lines.append(f"- 距上一档「{pred.next_band}」还差 **{pred.gap_to_next_band}** 分")
        lines.append(
            f"- 说明：当前用确定性内部质量分（composite={self.internal_composite}）线性映射；"
            "接入按标校准器后会更贴近真实青天分。"
        )
        lines.append("")

        # 红线
        lines.append("## 硬红线预检")
        lines.append("")
        if self.preflight.disqualified:
            lines.append("> ⛔ **命中判废级红线，先解决以下问题再谈打分。**")
            lines.append("")
        for f in self.preflight.findings:
            mark = {"fail": "✗", "pass": "✓", "unknown": "?", "advisory": "·"}.get(f.status, "·")
            lines.append(
                f"- {mark} [{f.severity}] {f.message}" + (f" → {f.action}" if f.action else "")
            )
        lines.append("")

        # 三轴 + 覆盖
        lines.append("## 三轴诊断 + 考量项覆盖")
        lines.append("")
        lines.append(
            f"- 针对性 {d.axes.specificity}　|　可行性 {d.axes.landing}　|　语言精练度 {d.axes.conciseness}"
        )
        addressed = sum(1 for c in d.considerations if c.addressed)
        lines.append(
            f"- 考量项覆盖：{addressed}/{len(d.considerations)}　"
            f"硬要素 {d.hard_element_count} 处　空泛 {d.generic_phrase_count} 处"
        )
        missing = [c.name for c in d.considerations if not c.addressed]
        if missing:
            lines.append(f"- 未覆盖：{'、'.join(missing)}")
        lines.append("")

        # 优化清单
        lines.append("## ROI 优化清单（按预期提分排序）")
        lines.append("")
        if not self.checklist:
            lines.append("- （无明显弱项，三轴与覆盖均达标）")
        else:
            for it in self.checklist:
                tag = f"考量项「{it.consideration}」" if it.consideration else it.issue
                lines.append(
                    f"{it.priority}. [{it.severity}|gain={it.expected_gain}] {tag}：{it.action}"
                )
        lines.append("")

        # 改写示例
        if self.rewrite_example is not None:
            lines.append("## 针对性改写示例")
            lines.append("")
            lines.append(self.rewrite_example.text)
            lines.append("")

        lines.append("---")
        lines.append("_本报告由确定性引擎生成，不参与正式评分；预测分接入按标校准器后更准。_")
        return "\n".join(lines)


@dataclass(frozen=True)
class ShigongAnalysisInput:
    profile: TenderProfile
    document_text: str = ""
    provided_evidence: dict | None = None
    judge_scores: list | None = None
    calibration_samples: list | None = None


@dataclass(frozen=True)
class ShigongAnalysisReport:
    tender_id: str
    tender_name: str
    version: str
    status: str
    profile: TenderProfile
    target_mapping: object
    preflight: object
    diagnostics: object
    compilation_advice: object
    strategy_recommendations: object
    judge_aggregation: object | None
    text_calibration: object
    summary: dict
    markdown_sections: dict


def _status_from(report: object | None) -> str:
    if report is None:
        return "pass"
    return str(getattr(report, "status", "pass") or "pass")


def _overall_status(reports: Sequence[object | None]) -> str:
    statuses = [_status_from(report) for report in reports if report is not None]
    if any(status in ("action_required", "error") for status in statuses):
        return "action_required"
    if any(status == "warning" for status in statuses):
        return "warning"
    return "pass"


def _append_stable_item_id(item_ids: list[str], seen: set[str], raw: object) -> None:
    item_id = str(raw or "").strip()
    if item_id and item_id not in seen:
        seen.add(item_id)
        item_ids.append(item_id)


def _focus_item_ids(
    diagnostics: object,
    compilation_advice: object,
    strategy_recommendations: object,
    judge_aggregation: object | None,
    text_calibration: object,
) -> list[str]:
    seen: set[str] = set()
    item_ids: list[str] = []

    for issue in getattr(diagnostics, "issues", ()):
        if str(getattr(issue, "severity", "")) in ("warning", "error"):
            _append_stable_item_id(item_ids, seen, getattr(issue, "item_id", ""))
    for advice in getattr(compilation_advice, "advice_items", ()):
        _append_stable_item_id(item_ids, seen, getattr(advice, "item_id", ""))
    for item_id in getattr(strategy_recommendations, "focus_item_ids", ()):
        _append_stable_item_id(item_ids, seen, item_id)
    if judge_aggregation is not None:
        for item_id in getattr(judge_aggregation, "missing_item_scores", ()):
            _append_stable_item_id(item_ids, seen, item_id)
        for item_id in getattr(judge_aggregation, "high_dispersion_item_ids", ()):
            _append_stable_item_id(item_ids, seen, item_id)
        for item_id in getattr(judge_aggregation, "unknown_item_ids", ()):
            _append_stable_item_id(item_ids, seen, item_id)
    for item_id in getattr(text_calibration, "high_delta_item_ids", ()):
        _append_stable_item_id(item_ids, seen, item_id)
    for item_id in getattr(text_calibration, "missing_text_item_ids", ()):
        _append_stable_item_id(item_ids, seen, item_id)
    return item_ids


def _analysis_summary(
    profile: TenderProfile,
    status: str,
    preflight: object,
    diagnostics: object,
    compilation_advice: object,
    strategy_recommendations: object,
    judge_aggregation: object | None,
    text_calibration: object,
) -> dict:
    focus_item_ids = _focus_item_ids(
        diagnostics,
        compilation_advice,
        strategy_recommendations,
        judge_aggregation,
        text_calibration,
    )
    return {
        "tender_id": profile.tender_id,
        "tender_name": profile.tender_name,
        "version": profile.version,
        "status": status,
        "preflight_status": _status_from(preflight),
        "diagnostics_status": _status_from(diagnostics),
        "compilation_status": _status_from(compilation_advice),
        "strategy_status": _status_from(strategy_recommendations),
        "judge_aggregation_present": judge_aggregation is not None,
        "judge_aggregation_status": (
            _status_from(judge_aggregation) if judge_aggregation is not None else "not_provided"
        ),
        "text_calibration_status": _status_from(text_calibration),
        "focus_item_ids": focus_item_ids,
        "requires_attention_item_ids": focus_item_ids,
        "hard_redline_policy": "仅作风险提示，不自动扣分、不判废、不裁决。",
        "does_not_disqualify": True,
        "affects_score": False,
    }


def _join_values(values: Sequence[object], empty: str = "无") -> str:
    normalized = [str(value) for value in values if str(value).strip()]
    return "、".join(normalized) if normalized else empty


def _build_markdown_sections(
    profile: TenderProfile,
    status: str,
    target_mapping: object,
    preflight: object,
    diagnostics: object,
    compilation_advice: object,
    strategy_recommendations: object,
    judge_aggregation: object | None,
    text_calibration: object,
) -> dict:
    target_mapping_dict = target_mapping_to_dict(target_mapping)
    preflight_dict = tender_preflight_to_dict(preflight)
    diagnostics_dict = shigong_diagnostics_to_dict(diagnostics)
    compilation_dict = compilation_advisor_to_dict(compilation_advice)
    strategy_dict = strategy_advisor_to_dict(strategy_recommendations)
    text_calibration_dict = text_calibration_to_dict(text_calibration)

    mapping_coverage = dict(target_mapping_dict["coverage"])
    preflight_summary = dict(preflight_dict["summary"])
    diagnostics_summary = dict(diagnostics_dict["summary"])
    compilation_summary = dict(compilation_dict["summary"])
    strategy_summary = dict(strategy_dict["summary"])
    text_summary = dict(text_calibration_dict["summary"])

    if judge_aggregation is None:
        judge_section = "- 未提供评委评分数据，已跳过聚合；不影响文本校准输出。"
    else:
        judge_dict = judge_aggregation_to_dict(judge_aggregation)
        judge_section = "\n".join(
            [
                f"- status：{judge_dict['status']}",
                f"- judge_count：{judge_dict['judge_count']}",
                f"- total_average_score：{judge_dict['total_average_score']}",
                f"- high_dispersion_item_ids：{_join_values(judge_dict['high_dispersion_item_ids'])}",
                f"- unknown_item_ids：{_join_values(judge_dict['unknown_item_ids'])}",
            ]
        )

    return {
        "项目基本信息": "\n".join(
            [
                f"- tender_id：{profile.tender_id}",
                f"- tender_name：{profile.tender_name}",
                f"- version：{profile.version}",
                f"- status：{status}",
            ]
        ),
        "按标评分项覆盖": "\n".join(
            [
                f"- item_count：{mapping_coverage['item_count']}",
                f"- mapped_item_count：{mapping_coverage['mapped_item_count']}",
                f"- unmapped_item_ids：{_join_values(mapping_coverage['unmapped_item_ids'])}",
                f"- legacy_dimension_refs：{_join_values(mapping_coverage['legacy_dimension_refs'])}",
                f"- hard_redline_count：{mapping_coverage['hard_redline_count']}",
            ]
        ),
        "预检结论": "\n".join(
            [
                f"- status：{preflight_dict['status']}",
                f"- issue_count：{preflight_summary['issue_count']}",
                f"- warning_count：{preflight_summary['warning_count']}",
                f"- error_count：{preflight_summary['error_count']}",
                f"- required_evidence_items：{_join_values(preflight_dict['required_evidence_items'])}",
            ]
        ),
        "施工诊断": "\n".join(
            [
                f"- status：{diagnostics_dict['status']}",
                f"- issue_count：{diagnostics_summary['issue_count']}",
                f"- warning_count：{diagnostics_summary['warning_count']}",
                f"- missing_evidence_item_ids：{_join_values(diagnostics_dict['missing_evidence_item_ids'])}",
                f"- unmapped_item_ids：{_join_values(diagnostics_dict['unmapped_item_ids'])}",
            ]
        ),
        "编制建议": "\n".join(
            [
                f"- status：{compilation_dict['status']}",
                f"- advice_item_count：{compilation_summary['advice_item_count']}",
                f"- high_priority_count：{compilation_summary['high_priority_count']}",
                f"- medium_priority_count：{compilation_summary['medium_priority_count']}",
                "- hard redline：仅提示，不执行否决、扣分、判废或裁决。",
            ]
        ),
        "高分策略": "\n".join(
            [
                f"- status：{strategy_dict['status']}",
                f"- recommendation_count：{strategy_summary['recommendation_count']}",
                f"- focus_item_ids：{_join_values(strategy_dict['focus_item_ids'])}",
                f"- strategy_type_counts：{strategy_dict['strategy_type_counts']}",
            ]
        ),
        "评委评分聚合": judge_section,
        "文本校准": "\n".join(
            [
                f"- status：{text_calibration_dict['status']}",
                f"- sample_count：{text_calibration_dict['sample_count']}",
                f"- text_empty：{text_calibration_dict['text_empty']}",
                f"- high_delta_item_ids：{_join_values(text_summary['high_delta_item_ids'])}",
                f"- missing_text_item_ids：{_join_values(text_summary['missing_text_item_ids'])}",
            ]
        ),
    }


def analyze_shigong_submission(
    profile: TenderProfile,
    document_text: str = "",
    provided_evidence: dict | None = None,
    judge_scores: list | None = None,
    calibration_samples: list | None = None,
) -> ShigongAnalysisReport:
    """串联已完成按标模块，生成结构化施组总分析报告。"""
    inputs = ShigongAnalysisInput(
        profile=profile,
        document_text=document_text,
        provided_evidence=provided_evidence,
        judge_scores=judge_scores,
        calibration_samples=calibration_samples,
    )

    try:
        validate_tender_profile(inputs.profile)
        target_mapping = build_target_mapping(inputs.profile)
        preflight = run_tender_preflight(inputs.profile)
        diagnostics = run_shigong_diagnostics(
            inputs.profile,
            document_text=inputs.document_text,
            provided_evidence=inputs.provided_evidence,
        )
        compilation_advice = build_compilation_advice(
            inputs.profile,
            document_text=inputs.document_text,
            provided_evidence=inputs.provided_evidence,
        )
        strategy_recommendations = build_strategy_recommendations(
            inputs.profile,
            document_text=inputs.document_text,
            provided_evidence=inputs.provided_evidence,
        )
        judge_aggregation = (
            aggregate_judge_scores(inputs.profile, inputs.judge_scores)
            if inputs.judge_scores
            else None
        )
        text_calibration = calibrate_text_against_profile(
            inputs.profile,
            document_text=inputs.document_text,
            judge_report=judge_aggregation,
            samples=inputs.calibration_samples,
        )
        status = _overall_status(
            (
                preflight,
                diagnostics,
                compilation_advice,
                strategy_recommendations,
                judge_aggregation,
                text_calibration,
            )
        )
        summary = _analysis_summary(
            inputs.profile,
            status,
            preflight,
            diagnostics,
            compilation_advice,
            strategy_recommendations,
            judge_aggregation,
            text_calibration,
        )
        markdown_sections = _build_markdown_sections(
            inputs.profile,
            status,
            target_mapping,
            preflight,
            diagnostics,
            compilation_advice,
            strategy_recommendations,
            judge_aggregation,
            text_calibration,
        )
    except ShigongAnalyzerError:
        raise
    except Exception as exc:
        raise ShigongAnalyzerError(f"按标施组总分析失败: {exc}") from exc

    return ShigongAnalysisReport(
        tender_id=inputs.profile.tender_id,
        tender_name=inputs.profile.tender_name,
        version=inputs.profile.version,
        status=status,
        profile=inputs.profile,
        target_mapping=target_mapping,
        preflight=preflight,
        diagnostics=diagnostics,
        compilation_advice=compilation_advice,
        strategy_recommendations=strategy_recommendations,
        judge_aggregation=judge_aggregation,
        text_calibration=text_calibration,
        summary=summary,
        markdown_sections=markdown_sections,
    )


def analyze_shigong_submission_from_file(
    profile_path: str | Path,
    document_text: str = "",
    provided_evidence: dict | None = None,
    judge_scores: list | None = None,
    calibration_samples: list | None = None,
) -> ShigongAnalysisReport:
    """从 TenderProfile JSON 加载后生成按标施组总分析报告。"""
    try:
        profile = load_tender_profile(profile_path)
    except TenderProfileValidationError as exc:
        raise ShigongAnalyzerError(f"TenderProfile 加载失败: {exc}") from exc
    return analyze_shigong_submission(
        profile,
        document_text=document_text,
        provided_evidence=provided_evidence,
        judge_scores=judge_scores,
        calibration_samples=calibration_samples,
    )


def shigong_analysis_to_dict(report: ShigongAnalysisReport) -> dict:
    """返回 JSON 友好的按标施组总分析报告 dict。"""
    if not isinstance(report, ShigongAnalysisReport):
        raise ShigongAnalyzerError("report 必须是 ShigongAnalysisReport")
    return {
        "tender_id": report.tender_id,
        "tender_name": report.tender_name,
        "version": report.version,
        "status": report.status,
        "profile": tender_profile_to_dict(report.profile),
        "target_mapping": target_mapping_to_dict(report.target_mapping),
        "preflight": tender_preflight_to_dict(report.preflight),
        "diagnostics": shigong_diagnostics_to_dict(report.diagnostics),
        "compilation_advice": compilation_advisor_to_dict(report.compilation_advice),
        "strategy_recommendations": strategy_advisor_to_dict(report.strategy_recommendations),
        "judge_aggregation": (
            judge_aggregation_to_dict(report.judge_aggregation)
            if report.judge_aggregation is not None
            else None
        ),
        "text_calibration": text_calibration_to_dict(report.text_calibration),
        "summary": dict(report.summary),
        "markdown_sections": dict(report.markdown_sections),
    }


def shigong_analysis_to_markdown(report: ShigongAnalysisReport) -> str:
    """使用结构化报告生成确定性 Markdown。"""
    if not isinstance(report, ShigongAnalysisReport):
        raise ShigongAnalyzerError("report 必须是 ShigongAnalysisReport")

    summary = dict(report.summary)
    lines: list[str] = [
        f"# 按标施组总分析报告 · {report.tender_name}",
        "",
        f"- tender_name：{report.tender_name}",
        f"- status：{report.status}",
        "",
        "## Summary",
        "",
        f"- preflight_status：{summary.get('preflight_status', '')}",
        f"- diagnostics_status：{summary.get('diagnostics_status', '')}",
        f"- compilation_status：{summary.get('compilation_status', '')}",
        f"- strategy_status：{summary.get('strategy_status', '')}",
        f"- judge_aggregation_status：{summary.get('judge_aggregation_status', '')}",
        f"- text_calibration_status：{summary.get('text_calibration_status', '')}",
        f"- focus_item_ids：{_join_values(summary.get('focus_item_ids', []))}",
        "",
    ]
    for section_title in _SHIGONG_ANALYSIS_SECTION_ORDER:
        lines.append(f"## {section_title}")
        lines.append("")
        section_body = str(report.markdown_sections.get(section_title, "")).strip()
        lines.extend(section_body.splitlines() if section_body else ["- （无内容）"])
        lines.append("")
    return "\n".join(lines).rstrip()


def analyze_shigong(
    text: str,
    tender_id: str,
    *,
    project_terms: Optional[Sequence[str]] = None,
    page_count: Optional[int] = None,
    shigong_count: int = 1,
    field_scores: Optional[Sequence[float]] = None,
) -> AnalysisReport:
    """对一份施组做完整的按标离线分析。"""
    profile = get_profile(tender_id)
    if profile is None:
        raise AnalyzerError(f"找不到标书配置: {tender_id}")

    preflight = check_tender_hard_lines(
        text, profile, page_count=page_count, shigong_count=shigong_count
    )
    diagnosis = diagnose_shigong(text, profile, project_terms=project_terms)
    checklist = build_optimization_checklist(diagnosis, profile)
    internal = composite_quality(text, profile, project_terms=project_terms)
    prediction = map_internal_to_target(internal, profile, field_scores=field_scores)
    leverage = assess_shigong_leverage(profile, field_shigong_scores=field_scores)
    target = (
        recommend_target(profile, field_shigong_scores=field_scores, current_f=prediction.f_score)
        if field_scores
        else None
    )

    rewrite_example: Optional[CompilationSuggestion] = None
    if diagnosis.axes.specificity < 0.6:
        sent = _first_generic_sentence(text)
        if sent:
            rewrite_example = suggest_rewrite(sent, profile, project_terms=project_terms)

    return AnalysisReport(
        profile=profile,
        internal_composite=internal,
        prediction=prediction,
        leverage=leverage,
        target=target,
        preflight=preflight,
        diagnosis=diagnosis,
        checklist=checklist,
        rewrite_example=rewrite_example,
    )


def analyze_to_markdown(
    text: str,
    tender_id: str,
    *,
    project_terms: Optional[Sequence[str]] = None,
    page_count: Optional[int] = None,
    shigong_count: int = 1,
    field_scores: Optional[Sequence[float]] = None,
) -> str:
    return analyze_shigong(
        text,
        tender_id,
        project_terms=project_terms,
        page_count=page_count,
        shigong_count=shigong_count,
        field_scores=field_scores,
    ).to_markdown()
