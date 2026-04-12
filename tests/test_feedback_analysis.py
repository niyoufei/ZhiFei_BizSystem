from __future__ import annotations

from app.domain.learning.feedback_analysis import (
    build_high_consensus_auto_approval,
    build_learning_quality_gate_payload,
    resolve_report_predicted_score_100,
)


def test_resolve_report_predicted_score_100_normalizes_five_scale_prediction() -> None:
    out = resolve_report_predicted_score_100(
        {"pred_total_score": 4.0},
        project_score_scale_max=5,
    )

    assert out == 80.0


def test_build_high_consensus_auto_approval_marks_high_consensus_sample_eligible() -> None:
    out = build_high_consensus_auto_approval(
        gt_for_learning={
            "final_score": 82.2,
            "judge_scores": [82.16, 82.19, 82.16, 82.18, 82.22, 82.19, 82.28],
        },
        min_judges=7,
        max_score_span=0.4,
        max_score_stddev=0.12,
        max_final_delta=0.15,
    )

    assert out["eligible"] is True
    assert out["judge_count"] == 7
    assert out["score_span"] == 0.12


def test_build_high_consensus_auto_approval_rejects_low_consensus_sample() -> None:
    out = build_high_consensus_auto_approval(
        gt_for_learning={
            "final_score": 82.2,
            "judge_scores": [80.5, 81.4, 82.3, 83.1, 84.2, 85.0, 86.1],
        },
        min_judges=7,
        max_score_span=0.4,
        max_score_stddev=0.12,
        max_final_delta=0.15,
    )

    assert out["eligible"] is False
    assert out["judge_count"] == 7


def test_build_learning_quality_gate_payload_reports_all_blocking_reasons() -> None:
    out = build_learning_quality_gate_payload(
        {
            "meta": {
                "material_utilization_gate": {"blocked": True},
                "evidence_trace": {"total_hits": 0},
                "score_self_awareness": {"level": "low", "score_0_100": 24.0},
                "material_quality": {"total_parsed_chars": 1200},
            }
        },
        min_awareness_score=60.0,
        min_evidence_hits=2,
    )

    assert out["blocked"] is True
    assert out["reasons"] == [
        "material_gate_blocked",
        "low_score_self_awareness",
        "missing_evidence_hits",
    ]
    assert "未纳入自动学习" in str(out["warning_message"] or "")
