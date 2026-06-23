from __future__ import annotations

import json

import pytest

from app.engine.strategy_advisor import (
    StrategyAdvisorError,
    assess_shigong_leverage,
    build_strategy_recommendations,
    build_strategy_recommendations_from_file,
    recommend_target,
    strategy_advisor_to_dict,
)
from app.engine.tender_profile import (
    HardRedline,
    ScoreBand,
    ScoringItem,
    TenderProfile,
    load_all_profiles,
)

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


def _synthetic_contract_profile() -> TenderProfile:
    band_full = ScoreBand(
        name="响应",
        lower=0,
        upper=4,
        lower_inclusive=True,
        upper_inclusive=True,
    )
    band_three = ScoreBand(
        name="响应",
        lower=0,
        upper=3,
        lower_inclusive=True,
        upper_inclusive=True,
    )
    return TenderProfile(
        tender_id="synthetic-007a",
        tender_name="007A synthetic tender",
        version="v-test",
        score_scale=10,
        scoring_items=(
            ScoringItem(
                item_id="item-mapping",
                name="施工部署",
                max_score=4,
                bands=(band_full,),
                evidence_requirements=("施工部署图文锚点",),
                legacy_dimension_refs=(),
            ),
            ScoringItem(
                item_id="item-missing-evidence-rule",
                name="质量控制",
                max_score=3,
                bands=(band_three,),
                evidence_requirements=(),
                legacy_dimension_refs=("custom-quality-ref",),
            ),
            ScoringItem(
                item_id="item-evidence-not-provided",
                name="安全文明",
                max_score=3,
                bands=(band_three,),
                evidence_requirements=("安全检查记录",),
                legacy_dimension_refs=("custom-safety-ref",),
            ),
        ),
        hard_redlines=(
            HardRedline(
                redline_id="redline-manual-review",
                description="不得出现备选施工组织方案",
                action="manual_review",
                applies_to=("item-mapping",),
            ),
        ),
        legacy_dimension_refs=("custom-quality-ref", "custom-safety-ref"),
        source_note="synthetic only",
    )


def _synthetic_profile_json() -> dict:
    return {
        "tender_id": "synthetic-007a",
        "tender_name": "007A synthetic tender",
        "version": "v-test",
        "score_scale": 10,
        "scoring_items": [
            {
                "item_id": "item-mapping",
                "name": "施工部署",
                "max_score": 4,
                "bands": [{"band_id": "full", "label": "响应", "min_score": 0, "max_score": 4}],
                "evidence_requirements": ["施工部署图文锚点"],
                "legacy_dimension_refs": [],
            },
            {
                "item_id": "item-missing-evidence-rule",
                "name": "质量控制",
                "max_score": 3,
                "bands": [{"band_id": "full", "label": "响应", "min_score": 0, "max_score": 3}],
                "evidence_requirements": [],
                "legacy_dimension_refs": ["custom-quality-ref"],
            },
            {
                "item_id": "item-evidence-not-provided",
                "name": "安全文明",
                "max_score": 3,
                "bands": [{"band_id": "full", "label": "响应", "min_score": 0, "max_score": 3}],
                "evidence_requirements": ["安全检查记录"],
                "legacy_dimension_refs": ["custom-safety-ref"],
            },
        ],
        "hard_redlines": [
            {
                "redline_id": "redline-manual-review",
                "description": "不得出现备选施工组织方案",
                "action": "manual_review",
                "applies_to": ["item-mapping"],
            }
        ],
        "legacy_dimension_refs": ["custom-quality-ref", "custom-safety-ref"],
        "source_note": "synthetic only",
    }


def _build_synthetic_strategy_report():
    return build_strategy_recommendations(
        _synthetic_contract_profile(),
        document_text="",
        provided_evidence={"item-mapping": "施工部署图文锚点"},
    )


def test_build_strategy_recommendations_returns_contract_report():
    report = _build_synthetic_strategy_report()

    assert report.tender_id == "synthetic-007a"
    assert report.status == "action_required"
    assert report.compilation_advice["tender_id"] == report.tender_id
    assert report.priority_counts["high"] >= 1
    assert report.summary["recommendation_count"] == len(report.recommendations)
    assert report.summary["does_not_disqualify"] is True


def test_unmapped_item_generates_mapping_or_scoring_response_strategy():
    report = _build_synthetic_strategy_report()

    rec = next(item for item in report.recommendations if item.item_id == "item-mapping")
    assert rec.strategy_type in ("mapping", "scoring_response")
    assert "补齐评审口径映射说明" in rec.action


def test_missing_or_uncovered_evidence_generates_evidence_strategy():
    report = _build_synthetic_strategy_report()

    evidence_recs = [item for item in report.recommendations if item.strategy_type == "evidence"]
    assert {item.item_id for item in evidence_recs} >= {
        "item-missing-evidence-rule",
        "item-evidence-not-provided",
    }
    assert all(
        "补充章节证据、量化指标、验收依据或图文锚点" in item.action for item in evidence_recs
    )


def test_hard_redline_generates_prompt_only_redline_strategy():
    report = _build_synthetic_strategy_report()

    rec = next(item for item in report.recommendations if item.strategy_type == "redline")
    assert "前置核查与人工复核" in rec.action
    assert "不作否决、扣分、判废或裁决" in rec.action
    assert report.hard_redline_count == 1


def test_empty_document_text_generates_document_strategy_without_verdict():
    report = _build_synthetic_strategy_report()

    rec = next(item for item in report.recommendations if item.strategy_type == "document")
    assert "不作判废或扣分判断" in rec.action
    assert report.compilation_advice["diagnostics"]["summary"]["document_text_empty"] is True


def test_custom_legacy_ref_does_not_fail():
    report = _build_synthetic_strategy_report()

    rec = next(
        item for item in report.recommendations if item.item_id == "item-evidence-not-provided"
    )
    assert rec.legacy_dimension_refs == ("custom-safety-ref",)


def test_invalid_profile_raises_strategy_advisor_error():
    invalid = TenderProfile(
        tender_id="",
        tender_name="invalid",
        version="v-test",
        score_scale=1,
        scoring_items=(),
    )

    with pytest.raises(StrategyAdvisorError):
        build_strategy_recommendations(invalid)


def test_build_strategy_recommendations_from_file_loads_synthetic_json(tmp_path):
    path = tmp_path / "synthetic_profile.json"
    path.write_text(json.dumps(_synthetic_profile_json(), ensure_ascii=False), encoding="utf-8")

    report = build_strategy_recommendations_from_file(
        path,
        document_text="",
        provided_evidence={"item-mapping": "施工部署图文锚点"},
    )

    assert report.tender_id == "synthetic-007a"
    assert report.recommendations


def test_strategy_advisor_to_dict_is_json_serializable():
    payload = strategy_advisor_to_dict(_build_synthetic_strategy_report())

    encoded = json.dumps(payload, ensure_ascii=False)
    assert "synthetic-007a" in encoded
    assert payload["recommendations"]
