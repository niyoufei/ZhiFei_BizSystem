from __future__ import annotations

import json

import pytest

from app.engine.compilation_advisor import (
    CompilationAdvisorError,
    build_compilation_advice,
    build_compilation_advice_from_file,
    build_gap_explanation_prompt,
    build_rewrite_prompt,
    compilation_advisor_to_dict,
    explain_gap,
    get_compilation_llm_backend,
    suggest_rewrite,
)
from app.engine.shigong_diagnostics import diagnose_shigong
from app.engine.tender_profile import (
    HardRedline,
    ScoreBand,
    ScoringItem,
    TenderProfile,
    load_all_profiles,
)

YUNKANG = "2026BFFGZ50127"
SAMPLE = "本工程为骨科医院局部改造，针对工程项目整体理解清晰，质量目标合格，工期可控。"


def _p():
    return load_all_profiles()[YUNKANG]


def _diag():
    return diagnose_shigong(SAMPLE, _p(), project_terms=["医院", "骨科"])


def test_backend_defaults_to_rules():
    assert get_compilation_llm_backend() == "rules"


def test_explain_gap_underestimate_rules():
    s = explain_gap(_diag(), predicted_f=3.9, real_f=4.44, profile=_p())
    assert "低估" in s.text
    assert s.source == "rules"
    assert s.affects_score is False and s.preview_only is True and s.no_write is True


def test_explain_gap_overestimate_rules():
    s = explain_gap(_diag(), predicted_f=4.2, real_f=3.5, profile=_p())
    assert "高估" in s.text


def test_explain_gap_uses_injected_llm():
    s = explain_gap(
        _diag(), predicted_f=3.9, real_f=4.44, profile=_p(), llm=lambda p: "LLM写的解释"
    )
    assert s.text == "LLM写的解释"
    assert s.source == "llm:injected"
    assert s.affects_score is False  # LLM 路径同样永不影响评分


def test_suggest_rewrite_rules_injects_terms_and_placeholders():
    s = suggest_rewrite(
        "严格按照规范确保工程质量，加强管理。",
        _p(),
        project_terms=["骨科医院"],
    )
    assert "骨科医院" in s.text
    assert "验收" in s.text and ("责任岗位" in s.text or "频次" in s.text)
    assert "严格按照" in s.text and "确保" in s.text  # 指出应删的空泛
    assert s.source == "rules"
    assert s.affects_score is False and s.preview_only is True


def test_suggest_rewrite_uses_injected_llm():
    s = suggest_rewrite("确保安全", _p(), project_terms=["医院"], llm=lambda p: "改写结果")
    assert s.text == "改写结果" and s.source == "llm:injected"
    assert s.affects_score is False


def test_prompts_are_nonempty_and_contextual():
    gp = build_gap_explanation_prompt(_diag(), 3.9, 4.44, _p())
    assert "真实青天F分" in gp and "3.9" in gp
    rp = build_rewrite_prompt("确保质量", _p(), project_terms=["骨科医院"])
    assert "骨科医院" in rp and "确保质量" in rp


def _contract_band() -> ScoreBand:
    return ScoreBand(
        name="合格",
        lower=0.0,
        upper=5.0,
        lower_inclusive=True,
        upper_inclusive=True,
        band_id="qualified",
        label="合格",
    )


def _contract_profile() -> TenderProfile:
    return TenderProfile(
        tender_id="synthetic-006a",
        tender_name="006A synthetic tender",
        version="006A",
        score_scale=10.0,
        scoring_items=(
            ScoringItem(
                item_id="quality",
                name="质量管理",
                max_score=5.0,
                bands=(_contract_band(),),
                evidence_requirements=("施工方案", "质量验收"),
                legacy_dimension_refs=("custom_quality_ref",),
            ),
            ScoringItem(
                item_id="safety",
                name="安全文明",
                max_score=5.0,
                bands=(_contract_band(),),
                evidence_requirements=(),
                legacy_dimension_refs=(),
            ),
        ),
        hard_redlines=(
            HardRedline(
                redline_id="redline_manual_review",
                description="不得提供两份及以上施工组织设计",
                action="manual_review",
                applies_to=("quality",),
            ),
        ),
        legacy_dimension_refs=("project_specific_axis",),
        source_note="synthetic only",
    )


def _clean_contract_profile() -> TenderProfile:
    return TenderProfile(
        tender_id="synthetic-clean-006a",
        tender_name="006A clean synthetic tender",
        version="006A",
        score_scale=5.0,
        scoring_items=(
            ScoringItem(
                item_id="quality",
                name="质量管理",
                max_score=5.0,
                bands=(_contract_band(),),
                evidence_requirements=("施工方案",),
                legacy_dimension_refs=("custom_quality_ref",),
            ),
        ),
        hard_redlines=(),
        legacy_dimension_refs=("project_specific_axis",),
    )


def test_build_compilation_advice_returns_contract_report():
    report = build_compilation_advice(
        _contract_profile(),
        document_text="施工方案已经覆盖质量验收和安全文明要求。",
        provided_evidence={"quality": ["施工方案", "质量验收"]},
    )

    assert report.tender_id == "synthetic-006a"
    assert report.status == "action_required"
    assert report.diagnostics["status"] in {"pass", "warning"}
    assert report.coverage
    assert report.summary["advice_item_count"] == len(report.advice_items)


def test_unmapped_item_generates_mapping_advice():
    report = build_compilation_advice(
        _contract_profile(),
        document_text="施工方案已经覆盖质量验收。",
        provided_evidence={"quality": ["施工方案", "质量验收"]},
    )

    mapping_items = [item for item in report.advice_items if item.category == "mapping"]
    assert any(item.item_id == "safety" for item in mapping_items)
    assert "safety" in report.unmapped_item_ids


def test_missing_or_uncovered_evidence_generates_evidence_advice():
    report = build_compilation_advice(
        _contract_profile(),
        document_text="施工方案已经覆盖质量验收。",
        provided_evidence={},
    )

    evidence_items = [item for item in report.advice_items if item.category == "evidence"]
    assert {item.item_id for item in evidence_items} >= {"quality", "safety"}
    assert any("施工方案" in item.evidence_requirement for item in evidence_items)


def test_hard_redline_is_advice_only_not_adjudication():
    report = build_compilation_advice(
        _contract_profile(),
        document_text="施工方案已经覆盖质量验收。",
        provided_evidence={"quality": ["施工方案", "质量验收"]},
    )

    redline_items = [item for item in report.advice_items if item.category == "redline"]
    assert len(redline_items) == 1
    assert "不执行" in redline_items[0].action
    assert report.summary["does_not_disqualify"] is True
    assert report.summary["affects_score"] is False


def test_empty_document_generates_document_advice_without_failure():
    report = build_compilation_advice(
        _clean_contract_profile(),
        document_text="",
        provided_evidence={"quality": ["施工方案"]},
    )

    document_items = [item for item in report.advice_items if item.category == "document"]
    assert len(document_items) == 1
    assert document_items[0].priority == "medium"
    assert report.status == "warning"
    assert report.summary["does_not_disqualify"] is True


def test_custom_legacy_ref_does_not_fail():
    report = build_compilation_advice(
        _clean_contract_profile(),
        document_text="施工方案",
        provided_evidence={"quality": ["施工方案"]},
    )

    assert report.status == "pass"
    assert "custom_quality_ref" in report.diagnostics["legacy_dimension_refs"]
    assert "project_specific_axis" in report.diagnostics["legacy_dimension_refs"]


def test_invalid_profile_raises_compilation_advisor_error():
    invalid = TenderProfile(
        tender_id="invalid",
        tender_name="invalid tender",
        version="006A",
        score_scale=6.0,
        scoring_items=_contract_profile().scoring_items,
    )

    with pytest.raises(CompilationAdvisorError):
        build_compilation_advice(invalid)


def test_build_compilation_advice_from_file_loads_synthetic_json(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "tender_id": "synthetic-file-006a",
                "tender_name": "006A file synthetic tender",
                "version": "006A",
                "score_scale": 5.0,
                "scoring_items": [
                    {
                        "item_id": "quality",
                        "name": "质量管理",
                        "max_score": 5.0,
                        "bands": [
                            {
                                "band_id": "qualified",
                                "label": "合格",
                                "min_score": 0.0,
                                "max_score": 5.0,
                            }
                        ],
                        "evidence_requirements": ["施工方案"],
                        "legacy_dimension_refs": ["custom_quality_ref"],
                    }
                ],
                "hard_redlines": [],
                "legacy_dimension_refs": ["project_specific_axis"],
                "source_note": "synthetic only",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = build_compilation_advice_from_file(
        profile_path,
        document_text="施工方案",
        provided_evidence={"quality": ["施工方案"]},
    )

    assert report.tender_id == "synthetic-file-006a"
    assert report.status == "pass"


def test_compilation_advisor_to_dict_is_json_serializable():
    report = build_compilation_advice(
        _contract_profile(),
        document_text="施工方案已经覆盖质量验收。",
        provided_evidence={"quality": ["施工方案", "质量验收"]},
    )

    payload = compilation_advisor_to_dict(report)
    json.dumps(payload, ensure_ascii=False)
    assert payload["diagnostics"]
    assert payload["coverage"]
    assert payload["summary"]["advice_item_count"] == len(payload["advice_items"])
