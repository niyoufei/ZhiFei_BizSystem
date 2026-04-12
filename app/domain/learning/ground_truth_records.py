from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional
from uuid import uuid4

from fastapi import HTTPException

DEFAULT_SCORE_SCALE_MAX = 100


def to_float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clip_score(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def normalize_score_scale_max(value: object, default: int = DEFAULT_SCORE_SCALE_MAX) -> int:
    numeric_value = to_float_or_none(value)
    if numeric_value is None:
        numeric = int(default)
    else:
        numeric = int(numeric_value)
    return 5 if numeric == 5 else 100


def resolve_project_score_scale_max(project: dict[str, object]) -> int:
    raw_meta = project.get("meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    return normalize_score_scale_max(
        (meta or {}).get("score_scale_max"),
        default=DEFAULT_SCORE_SCALE_MAX,
    )


def score_scale_decimal_places(score_scale_max: int) -> int:
    return 4 if int(normalize_score_scale_max(score_scale_max)) == 5 else 2


def quantize_decimal_score(value: Decimal, *, score_scale_max: int) -> float:
    places = score_scale_decimal_places(score_scale_max)
    quantizer = Decimal("1").scaleb(-places)
    return float(value.quantize(quantizer, rounding=ROUND_HALF_UP))


def score_scale_label(score_scale_max: int) -> str:
    return "5分制" if int(normalize_score_scale_max(score_scale_max)) == 5 else "100分制"


def convert_score_from_100(score: object, score_scale_max: int) -> Optional[float]:
    value = to_float_or_none(score)
    if value is None:
        return None
    clipped = Decimal(str(clip_score(value, 0.0, 100.0)))
    scale = Decimal(str(normalize_score_scale_max(score_scale_max)))
    converted = clipped * (scale / Decimal("100"))
    return quantize_decimal_score(converted, score_scale_max=score_scale_max)


def convert_score_to_100(score: object, score_scale_max: int) -> Optional[float]:
    value = to_float_or_none(score)
    if value is None:
        return None
    scale = Decimal(str(normalize_score_scale_max(score_scale_max)))
    if scale <= 0:
        return None
    clipped = Decimal(str(clip_score(value, 0.0, float(scale))))
    converted = clipped * (Decimal("100") / scale)
    return quantize_decimal_score(converted, score_scale_max=100)


def format_score_value_for_scale(score: object, score_scale_max: int) -> Optional[str]:
    value = to_float_or_none(score)
    if value is None:
        return None
    scale = normalize_score_scale_max(score_scale_max, default=DEFAULT_SCORE_SCALE_MAX)
    quantized = quantize_decimal_score(Decimal(str(value)), score_scale_max=scale)
    return f"{quantized:.{score_scale_decimal_places(scale)}f}"


def normalize_judge_scores_or_422(
    judge_scores: object,
    *,
    field_name: str = "judge_scores",
) -> list[float]:
    if not isinstance(judge_scores, list):
        raise HTTPException(status_code=422, detail=f"{field_name} 必须为数组。")
    judge_count = len(judge_scores)
    if judge_count not in (5, 7):
        raise HTTPException(status_code=422, detail=f"{field_name} 必须为 5 或 7 个评委得分。")
    normalized: list[float] = []
    for idx, value in enumerate(judge_scores, start=1):
        try:
            normalized.append(float(value))
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"{field_name}[{idx}] 不是有效数字：{exc}",
            )
    return normalized


def normalize_judge_weights_or_422(
    judge_weights: object,
    *,
    expected_count: int,
) -> Optional[list[float]]:
    if judge_weights is None:
        return None
    if not isinstance(judge_weights, list):
        raise HTTPException(status_code=422, detail="judge_weights 必须为数组。")
    if len(judge_weights) != expected_count:
        raise HTTPException(
            status_code=422,
            detail=f"judge_weights 长度需与 judge_scores 一致（当前应为 {expected_count}）。",
        )
    normalized: list[float] = []
    for idx, value in enumerate(judge_weights, start=1):
        try:
            normalized.append(float(value))
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"judge_weights[{idx}] 不是有效数字：{exc}",
            )
    return normalized


def normalize_qualitative_tags_or_422(
    tags_by_judge: object,
    *,
    expected_count: int,
) -> Optional[list[list[str]]]:
    if tags_by_judge is None:
        return None
    if not isinstance(tags_by_judge, list):
        raise HTTPException(status_code=422, detail="qualitative_tags_by_judge 必须为数组。")
    if len(tags_by_judge) != expected_count:
        raise HTTPException(
            status_code=422,
            detail=f"qualitative_tags_by_judge 长度需与 judge_scores 一致（当前应为 {expected_count}）。",
        )
    normalized: list[list[str]] = []
    for idx, tags in enumerate(tags_by_judge, start=1):
        if tags is None:
            normalized.append([])
            continue
        if not isinstance(tags, list):
            raise HTTPException(
                status_code=422,
                detail=f"qualitative_tags_by_judge[{idx}] 必须为字符串数组。",
            )
        clean_tags = [str(x).strip() for x in tags if str(x).strip()]
        normalized.append(clean_tags)
    return normalized


def parse_judge_scores_form(judge_scores: str) -> list[float]:
    try:
        scores = json.loads(judge_scores)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"评委得分格式错误：{exc}")
    return normalize_judge_scores_or_422(scores)


def assert_valid_final_score(final_score: float, *, score_scale_max: int = 100) -> None:
    scale = normalize_score_scale_max(score_scale_max, default=100)
    if not (0 <= float(final_score) <= float(scale)):
        raise HTTPException(status_code=422, detail=f"最终得分应在 0～{scale} 之间。")


def ground_truth_record_for_learning(
    record: dict[str, object],
    *,
    default_score_scale_max: int,
) -> dict[str, object]:
    score_scale_max = normalize_score_scale_max(
        record.get("score_scale_max"),
        default=default_score_scale_max,
    )
    final_raw = to_float_or_none(record.get("final_score_raw"))
    if final_raw is None:
        final_raw = to_float_or_none(record.get("final_score"))
    if final_raw is None:
        final_raw = 0.0
    final_100 = to_float_or_none(record.get("final_score_100"))
    if final_100 is None:
        final_100 = convert_score_to_100(final_raw, score_scale_max)
    final_100 = float(final_100 if final_100 is not None else 0.0)
    judge_scores = record.get("judge_scores") or []
    judge_count = len(judge_scores) if isinstance(judge_scores, list) else 0
    normalized = dict(record)
    normalized["score_scale_max"] = score_scale_max
    normalized["final_score_raw"] = round(float(final_raw), 2)
    normalized["final_score_100"] = round(final_100, 2)
    normalized["final_score"] = round(final_100, 2)
    normalized["judge_count"] = judge_count
    return normalized


def new_ground_truth_record(
    project_id: str,
    shigong_text: str,
    judge_scores: list[float],
    final_score: float,
    source: str,
    score_scale_max: int,
    judge_weights: Optional[list[float]] = None,
    qualitative_tags_by_judge: Optional[list[list[str]]] = None,
) -> dict[str, object]:
    score_scale = normalize_score_scale_max(score_scale_max, default=100)
    normalized_judge_scores = normalize_judge_scores_or_422(judge_scores)
    normalized_judge_weights = normalize_judge_weights_or_422(
        judge_weights,
        expected_count=len(normalized_judge_scores),
    )
    normalized_tags = normalize_qualitative_tags_or_422(
        qualitative_tags_by_judge,
        expected_count=len(normalized_judge_scores),
    )
    final_raw = float(final_score)
    final_100 = convert_score_to_100(final_raw, score_scale)
    final_100 = float(final_100 if final_100 is not None else 0.0)
    return {
        "id": str(uuid4()),
        "project_id": project_id,
        "shigong_text": shigong_text,
        "judge_scores": normalized_judge_scores,
        "judge_count": len(normalized_judge_scores),
        "score_scale_max": score_scale,
        "final_score": round(final_raw, 2),
        "final_score_raw": round(final_raw, 2),
        "final_score_100": round(final_100, 2),
        "judge_weights": normalized_judge_weights,
        "qualitative_tags_by_judge": normalized_tags,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
