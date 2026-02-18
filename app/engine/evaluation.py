from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence

DIMENSION_IDS = [f"{i:02d}" for i in range(1, 17)]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _rank(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _pearson(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den_x = math.sqrt(sum((a - mx) ** 2 for a in x))
    den_y = math.sqrt(sum((b - my) ** 2 for b in y))
    den = den_x * den_y
    if den <= 1e-12:
        return 0.0
    return num / den


def _spearman(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    return _pearson(_rank(x), _rank(y))


def _metrics(y_true: Sequence[float], y_pred: Sequence[float]) -> Dict[str, float]:
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


def _normalize_engine_version(value: Any) -> str:
    v = str(value or "").lower().strip()
    if v.startswith("v2"):
        return "v2"
    if v.startswith("v1"):
        return "v1"
    return "unknown"


def _latest_qt_by_submission(qingtian_results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for item in qingtian_results:
        sid = str(item.get("submission_id") or "")
        if not sid:
            continue
        prev = latest.get(sid)
        if prev is None or str(item.get("created_at", "")) >= str(prev.get("created_at", "")):
            latest[sid] = item
    return latest


def _latest_reports_by_engine(
    score_reports: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    # submission_id -> {"v1": report, "v2": report}
    latest: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for report in score_reports:
        sid = str(report.get("submission_id") or "")
        if not sid:
            continue
        engine = _normalize_engine_version(report.get("scoring_engine_version"))
        if engine not in {"v1", "v2"}:
            continue
        latest.setdefault(sid, {})
        prev = latest[sid].get(engine)
        if prev is None or str(report.get("created_at", "")) >= str(prev.get("created_at", "")):
            latest[sid][engine] = report
    return latest


def _dimension_vector_from_report(report: Dict[str, Any]) -> Dict[str, float]:
    vec: Dict[str, float] = {}
    rule_dim_scores = report.get("rule_dim_scores") or {}
    if isinstance(rule_dim_scores, dict) and rule_dim_scores:
        for dim_id in DIMENSION_IDS:
            item = rule_dim_scores.get(dim_id) or {}
            vec[dim_id] = _safe_float(item.get("dim_score", 0.0))
        return vec

    dim_scores = report.get("dimension_scores") or {}
    if isinstance(dim_scores, dict):
        for dim_id in DIMENSION_IDS:
            item = dim_scores.get(dim_id) or dim_scores.get(f"D{dim_id}") or {}
            vec[dim_id] = _safe_float(item.get("score", 0.0))
    return vec


def _cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    keys = [k for k in a.keys() if k in b]
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    na = math.sqrt(sum(a[k] ** 2 for k in keys))
    nb = math.sqrt(sum(b[k] ** 2 for k in keys))
    den = na * nb
    if den <= 1e-12:
        return 0.0
    return dot / den


def _expected_issue_from_reason(reason_text: str) -> str:
    t = (reason_text or "").lower()
    if any(k in t for k in ["冲突", "不一致", "矛盾"]):
        return "consistency"
    if any(k in t for k in ["空泛", "空话", "泛泛", "无证据"]):
        return "empty"
    if any(k in t for k in ["岗位", "验收", "落实", "动作", "措施"]):
        return "action"
    return "generic"


def _penalty_hit_rate(
    reasons: List[str], penalties: List[Dict[str, Any]], lint_findings: List[Dict[str, Any]]
) -> float:
    if not reasons:
        return 0.0
    codes = {str(p.get("code") or "") for p in penalties}
    lint_codes = {str(finding.get("issue_code") or "") for finding in lint_findings}
    hit = 0
    for r in reasons:
        expected = _expected_issue_from_reason(r)
        ok = False
        if expected == "consistency":
            ok = ("P-CONSIST-001" in codes) or ("ConsistencyConflict" in lint_codes)
        elif expected == "empty":
            ok = ("P-EMPTY-002" in codes) or ("EmptyPromiseWithoutEvidence" in lint_codes)
        elif expected == "action":
            ok = ("P-ACTION-002" in codes) or ("ActionMissingHardElements" in lint_codes)
        else:
            ok = bool(codes or lint_codes)
        if ok:
            hit += 1
    return hit / len(reasons)


def evaluate_project_variants(
    *,
    project_id: str,
    submissions: List[Dict[str, Any]],
    score_reports: List[Dict[str, Any]],
    qingtian_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    project_submission_ids = {
        str(s.get("id")) for s in submissions if str(s.get("project_id")) == project_id
    }
    reports = [
        r
        for r in score_reports
        if str(r.get("project_id")) == project_id
        and str(r.get("submission_id")) in project_submission_ids
    ]
    qt_latest = _latest_qt_by_submission(
        [q for q in qingtian_results if str(q.get("submission_id")) in project_submission_ids]
    )
    by_engine = _latest_reports_by_engine(reports)

    variants: Dict[str, Dict[str, Any]] = {
        "v1": {"pairs": []},
        "v2": {"pairs": []},
        "v2_calib": {"pairs": []},
    }

    for sid in sorted(project_submission_ids):
        qt = qt_latest.get(sid)
        if not qt:
            continue
        qt_total = _safe_float(qt.get("qt_total_score"))
        qt_dim = qt.get("qt_dim_scores") or {}
        reasons = []
        for item in qt.get("qt_reasons") or []:
            if isinstance(item, dict):
                reasons.append(str(item.get("text") or item.get("reason") or ""))
            else:
                reasons.append(str(item))

        v1_report = (by_engine.get(sid) or {}).get("v1")
        if v1_report:
            variants["v1"]["pairs"].append(
                (
                    sid,
                    qt_total,
                    _safe_float(v1_report.get("rule_total_score", 0.0)),
                    qt_dim,
                    reasons,
                    v1_report,
                )
            )

        v2_report = (by_engine.get(sid) or {}).get("v2")
        if v2_report:
            variants["v2"]["pairs"].append(
                (
                    sid,
                    qt_total,
                    _safe_float(v2_report.get("rule_total_score", 0.0)),
                    qt_dim,
                    reasons,
                    v2_report,
                )
            )
            pred = v2_report.get("pred_total_score")
            if pred is not None:
                variants["v2_calib"]["pairs"].append(
                    (sid, qt_total, _safe_float(pred), qt_dim, reasons, v2_report)
                )

    result: Dict[str, Any] = {
        "project_id": project_id,
        "sample_count_qt": len(qt_latest),
        "variants": {},
        "acceptance": {},
        "computed_at": _now_iso(),
    }

    for variant, data in variants.items():
        pairs = data["pairs"]
        y_true = [p[1] for p in pairs]
        y_pred = [p[2] for p in pairs]
        m = _metrics(y_true, y_pred)

        profile_sims: List[float] = []
        penalty_hits: List[float] = []
        for _, _, _, qt_dim, reasons, report in pairs:
            if isinstance(qt_dim, dict) and qt_dim:
                rule_vec = _dimension_vector_from_report(report)
                qt_vec = {
                    str(k): _safe_float(v) for k, v in qt_dim.items() if str(k) in DIMENSION_IDS
                }
                if qt_vec and rule_vec:
                    profile_sims.append(_cosine_similarity(rule_vec, qt_vec))
            penalty_hits.append(
                _penalty_hit_rate(
                    reasons,
                    report.get("penalties") or [],
                    report.get("lint_findings") or [],
                )
            )

        result["variants"][variant] = {
            "sample_count": len(pairs),
            "mae": m["mae"],
            "rmse": m["rmse"],
            "spearman": m["spearman"],
            "profile_similarity": round(sum(profile_sims) / len(profile_sims), 4)
            if profile_sims
            else None,
            "penalty_hit_rate": round(sum(penalty_hits) / len(penalty_hits), 4)
            if penalty_hits
            else None,
        }

    v1 = result["variants"].get("v1") or {}
    v2 = result["variants"].get("v2") or {}
    v2c = result["variants"].get("v2_calib") or {}

    def _num(x: Any) -> float:
        return _safe_float(x, 0.0)

    result["acceptance"] = {
        "mae_rmse_improved_vs_v1": (_num(v2c.get("mae")) < _num(v1.get("mae")))
        and (_num(v2c.get("rmse")) < _num(v1.get("rmse"))),
        "rank_corr_not_worse_vs_v1": _num(v2c.get("spearman")) >= _num(v1.get("spearman")),
        "profile_similarity_improved_v2_vs_v1": (
            v2.get("profile_similarity") is not None
            and v1.get("profile_similarity") is not None
            and _num(v2.get("profile_similarity")) > _num(v1.get("profile_similarity"))
        ),
        "penalty_hit_rate_improved_v2_vs_v1": (
            v2.get("penalty_hit_rate") is not None
            and v1.get("penalty_hit_rate") is not None
            and _num(v2.get("penalty_hit_rate")) > _num(v1.get("penalty_hit_rate"))
        ),
    }
    return result
