from __future__ import annotations

from typing import Any, Dict, List


def build_learning_profile(submissions: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not submissions:
        return {
            "dimension_multipliers": {},
            "rationale": {},
        }

    dimension_totals: Dict[str, float] = {}
    dimension_counts: Dict[str, int] = {}
    for s in submissions:
        report = s.get("report", {})
        for dim_id, dim in report.get("dimension_scores", {}).items():
            dimension_totals[dim_id] = dimension_totals.get(dim_id, 0.0) + float(
                dim.get("score", 0.0)
            )
            dimension_counts[dim_id] = dimension_counts.get(dim_id, 0) + 1

    dimension_avg = {
        dim_id: dimension_totals[dim_id] / dimension_counts[dim_id] for dim_id in dimension_totals
    }

    multipliers: Dict[str, float] = {}
    rationale: Dict[str, str] = {}
    for dim_id, avg in dimension_avg.items():
        if avg < 4.0:
            multipliers[dim_id] = 1.2
            rationale[dim_id] = "历史均分偏低，提升关注度。"
        elif avg > 8.0:
            multipliers[dim_id] = 0.9
            rationale[dim_id] = "历史均分偏高，适度回归均衡。"
        else:
            multipliers[dim_id] = 1.0
            rationale[dim_id] = "历史均分正常，保持权重。"

    return {
        "dimension_multipliers": multipliers,
        "rationale": rationale,
    }
