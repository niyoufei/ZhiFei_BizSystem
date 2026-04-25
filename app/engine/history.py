"""评分历史记录和趋势分析模块"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.storage import (
    append_score_history,
    get_project_score_history,
    load_score_history,
)


def record_score(
    project_id: str,
    submission_id: str,
    filename: str,
    total_score: float,
    dimension_scores: Dict[str, float],
    penalty_count: int,
) -> Dict[str, Any]:
    """记录一次评分到历史"""
    entry = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "submission_id": submission_id,
        "filename": filename,
        "total_score": total_score,
        "dimension_scores": dimension_scores,
        "penalty_count": penalty_count,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    append_score_history(entry)
    return entry


def _normalize_history_entry(
    entry: Dict[str, Any] | object,
    *,
    project_id_fallback: str,
) -> Dict[str, Any]:
    normalized = dict(entry) if isinstance(entry, dict) else {}
    normalized["id"] = str(normalized.get("id") or "").strip()
    normalized["project_id"] = str(
        normalized.get("project_id") or project_id_fallback or ""
    ).strip()
    normalized["submission_id"] = str(normalized.get("submission_id") or "").strip()
    normalized["filename"] = str(normalized.get("filename") or "").strip()
    try:
        normalized["total_score"] = float(normalized.get("total_score") or 0.0)
    except (TypeError, ValueError):
        normalized["total_score"] = 0.0
    raw_dimension_scores = normalized.get("dimension_scores")
    if isinstance(raw_dimension_scores, dict):
        normalized_dimension_scores: Dict[str, float] = {}
        for key, value in raw_dimension_scores.items():
            clean_key = str(key or "").strip()
            if not clean_key:
                continue
            try:
                normalized_dimension_scores[clean_key] = float(value or 0.0)
            except (TypeError, ValueError):
                normalized_dimension_scores[clean_key] = 0.0
        normalized["dimension_scores"] = normalized_dimension_scores
    else:
        normalized["dimension_scores"] = {}
    try:
        normalized["penalty_count"] = int(float(normalized.get("penalty_count") or 0))
    except (TypeError, ValueError):
        normalized["penalty_count"] = 0
    normalized["created_at"] = (
        str(normalized.get("created_at") or "").strip() or datetime.now(timezone.utc).isoformat()
    )
    return normalized


def get_history(project_id: str) -> Dict[str, Any]:
    """获取项目的评分历史"""
    entries = [
        _normalize_history_entry(entry, project_id_fallback=project_id)
        for entry in get_project_score_history(project_id)
    ]
    return {
        "project_id": project_id,
        "entries": entries,
        "total_count": len(entries),
    }


def calculate_trend(scores: List[float]) -> str:
    """计算趋势方向"""
    if len(scores) < 2:
        return "stable"

    # 使用简单线性趋势：比较前半和后半的平均值
    mid = len(scores) // 2
    if mid == 0:
        mid = 1

    first_half_avg = sum(scores[:mid]) / mid
    second_half_avg = sum(scores[mid:]) / (len(scores) - mid)

    diff = second_half_avg - first_half_avg
    threshold = 2.0  # 2分以上的变化算显著

    if diff > threshold:
        return "improving"
    elif diff < -threshold:
        return "declining"
    return "stable"


def analyze_trend(
    project_id: str, dimension_names: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """分析项目评分趋势"""
    entries = [
        _normalize_history_entry(entry, project_id_fallback=project_id)
        for entry in get_project_score_history(project_id)
    ]

    if not entries:
        return {
            "project_id": project_id,
            "total_submissions": 0,
            "score_history": [],
            "overall_trend": "stable",
            "avg_score": 0.0,
            "best_score": 0.0,
            "worst_score": 0.0,
            "latest_score": 0.0,
            "score_improvement": 0.0,
            "dimension_trends": [],
            "penalty_trend": [],
            "recommendations": ["暂无历史数据，请先提交施工组织设计进行评分"],
        }

    # 基础统计
    scores = [e["total_score"] for e in entries]
    avg_score = sum(scores) / len(scores)
    best_score = max(scores)
    worst_score = min(scores)
    latest_score = scores[-1]
    first_score = scores[0]
    score_improvement = latest_score - first_score

    # 构建历史序列
    score_history = [
        {
            "submission_id": e["submission_id"],
            "filename": e["filename"],
            "total_score": e["total_score"],
            "created_at": e["created_at"],
        }
        for e in entries
    ]

    # 整体趋势
    overall_trend = calculate_trend(scores)

    # 维度趋势分析
    dimension_trends = []
    if entries and "dimension_scores" in entries[0]:
        # 收集所有维度ID
        all_dims = set()
        for e in entries:
            all_dims.update(e.get("dimension_scores", {}).keys())

        for dim_id in sorted(all_dims):
            dim_scores = [e.get("dimension_scores", {}).get(dim_id, 0.0) for e in entries]
            dim_name = dimension_names.get(dim_id, dim_id) if dimension_names else dim_id

            if dim_scores:
                dimension_trends.append(
                    {
                        "dimension_id": dim_id,
                        "dimension_name": dim_name,
                        "scores": dim_scores,
                        "trend": calculate_trend(dim_scores),
                        "avg_score": sum(dim_scores) / len(dim_scores),
                        "latest_score": dim_scores[-1],
                    }
                )

    # 扣分项趋势
    penalty_trend = [e.get("penalty_count", 0) for e in entries]

    # 生成建议
    recommendations = generate_recommendations(
        overall_trend=overall_trend,
        score_improvement=score_improvement,
        dimension_trends=dimension_trends,
        penalty_trend=penalty_trend,
        latest_score=latest_score,
    )

    return {
        "project_id": project_id,
        "total_submissions": len(entries),
        "score_history": score_history,
        "overall_trend": overall_trend,
        "avg_score": round(avg_score, 2),
        "best_score": best_score,
        "worst_score": worst_score,
        "latest_score": latest_score,
        "score_improvement": round(score_improvement, 2),
        "dimension_trends": dimension_trends,
        "penalty_trend": penalty_trend,
        "recommendations": recommendations,
    }


def generate_recommendations(
    overall_trend: str,
    score_improvement: float,
    dimension_trends: List[Dict[str, Any]],
    penalty_trend: List[int],
    latest_score: float,
) -> List[str]:
    """基于趋势生成改进建议"""
    recommendations = []

    # 整体趋势建议
    if overall_trend == "improving":
        recommendations.append("评分整体呈上升趋势，继续保持当前改进方向")
    elif overall_trend == "declining":
        recommendations.append("评分呈下降趋势，建议回顾近期修改并对比历史高分版本")
    else:
        if latest_score < 70:
            recommendations.append("评分较低且无明显改善，建议重点关注扣分项")
        elif latest_score < 85:
            recommendations.append("评分稳定，可通过针对性优化突破瓶颈")

    # 维度趋势建议
    declining_dims = [d for d in dimension_trends if d["trend"] == "declining"]
    if declining_dims:
        dim_names = [d["dimension_name"] for d in declining_dims[:3]]
        recommendations.append(f"以下维度分数下降，需重点关注：{', '.join(dim_names)}")

    # 找出最弱维度
    if dimension_trends:
        weakest = min(dimension_trends, key=lambda x: x["latest_score"])
        if weakest["latest_score"] < weakest["avg_score"]:
            recommendations.append(
                f"【{weakest['dimension_name']}】是当前最弱维度，"
                f"最新得分 {weakest['latest_score']:.1f} 低于平均 {weakest['avg_score']:.1f}"
            )

    # 扣分项趋势建议
    if len(penalty_trend) >= 2:
        recent_penalty = penalty_trend[-1]
        prev_penalty = penalty_trend[-2]
        if recent_penalty > prev_penalty:
            recommendations.append(
                f"扣分项数量增加（{prev_penalty}→{recent_penalty}），注意避免空承诺和缺少行动项"
            )
        elif recent_penalty < prev_penalty:
            recommendations.append(
                f"扣分项数量减少（{prev_penalty}→{recent_penalty}），改进效果明显"
            )

    # 确保至少有一条建议
    if not recommendations:
        recommendations.append("继续保持，定期复盘评分报告以持续改进")

    return recommendations


def get_all_history() -> List[Dict[str, Any]]:
    """获取所有历史记录"""
    return load_score_history()
