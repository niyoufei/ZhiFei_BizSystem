from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.main import (
    _auto_update_feature_confidence_on_ground_truth,
    _build_ground_truth_feedback_guardrail,
    _build_ground_truth_learning_quality_gate,
    _capture_ground_truth_few_shot_features,
    _collect_applied_feature_ids_from_report,
)


def test_collect_applied_feature_ids_prefers_explicit_and_probe_fallback() -> None:
    report = {
        "suggestions": [
            {"dimension_id": "P02", "applied_feature_ids": ["F-explicit"]},
            {"dimension_id": "03"},
        ],
        "probe_dimensions": [{"id": "P03", "score_rate": 0.6}],
    }

    with patch("app.main.select_top_logic_skeletons") as mock_select:
        mock_select.side_effect = [
            [SimpleNamespace(feature_id="F-p02-1")],
        ]
        out = _collect_applied_feature_ids_from_report(report, top_k_per_probe=1)

    assert "F-explicit" in out
    assert "F-p02-1" in out


def test_collect_applied_feature_ids_uses_probe_dimensions_when_suggestions_missing() -> None:
    report = {
        "suggestions": [],
        "probe_dimensions": [
            {"id": "P01", "score_rate": 0.75},
            {"id": "P02", "score_rate": 0.82},
            {"id": "P03", "score_rate": 0.4},
        ],
    }
    with patch("app.main.select_top_logic_skeletons") as mock_select:
        mock_select.side_effect = [
            [SimpleNamespace(feature_id="F-p01")],
            [SimpleNamespace(feature_id="F-p03")],
        ]
        out = _collect_applied_feature_ids_from_report(report, top_k_per_probe=1)

    assert out == ["F-p01", "F-p03"]


def test_auto_update_feature_confidence_normalizes_five_scale_scores() -> None:
    gt_record = {
        "id": "gt-1",
        "final_score": 4.2,
        "score_scale_max": 5,
        "judge_scores": [4.0, 4.1, 4.2, 4.3, 4.4],
    }
    report = {
        "pred_total_score": 4.0,
    }

    with patch("app.main._collect_applied_feature_ids_from_report") as mock_collect:
        with patch("app.main.update_feature_confidence") as mock_update:
            with patch("app.main.load_feature_kb") as mock_load_feature_kb:
                mock_collect.return_value = ["F-1", "F-2"]
                mock_update.return_value = {"updated": 2, "retired": 0}
                mock_load_feature_kb.return_value = [
                    SimpleNamespace(feature_id="F-1", dimension_id="09"),
                    SimpleNamespace(feature_id="F-2", dimension_id="P14"),
                ]
                out = _auto_update_feature_confidence_on_ground_truth(
                    report=report,
                    gt_record=gt_record,
                    project_score_scale_max=5,
                )

    assert out["updated"] == 2
    assert out["applied_feature_ids"] == ["F-1", "F-2"]
    assert out["applied_dimension_ids"] == ["09", "14"]
    assert abs(out["delta_score_100"] - 4.0) < 1e-6
    assert 0.0 < out["time_decay_weight"] <= 1.0
    assert out["positive_or_negative_feedback"] == "positive"
    kwargs = mock_update.call_args.kwargs
    assert kwargs["applied_feature_ids"] == ["F-1", "F-2"]
    assert abs(kwargs["actual_score"] - 84.0) < 1e-6
    assert abs(kwargs["predicted_score"] - 80.0) < 1e-6


def test_auto_update_feature_confidence_returns_reason_when_no_features() -> None:
    gt_record = {
        "id": "gt-2",
        "final_score": 88,
        "judge_scores": [80, 81, 82, 83, 84],
    }
    report = {"pred_total_score": 85}

    with patch("app.main._collect_applied_feature_ids_from_report") as mock_collect:
        mock_collect.return_value = []
        out = _auto_update_feature_confidence_on_ground_truth(
            report=report,
            gt_record=gt_record,
            project_score_scale_max=100,
        )

    assert out["updated"] == 0
    assert out["reason"] == "no_applied_feature_ids"


def test_build_ground_truth_feedback_guardrail_blocks_extreme_delta() -> None:
    gt_record = {
        "id": "gt-guardrail",
        "final_score": 80,
        "score_scale_max": 100,
        "judge_scores": [78, 79, 80, 81, 82],
    }
    report = {"pred_total_score": 35}

    out = _build_ground_truth_feedback_guardrail(
        report=report,
        gt_record=gt_record,
        project_score_scale_max=100,
    )

    assert out["blocked"] is True
    assert out["requires_manual_confirmation"] is True
    assert out["abs_delta_100"] == 45.0
    assert out["relative_delta_ratio"] == 0.45
    assert "暂停自动调权" in (out["warning_message"] or "")


def test_build_ground_truth_feedback_guardrail_formats_warning_in_five_scale() -> None:
    gt_record = {
        "id": "gt-guardrail-five",
        "final_score": 4.31,
        "score_scale_max": 5,
        "judge_scores": [4.27, 4.25, 4.27, 4.28, 4.31, 4.33, 4.35],
    }
    report = {"pred_total_score": 0.609}

    out = _build_ground_truth_feedback_guardrail(
        report=report,
        gt_record=gt_record,
        project_score_scale_max=5,
    )

    assert out["blocked"] is True
    assert out["score_scale_max"] == 5
    assert out["abs_delta_100"] == pytest.approx(74.02, abs=1e-2)
    assert out["abs_delta_raw"] == pytest.approx(3.701, abs=1e-4)
    assert "5分制" in (out["warning_message"] or "")
    assert "100分口径" not in (out["warning_message"] or "")


def test_build_ground_truth_learning_quality_gate_blocks_low_quality_sample() -> None:
    gt_record = {
        "id": "gt-quality",
        "final_score": 80,
        "score_scale_max": 100,
        "judge_scores": [78, 79, 80, 81, 82],
    }
    report = {
        "pred_total_score": 79,
        "meta": {
            "material_utilization_gate": {"blocked": True},
            "evidence_trace": {"total_hits": 0},
            "score_self_awareness": {"level": "low", "score_0_100": 24.0},
            "material_quality": {"total_parsed_chars": 1200},
        },
    }

    out = _build_ground_truth_learning_quality_gate(
        report=report,
        gt_record=gt_record,
        project_score_scale_max=100,
    )

    assert out["blocked"] is True
    assert "material_gate_blocked" in out["reasons"]
    assert "missing_evidence_hits" in out["reasons"]
    assert "low_score_self_awareness" in out["reasons"]
    assert "未纳入自动学习" in (out["warning_message"] or "")


def test_capture_ground_truth_few_shot_features_distills_evidence(monkeypatch) -> None:
    report = {
        "dimension_scores": {
            "09": {
                "score": 9.5,
                "evidence": [
                    {
                        "anchor_label": "进度计划网",
                        "quote": "关键线路实行周纠偏与节点验收闭环。",
                    }
                ],
            }
        },
        "suggestions": [{"dimension_id": "09", "text": "补强关键节点纠偏闭环。"}],
    }
    gt_record = {
        "id": "gt-logic",
        "final_score": 86,
        "score_scale_max": 100,
        "judge_scores": [84, 85, 86, 87, 88],
        "qualitative_tags_by_judge": [["重点表扬工期组织", "节点管控清晰"]],
    }
    captured = {}

    def _fake_upsert(features):
        captured["count"] = len(features)
        captured["features"] = features
        return {"added": len(features), "updated": 0, "total": len(features)}

    monkeypatch.setattr("app.main.upsert_distilled_features", _fake_upsert)

    out = _capture_ground_truth_few_shot_features(
        report=report,
        gt_record=gt_record,
        project_score_scale_max=100,
        feedback_guardrail={"blocked": False},
        learning_quality_gate={"blocked": False},
        feature_confidence_update={"applied_dimension_ids": ["09"]},
    )

    assert out["captured"] == 1
    assert out["reason"] == "captured"
    assert out["dimension_ids"] == ["09"]
    assert captured["count"] == 1
    assert captured["features"][0].governance_status == "pending"
    assert captured["features"][0].source_record_ids == ["gt-logic"]
    assert captured["features"][0].source_highlights


def test_capture_ground_truth_few_shot_features_skips_learning_quality_blocked() -> None:
    out = _capture_ground_truth_few_shot_features(
        report={"dimension_scores": {"09": {"score": 9.0}}},
        gt_record={
            "id": "gt-low-quality",
            "final_score": 88,
            "score_scale_max": 100,
            "judge_scores": [88, 88, 88, 88, 88],
        },
        project_score_scale_max=100,
        feedback_guardrail={"blocked": False},
        learning_quality_gate={"blocked": True, "reasons": ["missing_evidence_hits"]},
        feature_confidence_update={"applied_dimension_ids": ["09"]},
    )

    assert out["captured"] == 0
    assert out["reason"] == "learning_quality_blocked"


def test_capture_ground_truth_few_shot_features_auto_adopts_high_consensus(monkeypatch) -> None:
    report = {
        "dimension_scores": {
            "09": {
                "score": 9.3,
                "evidence": [{"anchor_label": "进度计划网", "quote": "关键节点周纠偏闭环。"}],
            }
        },
        "suggestions": [{"dimension_id": "09", "text": "补强节点计划闭环。"}],
    }
    gt_record = {
        "id": "gt-consensus",
        "final_score": 86.0,
        "score_scale_max": 100,
        "judge_scores": [86.0, 86.1, 85.9, 86.05, 85.95, 86.0, 86.05],
        "qualitative_tags_by_judge": [["工期逻辑清晰"]],
    }
    captured = {}

    def _fake_upsert(features):
        captured["features"] = features
        return {"added": len(features), "updated": 0, "total": len(features)}

    monkeypatch.setattr("app.main.upsert_distilled_features", _fake_upsert)

    out = _capture_ground_truth_few_shot_features(
        report=report,
        gt_record=gt_record,
        project_score_scale_max=100,
        feedback_guardrail={"blocked": False, "threshold_blocked": False},
        learning_quality_gate={"blocked": False},
        feature_confidence_update={"applied_dimension_ids": ["09"]},
    )

    assert out["captured"] == 1
    assert out["manual_review"]["status"] == "adopted"
    assert captured["features"][0].governance_status == "auto_adopted"
