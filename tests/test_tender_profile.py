from __future__ import annotations

import pytest

from app.engine.tender_profile import (
    EVAL_METHOD_COMPREHENSIVE,
    EVAL_METHOD_TECH_REASONABLE_PRICE,
    QUALITY_AXES,
    WEIGHT_TYPES,
    TenderProfileError,
    field_target_score,
    load_all_profiles,
    percentile_in_field,
    profile_from_dict,
    summarize_field,
)

# 四个真实标的招标编号
YUNKANG = "2026BFFGZ50127"  # 运康骨科医院（综合评估法 / 5分）
TONGLING = "2026AFWGZ50330"  # 铜陵市立医院（综合评估法 / 5分）
FEIDONG = "2026ADDGZ50033"  # 肥东幼儿园（技术评分合理价格法 / 100分）
BAOHE = "2025BFBGZ50935"  # 包河道路（技术评分合理价格法 / 100分 / 7评委）


def _profiles():
    return load_all_profiles()


def test_all_four_real_profiles_load():
    profs = _profiles()
    for tid in (YUNKANG, TONGLING, FEIDONG, BAOHE):
        assert tid in profs, f"缺少标书配置: {tid}"
    assert profs[YUNKANG].eval_method == EVAL_METHOD_COMPREHENSIVE
    assert profs[TONGLING].eval_method == EVAL_METHOD_COMPREHENSIVE
    assert profs[FEIDONG].eval_method == EVAL_METHOD_TECH_REASONABLE_PRICE
    assert profs[BAOHE].eval_method == EVAL_METHOD_TECH_REASONABLE_PRICE
    for p in profs.values():
        assert tuple(p.quality_axes) == QUALITY_AXES
        assert p.shigong_weight_type in WEIGHT_TYPES


def test_score_scale_composition_and_judges():
    profs = _profiles()
    assert profs[YUNKANG].shigong_max_score == 5
    assert profs[FEIDONG].shigong_max_score == 100
    assert profs[BAOHE].judge_count_observed == 7
    assert profs[YUNKANG].score_composition["报价文件"] == 85
    # 施组胜负含金量分类（决定性 / 联动 / 门槛）
    assert profs[YUNKANG].shigong_weight_type == "decisive"
    assert profs[TONGLING].shigong_weight_type == "coupled"
    assert profs[FEIDONG].shigong_weight_type == "gate"


def test_considerations_vary_by_tender():
    profs = _profiles()
    c_yunkang = "；".join(profs[YUNKANG].considerations)
    c_tongling = "；".join(profs[TONGLING].considerations)
    # 运康含重点难点、不含绿建；铜陵相反
    assert "重点难点" in c_yunkang
    assert "绿色建筑" in c_tongling and "重点难点" not in c_tongling
    # 幼儿园/道路 10 项且含施工总平面图
    assert len(profs[FEIDONG].considerations) == 10
    assert len(profs[BAOHE].considerations) == 10
    assert any("总平面" in x for x in profs[FEIDONG].considerations)


def test_band_boundaries_case1_comprehensive_5():
    p = _profiles()[YUNKANG]
    assert p.band_of(0) is None  # 0 分=未提供/不得分，落在所有档外
    assert p.band_of(3.0) == "一般"  # 0<F<=3
    assert p.band_of(3.01) == "良好"
    assert p.band_of(4.44) == "良好"  # 长春（第一中标候选人）
    assert p.band_of(4.5) == "优秀"  # 4.5<=F
    assert p.band_of(5.0) == "优秀"


def test_band_boundaries_case2_three_bands():
    p = _profiles()[TONGLING]
    assert p.band_of(1.99) == "较差"
    assert p.band_of(2.0) == "一般"  # 2<=F<3.5
    assert p.band_of(3.49) == "一般"
    assert p.band_of(3.5) == "优秀"  # 3.5<=F
    assert p.band_of(4.34) == "优秀"  # 华南


def test_band_boundaries_case3_hundred_scale():
    p = _profiles()[FEIDONG]
    assert p.band_of(60.0) == "一般"  # 0<F<=60
    assert p.band_of(86.82) == "良好"  # 先华（全场最高）
    assert p.band_of(89.999) == "良好"
    assert p.band_of(90.0) == "优秀"


def test_normalization_makes_scales_comparable():
    profs = _profiles()
    n_yunkang = profs[YUNKANG].normalize(4.44)  # 0.888
    n_feidong = profs[FEIDONG].normalize(86.82)  # 0.8682
    assert abs(n_yunkang - 0.888) < 1e-6
    assert abs(n_feidong - 0.8682) < 1e-6
    # 归一化后同处 0..1 刻度，可直接跨标比较
    assert n_yunkang > n_feidong


def test_no_bidder_reached_excellent_band_in_real_data():
    """四标实测顶值均未进优秀档——目标应为「良好档全场最前」，而非满分/优秀。"""
    profs = _profiles()
    assert not profs[YUNKANG].is_top_band(4.44)
    assert not profs[FEIDONG].is_top_band(86.82)
    assert not profs[BAOHE].is_top_band(82.1)
    assert profs[YUNKANG].top_band_name() == "优秀"
    assert profs[FEIDONG].top_band_name() == "优秀"


def test_percentile_and_dynamic_field_target():
    # 案例1入围者施组分（报价均满85，施组定胜负）
    field = [4.44, 4.39, 4.38, 4.38, 4.36, 4.35, 4.32]
    assert percentile_in_field(4.44, field) == 1.0  # 长春全场第一
    assert field_target_score(field) == 4.44  # 动态目标=全场最高
    assert 0.0 < percentile_in_field(4.36, field) < 1.0
    summary = summarize_field(field)
    assert summary["max"] == 4.44 and summary["count"] == 7


def test_validate_rejects_bad_profiles():
    # 缺必填字段
    with pytest.raises(TenderProfileError):
        profile_from_dict({"tender_id": "x"})
    # 非法评标办法
    with pytest.raises(TenderProfileError):
        profile_from_dict(
            {
                "tender_id": "x",
                "tender_name": "x",
                "eval_method": "拍脑袋法",
                "shigong_max_score": 5,
                "bands": [{"name": "a", "lower": 0, "upper": 5}],
                "considerations": ["x"],
                "shigong_weight_type": "decisive",
            }
        )
    # 非法施组胜负权重类型
    with pytest.raises(TenderProfileError):
        profile_from_dict(
            {
                "tender_id": "x",
                "tender_name": "x",
                "eval_method": EVAL_METHOD_COMPREHENSIVE,
                "shigong_max_score": 5,
                "bands": [{"name": "a", "lower": 0, "upper": 5}],
                "considerations": ["x"],
                "shigong_weight_type": "瞎猜",
            }
        )
    # 满分非正
    with pytest.raises(TenderProfileError):
        profile_from_dict(
            {
                "tender_id": "x",
                "tender_name": "x",
                "eval_method": EVAL_METHOD_COMPREHENSIVE,
                "shigong_max_score": 0,
                "bands": [{"name": "a", "lower": 0, "upper": 5}],
                "considerations": ["x"],
                "shigong_weight_type": "decisive",
            }
        )
