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
from typing import Callable, Dict, Optional, Sequence, Tuple

from app.engine.shigong_diagnostics import ShigongDiagnosis
from app.engine.tender_profile import TenderScoringProfile

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
