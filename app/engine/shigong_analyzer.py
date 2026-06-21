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
from typing import List, Optional, Sequence

from app.engine.compilation_advisor import CompilationSuggestion, suggest_rewrite
from app.engine.shigong_diagnostics import (
    OptimizationItem,
    ShigongDiagnosis,
    build_optimization_checklist,
    diagnose_shigong,
)
from app.engine.strategy_advisor import (
    LeverageAssessment,
    TargetRecommendation,
    assess_shigong_leverage,
    recommend_target,
)
from app.engine.target_mapping import TargetPrediction, map_internal_to_target
from app.engine.tender_preflight import TenderPreflightResult, check_tender_hard_lines
from app.engine.tender_profile import TenderScoringProfile, get_profile
from app.engine.text_calibration import composite_quality

_GENERIC_SAMPLE = ("确保", "严格", "加强", "认真", "精心", "全面落实", "高度重视")


class AnalyzerError(ValueError):
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
