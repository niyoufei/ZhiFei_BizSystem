from __future__ import annotations

import json

import pytest

from app.engine.tender_preflight import (
    TenderPreflightError,
    check_tender_hard_lines,
    run_tender_preflight,
    run_tender_preflight_from_file,
    tender_preflight_to_dict,
)
from app.engine.tender_profile import (
    HardRedline,
    ScoreBand,
    ScoringItem,
    TenderProfile,
    load_all_profiles,
)

YUNKANG = "2026BFFGZ50127"  # require_site_plan=False, 有 format_hints
FEIDONG = "2026ADDGZ50033"  # require_site_plan=True

# 一段足够长（>200字）的施组正文，避免误触 NOT_PROVIDED
LONG = (
    "本工程为医院局部改造项目，施工组织设计涵盖工程概况、主要施工方法、拟投入主要物资计划、"
    "施工机械设备配置、劳动力安排计划、确保工程质量的技术组织措施、确保安全生产的技术组织措施、"
    "确保工期的技术组织措施、确保文明施工的技术组织措施等内容，措施力求针对性与可行性。"
) * 2


def _p(tid):
    return load_all_profiles()[tid]


def _find(result, code):
    return next((x for x in result.findings if x.code == code), None)


def test_site_plan_required_missing_then_present():
    p = _p(FEIDONG)
    r_missing = check_tender_hard_lines(LONG, p)  # LONG 不含总平面图
    sp = _find(r_missing, "SITE_PLAN_REQUIRED")
    assert sp is not None and sp.status == "fail" and sp.severity == "warn"

    r_present = check_tender_hard_lines(LONG + "\n施工总平面布置图见附图。", p)
    assert _find(r_present, "SITE_PLAN_REQUIRED").status == "pass"


def test_multiple_shigong_disqualifies():
    p = _p(FEIDONG)
    r = check_tender_hard_lines(LONG, p, shigong_count=2)
    ms = _find(r, "MULTIPLE_SHIGONG")
    assert ms.status == "fail" and ms.severity == "disqualify"
    assert r.disqualified is True
    r1 = check_tender_hard_lines(LONG, p, shigong_count=1)
    assert r1.disqualified is False


def test_not_provided_is_zero_not_disqualify():
    p = _p(FEIDONG)
    r = check_tender_hard_lines("太短了", p)
    npf = _find(r, "NOT_PROVIDED")
    assert npf.status == "fail" and npf.severity == "zero"
    assert r.disqualified is False  # 本项0分，但不等于废标


def test_page_limit_over_pass_unknown():
    p = _p(FEIDONG)
    assert _find(check_tender_hard_lines(LONG, p, page_count=60), "PAGE_LIMIT").status == "fail"
    assert _find(check_tender_hard_lines(LONG, p, page_count=40), "PAGE_LIMIT").status == "pass"
    assert _find(check_tender_hard_lines(LONG, p), "PAGE_LIMIT").status == "unknown"


def test_case1_no_site_plan_rule_but_has_format_advisory():
    p = _p(YUNKANG)
    r = check_tender_hard_lines(LONG, p)
    assert _find(r, "SITE_PLAN_REQUIRED") is None  # 本标不要求总平面图
    fmt = _find(r, "FORMAT")
    assert fmt is not None and fmt.status == "advisory"


def _score_band(max_score):
    return ScoreBand(
        name="baseline",
        lower=0.0,
        upper=float(max_score),
        lower_inclusive=True,
        upper_inclusive=True,
        band_id="baseline",
        label="Baseline",
    )


def _contract_profile(score_scale=100.0):
    return TenderProfile(
        tender_id="synthetic-004",
        tender_name="Synthetic Tender",
        version="v1",
        score_scale=score_scale,
        scoring_items=(
            ScoringItem(
                item_id="method",
                name="Construction method",
                max_score=40.0,
                bands=(_score_band(40.0),),
                evidence_requirements=("method statement",),
                legacy_dimension_refs=("dim_01",),
            ),
            ScoringItem(
                item_id="resources",
                name="Resources",
                max_score=30.0,
                bands=(_score_band(30.0),),
            ),
            ScoringItem(
                item_id="safety",
                name="Safety",
                max_score=30.0,
                bands=(_score_band(30.0),),
                evidence_requirements=("safety plan",),
                legacy_dimension_refs=("custom/safety-ref",),
            ),
        ),
        hard_redlines=(
            HardRedline(
                redline_id="single-plan",
                description="Only one construction organization design is allowed",
                action="fail",
                applies_to=("method",),
            ),
        ),
        legacy_dimension_refs=("dim_02",),
        source_note="synthetic",
    )


def _issue(report, code, item_id=None):
    return next(
        (
            issue
            for issue in report.issues
            if issue.code == code and (item_id is None or issue.item_id == item_id)
        ),
        None,
    )


def test_contract_profile_generates_preflight_report_with_mapping_coverage_and_summary():
    report = run_tender_preflight(_contract_profile())

    assert report.status == "warning"
    assert report.mapping.tender_id == "synthetic-004"
    assert report.coverage["item_count"] == 3
    assert report.coverage["mapped_item_count"] == 2
    assert report.summary["warning_count"] == 2
    assert report.summary["hard_redline_count"] == 1


def test_contract_preflight_reports_unmapped_missing_evidence_and_hard_redline_as_info():
    report = run_tender_preflight(_contract_profile())

    unmapped = _issue(report, "UNMAPPED_SCORING_ITEM", "resources")
    missing_evidence = _issue(report, "MISSING_EVIDENCE_REQUIREMENT", "resources")
    hard_redline = _issue(report, "HARD_REDLINE_DECLARED", "method")

    assert unmapped is not None and unmapped.severity == "warning"
    assert missing_evidence is not None and missing_evidence.severity == "warning"
    assert hard_redline is not None and hard_redline.severity == "info"
    assert hard_redline.details["action"] == "fail"
    assert report.hard_redline_count == 1


def test_contract_preflight_keeps_custom_legacy_ref_and_serializes_to_json():
    report = run_tender_preflight(_contract_profile())
    payload = tender_preflight_to_dict(report)

    assert "custom_safety_ref" in report.legacy_dimension_refs
    assert "mapping" in payload and "coverage" in payload and "summary" in payload
    json.dumps(payload, ensure_ascii=False)


def test_contract_preflight_invalid_profile_raises_tender_preflight_error():
    with pytest.raises(TenderPreflightError):
        run_tender_preflight(_contract_profile(score_scale=99.0))


def test_contract_preflight_from_file_loads_synthetic_json(tmp_path):
    path = tmp_path / "synthetic_profile.json"
    path.write_text(
        json.dumps(
            {
                "tender_id": "synthetic-file",
                "tender_name": "Synthetic File Tender",
                "version": "v1",
                "score_scale": 10,
                "scoring_items": [
                    {
                        "item_id": "plan",
                        "name": "Plan",
                        "max_score": 10,
                        "bands": [
                            {
                                "band_id": "base",
                                "label": "Base",
                                "min_score": 0,
                                "max_score": 10,
                            }
                        ],
                        "evidence_requirements": ["plan evidence"],
                        "legacy_dimension_refs": ["dim_03"],
                    }
                ],
                "hard_redlines": [],
                "legacy_dimension_refs": [],
                "source_note": "synthetic",
            }
        ),
        encoding="utf-8",
    )

    report = run_tender_preflight_from_file(path)

    assert report.tender_id == "synthetic-file"
    assert report.status == "pass"
    assert report.evidence_requirement_count == 1
