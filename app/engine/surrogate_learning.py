from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy 为可选依赖
    np = None

DIMENSION_IDS = [f"{i:02d}" for i in range(1, 17)]
UNIFORM_PRIOR = {dim_id: 1.0 / len(DIMENSION_IDS) for dim_id in DIMENSION_IDS}

# 业务标签 -> 维度映射（可持续补充）
TAG_TO_DIMENSIONS: Dict[str, List[str]] = {
    "扣了进度分": ["09", "15"],
    "工期偏弱": ["09"],
    "重点表扬了BIM": ["05", "14"],
    "智能建造": ["05", "14"],
    "绿色施工": ["03", "08"],
    "双碳": ["03", "05"],
    "应急不足": ["02", "07"],
    "危大工程": ["07", "02"],
    "特种作业": ["07", "02"],
}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_time_decay_weight(
    *,
    record_time: object,
    now: datetime | None = None,
    half_life_days: float = 30.0,
    min_decay: float = 0.01,
) -> float:
    """
    模块二：时间衰减（指数遗忘）。

    公式：
    W = 0.5 ** (age_days / half_life_days)
    - half_life_days=30 时，90 天样本权重约为 12.5%
    - 对未来时间/非法时间做边界保护
    """
    if half_life_days <= 0:
        half_life_days = 30.0
    now_dt = now or _now_utc()
    record_dt = _parse_datetime(record_time)
    if record_dt is None:
        return float(min_decay)
    age_days = max(0.0, (now_dt - record_dt).total_seconds() / 86400.0)
    decay = 0.5 ** (age_days / half_life_days)
    return max(float(min_decay), min(1.0, float(decay)))


def _normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    filled = {dim_id: max(0.0, _safe_float(weights.get(dim_id), 0.0)) for dim_id in DIMENSION_IDS}
    total = sum(filled.values())
    if total <= 1e-12:
        return dict(UNIFORM_PRIOR)
    return {dim_id: filled[dim_id] / total for dim_id in DIMENSION_IDS}


def _match_tag_dimensions(tags: Iterable[str]) -> List[str]:
    hits: List[str] = []
    for raw in tags:
        tag = str(raw or "").strip()
        if not tag:
            continue
        for key, dims in TAG_TO_DIMENSIONS.items():
            if key in tag or tag in key:
                hits.extend(dims)
    uniq = sorted({d for d in hits if d in DIMENSION_IDS})
    return uniq


def _extract_judge_items(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    支持两种输入：
    1) judge_feedbacks=[{score, qualitative_tags:[...]}]
    2) judge_scores + qualitative_tags_by_judge（与 judge_scores 同长度）
    """
    items = record.get("judge_feedbacks")
    if isinstance(items, list) and items:
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append(
                {
                    "score": _safe_float(it.get("score")),
                    "qualitative_tags": list(it.get("qualitative_tags") or []),
                }
            )
        if out:
            return out

    judge_scores = record.get("judge_scores")
    tags_by_judge = record.get("qualitative_tags_by_judge")
    if not isinstance(judge_scores, list) or not judge_scores:
        return []

    out = []
    for idx, value in enumerate(judge_scores):
        tags: List[str] = []
        if isinstance(tags_by_judge, list) and idx < len(tags_by_judge):
            raw_tags = tags_by_judge[idx]
            if isinstance(raw_tags, list):
                tags = [str(x) for x in raw_tags if str(x).strip()]
        out.append({"score": _safe_float(value), "qualitative_tags": tags})
    return out


def calibrate_weights(
    current_weights: Dict[str, float],
    feedback_records: Sequence[Dict[str, Any]],
    *,
    half_life_days: float = 30.0,
    lr_tag: float = 0.08,
    lr_global: float = 0.004,
    ridge_lambda: float = 0.06,
    min_weight: float = 0.005,
) -> Dict[str, Any]:
    """
    模块一核心：定向反演 + Ridge 稳定更新。

    - 有标签：误差梯度定向分配到命中维度（大幅调权）
    - 无标签：全局弱更新（防震荡）
    - 叠加时间衰减（模块二）
    - 使用 L2 正则将权重拉回先验，避免欠定问题导致权重崩溃
    """
    base = _normalize_weights(current_weights or {})
    gradient = {dim_id: 0.0 for dim_id in DIMENSION_IDS}
    stats = {
        "record_count": 0,
        "judge_count": 0,
        "tag_guided_updates": 0,
        "global_updates": 0,
        "avg_time_decay": 0.0,
    }
    decay_values: List[float] = []

    for record in feedback_records or []:
        if not isinstance(record, dict):
            continue
        predicted = _safe_float(record.get("predicted_total_score"))
        judge_items = _extract_judge_items(record)
        if not judge_items:
            continue
        stats["record_count"] += 1
        decay = compute_time_decay_weight(
            record_time=record.get("created_at") or record.get("timestamp"),
            half_life_days=half_life_days,
        )
        decay_values.append(decay)

        for judge in judge_items:
            delta = _safe_float(judge.get("score")) - predicted
            # 极小噪声不更新，避免无意义抖动
            if abs(delta) < 1e-6:
                continue
            stats["judge_count"] += 1
            tags = judge.get("qualitative_tags") or []
            matched_dims = _match_tag_dimensions(tags)
            if matched_dims:
                stats["tag_guided_updates"] += 1
                # 85% 梯度给命中维度，15% 给全局先验（防止过拟合）
                focused_mass = 0.85
                residual_mass = 1.0 - focused_mass
                focused_step = lr_tag * delta * decay * focused_mass
                residual_step = lr_tag * delta * decay * residual_mass
                focused_share = focused_step / max(1, len(matched_dims))
                for dim_id in matched_dims:
                    gradient[dim_id] += focused_share
                residual_share = residual_step / len(DIMENSION_IDS)
                for dim_id in DIMENSION_IDS:
                    gradient[dim_id] += residual_share
            else:
                stats["global_updates"] += 1
                global_step = lr_global * delta * decay
                share = global_step / len(DIMENSION_IDS)
                for dim_id in DIMENSION_IDS:
                    gradient[dim_id] += share

    if decay_values:
        stats["avg_time_decay"] = round(sum(decay_values) / len(decay_values), 6)

    # Ridge：w_new = w_old + grad - lambda * (w_old - prior)
    prior = dict(UNIFORM_PRIOR)
    updated: Dict[str, float] = {}
    for dim_id in DIMENSION_IDS:
        old_w = _safe_float(base.get(dim_id), prior[dim_id])
        ridge_pull = ridge_lambda * (old_w - prior[dim_id])
        new_w = old_w + gradient[dim_id] - ridge_pull
        updated[dim_id] = max(float(min_weight), float(new_w))

    normalized = _normalize_weights(updated)

    if np is not None:
        # 使用 numpy 给出稳定性指标，便于线上监控
        vec_old = np.array([base[d] for d in DIMENSION_IDS], dtype=float)
        vec_new = np.array([normalized[d] for d in DIMENSION_IDS], dtype=float)
        drift_l1 = float(np.abs(vec_new - vec_old).sum())
    else:  # pragma: no cover
        drift_l1 = float(sum(abs(normalized[d] - base[d]) for d in DIMENSION_IDS))

    return {
        "weights_norm": normalized,
        "dimension_multipliers": {
            d: round(normalized[d] / UNIFORM_PRIOR[d], 6) for d in DIMENSION_IDS
        },
        "stats": {
            **stats,
            "drift_l1": round(drift_l1, 6),
            "half_life_days": float(half_life_days),
        },
    }
