from __future__ import annotations

from typing import Dict

from app.domain.learning.ground_truth_records import to_float_or_none


def normalize_few_shot_distillation_state(payload: object) -> Dict[str, object]:
    distillation = dict(payload) if isinstance(payload, dict) else {}
    captured = int(to_float_or_none(distillation.get("captured")) or 0)
    manual_review = (
        dict(distillation.get("manual_review"))
        if isinstance(distillation.get("manual_review"), dict)
        else {}
    )
    review_status = (
        str(manual_review.get("status") or distillation.get("manual_review_status") or "")
        .strip()
        .lower()
    )
    if captured > 0:
        if review_status not in {"pending", "adopted", "ignored"}:
            review_status = "pending"
    else:
        review_status = "not_required"
    manual_review_note = str(
        manual_review.get("note") or distillation.get("manual_review_note") or ""
    ).strip()
    manual_reviewed_at = str(
        manual_review.get("reviewed_at") or distillation.get("manual_reviewed_at") or ""
    ).strip()
    normalized = dict(distillation)
    normalized["captured"] = captured
    normalized["manual_review"] = {
        "status": review_status,
        "note": manual_review_note or None,
        "reviewed_at": manual_reviewed_at or None,
    }
    normalized["manual_review_status"] = review_status
    normalized["manual_review_note"] = manual_review_note or None
    normalized["manual_reviewed_at"] = manual_reviewed_at or None
    return normalized


def normalize_learning_quality_gate_state(
    payload: object,
    *,
    default_min_awareness_score: float,
    default_min_evidence_hits: int,
) -> Dict[str, object]:
    gate = dict(payload) if isinstance(payload, dict) else {}
    reasons = [str(item).strip() for item in (gate.get("reasons") or []) if str(item).strip()]
    blocked = bool(gate.get("blocked"))
    status = str(gate.get("status") or "").strip()
    if not status:
        status = "blocked" if blocked else "accepted"
    warning_message = str(gate.get("warning_message") or "").strip()
    score_self_awareness_score = to_float_or_none(gate.get("score_self_awareness_score"))
    evidence_hits = int(to_float_or_none(gate.get("evidence_hits")) or 0)
    total_parsed_chars = int(to_float_or_none(gate.get("total_parsed_chars")) or 0)
    normalized = dict(gate)
    normalized["blocked"] = blocked
    normalized["status"] = status
    normalized["reasons"] = reasons
    normalized["score_self_awareness_score"] = (
        round(float(score_self_awareness_score), 2)
        if score_self_awareness_score is not None
        else None
    )
    normalized["score_self_awareness_level"] = (
        str(gate.get("score_self_awareness_level") or "").strip() or None
    )
    normalized["evidence_hits"] = evidence_hits
    normalized["material_gate_blocked"] = bool(gate.get("material_gate_blocked"))
    normalized["total_parsed_chars"] = total_parsed_chars
    normalized["min_awareness_score"] = round(
        float(to_float_or_none(gate.get("min_awareness_score")) or default_min_awareness_score),
        2,
    )
    normalized["min_evidence_hits"] = int(
        to_float_or_none(gate.get("min_evidence_hits")) or default_min_evidence_hits
    )
    normalized["warning_message"] = warning_message or None
    return normalized
