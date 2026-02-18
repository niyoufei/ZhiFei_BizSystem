"""
自我学习与进化：基于真实评标结果（青天大模型等）学习高分逻辑，生成编制指导。

进化产出用于两方向：
A、评分系统进化：产出 scoring_evolution（维度权重建议），使本系统预评分更贴近合肥市公共资源交易中心青天大模型；
B、编制系统指令：产出 compilation_instructions，用于约束施组编制输出（内容、图表、必备要素等）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from app.config import load_config
from app.engine.dimensions import DIMENSIONS
from app.engine.scorer import score_text


def _run_our_scorer(shigong_text: str) -> Dict[str, Any]:
    """对施组文本跑本系统评分，返回 report 字典（含 dimension_scores, penalties）。"""
    config = load_config()
    report = score_text(shigong_text, config.rubric, config.lexicon)
    return report.model_dump()


def build_evolution_report(
    project_id: str,
    ground_truth_records: List[Dict[str, Any]],
    project_context: str = "",
) -> Dict[str, Any]:
    """
    根据真实评标记录（5评委+最终得分）与本系统评分结果，分析高分逻辑并生成编制指导。
    每条 record 需含: shigong_text, judge_scores[5], final_score, (可选) judge_weights.
    """
    if not ground_truth_records:
        return {
            "project_id": project_id,
            "high_score_logic": ["暂无真实评标数据，请先录入青天大模型等评标结果。"],
            "writing_guidance": ["录入「真实评标结果」后，点击「学习进化」即可生成编制指导。"],
            "sample_count": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "scoring_evolution": _empty_scoring_evolution(),
            "compilation_instructions": _default_compilation_instructions([]),
        }

    # 对每条真实评标施组跑本系统评分
    analyses: List[Dict[str, Any]] = []
    for r in ground_truth_records:
        text = r.get("shigong_text") or ""
        if not text.strip():
            continue
        our_report = _run_our_scorer(text)
        final = float(r.get("final_score", 0.0))
        judge_scores = r.get("judge_scores") or []
        analyses.append(
            {
                "final_score": final,
                "judge_scores": judge_scores,
                "our_total": our_report.get("total_score", 0.0),
                "our_dimensions": {
                    dim_id: float(d.get("score", 0.0))
                    for dim_id, d in (our_report.get("dimension_scores") or {}).items()
                },
                "our_penalty_count": len(our_report.get("penalties") or []),
                "our_evidence_density": sum(
                    len((d.get("evidence") or []))
                    for d in (our_report.get("dimension_scores") or {}).values()
                ),
            }
        )

    if not analyses:
        return {
            "project_id": project_id,
            "high_score_logic": ["有效真实评标数据为空（施组文本缺失）。"],
            "writing_guidance": ["请录入包含施组全文的真实评标记录。"],
            "sample_count": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "scoring_evolution": _empty_scoring_evolution(),
            "compilation_instructions": _default_compilation_instructions([]),
        }

    # 按真实最终分排序，取高分组与低分组
    analyses.sort(key=lambda x: x["final_score"], reverse=True)
    n = len(analyses)
    high_group = analyses[: max(1, n // 3)]
    low_group = analyses[-max(1, n // 3) :] if n >= 2 else []

    high_score_logic: List[str] = []
    writing_guidance: List[str] = []

    # 高分逻辑：高分组在本系统中的共性
    if high_group:
        avg_our_total_high = sum(x["our_total"] for x in high_group) / len(high_group)
        avg_final_high = sum(x["final_score"] for x in high_group) / len(high_group)
        high_score_logic.append(
            f"真实高分施组（本批平均最终分 {avg_final_high:.1f}）在本系统平均得分为 {avg_our_total_high:.1f}，可作为自检参考。"
        )
        # 各维度在高分组的平均
        dim_sums: Dict[str, float] = {}
        dim_counts: Dict[str, int] = {}
        for a in high_group:
            for dim_id, score in a["our_dimensions"].items():
                dim_sums[dim_id] = dim_sums.get(dim_id, 0.0) + score
                dim_counts[dim_id] = dim_counts.get(dim_id, 0) + 1
        dim_avg_high = {d: dim_sums[d] / dim_counts[d] for d in dim_sums}
        strong_dims = sorted(dim_avg_high.items(), key=lambda x: x[1], reverse=True)[:5]
        if strong_dims:
            dim_names = {
                "07": "重难点及危大工程",
                "09": "进度保障措施",
                "02": "安全生产",
                "03": "文明施工",
                "08": "质量保障体系",
            }
            strong_desc = "、".join(f"{dim_names.get(d, d)}({s:.1f})" for d, s in strong_dims)
            high_score_logic.append(f"高分施组在本系统中维度得分突出的有：{strong_desc}。")
        avg_penalty_high = sum(x["our_penalty_count"] for x in high_group) / len(high_group)
        high_score_logic.append(
            f"高分施组平均扣分项数量较少（约 {avg_penalty_high:.1f} 项），建议控制空泛承诺与缺动作要素的表述。"
        )

    # 低分对比
    if low_group and low_group != high_group:
        avg_final_low = sum(x["final_score"] for x in low_group) / len(low_group)
        avg_penalty_low = sum(x["our_penalty_count"] for x in low_group) / len(low_group)
        high_score_logic.append(
            f"低分施组（平均最终分 {avg_final_low:.1f}）在本系统扣分项较多（约 {avg_penalty_low:.1f} 项），编制时需避免同类问题。"
        )

    # 编制指导（可执行建议）
    writing_guidance.append("结合真实评标学习结果，编制施组时建议：")
    writing_guidance.append(
        "1. 强化重难点与危大工程（07）、进度保障（09）、安全与文明（02/03）等维度的量化表述：参数、频次、责任岗位、验收闭环。"
    )
    writing_guidance.append(
        "2. 避免空泛承诺，将「保证、严格落实」等替换为可量化指标与具体动作（报验、旁站、签认等）。"
    )
    writing_guidance.append(
        "3. 措施类描述至少包含：控制参数/阈值、执行频次、责任岗位、验收或检查动作中的两类以上。"
    )
    if high_group:
        writing_guidance.append(
            "4. 自检时可参考本系统评分：真实高分施组在本系统通常维度07/09/02/03得分较高且扣分项少，可先在本系统内优化后再提交正式评标。"
        )

    # A、评分系统进化：基于高/低分组在本系统各维度得分差异，建议维度权重，使预评分更贴近青天
    scoring_evolution = _build_scoring_evolution(high_group, low_group)

    # B、编制系统指令：可导出为编制施组时的系统指令，强制按此输出内容与图表
    compilation_instructions = _build_compilation_instructions(high_score_logic, writing_guidance)

    return {
        "project_id": project_id,
        "high_score_logic": high_score_logic,
        "writing_guidance": writing_guidance,
        "sample_count": len(analyses),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "scoring_evolution": scoring_evolution,
        "compilation_instructions": compilation_instructions,
    }


def _empty_scoring_evolution() -> Dict[str, Any]:
    return {
        "dimension_multipliers": {},
        "rationale": {},
        "goal": "使本系统预评分更贴近合肥市公共资源交易中心青天大模型评分；请先录入真实评标并执行学习进化。",
    }


def _build_scoring_evolution(
    high_group: List[Dict[str, Any]], low_group: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    根据高分组与低分组在本系统各维度得分差异，给出维度权重建议，
    用于评分时使本系统预评分更贴近青天最终分。
    """
    multipliers: Dict[str, float] = {}
    rationale: Dict[str, str] = {}
    goal = "使本系统预评分更贴近合肥市公共资源交易中心青天大模型评分。"

    if not high_group:
        return {"dimension_multipliers": {}, "rationale": {}, "goal": goal}

    dim_ids = set()
    for a in high_group + low_group:
        dim_ids.update((a.get("our_dimensions") or {}).keys())
    if not dim_ids:
        return {"dimension_multipliers": {}, "rationale": {}, "goal": goal}

    for dim_id in sorted(dim_ids):
        high_vals = [(a.get("our_dimensions") or {}).get(dim_id, 0.0) for a in high_group]
        low_vals = [(a.get("our_dimensions") or {}).get(dim_id, 0.0) for a in low_group]
        avg_high = sum(high_vals) / len(high_vals) if high_vals else 0.0
        avg_low = sum(low_vals) / len(low_vals) if low_vals else 0.0
        delta = avg_high - avg_low
        dim_name = (DIMENSIONS.get(dim_id) or {}).get("name", dim_id)
        if delta >= 0.8:
            multipliers[dim_id] = min(1.15, 1.0 + delta * 0.03)
            rationale[
                dim_id
            ] = f"高分施组在本系统「{dim_name}」得分明显更高，建议适当提高权重以贴近青天。"
        elif delta <= -0.8:
            multipliers[dim_id] = max(0.85, 1.0 + delta * 0.03)
            rationale[
                dim_id
            ] = f"低分施组在本系统「{dim_name}」得分反而偏高，建议略降权重以贴近青天。"
        else:
            multipliers[dim_id] = 1.0
            rationale[dim_id] = f"「{dim_name}」高/低分组差异不大，保持权重。"

    return {
        "dimension_multipliers": multipliers,
        "rationale": rationale,
        "goal": goal,
    }


def _default_compilation_instructions(guidance_items: List[str]) -> Dict[str, Any]:
    """无进化数据时的默认编制指令结构。"""
    return {
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
        "guidance_items": guidance_items or ["请先执行「学习进化」生成编制指导后再导出系统指令。"],
    }


def _build_compilation_instructions(
    high_score_logic: List[str], writing_guidance: List[str]
) -> Dict[str, Any]:
    """
    从高分逻辑与编制指导构建编制系统指令，可用于强制约束施组输出的内容与图表。
    """
    base = _default_compilation_instructions(writing_guidance)
    base["high_score_summary"] = high_score_logic
    return base
