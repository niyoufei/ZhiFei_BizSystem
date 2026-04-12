from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domain.learning.ground_truth_scoring import (
    auto_compute_ground_truth_final_score_if_needed,
    calculate_ground_truth_final_score,
)


def test_calculate_ground_truth_final_score_respects_formula() -> None:
    judge_scores = [4.15, 4.09, 4.15, 4.15, 4.21, 3.96, 4.24]
    assert calculate_ground_truth_final_score(
        judge_scores,
        scoring_rule={"formula": "simple_mean", "rounding_digits": 2},
    ) == pytest.approx(4.14, abs=1e-6)
    assert calculate_ground_truth_final_score(
        judge_scores,
        scoring_rule={"formula": "trim_one_each_mean", "rounding_digits": 2},
    ) == pytest.approx(4.15, abs=1e-6)


def test_auto_compute_ground_truth_final_score_if_needed_uses_resolved_rule() -> None:
    result = auto_compute_ground_truth_final_score_if_needed(
        "p1",
        judge_scores=[4.33, 4.36, 4.35, 4.36, 4.8],
        final_score=0.0,
        project={"id": "p1", "meta": {"score_scale_max": 5}},
        resolve_scoring_rule=lambda _pid, _project: {
            "formula": "simple_mean",
            "auto_compute": True,
            "rounding_digits": 2,
        },
    )
    assert result == pytest.approx(4.44, abs=1e-6)


def test_auto_compute_ground_truth_final_score_if_needed_falls_back_when_resolver_rejects() -> None:
    result = auto_compute_ground_truth_final_score_if_needed(
        "p1",
        judge_scores=[4.33, 4.36, 4.35, 4.36, 4.8],
        final_score=0.0,
        project={"id": "p1", "meta": {"score_scale_max": 5}},
        resolve_scoring_rule=lambda _pid, _project: (_ for _ in ()).throw(
            HTTPException(status_code=404, detail="项目不存在")
        ),
    )
    assert result == 0.0
