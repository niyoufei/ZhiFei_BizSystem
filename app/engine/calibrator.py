from __future__ import annotations

import bisect
import math
import random
from typing import Any, Dict, List, Sequence, Tuple

from app.project_taxonomy import normalize_bid_method, normalize_project_type

DIMENSION_IDS = [f"{i:02d}" for i in range(1, 17)]

PROJECT_TYPE_FEATURE_KEYS = {
    "装修及景观": "project_type_decoration_landscape",
    "高标准农田": "project_type_high_standard_farmland",
    "生态环境": "project_type_ecological_environment",
    "服务方案": "project_type_service_solution",
    "其他项目": "project_type_other",
}

BID_METHOD_FEATURE_KEYS = {
    "AI合理价格法": "bid_method_ai_reasonable_price",
    "AI综合评估法（三阶段）": "bid_method_ai_comprehensive_three_stage",
    "综合评估法（三阶段）": "bid_method_comprehensive_three_stage",
    "评定分离": "bid_method_bid_evaluation_separation",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _rank(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j + 2) / 2.0  # rank starts from 1
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _pearson(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or not x:
        return 0.0
    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    num = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y))
    den_x = math.sqrt(sum((a - x_mean) ** 2 for a in x))
    den_y = math.sqrt(sum((b - y_mean) ** 2 for b in y))
    den = den_x * den_y
    if den <= 1e-12:
        return 0.0
    return num / den


def _spearman(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    return _pearson(_rank(x), _rank(y))


def build_feature_row(
    report: Dict[str, Any],
    *,
    submission: Dict[str, Any] | None = None,
    project: Dict[str, Any] | None = None,
    qingtian_result: Dict[str, Any] | None = None,
    feature_schema_version: str = "v2",
) -> Dict[str, Any]:
    submission = submission or {}
    project = project or {}
    qingtian_result = qingtian_result or {}
    text = str(submission.get("text") or "")
    meta = _safe_dict(report.get("meta"))

    rule_total = _safe_float(report.get("rule_total_score", report.get("total_score", 0.0)))
    consistency_bonus = _safe_float(report.get("consistency_bonus", 0.0))
    penalties = report.get("penalties") or []
    penalty_points = sum(_safe_float(p.get("points", p.get("deduct", 0.0))) for p in penalties)
    dim_total_80_raw = report.get("dim_total_80")
    dim_total_90_raw = report.get("dim_total_90")
    dim_total_80 = _safe_float(dim_total_80_raw, -1.0)
    dim_total_90 = _safe_float(dim_total_90_raw, -1.0)
    if dim_total_80 < 0 and dim_total_90 >= 0:
        dim_total_80 = dim_total_90 * (80.0 / 90.0)
    if dim_total_90 < 0 and dim_total_80 >= 0:
        dim_total_90 = dim_total_80 * (90.0 / 80.0)
    if dim_total_80 < 0 and dim_total_90 < 0:
        # 历史记录缺字段时，按总分近似回推维度主分
        dim_total_90 = max(0.0, min(90.0, rule_total - consistency_bonus + penalty_points))
        dim_total_80 = dim_total_90 * (80.0 / 90.0)
    mandatory_req_hit_rate = _safe_float(report.get("mandatory_req_hit_rate", 0.0))
    evidence_units_count = _safe_float(report.get("evidence_units_count", 0.0))
    requirement_hits = report.get("requirement_hits") or []
    mandatory_total = sum(1 for r in requirement_hits if bool(r.get("mandatory")))
    mandatory_hit = sum(
        1 for r in requirement_hits if bool(r.get("mandatory")) and bool(r.get("hit"))
    )

    x_features: Dict[str, float] = {
        "rule_total_score": rule_total,
        "dim_total_80": dim_total_80,
        "dim_total_90": dim_total_90,
        "consistency_bonus": consistency_bonus,
        "penalty_points": penalty_points,
        "penalties_count": float(len(penalties)),
        "mandatory_req_hit_rate": mandatory_req_hit_rate,
        "mandatory_req_total": float(mandatory_total),
        "mandatory_req_hit": float(mandatory_hit),
        "evidence_units_count": evidence_units_count,
        "text_length": float(len(text)),
        "has_table": 1.0 if ("\t" in text or "|" in text) else 0.0,
        "has_images": 1.0 if _safe_float(submission.get("image_count", 0.0)) > 0 else 0.0,
    }

    project_type = normalize_project_type(
        project.get("project_type") or submission.get("project_type")
    )
    bid_method = normalize_bid_method(project.get("bid_method") or submission.get("bid_method"))
    x_features["project_type_known"] = 1.0 if project_type else 0.0
    x_features["bid_method_known"] = 1.0 if bid_method else 0.0
    for option, feature_key in PROJECT_TYPE_FEATURE_KEYS.items():
        x_features[feature_key] = 1.0 if project_type == option else 0.0
    for option, feature_key in BID_METHOD_FEATURE_KEYS.items():
        x_features[feature_key] = 1.0 if bid_method == option else 0.0

    material_utilization = _safe_dict(meta.get("material_utilization"))
    material_quality = _safe_dict(meta.get("material_quality"))
    material_gate = _safe_dict(meta.get("material_utilization_gate")) or _safe_dict(
        meta.get("material_gate")
    )
    evidence_trace = _safe_dict(meta.get("evidence_trace"))
    material_retrieval = _safe_dict(meta.get("material_retrieval"))
    uncovered_types = (
        material_utilization.get("uncovered_types")
        if isinstance(material_utilization.get("uncovered_types"), list)
        else []
    )
    available_types = (
        material_utilization.get("available_types")
        if isinstance(material_utilization.get("available_types"), list)
        else []
    )
    available_type_count = float(len(available_types))
    uncovered_type_count = float(len(uncovered_types))
    coverage_ratio = (
        max(0.0, (available_type_count - uncovered_type_count) / available_type_count)
        if available_type_count > 0
        else 0.0
    )
    x_features.update(
        {
            "material_retrieval_hit_rate": _safe_float(
                material_utilization.get("retrieval_hit_rate", 0.0)
            ),
            "material_retrieval_file_coverage_rate": _safe_float(
                material_utilization.get("retrieval_file_coverage_rate", 0.0)
            ),
            "material_consistency_hit_rate": _safe_float(
                material_utilization.get("consistency_hit_rate", 0.0)
            ),
            "material_dimension_hit_rate": _safe_float(
                material_utilization.get("material_dimension_hit_rate", 0.0)
            ),
            "material_available_type_count": available_type_count,
            "material_uncovered_type_count": uncovered_type_count,
            "material_type_coverage_ratio": coverage_ratio,
            "material_total_files": _safe_float(material_quality.get("total_files", 0.0)),
            "material_total_parsed_chars": _safe_float(
                material_quality.get("total_parsed_chars", 0.0)
            ),
            "material_parse_fail_ratio": _safe_float(material_quality.get("parse_fail_ratio", 0.0)),
            "material_gate_passed": 1.0
            if bool(material_gate.get("passed", not bool(material_gate.get("blocked"))))
            else 0.0,
            "material_gate_blocked": 1.0 if bool(material_gate.get("blocked")) else 0.0,
            "material_gate_warned": 1.0 if bool(material_gate.get("warned")) else 0.0,
            "evidence_mandatory_hit_rate": _safe_float(
                evidence_trace.get("mandatory_hit_rate", 0.0)
            ),
            "evidence_source_files_hit_count": _safe_float(
                evidence_trace.get("source_files_hit_count", 0.0)
            ),
            "evidence_total_requirements": _safe_float(
                evidence_trace.get("total_requirements", 0.0)
            ),
            "evidence_total_hits": _safe_float(evidence_trace.get("total_hits", 0.0)),
            "material_dimension_requirements": _safe_float(
                material_retrieval.get("material_dimension_requirements", 0.0)
            ),
            "feature_confidence_requirements": _safe_float(
                material_retrieval.get("feature_confidence_requirements", 0.0)
            ),
            "feedback_evolution_requirements": _safe_float(
                material_retrieval.get("feedback_evolution_requirements", 0.0)
            ),
        }
    )

    weights_norm = ((report.get("meta") or {}).get("expert_profile_snapshot") or {}).get(
        "weights_norm"
    ) or {}
    rule_dim_scores = report.get("rule_dim_scores") or {}
    if not rule_dim_scores:
        legacy_dim_scores = report.get("dimension_scores") or {}
        for dim_id in DIMENSION_IDS:
            item = legacy_dim_scores.get(dim_id) or legacy_dim_scores.get(f"D{dim_id}") or {}
            rule_dim_scores[dim_id] = {"dim_score": _safe_float(item.get("score", 0.0))}

    for dim_id in DIMENSION_IDS:
        x_features[f"w_{dim_id}"] = _safe_float(weights_norm.get(dim_id, 1.0 / 16))
        x_features[f"dim_{dim_id}"] = _safe_float(
            (rule_dim_scores.get(dim_id) or {}).get("dim_score", 0.0)
        )

    label = qingtian_result.get("qt_total_score")
    y_label = _safe_float(label) if label is not None else None
    return {
        "feature_schema_version": feature_schema_version,
        "x_features": x_features,
        "y_label": y_label,
    }


def _vectorize_feature_rows(
    feature_rows: List[Dict[str, Any]],
) -> Tuple[List[str], List[List[float]], List[float]]:
    keys = sorted({key for row in feature_rows for key in (row.get("x_features") or {}).keys()})
    X: List[List[float]] = []
    y: List[float] = []
    for row in feature_rows:
        x = row.get("x_features") or {}
        label = row.get("y_label")
        if label is None:
            continue
        X.append([_safe_float(x.get(k, 0.0)) for k in keys])
        y.append(_safe_float(label))
    return keys, X, y


def _calc_metrics(y_true: Sequence[float], y_pred: Sequence[float]) -> Dict[str, float]:
    if not y_true:
        return {"mae": 0.0, "rmse": 0.0, "spearman": 0.0}
    abs_err = [abs(a - b) for a, b in zip(y_true, y_pred)]
    sq_err = [(a - b) ** 2 for a, b in zip(y_true, y_pred)]
    mae = sum(abs_err) / len(abs_err)
    rmse = math.sqrt(sum(sq_err) / len(sq_err))
    return {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "spearman": round(_spearman(list(y_true), list(y_pred)), 4),
    }


def calc_metrics(y_true: Sequence[float], y_pred: Sequence[float]) -> Dict[str, float]:
    """公共指标计算（MAE/RMSE/Spearman）。用于训练闸门与评估。"""
    return _calc_metrics(y_true, y_pred)


def _extract_xy_rule(feature_rows: List[Dict[str, Any]]) -> Tuple[List[float], List[float]]:
    xs: List[float] = []
    ys: List[float] = []
    for row in feature_rows:
        label = row.get("y_label")
        if label is None:
            continue
        x = row.get("x_features") or {}
        xs.append(_safe_float(x.get("rule_total_score", 0.0)))
        ys.append(_safe_float(label))
    return xs, ys


def _baseline_predictions(feature_rows: List[Dict[str, Any]]) -> List[float]:
    xs, _ = _extract_xy_rule(feature_rows)
    return [_clip(x) for x in xs]


def train_offset_calibrator(feature_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """恒定偏置校准：pred = clip(rule_total_score + bias)"""
    xs, ys = _extract_xy_rule(feature_rows)
    if len(xs) < 1:
        raise ValueError("训练样本不足，至少需要1条可用样本")
    diffs = [y - x for x, y in zip(xs, ys)]
    bias = sum(diffs) / len(diffs)
    preds = [_clip(x + bias) for x in xs]
    sigma = math.sqrt(sum((a - b) ** 2 for a, b in zip(ys, preds)) / len(xs))
    base = [_clip(x) for x in xs]
    model_metrics = _calc_metrics(ys, preds)
    base_metrics = _calc_metrics(ys, base)
    gate_passed = (
        model_metrics["mae"] <= base_metrics["mae"] + 1e-9
        and model_metrics["spearman"] >= base_metrics["spearman"] - 1e-9
    )
    return {
        "model_type": "offset",
        "feature_schema_version": "v2",
        "bias": round(float(bias), 6),
        "sigma": round(float(sigma), 4),
        "metrics": {
            **model_metrics,
            "baseline_mae": base_metrics["mae"],
            "baseline_rmse": base_metrics["rmse"],
            "baseline_spearman": base_metrics["spearman"],
            "sample_count": len(xs),
        },
        "gate_passed": gate_passed,
    }


def train_linear1d_calibrator(
    feature_rows: List[Dict[str, Any]],
    *,
    alpha: float = 1.0,
) -> Dict[str, Any]:
    """一维线性校准：pred = clip(slope*rule_total_score + intercept)"""
    xs, ys = _extract_xy_rule(feature_rows)
    if len(xs) < 3:
        raise ValueError("训练样本不足，至少需要3条可用样本")
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    alpha = max(0.0, float(alpha))
    slope = cov / (var + alpha) if (var + alpha) > 1e-12 else 0.0
    if slope < 0:
        slope = 0.0
    intercept = my - slope * mx
    preds = [_clip(slope * x + intercept) for x in xs]
    sigma = math.sqrt(sum((a - b) ** 2 for a, b in zip(ys, preds)) / n)
    base = [_clip(x) for x in xs]
    model_metrics = _calc_metrics(ys, preds)
    base_metrics = _calc_metrics(ys, base)
    gate_passed = (
        model_metrics["mae"] <= base_metrics["mae"] + 1e-9
        and model_metrics["spearman"] >= base_metrics["spearman"] - 1e-9
    )
    return {
        "model_type": "linear1d",
        "feature_schema_version": "v2",
        "alpha": alpha,
        "slope": round(float(slope), 8),
        "intercept": round(float(intercept), 8),
        "sigma": round(float(sigma), 4),
        "metrics": {
            **model_metrics,
            "baseline_mae": base_metrics["mae"],
            "baseline_rmse": base_metrics["rmse"],
            "baseline_spearman": base_metrics["spearman"],
            "sample_count": n,
        },
        "gate_passed": gate_passed,
    }


def _isotonic_fit(xs: List[float], ys: List[float]) -> Tuple[List[float], List[float]]:
    # 按 x 升序排序，并对重复 x 聚合为加权均值（权重=出现次数）
    pairs = sorted(zip(xs, ys), key=lambda p: p[0])
    uniq_x: List[float] = []
    uniq_y: List[float] = []
    weights: List[float] = []
    for x, y in pairs:
        if uniq_x and abs(x - uniq_x[-1]) <= 1e-12:
            w = weights[-1] + 1.0
            uniq_y[-1] = (uniq_y[-1] * weights[-1] + y) / w
            weights[-1] = w
        else:
            uniq_x.append(float(x))
            uniq_y.append(float(y))
            weights.append(1.0)

    # PAV algorithm
    blocks: List[Dict[str, float]] = []
    for x, y, w in zip(uniq_x, uniq_y, weights):
        blocks.append({"x": x, "sum_y": y * w, "sum_w": w})
        while len(blocks) >= 2:
            b2 = blocks[-1]
            b1 = blocks[-2]
            avg1 = b1["sum_y"] / b1["sum_w"]
            avg2 = b2["sum_y"] / b2["sum_w"]
            if avg1 <= avg2 + 1e-12:
                break
            # merge
            merged = {
                "x": b2["x"],
                "sum_y": b1["sum_y"] + b2["sum_y"],
                "sum_w": b1["sum_w"] + b2["sum_w"],
            }
            blocks = blocks[:-2]
            blocks.append(merged)

    # expand fitted y for each uniq_x
    fitted_y: List[float] = []
    idx = 0
    for block in blocks:
        avg = block["sum_y"] / block["sum_w"]
        # Determine how many original points collapsed into this block by sum_w
        count = int(round(block["sum_w"]))
        for _ in range(count):
            if idx < len(uniq_x):
                fitted_y.append(avg)
                idx += 1
    fitted_y = fitted_y[: len(uniq_x)]
    return uniq_x, fitted_y


def train_isotonic1d_calibrator(feature_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """单调回归校准（isotonic-like）：对 rule_total_score 做非降映射到 qt_total_score。"""
    xs, ys = _extract_xy_rule(feature_rows)
    if len(xs) < 5:
        raise ValueError("训练样本不足，至少需要5条可用样本")
    x_points, y_points = _isotonic_fit(xs, ys)

    def _predict_one(x: float) -> float:
        if not x_points:
            return 0.0
        if x <= x_points[0]:
            return y_points[0]
        if x >= x_points[-1]:
            return y_points[-1]
        j = bisect.bisect_right(x_points, x) - 1
        j = max(0, min(j, len(x_points) - 2))
        x0, x1 = x_points[j], x_points[j + 1]
        y0, y1 = y_points[j], y_points[j + 1]
        if abs(x1 - x0) <= 1e-12:
            return y0
        t = (x - x0) / (x1 - x0)
        return y0 + (y1 - y0) * t

    preds = [_clip(_predict_one(x)) for x in xs]
    sigma = math.sqrt(sum((a - b) ** 2 for a, b in zip(ys, preds)) / len(xs))
    base = [_clip(x) for x in xs]
    model_metrics = _calc_metrics(ys, preds)
    base_metrics = _calc_metrics(ys, base)
    gate_passed = (
        model_metrics["mae"] <= base_metrics["mae"] + 1e-9
        and model_metrics["spearman"] >= base_metrics["spearman"] - 1e-9
    )
    return {
        "model_type": "isotonic1d",
        "feature_schema_version": "v2",
        "x_points": [round(float(x), 6) for x in x_points],
        "y_points": [round(float(y), 6) for y in y_points],
        "sigma": round(float(sigma), 4),
        "metrics": {
            **model_metrics,
            "baseline_mae": base_metrics["mae"],
            "baseline_rmse": base_metrics["rmse"],
            "baseline_spearman": base_metrics["spearman"],
            "sample_count": len(xs),
            "support_points": len(x_points),
        },
        "gate_passed": gate_passed,
    }


def _kfold_splits(n: int, k: int, seed: int) -> List[List[int]]:
    n = max(0, int(n))
    if n <= 0:
        return []
    k = max(2, int(k))
    k = min(k, n)
    idx = list(range(n))
    rnd = random.Random(int(seed))
    rnd.shuffle(idx)
    folds: List[List[int]] = []
    base = n // k
    rem = n % k
    start = 0
    for i in range(k):
        size = base + (1 if i < rem else 0)
        folds.append(idx[start : start + size])
        start += size
    return [f for f in folds if f]


def cross_validate_calibrator(
    *,
    model_type: str,
    feature_rows: List[Dict[str, Any]],
    alpha: float = 1.0,
    seed: int = 42,
) -> Dict[str, Any]:
    rows = [r for r in feature_rows if r.get("y_label") is not None]
    n = len(rows)
    if n < 3:
        return {
            "ok": False,
            "reason": "样本不足",
            "metrics": {"mae": 0.0, "rmse": 0.0, "spearman": 0.0},
            "pred_count": 0,
        }

    # 小样本优先用 LOOCV，避免折数过少导致方差大
    use_loocv = n <= 10
    folds = [[i] for i in range(n)] if use_loocv else _kfold_splits(n, 5, seed)

    y_true: List[float] = []
    y_pred: List[float] = []

    def _train(train_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        mt = str(model_type).lower()
        if mt == "ridge":
            return train_ridge_calibrator(train_rows, alpha=float(alpha))
        if mt == "offset":
            return train_offset_calibrator(train_rows)
        if mt == "linear1d":
            return train_linear1d_calibrator(train_rows, alpha=float(alpha))
        if mt == "isotonic1d":
            return train_isotonic1d_calibrator(train_rows)
        raise ValueError(f"未知 model_type: {model_type}")

    for val_idx in folds:
        train_rows = [rows[i] for i in range(n) if i not in set(val_idx)]
        if len(train_rows) < 3:
            continue
        try:
            model = _train(train_rows)
        except Exception:
            continue
        for i in val_idx:
            x = rows[i].get("x_features") or {}
            try:
                pred, _ = predict_with_model(model, x)
            except Exception:
                continue
            y_true.append(_safe_float(rows[i].get("y_label")))
            y_pred.append(_safe_float(pred))

    if not y_true:
        return {
            "ok": False,
            "reason": "交叉验证失败",
            "metrics": {"mae": 0.0, "rmse": 0.0, "spearman": 0.0},
            "pred_count": 0,
        }

    return {
        "ok": True,
        "metrics": _calc_metrics(y_true, y_pred),
        "pred_count": len(y_true),
        "folds": len(folds),
        "mode": "loocv" if use_loocv else "kfold",
    }


def train_best_calibrator_auto(
    feature_rows: List[Dict[str, Any]],
    *,
    alpha: float = 1.0,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    训练多候选校准器并选择最优（以 CV-MAE 最小为主，且不降低 Spearman）。
    返回 best_artifact，其中包含 best_selection 诊断信息。
    """
    rows = [r for r in feature_rows if r.get("y_label") is not None]
    if len(rows) < 3:
        raise ValueError("训练样本不足，至少需要3条可用样本")

    y_true = [_safe_float(r.get("y_label")) for r in rows]
    baseline = _baseline_predictions(rows)
    baseline_metrics = _calc_metrics(y_true, baseline)

    # 候选集：优先单调/低复杂度，最后才尝试 ridge
    candidates: List[Dict[str, Any]] = []
    for mt, min_samples in [
        ("offset", 3),
        ("linear1d", 3),
        ("isotonic1d", 5),
        ("ridge", 8),
    ]:
        if len(rows) < min_samples:
            continue
        try:
            if mt == "offset":
                artifact = train_offset_calibrator(rows)
            elif mt == "linear1d":
                artifact = train_linear1d_calibrator(rows, alpha=float(alpha))
            elif mt == "isotonic1d":
                artifact = train_isotonic1d_calibrator(rows)
            else:
                artifact = train_ridge_calibrator(rows, alpha=float(alpha))
        except Exception as exc:
            candidates.append({"model_type": mt, "ok": False, "error": str(exc)})
            continue

        cv = cross_validate_calibrator(
            model_type=mt, feature_rows=rows, alpha=float(alpha), seed=int(seed)
        )
        cv_metrics = (
            (cv.get("metrics") or {})
            if bool(cv.get("ok"))
            else {"mae": 0.0, "rmse": 0.0, "spearman": 0.0}
        )
        improve_threshold = max(0.2, float(baseline_metrics["mae"]) * 0.01)
        spearman_tolerance = 0.02
        gate_passed = (
            bool(cv.get("ok"))
            and float(cv_metrics.get("mae", 0.0))
            <= float(baseline_metrics["mae"]) - improve_threshold
            and float(cv_metrics.get("spearman", 0.0))
            >= float(baseline_metrics["spearman"]) - spearman_tolerance
        )
        artifact.setdefault("metrics", {})
        artifact["metrics"]["cv_mae"] = cv_metrics.get("mae")
        artifact["metrics"]["cv_rmse"] = cv_metrics.get("rmse")
        artifact["metrics"]["cv_spearman"] = cv_metrics.get("spearman")
        artifact["metrics"]["cv_mode"] = cv.get("mode")
        artifact["metrics"]["cv_pred_count"] = cv.get("pred_count")
        artifact["metrics"]["baseline_mae"] = baseline_metrics["mae"]
        artifact["metrics"]["baseline_rmse"] = baseline_metrics["rmse"]
        artifact["metrics"]["baseline_spearman"] = baseline_metrics["spearman"]
        artifact["metrics"]["gate_improve_threshold"] = round(improve_threshold, 4)
        artifact["metrics"]["gate_spearman_tolerance"] = spearman_tolerance
        artifact["gate_passed"] = gate_passed

        candidates.append(
            {
                "model_type": mt,
                "ok": True,
                "gate_passed": gate_passed,
                "cv": cv,
                "metrics": artifact.get("metrics") or {},
            }
        )

    passing = [c for c in candidates if c.get("ok") and c.get("gate_passed")]
    if passing:
        best = sorted(passing, key=lambda c: float((c.get("metrics") or {}).get("cv_mae") or 1e9))[
            0
        ]
    else:
        ok = [c for c in candidates if c.get("ok")]
        best = (
            sorted(ok, key=lambda c: float((c.get("metrics") or {}).get("cv_mae") or 1e9))[0]
            if ok
            else None
        )

    if not best:
        raise ValueError("未能训练任何可用的校准器候选")

    best_type = str(best.get("model_type") or "ridge")
    if best_type == "offset":
        best_artifact = train_offset_calibrator(rows)
    elif best_type == "linear1d":
        best_artifact = train_linear1d_calibrator(rows, alpha=float(alpha))
    elif best_type == "isotonic1d":
        best_artifact = train_isotonic1d_calibrator(rows)
    else:
        best_artifact = train_ridge_calibrator(rows, alpha=float(alpha))

    # 将 auto 选择诊断写入 artifact，便于 UI/审计
    best_artifact.setdefault("metrics", {})
    best_artifact["metrics"]["baseline_mae"] = baseline_metrics["mae"]
    best_artifact["metrics"]["baseline_rmse"] = baseline_metrics["rmse"]
    best_artifact["metrics"]["baseline_spearman"] = baseline_metrics["spearman"]
    best_artifact["best_selection"] = {
        "baseline": baseline_metrics,
        "candidates": candidates,
        "selected_model_type": best_type,
    }

    # gate_passed: 使用候选阶段的 gate_passed（CV 口径）
    best_artifact["gate_passed"] = bool(best.get("gate_passed"))
    return best_artifact


def train_ridge_calibrator(
    feature_rows: List[Dict[str, Any]],
    *,
    alpha: float = 1.0,
    iterations: int = 1500,
    learning_rate: float = 0.02,
) -> Dict[str, Any]:
    feature_keys, X, y = _vectorize_feature_rows(feature_rows)
    if len(X) < 3:
        raise ValueError("训练样本不足，至少需要3条可用样本")

    n = len(X)
    m = len(feature_keys)
    means = [sum(X[i][j] for i in range(n)) / n for j in range(m)]
    stds: List[float] = []
    for j in range(m):
        var = sum((X[i][j] - means[j]) ** 2 for i in range(n)) / n
        std = math.sqrt(var)
        stds.append(std if std > 1e-8 else 1.0)
    Xs = [[(X[i][j] - means[j]) / stds[j] for j in range(m)] for i in range(n)]

    weights = [0.0] * m
    bias = sum(y) / n
    alpha = max(0.0, float(alpha))
    lr = max(1e-4, float(learning_rate))
    steps = max(300, int(iterations))

    for _ in range(steps):
        grad_w = [0.0] * m
        grad_b = 0.0
        inv_n2 = 2.0 / n
        for i in range(n):
            pred = _dot(weights, Xs[i]) + bias
            err = pred - y[i]
            grad_b += inv_n2 * err
            coeff = inv_n2 * err
            for j in range(m):
                grad_w[j] += coeff * Xs[i][j]
        for j in range(m):
            grad_w[j] += 2.0 * alpha * weights[j]
            weights[j] -= lr * grad_w[j]
        bias -= lr * grad_b

    preds = [_clip(_dot(weights, Xs[i]) + bias) for i in range(n)]
    sigma = math.sqrt(sum((a - b) ** 2 for a, b in zip(y, preds)) / n)

    baseline = []
    rule_idx = (
        feature_keys.index("rule_total_score") if "rule_total_score" in feature_keys else None
    )
    for i in range(n):
        if rule_idx is None:
            baseline.append(sum(y) / n)
        else:
            baseline.append(_clip(X[i][rule_idx]))

    model_metrics = _calc_metrics(y, preds)
    base_metrics = _calc_metrics(y, baseline)
    gate_passed = (
        model_metrics["mae"] <= base_metrics["mae"] + 1e-9
        and model_metrics["spearman"] >= base_metrics["spearman"] - 1e-9
    )

    return {
        "model_type": "ridge",
        "feature_schema_version": "v2",
        "feature_keys": feature_keys,
        "means": means,
        "stds": stds,
        "weights": weights,
        "bias": bias,
        "alpha": alpha,
        "sigma": round(float(sigma), 4),
        "metrics": {
            **model_metrics,
            "baseline_mae": base_metrics["mae"],
            "baseline_rmse": base_metrics["rmse"],
            "baseline_spearman": base_metrics["spearman"],
            "sample_count": n,
        },
        "gate_passed": gate_passed,
    }


def predict_with_model(
    model_artifact: Dict[str, Any], x_features: Dict[str, Any]
) -> Tuple[float, Dict[str, float]]:
    model_type = str(model_artifact.get("model_type") or "").lower().strip()
    if not model_type:
        # 兼容旧模型：存在 feature_keys 则默认 ridge
        model_type = "ridge" if model_artifact.get("feature_keys") else "offset"

    if model_type == "offset":
        x = _safe_float(x_features.get("rule_total_score", 0.0))
        bias = _safe_float(model_artifact.get("bias", 0.0))
        pred = _clip(x + bias)
        sigma = max(0.5, _safe_float(model_artifact.get("sigma", 2.0), 2.0))
    elif model_type == "linear1d":
        x = _safe_float(x_features.get("rule_total_score", 0.0))
        slope = _safe_float(model_artifact.get("slope", 0.0))
        intercept = _safe_float(model_artifact.get("intercept", 0.0))
        pred = _clip(slope * x + intercept)
        sigma = max(0.5, _safe_float(model_artifact.get("sigma", 2.0), 2.0))
    elif model_type == "isotonic1d":
        x = _safe_float(x_features.get("rule_total_score", 0.0))
        x_points = [float(v) for v in (model_artifact.get("x_points") or [])]
        y_points = [float(v) for v in (model_artifact.get("y_points") or [])]
        if not x_points or len(x_points) != len(y_points):
            raise ValueError("isotonic1d 模型参数不完整")
        if x <= x_points[0]:
            pred = _clip(y_points[0])
        elif x >= x_points[-1]:
            pred = _clip(y_points[-1])
        else:
            j = bisect.bisect_right(x_points, x) - 1
            j = max(0, min(j, len(x_points) - 2))
            x0, x1 = x_points[j], x_points[j + 1]
            y0, y1 = y_points[j], y_points[j + 1]
            if abs(x1 - x0) <= 1e-12:
                pred = _clip(y0)
            else:
                t = (x - x0) / (x1 - x0)
                pred = _clip(y0 + (y1 - y0) * t)
        sigma = max(0.5, _safe_float(model_artifact.get("sigma", 2.0), 2.0))
    else:
        # ridge (multi-feature)
        feature_keys = list(model_artifact.get("feature_keys") or [])
        means = list(model_artifact.get("means") or [])
        stds = list(model_artifact.get("stds") or [])
        weights = list(model_artifact.get("weights") or [])
        bias = _safe_float(model_artifact.get("bias", 0.0))

        if not feature_keys or not (len(feature_keys) == len(means) == len(stds) == len(weights)):
            raise ValueError("校准模型参数不完整")

        vec = [_safe_float(x_features.get(k, 0.0)) for k in feature_keys]
        scaled = [
            (vec[i] - means[i]) / (stds[i] if abs(stds[i]) > 1e-12 else 1.0)
            for i in range(len(feature_keys))
        ]
        pred = _clip(_dot(weights, scaled) + bias)
        sigma = max(0.5, _safe_float(model_artifact.get("sigma", 2.0), 2.0))

    lower = _clip(pred - sigma)
    upper = _clip(pred + sigma)
    return round(pred, 2), {
        "sigma": round(sigma, 2),
        "lower": round(lower, 2),
        "upper": round(upper, 2),
    }
