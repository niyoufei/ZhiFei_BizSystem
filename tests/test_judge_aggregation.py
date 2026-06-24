from __future__ import annotations

import json

import pytest

from app.engine.judge_aggregation import (
    JudgeAggregationError,
    JudgeScoreInput,
    aggregate_judge_scores,
    aggregate_judge_scores_from_dict,
    aggregate_judges,
    aggregate_judges_trimmed,
    analyze_dispersion,
    judge_aggregation_to_dict,
    predicted_mean_from_base,
)
from app.engine.tender_profile import HardRedline, ScoreBand, ScoringItem, TenderProfile

# 真实评委分（评标一览表实测）
CHANGCHUN = [4.33, 4.36, 4.35, 4.36, 4.8]  # 运康骨科 长春建设 -> 4.44
LUJIN = [82.06, 82.09, 82.06, 82.08, 82.12, 82.09, 82.18]  # 包河道路 庐金(7评委) -> 82.10
KAIYANG = [4.18, 4.21, 4.23, 4.21, 4.67]  # 运康骨科 凯扬 -> 4.30


def test_simple_mean_matches_real_results():
    assert aggregate_judges(CHANGCHUN) == 4.44
    assert aggregate_judges(LUJIN) == 82.10
    assert aggregate_judges(KAIYANG) == 4.30


def test_trimmed_differs_from_simple_mean():
    # 真实用简单平均；去最高最低会得到不同结果，证明不能用 trimmed 替代
    assert aggregate_judges_trimmed(CHANGCHUN) != aggregate_judges(CHANGCHUN)


def test_dispersion_separates_base_and_attention():
    d = analyze_dispersion(CHANGCHUN)  # tol=0.1
    assert d.mean == 4.44
    assert d.base_estimate == 4.36  # 中位数=基准（抗宽松评委）
    assert d.most_deviant_index == 4  # 评委5（4.8）调了关注度
    assert d.most_deviant_value == 4.8
    assert d.n_consensus == 4  # 4 位未调（与基准在0.1内）
    assert d.n_adjusted == 1


def test_dispersion_tight_cluster_high_consensus():
    d = analyze_dispersion(LUJIN)
    assert d.n == 7
    assert d.spread <= 0.2  # 7 位高度一致
    assert d.most_deviant_value == 82.18


def test_predicted_mean_from_base():
    # 多数评委不调关注度 -> 均分≈基准
    assert predicted_mean_from_base(4.36) == 4.36
    # 给出各评委偏移则按均值重建
    assert predicted_mean_from_base(4.36, adjustment_offsets=[0, 0, 0, 0, 0.44]) == 4.45


def test_empty_raises():
    with pytest.raises(ValueError):
        aggregate_judges([])
    with pytest.raises(ValueError):
        analyze_dispersion([])


def _synthetic_profile() -> TenderProfile:
    return TenderProfile(
        tender_id="synthetic-008a",
        tender_name="008A Synthetic Tender",
        version="v1",
        score_scale=10.0,
        scoring_items=(
            ScoringItem(
                item_id="plan",
                name="施工方案",
                max_score=6.0,
                bands=(
                    ScoreBand(
                        name="good",
                        lower=4.0,
                        upper=6.0,
                        lower_inclusive=True,
                        upper_inclusive=True,
                        band_id="good",
                        label="良好",
                        triggers=("针对性",),
                    ),
                ),
                evidence_requirements=("施工部署",),
                legacy_dimension_refs=("dim_01", "custom_plan_ref"),
            ),
            ScoringItem(
                item_id="organization",
                name="组织管理",
                max_score=4.0,
                bands=(
                    ScoreBand(
                        name="qualified",
                        lower=2.0,
                        upper=4.0,
                        lower_inclusive=True,
                        upper_inclusive=True,
                        band_id="qualified",
                        label="合格",
                    ),
                ),
                evidence_requirements=("资源配置",),
                legacy_dimension_refs=("custom_org_ref",),
            ),
        ),
        hard_redlines=(
            HardRedline(
                redline_id="redline_manual",
                description="仅记录，不在聚合节点裁决",
                action="manual_review",
                applies_to=("plan",),
            ),
        ),
        legacy_dimension_refs=("custom_profile_ref",),
        source_note="synthetic only",
    )


def test_008a_aggregate_judge_scores_builds_report_from_synthetic_profile():
    report = aggregate_judge_scores(
        _synthetic_profile(),
        [
            JudgeScoreInput("judge-1", {"plan": 5.0, "organization": 3.0}),
            JudgeScoreInput("judge-2", {"plan": 4.5, "organization": 3.5}),
            JudgeScoreInput("judge-3", {"plan": 4.0, "organization": 3.0}),
        ],
    )

    assert report.status == "pass"
    assert report.tender_id == "synthetic-008a"
    assert report.judge_count == 3
    assert report.missing_item_scores == ()
    assert report.unknown_item_ids == ()
    assert report.high_dispersion_item_ids == ()

    plan = next(item for item in report.item_aggregations if item.item_id == "plan")
    assert plan.average_score == pytest.approx(4.5)
    assert plan.median_score == pytest.approx(4.5)
    assert plan.min_score == pytest.approx(4.0)
    assert plan.max_observed_score == pytest.approx(5.0)
    assert plan.score_spread == pytest.approx(1.0)
    assert plan.normalized_score == pytest.approx(0.75)
    assert plan.legacy_dimension_refs == ("dim_01", "custom_plan_ref")

    assert report.total_average_score == pytest.approx(7.6667)
    assert report.total_normalized_score == pytest.approx(0.7667)
    assert report.coverage["provided_score_count"] == 6
    assert report.coverage["coverage_ratio"] == pytest.approx(1.0)


def test_008a_missing_scores_are_recorded_structurally():
    report = aggregate_judge_scores(
        _synthetic_profile(),
        [
            {"judge_id": "judge-1", "item_scores": {"plan": 5.0}},
            {"judge_id": "judge-2", "item_scores": {}},
        ],
    )

    assert report.status == "warning"
    assert report.missing_item_scores == ("organization",)
    plan = next(item for item in report.item_aggregations if item.item_id == "plan")
    organization = next(item for item in report.item_aggregations if item.item_id == "organization")
    assert plan.missing_judge_ids == ("judge-2",)
    assert organization.missing_judge_ids == ("judge-1", "judge-2")


def test_008a_unknown_item_id_is_warning_not_silent_drop():
    report = aggregate_judge_scores(
        _synthetic_profile(),
        [
            {"judge_id": "judge-1", "item_scores": {"plan": 5.0, "unknown": 1.0}},
            {"judge_id": "judge-2", "item_scores": {"plan": 4.5, "organization": 3.0}},
        ],
    )

    assert report.status == "warning"
    assert report.unknown_item_ids == ("unknown",)


def test_008a_score_above_item_max_raises_error():
    with pytest.raises(JudgeAggregationError):
        aggregate_judge_scores(
            _synthetic_profile(),
            [{"judge_id": "judge-1", "item_scores": {"plan": 6.1}}],
        )


def test_008a_high_dispersion_item_ids_are_reported():
    report = aggregate_judge_scores(
        _synthetic_profile(),
        [
            {"judge_id": "judge-1", "item_scores": {"plan": 6.0, "organization": 3.0}},
            {"judge_id": "judge-2", "item_scores": {"plan": 1.0, "organization": 3.0}},
        ],
    )

    assert report.status == "warning"
    assert report.high_dispersion_item_ids == ("plan",)


def test_008a_custom_legacy_refs_do_not_fail():
    report = aggregate_judge_scores(
        _synthetic_profile(),
        [{"judge_id": "judge-1", "item_scores": {"plan": 5.0, "organization": 3.0}}],
    )

    organization = next(item for item in report.item_aggregations if item.item_id == "organization")
    assert organization.legacy_dimension_refs == ("custom_org_ref",)


def test_008a_aggregate_judge_scores_from_dict_payload():
    report = aggregate_judge_scores_from_dict(
        _synthetic_profile(),
        {
            "judge_scores": [
                {"judge_id": "judge-1", "item_scores": {"plan": 5.0, "organization": 3.0}},
                {"judge_id": "judge-2", "item_scores": {"plan": 4.0, "organization": 3.0}},
            ]
        },
    )

    assert report.status == "pass"
    assert report.total_average_score == pytest.approx(7.5)


def test_008a_judge_aggregation_to_dict_is_json_serializable():
    report = aggregate_judge_scores(
        _synthetic_profile(),
        [{"judge_id": "judge-1", "item_scores": {"plan": 5.0, "organization": 3.0}}],
    )

    payload = judge_aggregation_to_dict(report)
    assert payload["item_aggregations"][0]["item_id"] == "plan"
    json.dumps(payload, ensure_ascii=False)
