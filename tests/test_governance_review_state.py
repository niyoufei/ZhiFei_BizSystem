from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domain.governance.review_state import (
    apply_feedback_guardrail_review_state,
    apply_few_shot_review_state,
)


def test_apply_feedback_guardrail_review_state_approves_blocked_sample() -> None:
    payload = {
        "feedback_guardrail": {
            "blocked": True,
            "threshold_blocked": True,
            "actual_score_100": 82.0,
            "predicted_score_100": 34.0,
        }
    }

    result = apply_feedback_guardrail_review_state(
        payload,
        action="approve",
        note="人工放行",
        reviewed_at="2026-04-12T12:00:00+08:00",
        default_score_scale_max=100,
        default_threshold_ratio=0.35,
    )

    assert result["blocked"] is False
    assert result["threshold_blocked"] is True
    assert result["manual_review_status"] == "approved"
    assert result["manual_review_note"] == "人工放行"
    assert result["manual_reviewed_at"] == "2026-04-12T12:00:00+08:00"


def test_apply_feedback_guardrail_review_state_rejects_non_blocked_sample() -> None:
    with pytest.raises(HTTPException) as exc_info:
        apply_feedback_guardrail_review_state(
            {"feedback_guardrail": {"blocked": False, "threshold_blocked": False}},
            action="approve",
            note="",
            reviewed_at="2026-04-12T12:00:00+08:00",
            default_score_scale_max=100,
            default_threshold_ratio=0.35,
        )

    assert exc_info.value.status_code == 422
    assert "无需人工放行/拒绝" in str(exc_info.value.detail)


def test_apply_few_shot_review_state_sets_resolved_feature_ids() -> None:
    result = apply_few_shot_review_state(
        {
            "captured": 2,
            "reason": "captured",
            "feature_ids": ["legacy"],
        },
        action="adopt",
        note="采纳",
        reviewed_at="2026-04-12T12:05:00+08:00",
        resolved_feature_ids=["F-1", "F-2", ""],
    )

    assert result["captured"] == 2
    assert result["manual_review_status"] == "adopted"
    assert result["manual_review_note"] == "采纳"
    assert result["manual_reviewed_at"] == "2026-04-12T12:05:00+08:00"
    assert result["feature_ids"] == ["F-1", "F-2"]


def test_apply_few_shot_review_state_reset_without_capture_is_not_required() -> None:
    result = apply_few_shot_review_state(
        {"captured": 0, "reason": "not_executed"},
        action="reset",
        note="",
        reviewed_at="2026-04-12T12:05:00+08:00",
        resolved_feature_ids=[],
    )

    assert result["manual_review_status"] == "not_required"
    assert result["manual_reviewed_at"] == "2026-04-12T12:05:00+08:00"
