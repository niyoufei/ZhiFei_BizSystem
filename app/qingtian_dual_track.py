from __future__ import annotations

from typing import Dict, List, Optional


def _main():
    import app.main as main_mod

    return main_mod


def build_submission_dual_track_summary(
    submission: Dict[str, object],
    *,
    latest_qingtian_by_submission: Optional[Dict[str, Dict[str, object]]] = None,
    allow_pred_score: bool = True,
    score_scale_max: int,
) -> Dict[str, object]:
    main = _main()
    report = submission.get("report") if isinstance(submission.get("report"), dict) else {}
    raw_rule_total = main._to_float_or_none(report.get("rule_total_score"))
    if raw_rule_total is None:
        raw_rule_total = main._to_float_or_none(report.get("total_score"))
    raw_pred_total = main._to_float_or_none(report.get("pred_total_score"))
    if not allow_pred_score:
        raw_pred_total = None

    display_fields = main._resolve_submission_score_fields(
        submission,
        allow_pred_score=allow_pred_score,
        score_scale_max=score_scale_max,
    )
    submission_id = str(submission.get("id") or "")
    qingtian_row = (
        (latest_qingtian_by_submission or {}).get(submission_id)
        if isinstance(latest_qingtian_by_submission, dict)
        else None
    )
    qingtian_score_100 = (
        main._resolve_qingtian_total_score_100(
            qingtian_row if isinstance(qingtian_row, dict) else {},
            default_score_scale_max=score_scale_max,
        )
        if isinstance(qingtian_row, dict)
        else None
    )
    qingtian_score = main._convert_score_from_100(qingtian_score_100, score_scale_max)

    independent_delta_100 = (
        round(float(raw_rule_total) - float(qingtian_score_100), 2)
        if raw_rule_total is not None and qingtian_score_100 is not None
        else None
    )
    approximation_delta_100 = (
        round(float(raw_pred_total) - float(qingtian_score_100), 2)
        if raw_pred_total is not None and qingtian_score_100 is not None
        else None
    )
    independent_abs_delta_100 = (
        round(abs(float(independent_delta_100)), 2) if independent_delta_100 is not None else None
    )
    approximation_abs_delta_100 = (
        round(abs(float(approximation_delta_100)), 2)
        if approximation_delta_100 is not None
        else None
    )
    abs_delta_improvement_100 = (
        round(float(independent_abs_delta_100) - float(approximation_abs_delta_100), 2)
        if independent_abs_delta_100 is not None and approximation_abs_delta_100 is not None
        else None
    )

    alignment_status = "independent_only"
    governance_hint = "当前仅有独立评分，可继续录入真实评标并训练逼近层。"
    if qingtian_score_100 is not None and raw_pred_total is not None:
        if abs_delta_improvement_100 is not None and abs_delta_improvement_100 > 0:
            alignment_status = "approximation_better"
            governance_hint = "逼近层当前更接近青天，可继续沉淀校准样本和 few-shot。"
        elif abs_delta_improvement_100 is not None and abs_delta_improvement_100 < 0:
            alignment_status = "independent_better"
            governance_hint = "独立层当前更接近青天，建议优先查看闭环治理面板。"
        else:
            alignment_status = "tracks_tied"
            governance_hint = "独立层与逼近层当前和青天偏差相当，可继续观察。"
    elif qingtian_score_100 is not None:
        alignment_status = "await_approximation"
        governance_hint = "已录入青天对照，但当前尚未形成逼近分，建议继续闭环进化。"
    elif raw_pred_total is not None:
        alignment_status = "await_ground_truth"
        governance_hint = "已生成逼近分，需录入青天结果后才能验证逼近效果。"

    return {
        "display_score_source": str(display_fields.get("score_source") or "rule"),
        "display_score_label": "逼近分" if raw_pred_total is not None else "独立分",
        "display_total_score": display_fields.get("total_score"),
        "independent_score": display_fields.get("rule_total_score"),
        "approximation_score": display_fields.get("pred_total_score"),
        "qingtian_score": float(qingtian_score) if qingtian_score is not None else None,
        "scale_max": int(score_scale_max),
        "scale_label": main._score_scale_label(score_scale_max),
        "has_approximation_score": raw_pred_total is not None,
        "has_ground_truth": qingtian_score_100 is not None,
        "independent_delta_100": independent_delta_100,
        "approximation_delta_100": approximation_delta_100,
        "independent_abs_delta_100": independent_abs_delta_100,
        "approximation_abs_delta_100": approximation_abs_delta_100,
        "abs_delta_improvement_100": abs_delta_improvement_100,
        "alignment_status": alignment_status,
        "governance_hint": governance_hint,
    }


def build_submission_dual_track_overview(
    summaries: List[Dict[str, object]],
) -> Dict[str, object]:
    main = _main()
    rows = [row for row in summaries if isinstance(row, dict)]
    if not rows:
        return {
            "submission_count": 0,
            "dual_track_count": 0,
            "ground_truth_count": 0,
            "independent_avg": None,
            "approximation_avg": None,
            "qingtian_avg": None,
            "independent_abs_delta_avg_100": None,
            "approximation_abs_delta_avg_100": None,
            "abs_delta_improvement_avg_100": None,
            "headline": "当前暂无已评分施组。",
        }

    def _avg(values: List[Optional[float]]) -> Optional[float]:
        nums = [float(v) for v in values if v is not None]
        if not nums:
            return None
        return round(sum(nums) / len(nums), 2)

    independent_rows = [row for row in rows if row.get("independent_score") is not None]
    approximation_rows = [row for row in rows if bool(row.get("has_approximation_score"))]
    qingtian_rows = [row for row in rows if bool(row.get("has_ground_truth"))]
    improvement_avg = _avg(
        [main._to_float_or_none(row.get("abs_delta_improvement_100")) for row in rows]
    )

    headline = "当前默认展示独立分。"
    if approximation_rows:
        headline = "当前默认展示逼近分，并保留独立分作审计基线。"
    if improvement_avg is not None:
        if improvement_avg > 0:
            headline = "逼近层整体上更接近青天，建议继续用治理面板稳态收敛。"
        elif improvement_avg < 0:
            headline = "独立层整体上更接近青天，建议先检查闭环样本和校准版本。"

    return {
        "submission_count": len(rows),
        "dual_track_count": len(approximation_rows),
        "ground_truth_count": len(qingtian_rows),
        "independent_avg": _avg(
            [main._to_float_or_none(row.get("independent_score")) for row in independent_rows]
        ),
        "approximation_avg": _avg(
            [main._to_float_or_none(row.get("approximation_score")) for row in approximation_rows]
        ),
        "qingtian_avg": _avg(
            [main._to_float_or_none(row.get("qingtian_score")) for row in qingtian_rows]
        ),
        "independent_abs_delta_avg_100": _avg(
            [main._to_float_or_none(row.get("independent_abs_delta_100")) for row in qingtian_rows]
        ),
        "approximation_abs_delta_avg_100": _avg(
            [
                main._to_float_or_none(row.get("approximation_abs_delta_100"))
                for row in qingtian_rows
            ]
        ),
        "abs_delta_improvement_avg_100": improvement_avg,
        "headline": headline,
    }


def ingest_qingtian_result(submission_id: str, payload: object):
    main = _main()
    main.ensure_data_dirs()
    submissions = main.load_submissions()
    submission = main._find_submission(submission_id, submissions)
    project_id = str(submission.get("project_id") or "")
    projects = main.load_projects()
    project = main._find_project(project_id, projects)

    model_version = str(
        getattr(payload, "qingtian_model_version", None)
        or project.get("qingtian_model_version")
        or main.DEFAULT_QINGTIAN_MODEL_VERSION
    )
    record = {
        "id": str(main.uuid4()),
        "submission_id": submission_id,
        "qingtian_model_version": model_version,
        "qt_total_score": float(getattr(payload, "qt_total_score")),
        "qt_dim_scores": getattr(payload, "qt_dim_scores"),
        "qt_reasons": getattr(payload, "qt_reasons"),
        "raw_payload": getattr(payload, "raw_payload"),
        "created_at": main._now_iso(),
    }
    results = main.load_qingtian_results()
    results.append(record)
    main.save_qingtian_results(results)

    if str(project.get("status") or "") == "scoring_preparation":
        project["status"] = "submitted_to_qingtian"
        project["updated_at"] = main._now_iso()
        main.save_projects(projects)

    return main.QingTianResultRecord(**record)


def get_latest_qingtian_result(submission_id: str):
    main = _main()
    main.ensure_data_dirs()
    results = [
        r for r in main.load_qingtian_results() if str(r.get("submission_id")) == submission_id
    ]
    if not results:
        raise main.HTTPException(status_code=404, detail="暂无青天评标结果")
    latest = sorted(results, key=lambda x: str(x.get("created_at", "")), reverse=True)[0]
    return main.QingTianResultRecord(**latest)
