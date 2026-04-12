from __future__ import annotations

from decimal import Decimal
from typing import Dict

from app.domain.learning.feedback_state import normalize_learning_quality_gate_state
from app.domain.learning.ground_truth_records import (
    DEFAULT_SCORE_SCALE_MAX,
    convert_score_from_100,
    format_score_value_for_scale,
    normalize_score_scale_max,
    quantize_decimal_score,
    score_scale_label,
    to_float_or_none,
)


def normalize_feedback_guardrail_display_metrics(
    payload: object,
    *,
    default_score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
) -> Dict[str, object]:
    normalized = dict(payload) if isinstance(payload, dict) else {}
    score_scale_max = normalize_score_scale_max(
        normalized.get("score_scale_max"),
        default=default_score_scale_max,
    )
    normalized["score_scale_max"] = score_scale_max
    normalized["score_scale_label"] = score_scale_label(score_scale_max)

    actual_score_raw = to_float_or_none(normalized.get("actual_score_raw"))
    if actual_score_raw is None:
        actual_score_raw = convert_score_from_100(
            normalized.get("actual_score_100"), score_scale_max
        )
    predicted_score_raw = to_float_or_none(normalized.get("predicted_score_raw"))
    if predicted_score_raw is None:
        predicted_score_raw = convert_score_from_100(
            normalized.get("predicted_score_100"),
            score_scale_max,
        )
    current_score_raw = to_float_or_none(normalized.get("current_score_raw"))
    if current_score_raw is None:
        current_score_raw = convert_score_from_100(
            normalized.get("current_score_100"),
            score_scale_max,
        )
    if current_score_raw is None:
        current_score_raw = predicted_score_raw

    abs_delta_raw = to_float_or_none(normalized.get("abs_delta_raw"))
    if abs_delta_raw is None:
        if actual_score_raw is not None and current_score_raw is not None:
            abs_delta_raw = abs(float(actual_score_raw) - float(current_score_raw))
        else:
            abs_delta_raw = convert_score_from_100(
                normalized.get("abs_delta_100"),
                score_scale_max,
            )

    if actual_score_raw is not None:
        normalized["actual_score_raw"] = quantize_decimal_score(
            Decimal(str(actual_score_raw)),
            score_scale_max=score_scale_max,
        )
    if predicted_score_raw is not None:
        normalized["predicted_score_raw"] = quantize_decimal_score(
            Decimal(str(predicted_score_raw)),
            score_scale_max=score_scale_max,
        )
    if current_score_raw is not None:
        normalized["current_score_raw"] = quantize_decimal_score(
            Decimal(str(current_score_raw)),
            score_scale_max=score_scale_max,
        )
    if abs_delta_raw is not None:
        normalized["abs_delta_raw"] = quantize_decimal_score(
            Decimal(str(abs_delta_raw)),
            score_scale_max=score_scale_max,
        )
    return normalized


def build_feedback_guardrail_delta_text(
    payload: object,
    *,
    default_score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
) -> str:
    normalized = normalize_feedback_guardrail_display_metrics(
        payload,
        default_score_scale_max=default_score_scale_max,
    )
    abs_delta_raw = to_float_or_none(normalized.get("abs_delta_raw"))
    if abs_delta_raw is None or abs_delta_raw <= 0:
        return ""
    score_scale_max = normalize_score_scale_max(
        normalized.get("score_scale_max"),
        default=default_score_scale_max,
    )
    ratio = float(to_float_or_none(normalized.get("relative_delta_ratio")) or 0.0) * 100.0
    abs_delta_text = format_score_value_for_scale(abs_delta_raw, score_scale_max) or "0.00"
    return f"{abs_delta_text} 分（{score_scale_label(score_scale_max)}，{ratio:.1f}%）"


def normalize_feedback_guardrail_state(
    payload: object,
    *,
    default_score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
    default_threshold_ratio: float,
) -> Dict[str, object]:
    guardrail = dict(payload) if isinstance(payload, dict) else {}
    current_score_100 = to_float_or_none(guardrail.get("current_score_100"))
    predicted_score_100 = to_float_or_none(guardrail.get("predicted_score_100"))
    if current_score_100 is None and predicted_score_100 is not None:
        guardrail["current_score_100"] = predicted_score_100

    threshold_blocked = bool(guardrail.get("threshold_blocked", guardrail.get("blocked")))
    manual_review = (
        dict(guardrail.get("manual_review"))
        if isinstance(guardrail.get("manual_review"), dict)
        else {}
    )
    review_status = (
        str(manual_review.get("status") or guardrail.get("manual_review_status") or "")
        .strip()
        .lower()
    )
    if threshold_blocked:
        if review_status not in {"pending", "approved", "rejected"}:
            review_status = "pending"
    else:
        review_status = "not_required"

    manual_review_note = str(
        manual_review.get("note") or guardrail.get("manual_review_note") or ""
    ).strip()
    manual_reviewed_at = str(
        manual_review.get("reviewed_at") or guardrail.get("manual_reviewed_at") or ""
    ).strip()

    blocked = bool(threshold_blocked and review_status != "approved")
    if threshold_blocked and review_status == "approved":
        status = "manually_approved"
    elif threshold_blocked and review_status == "rejected":
        status = "manually_rejected"
    elif threshold_blocked:
        status = "blocked"
    else:
        status = str(guardrail.get("status") or "accepted").strip() or "accepted"

    normalized = normalize_feedback_guardrail_display_metrics(
        guardrail,
        default_score_scale_max=default_score_scale_max,
    )
    normalized["threshold_blocked"] = threshold_blocked
    normalized["manual_review"] = {
        "status": review_status,
        "note": manual_review_note or None,
        "reviewed_at": manual_reviewed_at or None,
    }
    normalized["manual_review_status"] = review_status
    normalized["manual_review_note"] = manual_review_note or None
    normalized["manual_reviewed_at"] = manual_reviewed_at or None
    normalized["blocked"] = blocked
    normalized["status"] = status
    normalized["requires_manual_confirmation"] = bool(
        threshold_blocked and review_status == "pending"
    )
    normalized["manual_override_hint"] = "confirm_extreme_sample=1" if blocked else None
    normalized["threshold_ratio"] = round(float(default_threshold_ratio), 4)

    if threshold_blocked:
        existing_warning = str(normalized.get("warning_message") or "").strip()
        should_refresh_warning = not existing_warning or (
            int(to_float_or_none(normalized.get("score_scale_max")) or default_score_scale_max) == 5
            and "100分口径" in existing_warning
        )
        if should_refresh_warning:
            delta_text = build_feedback_guardrail_delta_text(
                normalized,
                default_score_scale_max=int(
                    to_float_or_none(normalized.get("score_scale_max")) or default_score_scale_max
                ),
            )
            if delta_text:
                normalized["warning_message"] = (
                    f"当前分与真实总分偏差 {delta_text}，"
                    "已暂停自动调权/自动校准，请人工确认后再执行「学习进化」或「一键闭环执行」。"
                )
            else:
                normalized[
                    "warning_message"
                ] = "检测到极端偏差样本，已暂停自动调权/自动校准，请人工确认后再执行。"
    return normalized


def extract_feedback_guardrail(
    payload: object,
    *,
    default_score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
    default_threshold_ratio: float,
) -> Dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    guardrail = payload.get("feedback_guardrail")
    if isinstance(guardrail, dict):
        merged_guardrail = dict(guardrail)
        if merged_guardrail.get("score_scale_max") is None:
            merged_guardrail["score_scale_max"] = payload.get("score_scale_max")
        if merged_guardrail.get("actual_score_raw") is None:
            merged_guardrail["actual_score_raw"] = (
                payload.get("final_score_raw")
                if payload.get("final_score_raw") is not None
                else payload.get("final_score")
            )
        if merged_guardrail.get("actual_score_100") is None:
            merged_guardrail["actual_score_100"] = payload.get("final_score_100")
        guardrail = merged_guardrail
    return normalize_feedback_guardrail_state(
        guardrail,
        default_score_scale_max=default_score_scale_max,
        default_threshold_ratio=default_threshold_ratio,
    )


def extract_learning_quality_gate(
    payload: object,
    *,
    default_min_awareness_score: float,
    default_min_evidence_hits: int,
) -> Dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    gate = payload.get("learning_quality_gate")
    return normalize_learning_quality_gate_state(
        gate,
        default_min_awareness_score=default_min_awareness_score,
        default_min_evidence_hits=default_min_evidence_hits,
    )


def feedback_guardrail_is_blocked(
    payload: object,
    *,
    default_score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
    default_threshold_ratio: float,
) -> bool:
    guardrail = extract_feedback_guardrail(
        payload,
        default_score_scale_max=default_score_scale_max,
        default_threshold_ratio=default_threshold_ratio,
    )
    return bool(guardrail.get("blocked"))


def feedback_guardrail_blocks_training(
    payload: object,
    *,
    default_score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
    default_threshold_ratio: float,
) -> bool:
    guardrail = extract_feedback_guardrail(
        payload,
        default_score_scale_max=default_score_scale_max,
        default_threshold_ratio=default_threshold_ratio,
    )
    return bool(guardrail.get("threshold_blocked"))
