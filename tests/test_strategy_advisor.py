from __future__ import annotations

from app.engine.strategy_advisor import assess_shigong_leverage, recommend_target
from app.engine.tender_profile import load_all_profiles

YUNKANG = "2026BFFGZ50127"  # decisive
TONGLING = "2026AFWGZ50330"  # coupled
FEIDONG = "2026ADDGZ50033"  # gate
BAOHE = "2025BFBGZ50935"  # gate


def _p(tid):
    return load_all_profiles()[tid]


def test_leverage_levels_match_real_methods():
    a1 = assess_shigong_leverage(_p(YUNKANG))
    assert a1.leverage_level == "decisive" and a1.recommended_effort == "high"
    a2 = assess_shigong_leverage(_p(TONGLING))
    assert a2.leverage_level == "coupled" and a2.recommended_effort == "medium"
    a3 = assess_shigong_leverage(_p(FEIDONG))
    assert a3.leverage_level == "gate" and a3.recommended_effort == "low-gate"
    assert "门槛" in a3.decided_by or "入围" in a3.decided_by


def test_field_signal_flags_low_spread_as_gate_reality():
    # 案例3真实施组分高度聚集(86.3~86.8)，应被判为几乎无区分度
    field = [86.40, 86.46, 86.75, 86.50, 86.34, 86.80, 86.82, 86.58, 86.50]
    a = assess_shigong_leverage(_p(FEIDONG), field_shigong_scores=field)
    assert a.field_signal is not None
    assert "无区分度" in a.field_signal


def test_field_signal_flags_spread_as_worth_effort():
    # 案例1施组分有明显区分度(3.5~4.44)，应判为值得发力
    field = [4.44, 4.30, 3.51, 3.74, 4.22, 3.66, 4.38]
    a = assess_shigong_leverage(_p(YUNKANG), field_shigong_scores=field)
    assert a.field_signal is not None
    assert "值得" in a.field_signal


def test_identical_price_note():
    a = assess_shigong_leverage(
        _p(FEIDONG),
        field_price_scores=[85.0, 85.0, 85.0],
    )
    assert any("价格已无区分度" in r for r in a.rationale)


def test_recommend_target_is_field_top():
    field = [4.44, 4.39, 4.38, 4.38, 4.36, 4.35, 4.32]
    rec = recommend_target(_p(YUNKANG), field_shigong_scores=field, current_f=3.74)
    assert rec.target_f == 4.44  # 全场最前
    assert rec.target_band == "良好"
    assert abs(rec.current_gap - 0.70) < 1e-6  # 4.44 - 3.74
    assert "不必追绝对满分" in rec.ceiling_note


def test_recommend_target_without_field_is_none():
    rec = recommend_target(_p(YUNKANG))
    assert rec.target_f is None and rec.current_gap is None
