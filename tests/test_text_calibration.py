from __future__ import annotations

from app.engine.shigong_diagnostics import diagnose_shigong
from app.engine.target_mapping import map_internal_to_target
from app.engine.tender_profile import load_all_profiles
from app.engine.text_calibration import (
    CalibrationSample,
    build_samples,
    composite_quality,
    feature_vector,
    fit_calibrator_1d,
    leave_one_out_mae,
    pearson_correlation,
)

YUNKANG = "2026BFFGZ50127"
GOOD = (
    "本工程为骨科医院局部改造，针对工程项目整体理解清晰。重点难点为不停诊改造，"
    "措施为分区施工。新技术采用BIM，新工艺为装配式。工期120天，质量目标一次验收合格。"
    "人材机配置：项目经理1名，劳动力80人，塔吊2台，每日旁站验收。安全文明生产：扬尘监测每日3次，安全员驻场。"
)
WEAK = "我公司将严格按照规范确保质量，加强管理，认真组织，精心施工，确保工期，积极努力。"


def _p():
    return load_all_profiles()[YUNKANG]


def test_fit_recovers_linear_relation():
    samples = [(x, 0.005 * x + 0.3) for x in (10, 20, 30, 40, 50)]
    cal = fit_calibrator_1d(samples)
    assert abs(cal.slope - 0.005) < 1e-6
    assert abs(cal.intercept - 0.3) < 1e-6
    assert abs(cal.predict_normalized(60) - 0.6) < 1e-6


def test_fit_degenerate_and_single():
    flat = fit_calibrator_1d([(50, 0.8), (50, 0.9)])  # x 无方差
    assert flat.slope == 0.0 and abs(flat.intercept - 0.85) < 1e-9
    one = fit_calibrator_1d([(40, 0.7)])
    assert one.slope == 0.0 and abs(one.intercept - 0.7) < 1e-9


def test_loo_model_beats_baseline_when_baseline_wrong():
    # 真实关系远离基线 x/100 -> 校准器应显著更优
    samples = [(x, 0.004 * x + 0.4) for x in (10, 25, 40, 55, 70, 85)]
    res = leave_one_out_mae(samples)
    assert res["insufficient"] == 0.0
    assert res["model_mae"] < res["baseline_mae"]
    assert res["gate_pass"] == 1.0


def test_loo_insufficient_when_too_few():
    assert leave_one_out_mae([(10, 0.5), (20, 0.6)]).get("insufficient") == 1.0


def test_composite_monotonic_good_beats_weak():
    p = _p()
    assert composite_quality(GOOD, p, project_terms=["医院", "骨科"]) > composite_quality(WEAK, p)


def test_calibrator_plugs_into_target_mapping():
    p = _p()
    cal = fit_calibrator_1d([(0.0, 0.0), (100.0, 1.0)])  # 近似恒等
    pred = map_internal_to_target(88.8, p, calibrator=cal.as_target_calibrator())
    assert pred.method == "calibrated"
    assert abs(pred.f_score - 4.44) < 0.01
    assert pred.band == "良好"


def test_pearson_correlation_signals_predictive_power():
    pos = [(x, 0.004 * x + 0.4) for x in (10, 25, 40, 55, 70)]
    assert abs(pearson_correlation(pos) - 1.0) < 1e-6  # 完全正相关
    neg = [(x, -0.004 * x + 0.9) for x in (10, 25, 40, 55, 70)]
    assert abs(pearson_correlation(neg) + 1.0) < 1e-6  # 完全负相关
    flat = [(50, 0.8), (50, 0.85), (50, 0.9)]
    assert pearson_correlation(flat) == 0.0  # 无方差→无相关


def test_feature_vector_and_build_samples():
    p = _p()
    fv = feature_vector(diagnose_shigong(GOOD, p, project_terms=["医院"]))
    for k in ("specificity", "landing", "conciseness", "coverage_rate", "hard_per_k"):
        assert k in fv
    pts = build_samples([CalibrationSample(text=GOOD, profile=p, real_f=4.44)])
    assert len(pts) == 1
    composite, norm_real = pts[0]
    assert abs(norm_real - 0.888) < 1e-6  # 4.44/5
    assert 0.0 <= composite <= 100.0
