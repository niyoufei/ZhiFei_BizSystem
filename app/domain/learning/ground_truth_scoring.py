from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

from fastapi import HTTPException

from app.domain.learning.ground_truth_records import normalize_judge_scores_or_422, to_float_or_none


def quantize_ground_truth_final_score(value: object, *, digits: int = 2) -> float | None:
    try:
        decimal_value = Decimal(str(value))
    except Exception:
        return None
    quantizer = Decimal("1").scaleb(-max(0, int(digits)))
    return float(decimal_value.quantize(quantizer, rounding=ROUND_HALF_UP))


def calculate_ground_truth_final_score(
    judge_scores: object,
    *,
    scoring_rule: dict[str, object],
) -> float:
    normalized_scores = normalize_judge_scores_or_422(judge_scores)
    score_values = [Decimal(str(score)) for score in normalized_scores]
    formula = str(scoring_rule.get("formula") or "").strip()
    if formula == "trim_one_each_mean" and len(score_values) > 2:
        score_values = sorted(score_values)[1:-1]
    average = sum(score_values, Decimal("0")) / Decimal(str(len(score_values)))
    digits = int(to_float_or_none(scoring_rule.get("rounding_digits")) or 2)
    quantized = quantize_ground_truth_final_score(average, digits=digits)
    return float(quantized if quantized is not None else 0.0)


def auto_compute_ground_truth_final_score_if_needed(
    project_id: str,
    *,
    judge_scores: object,
    final_score: object,
    project: dict[str, object] | None,
    resolve_scoring_rule: Callable[[str, dict[str, object]], dict[str, object]],
) -> float:
    provided_score = to_float_or_none(final_score)
    if provided_score is None:
        raise HTTPException(status_code=422, detail="最终得分格式错误。")
    if float(provided_score) > 0.0:
        return float(provided_score)
    if not isinstance(project, dict):
        return float(provided_score)
    try:
        scoring_rule = resolve_scoring_rule(project_id, project)
    except HTTPException:
        return float(provided_score)
    if not bool(scoring_rule.get("auto_compute")):
        return float(provided_score)
    try:
        computed_score = calculate_ground_truth_final_score(
            judge_scores,
            scoring_rule=scoring_rule,
        )
    except HTTPException:
        return float(provided_score)
    if float(computed_score) <= 0.0:
        return float(provided_score)
    return float(computed_score)
