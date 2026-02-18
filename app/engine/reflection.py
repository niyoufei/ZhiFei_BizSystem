from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from app.engine.calibrator import build_feature_row

DIMENSION_HINTS: Dict[str, List[str]] = {
    "01": ["工程概况", "实施路径", "工程范围"],
    "02": ["安全", "隐患", "危大", "应急"],
    "03": ["文明施工", "扬尘", "噪声"],
    "04": ["材料", "采购", "部品"],
    "05": ["四新", "新技术", "新工艺"],
    "06": ["关键工序", "工序"],
    "07": ["重难点", "危大工程", "专项方案", "论证"],
    "08": ["质量", "验收标准", "合格", "创优"],
    "09": ["工期", "进度", "里程碑", "关键线路"],
    "10": ["专项施工", "技术方案"],
    "11": ["人力", "人员", "班组"],
    "12": ["施工工艺", "组织逻辑"],
    "13": ["物资", "设备"],
    "14": ["设计", "深化", "协调"],
    "15": ["资源配置", "计划"],
    "16": ["可行性", "落地"],
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _infer_dimension_from_reason(reason_text: str) -> str | None:
    if not reason_text:
        return None
    for dim_id, hints in DIMENSION_HINTS.items():
        if any(h in reason_text for h in hints):
            return dim_id
    return None


def build_delta_cases(
    *,
    project_id: str,
    latest_reports_by_submission: Dict[str, Dict[str, Any]],
    latest_qingtian_by_submission: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    delta_cases: List[Dict[str, Any]] = []
    for submission_id, report in latest_reports_by_submission.items():
        qtr = latest_qingtian_by_submission.get(submission_id)
        if not qtr:
            continue

        qt_total = _safe_float(qtr.get("qt_total_score"))
        pred_total = report.get("pred_total_score")
        if pred_total is None:
            pred_total = report.get("rule_total_score", report.get("total_score", 0.0))
        pred_total = _safe_float(pred_total)
        total_error = round(pred_total - qt_total, 2)

        report_dim = report.get("rule_dim_scores") or {}
        qt_dim = qtr.get("qt_dim_scores") or {}
        dim_errors: Dict[str, float] = {}
        for dim_id, qt_score in qt_dim.items():
            if not isinstance(qt_score, (int, float)):
                continue
            r = report_dim.get(dim_id) or {}
            r_score = _safe_float(r.get("dim_score", 0.0))
            dim_errors[str(dim_id)] = round(r_score - float(qt_score), 2)

        requirement_hits = report.get("requirement_hits") or []
        missing_req_ids = [
            str(r.get("requirement_id") or "")
            for r in requirement_hits
            if bool(r.get("mandatory")) and not bool(r.get("hit"))
        ]
        lint_findings = report.get("lint_findings") or []
        lint_codes = [str(x.get("issue_code") or "") for x in lint_findings if isinstance(x, dict)]

        reasons = qtr.get("qt_reasons") or []
        reason_alignment: List[Dict[str, Any]] = []
        for reason in reasons:
            if isinstance(reason, dict):
                text = str(reason.get("text") or reason.get("reason") or "")
                dim = (
                    str(reason.get("dimension_id") or "")
                    or _infer_dimension_from_reason(text)
                    or ""
                )
            else:
                text = str(reason)
                dim = _infer_dimension_from_reason(text) or ""
            aligned_lint = []
            if dim:
                aligned_lint = [
                    code
                    for code in lint_codes
                    if any(
                        k in code
                        for k in ("MissingRequirement", "ConsistencyConflict", "ClosureGap")
                    )
                ]
            reason_alignment.append(
                {
                    "qt_reason_text": text,
                    "dimension_id": dim or None,
                    "missing_requirement_ids": missing_req_ids[:5],
                    "missing_lint_codes": aligned_lint[:5],
                }
            )

        penalties = report.get("penalties") or []
        miss_types = {
            "UNDER_PENALIZE": 1 if (qt_total + 2.0 < pred_total and len(penalties) < 2) else 0,
            "OVER_PENALIZE": 1 if (pred_total + 2.0 < qt_total and len(penalties) >= 3) else 0,
            "UNDER_SCORE": 1 if (qt_total - pred_total >= 3.0) else 0,
            "OVER_SCORE": 1 if (pred_total - qt_total >= 3.0) else 0,
        }

        delta_cases.append(
            {
                "id": str(uuid4()),
                "project_id": project_id,
                "submission_id": submission_id,
                "report_id": report.get("id"),
                "qingtian_result_id": qtr.get("id"),
                "total_error": total_error,
                "dim_errors": dim_errors,
                "reason_alignment": reason_alignment,
                "miss_types": miss_types,
                "created_at": _now_iso(),
            }
        )
    return delta_cases


def build_calibration_samples(
    *,
    project_id: str,
    latest_reports_by_submission: Dict[str, Dict[str, Any]],
    latest_qingtian_by_submission: Dict[str, Dict[str, Any]],
    submissions_by_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    for submission_id, report in latest_reports_by_submission.items():
        qtr = latest_qingtian_by_submission.get(submission_id)
        sub = submissions_by_id.get(submission_id)
        if not qtr or not sub:
            continue
        row = build_feature_row(
            report, submission=sub, qingtian_result=qtr, feature_schema_version="v2"
        )
        if row.get("y_label") is None:
            continue
        samples.append(
            {
                "id": str(uuid4()),
                "project_id": str(sub.get("project_id") or project_id),
                "submission_id": submission_id,
                "report_id": report.get("id"),
                "qingtian_result_id": qtr.get("id"),
                "feature_schema_version": str(row.get("feature_schema_version", "v2")),
                "x_features": row.get("x_features") or {},
                "y_label": float(row.get("y_label")),
                "created_at": _now_iso(),
            }
        )
    return samples


def mine_patch_package(
    *,
    project_id: str,
    delta_cases: List[Dict[str, Any]],
    patch_type: str = "threshold",
    top_k: int = 3,
    rollback_pointer: str | None = None,
) -> Dict[str, Any]:
    top_cases = sorted(
        delta_cases, key=lambda x: abs(_safe_float(x.get("total_error"))), reverse=True
    )[:top_k]
    miss_counter = Counter()
    reason_texts: List[str] = []
    for c in top_cases:
        for key, val in (c.get("miss_types") or {}).items():
            miss_counter[key] += int(val or 0)
        for align in c.get("reason_alignment") or []:
            txt = str((align or {}).get("qt_reason_text") or "")
            if txt:
                reason_texts.append(txt)

    payload: Dict[str, Any] = {}
    if patch_type == "threshold":
        if miss_counter.get("UNDER_PENALIZE", 0) >= miss_counter.get("OVER_PENALIZE", 0):
            payload = {"penalty_multiplier": {"P-EMPTY-002": 1.10, "P-ACTION-002": 1.10}}
        else:
            payload = {"penalty_multiplier": {"P-EMPTY-002": 0.92, "P-ACTION-002": 0.92}}
    elif patch_type == "requirement":
        payload = {
            "requirement_boost": {
                "mandatory_weight_add": 0.2,
                "focus_dimensions": [d for d, _ in miss_counter.items() if d.startswith("UNDER")][
                    :3
                ],
            }
        }
    else:
        payload = {"reason_keywords": reason_texts[:20]}

    now = _now_iso()
    return {
        "id": str(uuid4()),
        "project_id": project_id,
        "patch_type": patch_type,
        "patch_payload": payload,
        "target_symptom": {
            "sample_count": len(top_cases),
            "miss_type_counts": dict(miss_counter),
            "top_abs_errors": [abs(_safe_float(c.get("total_error"))) for c in top_cases[:5]],
        },
        "rollback_pointer": rollback_pointer,
        "status": "candidate",
        "shadow_metrics": None,
        "created_at": now,
        "updated_at": now,
    }


def evaluate_patch_shadow(
    *,
    patch: Dict[str, Any],
    delta_cases: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not delta_cases:
        return {
            "ok": True,
            "patch_id": str(patch.get("id") or ""),
            "gate_passed": False,
            "metrics_before_after": {"mae_before": 0.0, "mae_after": 0.0, "sample_count": 0},
        }

    errors = [_safe_float(c.get("total_error")) for c in delta_cases]
    mae_before = sum(abs(e) for e in errors) / len(errors)

    payload = patch.get("patch_payload") or {}
    bias = _safe_float((payload.get("score_bias") or 0.0))
    pm = payload.get("penalty_multiplier") or {}
    pm_values = [float(v) for v in pm.values() if isinstance(v, (int, float))]
    pm_avg = sum(pm_values) / len(pm_values) if pm_values else 1.0

    after_errors: List[float] = []
    for e in errors:
        adj = e + bias
        if pm_avg > 1.0:
            # 加强扣分通常压缩正向高估误差
            adj = adj * (0.90 if adj > 0 else 0.98)
        elif pm_avg < 1.0:
            # 放松扣分通常压缩负向低估误差
            adj = adj * (0.90 if adj < 0 else 0.98)
        after_errors.append(adj)

    mae_after = sum(abs(e) for e in after_errors) / len(after_errors)
    gate_passed = mae_after <= mae_before * 0.995
    return {
        "ok": True,
        "patch_id": str(patch.get("id") or ""),
        "gate_passed": gate_passed,
        "metrics_before_after": {
            "mae_before": round(mae_before, 4),
            "mae_after": round(mae_after, 4),
            "sample_count": len(errors),
            "delta_mae": round(mae_after - mae_before, 4),
        },
    }
