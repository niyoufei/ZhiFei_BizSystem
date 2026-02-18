from __future__ import annotations

from typing import Any, Dict, List, Tuple


def build_adaptive_suggestions(
    submissions: List[Dict[str, Any]], lexicon: Dict[str, Any]
) -> Dict[str, Any]:
    penalty_stats: Dict[str, int] = {}
    for s in submissions:
        report = s.get("report", {})
        for p in report.get("penalties", []):
            code = p.get("code", "UNKNOWN")
            penalty_stats[code] = penalty_stats.get(code, 0) + 1

    suggestions: List[Dict[str, str]] = []
    if penalty_stats.get("P-ACTION-001", 0) >= 3:
        suggestions.append(
            {
                "target": "rule",
                "action": "强化措施类语句的落地要素判定，要求至少两类要素。",
                "expected_effect": "减少措施虚化扣分。",
            }
        )
    if penalty_stats.get("P-EMPTY-001", 0) >= 3:
        suggestions.append(
            {
                "target": "lexicon.empty_promises",
                "action": "扩展空泛承诺词表，并增加量化/验收的反向约束。",
                "expected_effect": "减少空话承诺扣分。",
            }
        )

    # 维度命中稀疏建议
    dim_keywords = lexicon.get("dimension_keywords", {})
    for dim_id, words in dim_keywords.items():
        if len(words) < 3:
            suggestions.append(
                {
                    "target": f"dimension_keywords.{dim_id}",
                    "action": "补充该维度关键词同义词与行业术语。",
                    "expected_effect": "提升维度命中率。",
                }
            )

    source = {
        "built_from": [
            "submissions.report.penalties",
            "lexicon.dimension_keywords",
        ],
        "from_compare_endpoint": False,
        "from_insights_endpoint": False,
        "submissions_total": len(submissions),
        "submissions_with_report": sum(1 for s in submissions if isinstance(s.get("report"), dict)),
        "penalties_observed": int(sum(penalty_stats.values())),
        "note": "当前自适应建议来自历史评分报告与词库统计，不直接读取对比/洞察接口输出。",
    }

    return {
        "penalty_stats": penalty_stats,
        "suggestions": suggestions[:10],
        "source": source,
    }


def build_adaptive_patch(lexicon: Dict[str, Any], penalty_stats: Dict[str, int]) -> Dict[str, Any]:
    patch: Dict[str, Any] = {"lexicon_additions": {}, "rubric_adjustments": {}}

    if penalty_stats.get("P-EMPTY-001", 0) >= 3:
        extra_empty = ["务必", "保证", "全面落实", "切实执行", "严格落实"]
        patch["lexicon_additions"]["empty_promises"] = {"keywords": extra_empty}

    if penalty_stats.get("P-ACTION-001", 0) >= 3:
        extra_action = ["巡检", "复盘", "复核", "旁站", "交底"]
        patch["lexicon_additions"]["action_triggers"] = extra_action

    # 如果高优维度平均偏低，可提示提高权重
    patch["rubric_adjustments"]["hint"] = "若高优维度长期偏低，可提高其权重或下调阈值。"
    return patch


def apply_adaptive_patch(
    lexicon: Dict[str, Any], patch: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    updated = dict(lexicon)
    changes: List[str] = []

    additions = patch.get("lexicon_additions", {})
    if "empty_promises" in additions:
        updated.setdefault("empty_promises", {})
        updated["empty_promises"].setdefault("keywords", [])
        for kw in additions["empty_promises"].get("keywords", []):
            if kw not in updated["empty_promises"]["keywords"]:
                updated["empty_promises"]["keywords"].append(kw)
                changes.append(f"empty_promises+={kw}")

    if "action_triggers" in additions:
        updated.setdefault("action_triggers", [])
        for kw in additions.get("action_triggers", []):
            if kw not in updated["action_triggers"]:
                updated["action_triggers"].append(kw)
                changes.append(f"action_triggers+={kw}")

    return updated, changes


def apply_rubric_patch(
    rubric: Dict[str, Any], patch_rubric: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    """
    将补丁中的 rubric_adjustments 合并到当前 rubric，不覆盖已有关键结构，仅追加可追溯的调整。
    返回 (更新后的 rubric, 变更描述列表)。
    """
    import copy

    updated = copy.deepcopy(rubric)
    changes: List[str] = []

    if not patch_rubric:
        return updated, changes

    # 自适应提示：追加到 adaptive_hints，便于审计
    if "hint" in patch_rubric:
        updated.setdefault("adaptive_hints", [])
        if not isinstance(updated["adaptive_hints"], list):
            updated["adaptive_hints"] = []
        hint = patch_rubric["hint"]
        if hint and hint not in updated["adaptive_hints"]:
            updated["adaptive_hints"].append(hint)
            changes.append("rubric.adaptive_hints+=hint")

    # 可选：按维度微调权重（补丁中有 dimension_weights 时）
    dim_weights = patch_rubric.get("dimension_weights")
    if isinstance(dim_weights, dict):
        dims = updated.get("dimensions") or {}
        for dim_id, adj in dim_weights.items():
            if dim_id not in dims or not isinstance(adj, dict):
                continue
            for key, value in adj.items():
                if key in ("weight", "per_hit", "suggestion_threshold", "max_score"):
                    dims[dim_id][key] = value
                    changes.append(f"rubric.dimensions.{dim_id}.{key}={value}")

    return updated, changes
