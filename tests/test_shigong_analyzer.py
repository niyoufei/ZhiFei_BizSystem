from __future__ import annotations

import json

import pytest

from app.engine.shigong_analyzer import (
    AnalyzerError,
    ShigongAnalysisReport,
    ShigongAnalyzerError,
    analyze_shigong,
    analyze_shigong_submission,
    analyze_shigong_submission_from_file,
    analyze_to_markdown,
    shigong_analysis_to_dict,
    shigong_analysis_to_markdown,
)
from app.engine.tender_profile import (
    HardRedline,
    ScoreBand,
    ScoringItem,
    TenderProfile,
    tender_profile_to_dict,
)

YUNKANG = "2026BFFGZ50127"
SAMPLE = (
    "本工程为骨科医院局部改造，针对工程项目整体理解清晰。重点难点为不停诊改造，措施为分区施工。"
    "新技术采用BIM，新工艺为装配式。确保工期120天，质量目标一次验收合格。人材机配置：项目经理1名，"
    "劳动力80人，塔吊2台，每日旁站验收。安全文明生产：扬尘监测每日3次，安全员驻场。"
) * 2
TERMS = ["医院", "骨科", "改造", "不停诊"]


def test_analyze_returns_full_report():
    r = analyze_shigong(SAMPLE, YUNKANG, project_terms=TERMS)
    assert r.profile.tender_id == YUNKANG
    assert 0.0 <= r.internal_composite <= 100.0
    assert r.prediction.band is not None
    assert r.leverage.leverage_level == "decisive"
    assert r.diagnosis.coverage_rate > 0


def test_markdown_has_key_sections():
    md = analyze_to_markdown(SAMPLE, YUNKANG, project_terms=TERMS)
    for header in ("# 施组分析报告", "## 预测", "## 硬红线预检", "## 三轴诊断", "## ROI 优化清单"):
        assert header in md


def test_field_scores_attach_target_and_percentile():
    field = [4.44, 4.30, 3.51, 3.66, 4.38]
    r = analyze_shigong(SAMPLE, YUNKANG, project_terms=TERMS, field_scores=field)
    assert r.target is not None and r.target.target_f == 4.44
    assert r.prediction.percentile_in_field is not None


def test_disqualify_shows_in_markdown():
    md = analyze_to_markdown(SAMPLE, YUNKANG, project_terms=TERMS, shigong_count=2)
    assert "判废" in md


def test_unknown_tender_raises():
    with pytest.raises(AnalyzerError):
        analyze_shigong(SAMPLE, "NO_SUCH_TENDER")


def test_deterministic():
    a = analyze_to_markdown(SAMPLE, YUNKANG, project_terms=TERMS)
    b = analyze_to_markdown(SAMPLE, YUNKANG, project_terms=TERMS)
    assert a == b


SYNTHETIC_DOCUMENT = (
    "施工方案采用BIM深化、流水段组织和样板引路。"
    "进度资源配置包含总进度计划、劳动力曲线和材料进场计划。"
    "质量安全章节设置安全员、扬尘监测、旁站验收和整改闭环。"
)
SYNTHETIC_EVIDENCE = {
    "item-method": {"BIM": True, "流水段": True},
    "item-schedule": {"总进度计划": True, "劳动力": True},
    "item-safety": {"安全员": True, "扬尘监测": True},
}


def _synthetic_band(item_id: str, max_score: float) -> ScoreBand:
    return ScoreBand(
        name=f"{item_id}-full",
        lower=0.0,
        upper=max_score,
        lower_inclusive=True,
        upper_inclusive=True,
        band_id=f"{item_id}-band-full",
        label="满足要求",
        description="synthetic only",
        triggers=("量化", "闭环"),
    )


def _synthetic_profile() -> TenderProfile:
    return TenderProfile(
        tender_id="SYN-010A",
        tender_name="010A synthetic 施工组织设计",
        version="2026.06",
        score_scale=10.0,
        scoring_items=(
            ScoringItem(
                item_id="item-method",
                name="施工方案",
                max_score=4.0,
                bands=(_synthetic_band("item-method", 4.0),),
                evidence_requirements=("BIM", "流水段"),
                legacy_dimension_refs=("dim_01", "custom_quality_ref"),
            ),
            ScoringItem(
                item_id="item-schedule",
                name="进度资源",
                max_score=3.0,
                bands=(_synthetic_band("item-schedule", 3.0),),
                evidence_requirements=("总进度计划", "劳动力"),
                legacy_dimension_refs=("bespoke_schedule_ref",),
            ),
            ScoringItem(
                item_id="item-safety",
                name="质量安全",
                max_score=3.0,
                bands=(_synthetic_band("item-safety", 3.0),),
                evidence_requirements=("安全员", "扬尘监测"),
                legacy_dimension_refs=("dim_16", "custom_safety_ref"),
            ),
        ),
        hard_redlines=(
            HardRedline(
                redline_id="redline-alt-plan",
                description="不得提交备选施工方案",
                action="fail",
                applies_to=("item-method",),
            ),
        ),
        legacy_dimension_refs=("project_specific_ref",),
        source_note="synthetic contract test only",
    )


def _judge_scores() -> list[dict]:
    return [
        {
            "judge_id": "j1",
            "item_scores": {"item-method": 3.6, "item-schedule": 2.5, "item-safety": 2.7},
        },
        {
            "judge_id": "j2",
            "item_scores": {"item-method": 3.4, "item-schedule": 2.7, "item-safety": 2.8},
        },
    ]


def _calibration_samples() -> list[dict]:
    return [
        {
            "sample_id": "sample-001",
            "item_id": "item-method",
            "text": "BIM深化和流水段组织",
            "expected_score": 3.2,
            "observed_score": 3.4,
        }
    ]


def test_010a_synthetic_profile_generates_report():
    report = analyze_shigong_submission(
        _synthetic_profile(),
        document_text=SYNTHETIC_DOCUMENT,
        provided_evidence=SYNTHETIC_EVIDENCE,
    )

    assert isinstance(report, ShigongAnalysisReport)
    assert report.tender_id == "SYN-010A"
    assert report.tender_name == "010A synthetic 施工组织设计"
    assert report.status in {"pass", "warning", "action_required"}


def test_010a_report_contains_all_subreports():
    report = analyze_shigong_submission(
        _synthetic_profile(),
        document_text=SYNTHETIC_DOCUMENT,
        provided_evidence=SYNTHETIC_EVIDENCE,
    )
    payload = shigong_analysis_to_dict(report)

    for key in (
        "target_mapping",
        "preflight",
        "diagnostics",
        "compilation_advice",
        "strategy_recommendations",
        "text_calibration",
    ):
        assert payload[key]


def test_010a_empty_judge_scores_do_not_fail():
    report = analyze_shigong_submission(
        _synthetic_profile(),
        document_text=SYNTHETIC_DOCUMENT,
        provided_evidence=SYNTHETIC_EVIDENCE,
        judge_scores=[],
    )

    assert report.judge_aggregation is None
    assert "未提供评委评分数据" in shigong_analysis_to_markdown(report)


def test_010a_non_empty_judge_scores_and_calibration_samples_are_used():
    report = analyze_shigong_submission(
        _synthetic_profile(),
        document_text=SYNTHETIC_DOCUMENT,
        provided_evidence=SYNTHETIC_EVIDENCE,
        judge_scores=_judge_scores(),
        calibration_samples=_calibration_samples(),
    )
    payload = shigong_analysis_to_dict(report)

    assert payload["judge_aggregation"]["judge_count"] == 2
    assert payload["text_calibration"]["sample_count"] == 1
    assert payload["text_calibration"]["judge_summary"]["judge_count"] == 2


def test_010a_hard_redline_is_advisory_and_custom_legacy_ref_is_stable():
    report = analyze_shigong_submission(
        _synthetic_profile(),
        document_text=SYNTHETIC_DOCUMENT,
        provided_evidence=SYNTHETIC_EVIDENCE,
    )
    payload = shigong_analysis_to_dict(report)

    assert payload["target_mapping"]["coverage"]["hard_redline_count"] == 1
    assert payload["summary"]["does_not_disqualify"] is True
    assert payload["summary"]["affects_score"] is False
    assert "custom_quality_ref" in payload["target_mapping"]["coverage"]["legacy_dimension_refs"]
    assert "project_specific_ref" in payload["target_mapping"]["coverage"]["legacy_dimension_refs"]


def test_010a_invalid_profile_raises_shigong_analyzer_error():
    invalid_profile = TenderProfile(
        tender_id="BAD-010A",
        tender_name="invalid",
        version="bad",
        score_scale=10.0,
        scoring_items=(
            ScoringItem(
                item_id="item-only",
                name="single item",
                max_score=1.0,
                bands=(_synthetic_band("item-only", 1.0),),
                evidence_requirements=("证据",),
                legacy_dimension_refs=("custom_ref",),
            ),
        ),
    )

    with pytest.raises(ShigongAnalyzerError):
        analyze_shigong_submission(invalid_profile)


def test_010a_from_file_loads_temp_profile(tmp_path):
    profile = _synthetic_profile()
    profile_path = tmp_path / "synthetic-profile.json"
    profile_path.write_text(
        json.dumps(tender_profile_to_dict(profile), ensure_ascii=False),
        encoding="utf-8",
    )

    report = analyze_shigong_submission_from_file(
        profile_path,
        document_text=SYNTHETIC_DOCUMENT,
        provided_evidence=SYNTHETIC_EVIDENCE,
    )

    assert report.tender_id == profile.tender_id


def test_010a_to_dict_is_json_serializable_and_markdown_has_fixed_sections():
    report = analyze_shigong_submission(
        _synthetic_profile(),
        document_text=SYNTHETIC_DOCUMENT,
        provided_evidence=SYNTHETIC_EVIDENCE,
    )

    json.dumps(shigong_analysis_to_dict(report), ensure_ascii=False)
    markdown = shigong_analysis_to_markdown(report)
    for header in (
        "## 项目基本信息",
        "## 按标评分项覆盖",
        "## 预检结论",
        "## 施工诊断",
        "## 编制建议",
        "## 高分策略",
        "## 评委评分聚合",
        "## 文本校准",
    ):
        assert header in markdown
