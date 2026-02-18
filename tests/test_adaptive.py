"""Unit tests for app/engine/adaptive.py"""

from __future__ import annotations

from app.engine.adaptive import (
    apply_adaptive_patch,
    build_adaptive_patch,
    build_adaptive_suggestions,
)

# ============================================================
# Tests for build_adaptive_suggestions
# ============================================================


def test_build_adaptive_suggestions_empty():
    """空提交列表应返回空统计和空建议"""
    result = build_adaptive_suggestions([], {})
    assert result["penalty_stats"] == {}
    assert result["suggestions"] == []
    assert result["source"]["from_compare_endpoint"] is False
    assert result["source"]["from_insights_endpoint"] is False


def test_build_adaptive_suggestions_penalty_stats():
    """正确统计惩罚代码出现次数"""
    submissions = [
        {"report": {"penalties": [{"code": "P-ACTION-001"}, {"code": "P-EMPTY-001"}]}},
        {"report": {"penalties": [{"code": "P-ACTION-001"}]}},
        {"report": {"penalties": [{"code": "P-ACTION-001"}]}},
    ]
    result = build_adaptive_suggestions(submissions, {})
    assert result["penalty_stats"]["P-ACTION-001"] == 3
    assert result["penalty_stats"]["P-EMPTY-001"] == 1
    assert result["source"]["submissions_total"] == 3
    assert result["source"]["penalties_observed"] == 4


def test_build_adaptive_suggestions_action_suggestion():
    """P-ACTION-001 >= 3 时应生成强化措施建议"""
    submissions = [
        {"report": {"penalties": [{"code": "P-ACTION-001"}]}},
        {"report": {"penalties": [{"code": "P-ACTION-001"}]}},
        {"report": {"penalties": [{"code": "P-ACTION-001"}]}},
    ]
    result = build_adaptive_suggestions(submissions, {})
    suggestion_targets = [s["target"] for s in result["suggestions"]]
    assert "rule" in suggestion_targets


def test_build_adaptive_suggestions_empty_promise_suggestion():
    """P-EMPTY-001 >= 3 时应生成空泛承诺建议"""
    submissions = [
        {"report": {"penalties": [{"code": "P-EMPTY-001"}]}},
        {"report": {"penalties": [{"code": "P-EMPTY-001"}]}},
        {"report": {"penalties": [{"code": "P-EMPTY-001"}]}},
    ]
    result = build_adaptive_suggestions(submissions, {})
    suggestion_targets = [s["target"] for s in result["suggestions"]]
    assert "lexicon.empty_promises" in suggestion_targets


def test_build_adaptive_suggestions_sparse_dimension_keywords():
    """维度关键词少于3个时应生成补充建议"""
    lexicon = {
        "dimension_keywords": {
            "D001": ["词1", "词2"],  # 少于3个
            "D002": ["词1", "词2", "词3", "词4"],  # 足够
        }
    }
    result = build_adaptive_suggestions([], lexicon)
    suggestion_targets = [s["target"] for s in result["suggestions"]]
    assert "dimension_keywords.D001" in suggestion_targets
    assert "dimension_keywords.D002" not in suggestion_targets


def test_build_adaptive_suggestions_limited_to_10():
    """建议数量应限制在10个以内"""
    lexicon = {"dimension_keywords": {f"D{i:03d}": ["词1"] for i in range(20)}}
    result = build_adaptive_suggestions([], lexicon)
    assert len(result["suggestions"]) <= 10


def test_build_adaptive_suggestions_unknown_code():
    """缺少code字段的惩罚应使用UNKNOWN"""
    submissions = [
        {"report": {"penalties": [{}]}},
    ]
    result = build_adaptive_suggestions(submissions, {})
    assert result["penalty_stats"].get("UNKNOWN", 0) == 1


# ============================================================
# Tests for build_adaptive_patch
# ============================================================


def test_build_adaptive_patch_empty_penalty_stats():
    """空惩罚统计应返回空补丁（仅有hint）"""
    patch = build_adaptive_patch({}, {})
    assert patch["lexicon_additions"] == {}
    assert "hint" in patch["rubric_adjustments"]


def test_build_adaptive_patch_empty_promises_addition():
    """P-EMPTY-001 >= 3 时应添加空泛承诺词"""
    penalty_stats = {"P-EMPTY-001": 3}
    patch = build_adaptive_patch({}, penalty_stats)
    assert "empty_promises" in patch["lexicon_additions"]
    assert "keywords" in patch["lexicon_additions"]["empty_promises"]


def test_build_adaptive_patch_action_triggers_addition():
    """P-ACTION-001 >= 3 时应添加措施触发词"""
    penalty_stats = {"P-ACTION-001": 3}
    patch = build_adaptive_patch({}, penalty_stats)
    assert "action_triggers" in patch["lexicon_additions"]


def test_build_adaptive_patch_both_additions():
    """两种惩罚都 >= 3 时应同时添加两类词"""
    penalty_stats = {"P-EMPTY-001": 5, "P-ACTION-001": 4}
    patch = build_adaptive_patch({}, penalty_stats)
    assert "empty_promises" in patch["lexicon_additions"]
    assert "action_triggers" in patch["lexicon_additions"]


def test_build_adaptive_patch_below_threshold():
    """惩罚计数 < 3 时不应生成补丁"""
    penalty_stats = {"P-EMPTY-001": 2, "P-ACTION-001": 2}
    patch = build_adaptive_patch({}, penalty_stats)
    assert patch["lexicon_additions"] == {}


# ============================================================
# Tests for apply_adaptive_patch
# ============================================================


def test_apply_adaptive_patch_empty():
    """空补丁应返回原词库不变"""
    lexicon = {"existing": "data"}
    patch = {"lexicon_additions": {}}
    updated, changes = apply_adaptive_patch(lexicon, patch)
    assert updated == lexicon
    assert changes == []


def test_apply_adaptive_patch_empty_promises():
    """应正确添加空泛承诺词"""
    lexicon = {}
    patch = {"lexicon_additions": {"empty_promises": {"keywords": ["务必", "保证"]}}}
    updated, changes = apply_adaptive_patch(lexicon, patch)
    assert "empty_promises" in updated
    assert "务必" in updated["empty_promises"]["keywords"]
    assert "保证" in updated["empty_promises"]["keywords"]
    assert len(changes) == 2


def test_apply_adaptive_patch_action_triggers():
    """应正确添加措施触发词"""
    lexicon = {}
    patch = {"lexicon_additions": {"action_triggers": ["巡检", "复盘"]}}
    updated, changes = apply_adaptive_patch(lexicon, patch)
    assert "巡检" in updated["action_triggers"]
    assert "复盘" in updated["action_triggers"]
    assert len(changes) == 2


def test_apply_adaptive_patch_no_duplicates():
    """已存在的词不应重复添加"""
    lexicon = {
        "empty_promises": {"keywords": ["务必"]},
        "action_triggers": ["巡检"],
    }
    patch = {
        "lexicon_additions": {
            "empty_promises": {"keywords": ["务必", "保证"]},
            "action_triggers": ["巡检", "复盘"],
        }
    }
    updated, changes = apply_adaptive_patch(lexicon, patch)
    # 只添加新词
    assert updated["empty_promises"]["keywords"].count("务必") == 1
    assert updated["action_triggers"].count("巡检") == 1
    # changes 只记录新增
    assert "empty_promises+=务必" not in changes
    assert "empty_promises+=保证" in changes
    assert "action_triggers+=巡检" not in changes
    assert "action_triggers+=复盘" in changes


def test_apply_adaptive_patch_preserves_original():
    """应保留原词库的其他数据"""
    lexicon = {"other_key": "other_value", "action_triggers": ["原词"]}
    patch = {"lexicon_additions": {"action_triggers": ["新词"]}}
    updated, changes = apply_adaptive_patch(lexicon, patch)
    assert updated["other_key"] == "other_value"
    assert "原词" in updated["action_triggers"]
    assert "新词" in updated["action_triggers"]
