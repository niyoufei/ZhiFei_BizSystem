from __future__ import annotations

from app.engine.target_mapping import band_gap, map_internal_to_target
from app.engine.tender_profile import load_all_profiles

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
