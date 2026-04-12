from __future__ import annotations

from app.domain.learning.feedback_state import (
    normalize_few_shot_distillation_state,
    normalize_learning_quality_gate_state,
)


def test_normalize_few_shot_distillation_state_defaults_review_when_captured() -> None:
    out = normalize_few_shot_distillation_state({"captured": 2, "reason": "captured"})

    assert out["captured"] == 2
    assert out["manual_review_status"] == "pending"
    assert out["manual_review"]["status"] == "pending"


def test_normalize_few_shot_distillation_state_marks_not_required_when_empty() -> None:
    out = normalize_few_shot_distillation_state({"captured": 0})

    assert out["captured"] == 0
    assert out["manual_review_status"] == "not_required"


def test_normalize_learning_quality_gate_state_applies_defaults_and_rounding() -> None:
    out = normalize_learning_quality_gate_state(
        {
            "blocked": True,
            "reasons": [" low_score_self_awareness ", ""],
            "score_self_awareness_score": "44.678",
            "evidence_hits": "0",
            "material_gate_blocked": 1,
            "total_parsed_chars": "1234",
        },
        default_min_awareness_score=45.0,
        default_min_evidence_hits=1,
    )

    assert out["blocked"] is True
    assert out["status"] == "blocked"
    assert out["reasons"] == ["low_score_self_awareness"]
    assert out["score_self_awareness_score"] == 44.68
    assert out["min_awareness_score"] == 45.0
    assert out["min_evidence_hits"] == 1
