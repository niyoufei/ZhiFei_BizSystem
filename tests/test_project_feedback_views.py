from __future__ import annotations

from app.domain.learning.project_feedback_views import (
    build_manual_confirmation_detail,
    collect_blocked_ground_truth_guardrails,
    list_project_ground_truth_records,
    summarize_project_feedback_guardrail,
)


def test_list_project_ground_truth_records_excludes_blocked_rows_by_default() -> None:
    rows = [
        {
            "id": "gt-1",
            "project_id": "p1",
            "feedback_guardrail": {
                "blocked": True,
                "threshold_blocked": True,
                "manual_review_status": "pending",
                "abs_delta_100": 35.0,
            },
        },
        {
            "id": "gt-2",
            "project_id": "p1",
            "feedback_guardrail": {
                "blocked": False,
                "threshold_blocked": False,
            },
        },
        {
            "id": "gt-3",
            "project_id": "p2",
        },
    ]

    result = list_project_ground_truth_records(
        "p1",
        rows=rows,
        default_score_scale_max=100,
        default_threshold_ratio=0.35,
    )

    assert [row["id"] for row in result] == ["gt-2"]


def test_collect_blocked_ground_truth_guardrails_filters_record_ids() -> None:
    rows = [
        {
            "id": "gt-1",
            "project_id": "p1",
            "feedback_guardrail": {
                "blocked": True,
                "threshold_blocked": True,
                "manual_review_status": "pending",
                "abs_delta_100": 42.0,
            },
        },
        {
            "id": "gt-2",
            "project_id": "p1",
            "feedback_guardrail": {
                "blocked": True,
                "threshold_blocked": True,
                "manual_review_status": "rejected",
                "abs_delta_100": 21.0,
            },
        },
    ]

    result = collect_blocked_ground_truth_guardrails(
        "p1",
        rows=rows,
        record_ids=["gt-2"],
        default_score_scale_max=100,
        default_threshold_ratio=0.35,
    )

    assert len(result) == 1
    assert result[0]["record_id"] == "gt-2"
    guardrail = result[0]["feedback_guardrail"]
    assert guardrail["blocked"] is True
    assert guardrail["threshold_blocked"] is True
    assert guardrail["manual_review_status"] == "rejected"
    assert guardrail["status"] == "manually_rejected"
    assert guardrail["score_scale_label"] == "100分制"


def test_summarize_project_feedback_guardrail_tracks_pending_rows_and_detail() -> None:
    rows = [
        {
            "id": "gt-1",
            "project_id": "p1",
            "feedback_guardrail": {
                "blocked": True,
                "threshold_blocked": True,
                "manual_review_status": "pending",
                "abs_delta_100": 44.0,
                "abs_delta_raw": 2.2,
                "score_scale_max": 5,
            },
        },
        {
            "id": "gt-2",
            "project_id": "p1",
            "feedback_guardrail": {
                "blocked": True,
                "threshold_blocked": True,
                "manual_review_status": "rejected",
                "abs_delta_100": 21.0,
                "abs_delta_raw": 1.05,
                "score_scale_max": 5,
            },
        },
    ]

    summary = summarize_project_feedback_guardrail(
        "p1",
        rows=rows,
        default_score_scale_max=100,
        default_threshold_ratio=0.35,
    )

    assert summary["blocked"] is True
    assert summary["blocked_count"] == 2
    assert summary["pending_blocked_count"] == 1
    assert summary["score_scale_max"] == 5
    assert summary["max_abs_delta_raw"] == 2.2
    assert summary["manual_override_hint"] == "confirm_extreme_sample=1"

    detail = build_manual_confirmation_detail(summary, action_label="学习进化")
    assert "2 条极端偏差样本" in detail
    assert "2.2000 分（5分制，44.0%）" in detail
