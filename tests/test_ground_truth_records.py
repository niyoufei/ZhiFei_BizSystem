from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domain.learning.ground_truth_records import (
    assert_valid_final_score,
    ground_truth_record_for_learning,
    new_ground_truth_record,
    parse_judge_scores_form,
    resolve_project_score_scale_max,
)


def test_resolve_project_score_scale_max_prefers_five_point_scale() -> None:
    project = {"id": "p1", "meta": {"score_scale_max": 5}}
    assert resolve_project_score_scale_max(project) == 5


def test_new_ground_truth_record_normalizes_five_point_scale_to_hundred_scale() -> None:
    record = new_ground_truth_record(
        project_id="p1",
        shigong_text="示例施组文本" * 20,
        judge_scores=[4.0, 4.2, 4.4, 4.1, 4.3],
        final_score=4.3,
        source="manual",
        score_scale_max=5,
    )
    assert record["score_scale_max"] == 5
    assert record["final_score"] == 4.3
    assert record["final_score_100"] == 86.0


def test_parse_judge_scores_form_rejects_invalid_judge_count() -> None:
    with pytest.raises(HTTPException) as exc_info:
        parse_judge_scores_form("[1,2,3]")
    assert exc_info.value.status_code == 422
    assert "5 或 7" in str(exc_info.value.detail)


def test_assert_valid_final_score_rejects_out_of_range_value() -> None:
    with pytest.raises(HTTPException) as exc_info:
        assert_valid_final_score(5.1, score_scale_max=5)
    assert exc_info.value.status_code == 422
    assert "0～5" in str(exc_info.value.detail)


def test_ground_truth_record_for_learning_backfills_hundred_scale_and_judge_count() -> None:
    out = ground_truth_record_for_learning(
        {
            "final_score": 4.2,
            "score_scale_max": 5,
            "judge_scores": [4.1, 4.2, 4.3, 4.4, 4.0],
        },
        default_score_scale_max=100,
    )

    assert out["score_scale_max"] == 5
    assert out["final_score_raw"] == 4.2
    assert out["final_score_100"] == 84.0
    assert out["final_score"] == 84.0
    assert out["judge_count"] == 5
