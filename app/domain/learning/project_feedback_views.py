from __future__ import annotations

from decimal import Decimal
from typing import Dict, Iterable, List, Optional

from app.domain.learning.feedback_guardrails import (
    build_feedback_guardrail_delta_text,
    extract_feedback_guardrail,
    feedback_guardrail_is_blocked,
)
from app.domain.learning.ground_truth_records import (
    DEFAULT_SCORE_SCALE_MAX,
    normalize_score_scale_max,
    quantize_decimal_score,
    score_scale_label,
    to_float_or_none,
)


def list_project_ground_truth_records(
    project_id: str,
    *,
    rows: Iterable[Dict[str, object]],
    include_guardrail_blocked: bool = False,
    default_score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
    default_threshold_ratio: float,
) -> List[Dict[str, object]]:
    project_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("project_id") or "") == str(project_id)
    ]
    if include_guardrail_blocked:
        return project_rows
    return [
        row
        for row in project_rows
        if not feedback_guardrail_is_blocked(
            row,
            default_score_scale_max=default_score_scale_max,
            default_threshold_ratio=default_threshold_ratio,
        )
    ]


def collect_blocked_ground_truth_guardrails(
    project_id: str,
    *,
    rows: Iterable[Dict[str, object]],
    record_ids: Optional[List[str]] = None,
    default_score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
    default_threshold_ratio: float,
) -> List[Dict[str, object]]:
    target_ids = {
        str(record_id or "").strip()
        for record_id in (record_ids or [])
        if str(record_id or "").strip()
    }
    blocked_rows: List[Dict[str, object]] = []
    for row in list_project_ground_truth_records(
        project_id,
        rows=rows,
        include_guardrail_blocked=True,
        default_score_scale_max=default_score_scale_max,
        default_threshold_ratio=default_threshold_ratio,
    ):
        row_id = str(row.get("id") or "").strip()
        if target_ids and row_id not in target_ids:
            continue
        guardrail = extract_feedback_guardrail(
            row,
            default_score_scale_max=default_score_scale_max,
            default_threshold_ratio=default_threshold_ratio,
        )
        if not bool(guardrail.get("blocked")):
            continue
        blocked_rows.append(
            {
                "record_id": row_id,
                "feedback_guardrail": guardrail,
            }
        )
    return blocked_rows


def summarize_project_feedback_guardrail(
    project_id: str,
    *,
    rows: Iterable[Dict[str, object]],
    record_ids: Optional[List[str]] = None,
    default_score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
    default_threshold_ratio: float,
) -> Dict[str, object]:
    blocked_rows = collect_blocked_ground_truth_guardrails(
        project_id,
        rows=rows,
        record_ids=record_ids,
        default_score_scale_max=default_score_scale_max,
        default_threshold_ratio=default_threshold_ratio,
    )
    if not blocked_rows:
        return {
            "blocked": False,
            "blocked_record_ids": [],
            "blocked_count": 0,
            "pending_blocked_count": 0,
        }

    blocked_record_ids = [str(item.get("record_id") or "") for item in blocked_rows]
    pending_rows = [
        item
        for item in blocked_rows
        if str((item.get("feedback_guardrail") or {}).get("manual_review_status") or "pending")
        == "pending"
    ]
    max_abs_delta = max(
        float(to_float_or_none((item.get("feedback_guardrail") or {}).get("abs_delta_100")) or 0.0)
        for item in blocked_rows
    )
    scale_max = normalize_score_scale_max(
        (
            (pending_rows[0] if pending_rows else blocked_rows[0]).get("feedback_guardrail") or {}
        ).get("score_scale_max"),
        default=default_score_scale_max,
    )
    max_abs_delta_raw = max(
        float(to_float_or_none((item.get("feedback_guardrail") or {}).get("abs_delta_raw")) or 0.0)
        for item in blocked_rows
    )
    warning_message = str(
        (
            (
                (pending_rows[0] if pending_rows else blocked_rows[0]).get("feedback_guardrail")
                or {}
            ).get("warning_message")
            or ""
        )
    ).strip()
    if not warning_message:
        warning_message = (
            f"检测到 {len(blocked_rows)} 条极端偏差样本，已暂停自动调权/自动校准。"
            "请人工确认后再执行「学习进化」或「一键闭环执行」。"
        )
    return {
        "blocked": bool(pending_rows),
        "blocked_record_ids": blocked_record_ids,
        "blocked_count": len(blocked_rows),
        "pending_blocked_count": len(pending_rows),
        "max_abs_delta_100": round(max_abs_delta, 2),
        "max_abs_delta_raw": (
            quantize_decimal_score(
                Decimal(str(max_abs_delta_raw)),
                score_scale_max=scale_max,
            )
            if max_abs_delta_raw > 0
            else 0.0
        ),
        "score_scale_max": scale_max,
        "score_scale_label": score_scale_label(scale_max),
        "requires_manual_confirmation": bool(pending_rows),
        "warning_message": warning_message,
        "manual_override_hint": "confirm_extreme_sample=1" if pending_rows else None,
    }


def build_manual_confirmation_detail(
    summary: Dict[str, object],
    *,
    action_label: str,
    default_score_scale_max: int = DEFAULT_SCORE_SCALE_MAX,
) -> str:
    blocked_count = int(to_float_or_none(summary.get("blocked_count")) or 0)
    max_abs_delta = build_feedback_guardrail_delta_text(
        {
            "abs_delta_raw": summary.get("max_abs_delta_raw"),
            "relative_delta_ratio": (
                float(to_float_or_none(summary.get("max_abs_delta_100")) or 0.0) / 100.0
            ),
            "score_scale_max": summary.get("score_scale_max"),
        },
        default_score_scale_max=int(
            to_float_or_none(summary.get("score_scale_max")) or default_score_scale_max
        ),
    )
    return (
        f"检测到 {blocked_count} 条极端偏差样本（最大偏差 {max_abs_delta or '未提供'}），"
        f"已暂停自动纳入 {action_label}。请人工确认后重试，并附带 confirm_extreme_sample=1。"
    )
