from __future__ import annotations

import json

from app.engine.shigong_diagnostics import diagnose_shigong
from app.engine.target_mapping import map_internal_to_target
from app.engine.tender_profile import (
    HardRedline,
    ScoreBand,
    ScoringItem,
    TenderProfile,
    load_all_profiles,
)
from app.engine.text_calibration import (
    CalibrationSample,
    TextCalibrationError,
    TextCalibrationReport,
    TextCalibrationSample,
    build_samples,
    calibrate_text_against_profile,
    calibrate_text_against_profile_from_file,
    composite_quality,
    feature_vector,
    fit_calibrator_1d,
    leave_one_out_mae,
    pearson_correlation,
    text_calibration_to_dict,
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


def _contract_profile():
    return TenderProfile(
        tender_id="synthetic-009a",
        tender_name="009A synthetic tender",
        version="v1",
        score_scale=10.0,
        scoring_items=(
            ScoringItem(
                item_id="deployment",
                name="施工部署",
                max_score=6.0,
                bands=(
                    ScoreBand(name="一般", lower=0.0, upper=3.0, lower_inclusive=True),
                    ScoreBand(name="优秀", lower=3.0, upper=6.0),
                ),
                evidence_requirements=("分区施工", "BIM"),
                legacy_dimension_refs=("custom_alpha_ref",),
            ),
            ScoringItem(
                item_id="safety",
                name="安全文明",
                max_score=4.0,
                bands=(
                    ScoreBand(name="一般", lower=0.0, upper=2.0, lower_inclusive=True),
                    ScoreBand(name="优秀", lower=2.0, upper=4.0),
                ),
                evidence_requirements=("扬尘监测",),
                legacy_dimension_refs=("non_01_16_ref",),
            ),
        ),
        hard_redlines=(
            HardRedline(
                redline_id="manual-check",
                description="只作为 synthetic 红线结构覆盖",
                action="manual_review",
                applies_to=("deployment",),
            ),
        ),
        legacy_dimension_refs=("profile_custom_ref",),
        source_note="synthetic only",
    )


def _aligned_samples():
    return [
        TextCalibrationSample(
            sample_id="s1",
            item_id="deployment",
            text="分区施工与 BIM 组织清晰",
            expected_score=5.0,
            observed_score=5.2,
            metadata={"source": "synthetic"},
        ),
        {
            "sample_id": "s2",
            "item_id": "safety",
            "text": "扬尘监测每日执行",
            "expected_score": 3.5,
            "observed_score": 3.4,
            "metadata": {"source": "synthetic"},
        },
    ]


def test_per_tender_text_calibration_builds_report_with_coverage_and_summary():
    report = calibrate_text_against_profile(
        _contract_profile(),
        document_text="本项目采用分区施工，结合 BIM 深化，并设置扬尘监测。",
        samples=_aligned_samples(),
    )

    assert isinstance(report, TextCalibrationReport)
    assert report.status == "pass"
    assert len(report.items) == 2
    assert report.coverage["item_count"] == 2
    assert report.coverage["sampled_item_count"] == 2
    assert report.summary["item_count"] == 2


def test_empty_document_records_text_missing_without_invalidating_profile():
    report = calibrate_text_against_profile(
        _contract_profile(),
        document_text="",
        samples=_aligned_samples(),
    )

    assert report.status == "warning"
    assert report.text_empty is True
    assert set(report.missing_text_item_ids) == {"deployment", "safety"}
    assert {item.calibration_status for item in report.items} == {"text_missing"}


def test_evidence_requirements_match_into_evidence_hits():
    report = calibrate_text_against_profile(
        _contract_profile(),
        document_text="现场按分区施工推进，BIM 统筹碰撞检查，扬尘监测每日记录。",
        samples=_aligned_samples(),
    )

    deployment = next(item for item in report.items if item.item_id == "deployment")
    assert deployment.text_present is True
    assert deployment.evidence_hits == ("分区施工", "BIM")


def test_samples_compute_delta_and_high_delta_items():
    report = calibrate_text_against_profile(
        _contract_profile(),
        document_text="分区施工 BIM 扬尘监测",
        samples=[
            {
                "sample_id": "s-high",
                "item_id": "deployment",
                "text": "分区施工 BIM",
                "expected_score": 5.8,
                "observed_score": 3.0,
                "metadata": {},
            },
            {
                "sample_id": "s-ok",
                "item_id": "safety",
                "text": "扬尘监测",
                "expected_score": 3.0,
                "observed_score": 3.1,
                "metadata": {},
            },
        ],
    )

    deployment = next(item for item in report.items if item.item_id == "deployment")
    assert deployment.expected_average == 5.8
    assert deployment.observed_average == 3.0
    assert deployment.score_delta == -2.8
    assert deployment.calibration_status == "over_claimed"
    assert report.high_delta_item_ids == ("deployment",)


def test_custom_legacy_dimension_refs_do_not_fail():
    report = calibrate_text_against_profile(
        _contract_profile(),
        document_text="分区施工 BIM 扬尘监测",
        samples=_aligned_samples(),
    )

    refs = {ref for item in report.items for ref in item.legacy_dimension_refs}
    assert "custom_alpha_ref" in refs
    assert "non_01_16_ref" in refs


def test_unknown_sample_item_id_raises_text_calibration_error():
    try:
        calibrate_text_against_profile(
            _contract_profile(),
            document_text="分区施工 BIM 扬尘监测",
            samples=[
                {
                    "sample_id": "bad",
                    "item_id": "unknown",
                    "text": "synthetic",
                    "expected_score": 1.0,
                    "observed_score": 1.0,
                    "metadata": {},
                }
            ],
        )
    except TextCalibrationError as exc:
        assert "未知 sample.item_id" in str(exc)
    else:
        raise AssertionError("expected TextCalibrationError")


def test_calibrate_text_against_profile_from_file_loads_temp_json(tmp_path):
    profile_path = tmp_path / "synthetic_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "tender_id": "synthetic-file",
                "tender_name": "file profile",
                "version": "v1",
                "score_scale": 2.0,
                "scoring_items": [
                    {
                        "item_id": "item-a",
                        "name": "资料完整性",
                        "max_score": 2.0,
                        "bands": [
                            {"band_id": "good", "label": "好", "min_score": 0, "max_score": 2}
                        ],
                        "evidence_requirements": ["资料清单"],
                        "legacy_dimension_refs": ["file_custom_ref"],
                    }
                ],
                "hard_redlines": [
                    {
                        "redline_id": "manual",
                        "description": "manual review only",
                        "action": "manual_review",
                        "applies_to": ["item-a"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = calibrate_text_against_profile_from_file(
        profile_path,
        document_text="资料清单完整",
        samples=[
            {
                "sample_id": "file-sample",
                "item_id": "item-a",
                "text": "资料清单完整",
                "expected_score": 1.8,
                "observed_score": 1.7,
                "metadata": {},
            }
        ],
    )

    assert report.tender_id == "synthetic-file"
    assert report.items[0].evidence_hits == ("资料清单",)


def test_text_calibration_to_dict_is_json_serializable():
    report = calibrate_text_against_profile(
        _contract_profile(),
        document_text="分区施工 BIM 扬尘监测",
        samples=_aligned_samples(),
    )
    payload = text_calibration_to_dict(report)

    assert payload["items"][0]["item_id"] == "deployment"
    json.dumps(payload, ensure_ascii=False)
