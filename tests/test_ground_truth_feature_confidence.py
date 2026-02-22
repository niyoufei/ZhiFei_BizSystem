from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.main import (
    _auto_update_feature_confidence_on_ground_truth,
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
            mock_collect.return_value = ["F-1", "F-2"]
            mock_update.return_value = {"updated": 2, "retired": 0}
            out = _auto_update_feature_confidence_on_ground_truth(
                report=report,
                gt_record=gt_record,
                project_score_scale_max=5,
            )

    assert out["updated"] == 2
    assert out["applied_feature_ids"] == ["F-1", "F-2"]
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
