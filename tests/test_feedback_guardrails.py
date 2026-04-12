from __future__ import annotations

from app.domain.learning.feedback_guardrails import (
    build_feedback_guardrail_delta_text,
    extract_feedback_guardrail,
    extract_learning_quality_gate,
    feedback_guardrail_blocks_training,
    normalize_feedback_guardrail_state,
)


def test_normalize_feedback_guardrail_state_marks_pending_blocked_sample() -> None:
    out = normalize_feedback_guardrail_state(
        {
            "blocked": True,
            "predicted_score_100": 80,
            "actual_score_100": 60,
            "relative_delta_ratio": 0.2,
            "score_scale_max": 5,
        },
        default_score_scale_max=100,
        default_threshold_ratio=0.15,
    )

    assert out["threshold_blocked"] is True
    assert out["blocked"] is True
    assert out["manual_review_status"] == "pending"
    assert out["score_scale_label"] == "5分制"
    assert "当前分与真实总分偏差" in str(out["warning_message"] or "")


def test_extract_feedback_guardrail_merges_record_level_score_context() -> None:
    out = extract_feedback_guardrail(
        {
            "score_scale_max": 5,
            "final_score_raw": 3.2,
            "final_score_100": 64,
            "feedback_guardrail": {
                "blocked": True,
                "predicted_score_100": 82,
                "relative_delta_ratio": 0.18,
            },
        },
        default_score_scale_max=100,
        default_threshold_ratio=0.15,
    )

    assert out["score_scale_max"] == 5
    assert out["actual_score_raw"] == 3.2
    assert out["actual_score_100"] == 64


def test_build_feedback_guardrail_delta_text_formats_five_scale_delta() -> None:
    text = build_feedback_guardrail_delta_text(
        {
            "score_scale_max": 5,
            "abs_delta_raw": 1.2,
            "relative_delta_ratio": 0.24,
        },
        default_score_scale_max=100,
    )

    assert text == "1.2000 分（5分制，24.0%）"


def test_feedback_guardrail_blocks_training_even_when_manually_approved() -> None:
    assert (
        feedback_guardrail_blocks_training(
            {
                "feedback_guardrail": {
                    "threshold_blocked": True,
                    "manual_review": {"status": "approved"},
                }
            },
            default_score_scale_max=100,
            default_threshold_ratio=0.15,
        )
        is True
    )


def test_extract_learning_quality_gate_uses_defaults() -> None:
    out = extract_learning_quality_gate(
        {
            "learning_quality_gate": {
                "blocked": True,
                "reasons": ["missing_evidence_hits"],
                "score_self_awareness_score": 44.4,
            }
        },
        default_min_awareness_score=45.0,
        default_min_evidence_hits=1,
    )

    assert out["blocked"] is True
    assert out["min_awareness_score"] == 45.0
    assert out["min_evidence_hits"] == 1
