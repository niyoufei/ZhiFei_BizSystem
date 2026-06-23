from __future__ import annotations

import json

import pytest

from app.engine.shigong_diagnostics import (
    ShigongDiagnosticReport,
    ShigongDiagnosticsError,
    build_optimization_checklist,
    decompose_high_score_sample,
    diagnose_shigong,
    run_shigong_diagnostics,
    run_shigong_diagnostics_from_file,
    shigong_diagnostics_to_dict,
)
from app.engine.tender_profile import (
    HardRedline,
    ScoreBand,
    ScoringItem,
    TenderProfile,
    load_all_profiles,
)

YUNKANG = "2026BFFGZ50127"  # 6 项考量
PROJECT_TERMS = ["医院", "骨科", "改造", "不停诊"]

# 强施组：针对性 + 硬要素 + 覆盖全部6项考量
GOOD = (
    "本工程为骨科医院局部改造，针对工程项目整体理解清晰。重点难点为不停诊改造，"
    "措施为分区施工、动线隔离。新技术采用BIM建模，新工艺为装配式隔墙。"
    "工期总日历天数120天，质量目标为一次验收合格。人材机配置：项目经理1名，"
    "劳动力高峰80人，塔吊2台，每日巡检并旁站验收。安全文明生产：扬尘监测每日3次，"
    "PM2.5阈值控制，安全员驻场，隐蔽验收闭环。"
)
# 弱施组：全是空泛套话、无硬要素、覆盖少
WEAK = (
    "我公司将严格按照规范确保工程质量，加强管理，认真组织，精心施工，"
    "全面落实各项要求，高度重视，确保工期，积极努力完成本工程。"
)


def _p():
    return load_all_profiles()[YUNKANG]


def test_good_beats_weak_on_three_axes_and_coverage():
    p = _p()
    g = diagnose_shigong(GOOD, p, project_terms=PROJECT_TERMS)
    w = diagnose_shigong(WEAK, p, project_terms=PROJECT_TERMS)
    assert g.axes.landing > w.axes.landing
    assert g.axes.specificity > w.axes.specificity
    assert g.axes.conciseness > w.axes.conciseness
    assert g.coverage_rate > w.coverage_rate


def test_good_covers_all_considerations():
    g = diagnose_shigong(GOOD, _p(), project_terms=PROJECT_TERMS)
    assert g.coverage_rate == 1.0
    assert g.hard_element_count >= 5
    assert g.generic_phrase_count == 0


def test_weak_checklist_prioritises_missing_then_specificity():
    p = _p()
    w = diagnose_shigong(WEAK, p, project_terms=PROJECT_TERMS)
    checklist = build_optimization_checklist(w, p)
    assert checklist, "弱施组应产出优化项"
    assert checklist[0].issue == "MISSING"
    assert checklist[0].priority == 1
    issues = [i.issue for i in checklist]
    assert "LOW_SPECIFICITY" in issues
    # 按预期提分降序
    gains = [i.expected_gain for i in checklist]
    assert gains == sorted(gains, reverse=True)


def test_good_checklist_is_shorter_than_weak():
    p = _p()
    g = diagnose_shigong(GOOD, p, project_terms=PROJECT_TERMS)
    w = diagnose_shigong(WEAK, p, project_terms=PROJECT_TERMS)
    assert len(build_optimization_checklist(g, p)) < len(build_optimization_checklist(w, p))


def test_decompose_high_score_sample_lists_strengths():
    dec = decompose_high_score_sample(GOOD, _p(), project_terms=PROJECT_TERMS)
    assert dec.strengths
    assert "拆解" in dec.summary
    assert any("针对性" in s or "落地" in s or "覆盖" in s for s in dec.strengths)


def test_diagnose_is_deterministic():
    p = _p()
    a = diagnose_shigong(GOOD, p, project_terms=PROJECT_TERMS)
    b = diagnose_shigong(GOOD, p, project_terms=PROJECT_TERMS)
    assert a.to_dict() == b.to_dict()


def _contract_band(max_score: float) -> ScoreBand:
    return ScoreBand(
        name="合格",
        lower=0.0,
        upper=max_score,
        lower_inclusive=True,
        upper_inclusive=True,
        band_id="pass",
        label="合格",
    )


def _synthetic_contract_profile() -> TenderProfile:
    return TenderProfile(
        tender_id="synthetic-005a",
        tender_name="005A synthetic tender",
        version="v1",
        score_scale=10.0,
        scoring_items=(
            ScoringItem(
                item_id="deployment",
                name="施工部署",
                max_score=4.0,
                bands=(_contract_band(4.0),),
                evidence_requirements=("施工部署",),
                legacy_dimension_refs=("dim_01",),
            ),
            ScoringItem(
                item_id="quality",
                name="质量安全",
                max_score=3.0,
                bands=(_contract_band(3.0),),
                evidence_requirements=(),
                legacy_dimension_refs=(),
            ),
            ScoringItem(
                item_id="custom",
                name="自定义专项",
                max_score=3.0,
                bands=(_contract_band(3.0),),
                evidence_requirements=("专项方案",),
                legacy_dimension_refs=("custom-ref-alpha",),
            ),
        ),
        hard_redlines=(
            HardRedline(
                redline_id="hr_manual",
                description="不得提供备选施工组织设计",
                action="manual_review",
                applies_to=("deployment",),
            ),
        ),
        legacy_dimension_refs=("project-custom-ref",),
    )


def test_contract_diagnostics_report_preserves_preflight_mapping_signals():
    report = run_shigong_diagnostics(
        _synthetic_contract_profile(),
        document_text="施工部署包含专项方案。",
        provided_evidence={"deployment": ["施工部署"], "custom": ["专项方案"]},
    )

    assert isinstance(report, ShigongDiagnosticReport)
    assert report.preflight_status == "warning"
    assert report.coverage["item_count"] == 3
    assert report.summary["issue_count"] == len(report.issues)
    assert report.unmapped_item_ids == ("quality",)
    assert report.missing_evidence_item_ids == ("quality",)
    assert report.hard_redline_count == 1
    assert "custom_ref_alpha" in report.legacy_dimension_refs

    by_code = {issue.code: issue for issue in report.issues}
    assert by_code["UNMAPPED_SCORING_ITEM"].severity == "warning"
    assert by_code["MISSING_EVIDENCE_REQUIREMENT"].severity == "warning"
    assert by_code["HARD_REDLINE_DECLARED"].severity == "info"
    assert report.status == "warning"


def test_contract_diagnostics_flags_missing_evidence_and_empty_document_without_error():
    report = run_shigong_diagnostics(
        _synthetic_contract_profile(),
        document_text="",
        provided_evidence={"deployment": ["施工部署"]},
    )

    issues = [issue for issue in report.issues if issue.code == "EVIDENCE_NOT_PROVIDED"]
    assert [issue.item_id for issue in issues] == ["custom"]
    assert any(issue.code == "DOCUMENT_TEXT_EMPTY" for issue in report.issues)
    assert all(issue.severity != "error" for issue in report.issues)
    assert report.status == "warning"


def test_contract_diagnostics_rejects_invalid_profile():
    invalid = TenderProfile(
        tender_id="bad",
        tender_name="Bad",
        version="v1",
        score_scale=11.0,
        scoring_items=_synthetic_contract_profile().scoring_items,
    )

    with pytest.raises(ShigongDiagnosticsError):
        run_shigong_diagnostics(invalid)


def test_contract_diagnostics_from_file_and_to_dict_are_json_serializable(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "tender_id": "file-profile",
                "tender_name": "File profile",
                "version": "v1",
                "score_scale": 2,
                "scoring_items": [
                    {
                        "item_id": "plan",
                        "name": "施工计划",
                        "max_score": 2,
                        "bands": [
                            {
                                "band_id": "pass",
                                "label": "合格",
                                "min_score": 0,
                                "max_score": 2,
                            }
                        ],
                        "evidence_requirements": ["施工计划"],
                        "legacy_dimension_refs": ["custom-file-ref"],
                    }
                ],
                "hard_redlines": [
                    {
                        "redline_id": "hr_file",
                        "description": "人工复核红线",
                        "action": "manual_review",
                        "applies_to": ["plan"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_shigong_diagnostics_from_file(
        profile_path,
        document_text="施工计划",
        provided_evidence={"plan": ["施工计划"]},
    )
    payload = shigong_diagnostics_to_dict(report)

    assert payload["tender_id"] == "file-profile"
    assert payload["hard_redline_count"] == 1
    assert json.loads(json.dumps(payload, ensure_ascii=False))["summary"]["issue_count"] >= 1
