from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException

from app.domain.learning.feedback_guardrails import (
    extract_feedback_guardrail,
    normalize_feedback_guardrail_state,
)
from app.domain.learning.feedback_state import normalize_few_shot_distillation_state
from app.domain.learning.ground_truth_records import to_float_or_none


def apply_feedback_guardrail_review_state(
    payload: object,
    *,
    action: str,
    note: str,
    reviewed_at: str | None,
    default_score_scale_max: int,
    default_threshold_ratio: float,
) -> dict[str, object]:
    guardrail = extract_feedback_guardrail(
        payload,
        default_score_scale_max=default_score_scale_max,
        default_threshold_ratio=default_threshold_ratio,
    )
    action_text = str(action or "").strip().lower()
    if not bool(guardrail.get("threshold_blocked")) and action_text != "reset":
        raise HTTPException(status_code=422, detail="该样本未触发极端偏差拦截，无需人工放行/拒绝。")
    if action_text == "approve":
        review_status = "approved"
    elif action_text == "reject":
        review_status = "rejected"
    elif action_text == "reset":
        review_status = "pending" if bool(guardrail.get("threshold_blocked")) else "not_required"
    else:
        raise HTTPException(status_code=422, detail="action 仅支持 approve、reject、reset")

    guardrail["manual_review"] = {
        "status": review_status,
        "note": str(note or "").strip() or None,
        "reviewed_at": str(reviewed_at or "").strip() or None
        if review_status != "pending"
        else None,
    }
    return normalize_feedback_guardrail_state(
        guardrail,
        default_score_scale_max=default_score_scale_max,
        default_threshold_ratio=default_threshold_ratio,
    )


def apply_few_shot_review_state(
    payload: object,
    *,
    action: str,
    note: str,
    reviewed_at: str | None,
    resolved_feature_ids: Iterable[object] | None = None,
) -> dict[str, object]:
    distillation = normalize_few_shot_distillation_state(payload)
    captured = int(to_float_or_none(distillation.get("captured")) or 0)
    action_text = str(action or "").strip().lower()
    if captured <= 0 and action_text != "reset":
        raise HTTPException(status_code=422, detail="该样本尚未捕获少样本特征，无需采纳治理。")
    if action_text == "adopt":
        review_status = "adopted"
    elif action_text == "ignore":
        review_status = "ignored"
    elif action_text == "reset":
        review_status = "pending" if captured > 0 else "not_required"
    else:
        raise HTTPException(status_code=422, detail="action 仅支持 adopt、ignore、reset")

    distillation["manual_review"] = {
        "status": review_status,
        "note": str(note or "").strip() or None,
        "reviewed_at": str(reviewed_at or "").strip() or None
        if review_status != "pending"
        else None,
    }
    if resolved_feature_ids is not None:
        distillation["feature_ids"] = [
            str(item or "").strip() for item in resolved_feature_ids if str(item or "").strip()
        ]
    return normalize_few_shot_distillation_state(distillation)
