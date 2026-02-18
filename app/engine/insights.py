from __future__ import annotations

from typing import Any, Dict, List


def build_project_insights(submissions: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not submissions:
        return {
            "weakest_dims": [],
            "frequent_penalties": [],
            "recommendations": [],
            "dimension_avg": {},
        }

    dimension_totals: Dict[str, float] = {}
    dimension_counts: Dict[str, int] = {}
    penalty_stats: Dict[str, int] = {}

    for s in submissions:
        report = s.get("report", {})
        for dim_id, dim in report.get("dimension_scores", {}).items():
            dimension_totals[dim_id] = dimension_totals.get(dim_id, 0.0) + float(
                dim.get("score", 0.0)
            )
            dimension_counts[dim_id] = dimension_counts.get(dim_id, 0) + 1
        for p in report.get("penalties", []):
            code = p.get("code", "UNKNOWN")
            penalty_stats[code] = penalty_stats.get(code, 0) + 1

    dimension_avg = {
        dim_id: round(dimension_totals[dim_id] / dimension_counts[dim_id], 2)
        for dim_id in dimension_totals
    }

    weakest_dims = sorted(
        [{"dimension": dim_id, "avg_score": dimension_avg[dim_id]} for dim_id in dimension_avg],
        key=lambda x: x["avg_score"],
    )[:5]

    frequent_penalties = sorted(
        [{"code": code, "count": count} for code, count in penalty_stats.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]

    recommendations: List[Dict[str, str]] = []
    for item in frequent_penalties:
        if item["code"] == "P-ACTION-001":
            recommendations.append(
                {
                    "reason": "措施落地要素缺失高频",
                    "action": "统一补齐参数/频次/验收/责任四要素，形成可核查闭环。",
                }
            )
        elif item["code"] == "P-EMPTY-001":
            recommendations.append(
                {
                    "reason": "空泛承诺出现频繁",
                    "action": "将承诺句替换为量化指标+责任岗位+验收动作。",
                }
            )

    for dim in weakest_dims:
        recommendations.append(
            {
                "reason": f"维度{dim['dimension']}平均分偏低",
                "action": "补充关键流程/参数/验收节点，提高可执行性与证据密度。",
            }
        )

    return {
        "weakest_dims": weakest_dims,
        "frequent_penalties": frequent_penalties,
        "recommendations": recommendations[:8],
        "dimension_avg": dimension_avg,
    }
