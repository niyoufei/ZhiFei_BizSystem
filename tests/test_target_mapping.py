from __future__ import annotations

import pytest

from app.engine.target_mapping import (
    TargetMappingError,
    band_gap,
    build_target_mapping,
    collect_legacy_dimension_refs,
    map_internal_to_target,
    normalize_legacy_dimension_ref,
    target_mapping_to_dict,
)
from app.engine.tender_profile import (
    HardRedline,
    ScoreBand,
    ScoringItem,
    TenderProfile,
    load_all_profiles,
)

YUNKANG = "2026BFFGZ50127"  # 综合评估法 / 5分
FEIDONG = "2026ADDGZ50033"  # 技术评分合理价格法 / 100分


def _p(tid):
    return load_all_profiles()[tid]


def test_baseline_linear_map_5_scale():
    p = _p(YUNKANG)
    pred = map_internal_to_target(88.8, p)  # 88.8/100*5 = 4.44（=长春实测顶值）
    assert abs(pred.f_score - 4.44) < 1e-6
    assert pred.band == "良好"
    assert abs(pred.normalized - 0.888) < 1e-6
    assert pred.is_top_band is False
    assert pred.next_band == "优秀"
    assert abs(pred.gap_to_next_band - 0.06) < 1e-6  # 距优秀(4.5)还差0.06
    assert pred.method == "baseline_linear"


def test_baseline_linear_map_100_scale():
    p = _p(FEIDONG)
    pred = map_internal_to_target(86.82, p)  # 100分制下 f=86.82
    assert abs(pred.f_score - 86.82) < 1e-6
    assert pred.band == "良好"
    assert pred.next_band == "优秀"
    assert abs(pred.gap_to_next_band - 3.18) < 1e-6  # 距优秀(90)还差3.18


def test_percentile_in_field_attached():
    p = _p(YUNKANG)
    field = [4.44, 4.39, 4.38, 4.38, 4.36, 4.35, 4.32]
    pred = map_internal_to_target(88.8, p, field_scores=field)  # f=4.44=全场最高
    assert pred.percentile_in_field == 1.0
    pred_low = map_internal_to_target(87.2, p, field_scores=field)  # f=4.36
    assert pred_low.percentile_in_field is not None
    assert 0.0 < pred_low.percentile_in_field < 1.0


def test_calibrator_hook_overrides_baseline():
    p = _p(YUNKANG)

    def fake_calibrator(internal: float, profile) -> float:
        return 4.5  # 假装校准器直接给优秀线

    pred = map_internal_to_target(50.0, p, calibrator=fake_calibrator)
    assert pred.method == "calibrated"
    assert pred.f_score == 4.5
    assert pred.band == "优秀"
    assert pred.is_top_band is True
    assert pred.next_band is None
    assert pred.gap_to_next_band is None


def test_clamp_and_zero():
    p = _p(YUNKANG)
    assert map_internal_to_target(0.0, p).f_score == 0.0
    # 超满分钳制
    over = map_internal_to_target(200.0, p)
    assert over.f_score == 5.0 and over.band == "优秀"


def test_band_gap_at_top_is_none():
    p = _p(YUNKANG)
    name, gap = band_gap(p, 4.8)  # 已在优秀
    assert name is None and gap is None


def _synthetic_profile() -> TenderProfile:
    return TenderProfile(
        tender_id="synthetic-003",
        tender_name="Synthetic Tender 003",
        version="v003",
        score_scale=100.0,
        scoring_items=(
            ScoringItem(
                item_id="item_plan",
                name="施工部署",
                max_score=60.0,
                bands=(
                    ScoreBand(
                        name="basic",
                        lower=0.0,
                        upper=36.0,
                        lower_inclusive=True,
                        label="基础",
                        triggers=("泛泛描述",),
                    ),
                    ScoreBand(
                        name="excellent",
                        lower=36.0,
                        upper=60.0,
                        label="优秀",
                        triggers=("节点清晰", "资源匹配"),
                    ),
                ),
                evidence_requirements=("进度节点", "资源配置"),
                legacy_dimension_refs=(
                    "01",
                    "dim_01",
                    "DIM-01",
                    "dimension_01",
                    "DIM-16",
                    "Custom Ref!!",
                ),
            ),
            ScoringItem(
                item_id="item_safety",
                name="安全文明",
                max_score=40.0,
                bands=(
                    ScoreBand(
                        name="qualified",
                        lower=0.0,
                        upper=40.0,
                        lower_inclusive=True,
                        label="合格",
                        triggers=("措施闭环",),
                    ),
                ),
                evidence_requirements=("安全措施",),
                legacy_dimension_refs=(),
            ),
        ),
        hard_redlines=(
            HardRedline(
                redline_id="rl_multiple",
                description="不得提交多套施工组织设计",
                action="fail",
                applies_to=("document",),
            ),
        ),
        legacy_dimension_refs=("02", "DIM-16", "Custom Ref!!"),
    )


def test_build_target_mapping_from_synthetic_profile():
    mapping = build_target_mapping(_synthetic_profile())

    assert mapping.tender_id == "synthetic-003"
    assert mapping.tender_name == "Synthetic Tender 003"
    assert mapping.version == "v003"
    assert mapping.score_scale == 100.0
    assert len(mapping.targets) == 2

    first = mapping.targets[0]
    assert first.item_id == "item_plan"
    assert first.max_score == 60.0
    assert first.evidence_requirements == ("进度节点", "资源配置")
    assert first.band_count == 2
    assert first.band_labels == ("基础", "优秀")
    assert first.band_triggers == ("泛泛描述", "节点清晰", "资源匹配")

    assert mapping.coverage.item_count == 2
    assert mapping.coverage.total_score == 100.0
    assert mapping.coverage.mapped_item_count == 1


def test_legacy_refs_are_normalized_deduped_and_sorted():
    mapping = build_target_mapping(_synthetic_profile())

    assert normalize_legacy_dimension_ref("01") == "dim_01"
    assert normalize_legacy_dimension_ref("dim_01") == "dim_01"
    assert normalize_legacy_dimension_ref("DIM-01") == "dim_01"
    assert normalize_legacy_dimension_ref("dimension_01") == "dim_01"
    assert normalize_legacy_dimension_ref("02") == "dim_02"
    assert normalize_legacy_dimension_ref("DIM-16") == "dim_16"
    assert normalize_legacy_dimension_ref("Custom Ref!!") == "custom_ref"

    assert mapping.targets[0].legacy_dimension_refs == (
        "custom_ref",
        "dim_01",
        "dim_16",
    )
    assert mapping.coverage.legacy_dimension_refs == (
        "custom_ref",
        "dim_01",
        "dim_02",
        "dim_16",
    )
    assert collect_legacy_dimension_refs(_synthetic_profile()) == [
        "custom_ref",
        "dim_01",
        "dim_02",
        "dim_16",
    ]


def test_unmapped_item_ids_include_items_without_legacy_refs():
    mapping = build_target_mapping(_synthetic_profile())

    assert mapping.targets[1].legacy_dimension_refs == ()
    assert mapping.coverage.unmapped_item_ids == ("item_safety",)


def test_hard_redline_is_carried_without_decision_logic():
    mapping = build_target_mapping(_synthetic_profile())

    assert mapping.hard_redlines == (
        {
            "redline_id": "rl_multiple",
            "description": "不得提交多套施工组织设计",
            "action": "fail",
            "applies_to": ["document"],
        },
    )
    assert mapping.coverage.hard_redline_count == 1


def test_target_mapping_to_dict_is_json_friendly():
    data = target_mapping_to_dict(build_target_mapping(_synthetic_profile()))

    assert data["targets"][0]["legacy_dimension_refs"] == [
        "custom_ref",
        "dim_01",
        "dim_16",
    ]
    assert data["hard_redlines"][0] == {
        "redline_id": "rl_multiple",
        "description": "不得提交多套施工组织设计",
        "action": "fail",
        "applies_to": ["document"],
    }
    assert data["coverage"]["unmapped_item_ids"] == ["item_safety"]


def test_invalid_profile_raises_target_mapping_error():
    invalid = TenderProfile(
        tender_id="synthetic-003-invalid",
        tender_name="Synthetic Tender 003 Invalid",
        version="v003",
        score_scale=100.0,
        scoring_items=(
            ScoringItem(
                item_id="item_plan",
                name="施工部署",
                max_score=90.0,
            ),
        ),
    )

    with pytest.raises(TargetMappingError, match="target mapping"):
        build_target_mapping(invalid)
