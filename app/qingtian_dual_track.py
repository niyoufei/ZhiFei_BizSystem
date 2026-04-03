from __future__ import annotations

import html
from decimal import Decimal
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
    report_meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    current_score_failure = main._report_current_score_failure(report)
    current_score_failed = bool(current_score_failure)
    current_score_failure_message = (
        str(current_score_failure.get("message") or "").strip()
        or "计算中断异常：校准引擎处理失败，请重试"
    )
    util_gate = main._normalize_material_utilization_gate_state(
        report_meta.get("material_utilization_gate")
    )
    raw_rule_total = main._to_float_or_none(report.get("rule_total_score"))
    if raw_rule_total is None:
        raw_rule_total = main._to_float_or_none(report.get("total_score"))
    raw_pred_total = main._to_float_or_none(report.get("pred_total_score"))
    if not allow_pred_score:
        raw_pred_total = None
    display_total_score_100 = raw_pred_total if raw_pred_total is not None else raw_rule_total

    display_fields = main._resolve_submission_score_fields(
        submission,
        allow_pred_score=allow_pred_score,
        score_scale_max=score_scale_max,
    )
    display_score_source = str(display_fields.get("score_source") or "rule").strip() or "rule"
    is_exact_ground_truth = bool(
        display_score_source == "ground_truth" or main._report_uses_exact_ground_truth_score(report)
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

    def _quantize_native_delta(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return main._quantize_decimal_score(
            Decimal(str(value)),
            score_scale_max=score_scale_max,
        )

    independent_delta_100 = (
        round(float(raw_rule_total) - float(qingtian_score_100), 2)
        if raw_rule_total is not None and qingtian_score_100 is not None
        else None
    )
    approximation_delta_100 = (
        round(float(raw_pred_total) - float(qingtian_score_100), 2)
        if raw_pred_total is not None
        and qingtian_score_100 is not None
        and not is_exact_ground_truth
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
    independent_score = main._to_float_or_none(display_fields.get("rule_total_score"))
    approximation_score = (
        None
        if is_exact_ground_truth
        else main._to_float_or_none(display_fields.get("pred_total_score"))
    )
    display_total_score = main._to_float_or_none(display_fields.get("total_score"))
    if current_score_failed:
        display_total_score = None
        display_total_score_100 = None
    independent_delta = (
        _quantize_native_delta(float(independent_score) - float(qingtian_score))
        if independent_score is not None and qingtian_score is not None
        else None
    )
    approximation_delta = (
        _quantize_native_delta(float(approximation_score) - float(qingtian_score))
        if approximation_score is not None
        and qingtian_score is not None
        and not is_exact_ground_truth
        else None
    )
    independent_abs_delta = (
        _quantize_native_delta(abs(float(independent_delta)))
        if independent_delta is not None
        else None
    )
    approximation_abs_delta = (
        _quantize_native_delta(abs(float(approximation_delta)))
        if approximation_delta is not None
        else None
    )
    abs_delta_improvement = (
        _quantize_native_delta(float(independent_abs_delta) - float(approximation_abs_delta))
        if independent_abs_delta is not None and approximation_abs_delta is not None
        else None
    )

    alignment_status = "independent_only"
    governance_hint = "当前仅有独立评分，可继续录入真实评标并训练当前分层。"
    if is_exact_ground_truth:
        alignment_status = "ground_truth_exact"
        governance_hint = "当前总分已直接采用真实评标结果，独立分继续保留作审计基线。"
    elif current_score_failed:
        alignment_status = "current_score_failed"
        governance_hint = current_score_failure_message
    elif qingtian_score_100 is not None and raw_pred_total is not None:
        if abs_delta_improvement_100 is not None and abs_delta_improvement_100 > 0:
            alignment_status = "approximation_better"
            governance_hint = "当前分层当前更接近青天，可继续沉淀校准样本和 few-shot。"
        elif abs_delta_improvement_100 is not None and abs_delta_improvement_100 < 0:
            alignment_status = "independent_better"
            governance_hint = "独立层当前更接近青天，建议优先查看评分治理面板。"
        else:
            alignment_status = "tracks_tied"
            governance_hint = "独立层与当前分层当前和青天偏差相当，可继续观察。"
    elif qingtian_score_100 is not None:
        alignment_status = "await_approximation"
        governance_hint = "已录入青天对照，但当前尚未形成当前分，建议继续闭环进化。"
    elif raw_pred_total is not None:
        alignment_status = "await_ground_truth"
        governance_hint = "已生成当前分，需录入青天结果后才能验证校准效果。"

    return {
        "display_score_source": display_score_source,
        "display_score_label": (
            "计算中断异常"
            if current_score_failed
            else (
                "真实分"
                if is_exact_ground_truth
                else ("当前分" if raw_pred_total is not None else "独立分")
            )
        ),
        "display_total_score": display_total_score,
        "display_total_score_100": display_total_score_100,
        "independent_score": independent_score,
        "independent_score_100": raw_rule_total,
        "approximation_score": approximation_score,
        "approximation_score_100": None if is_exact_ground_truth else raw_pred_total,
        "qingtian_score": float(qingtian_score) if qingtian_score is not None else None,
        "scale_max": int(score_scale_max),
        "scale_label": main._score_scale_label(score_scale_max),
        "has_approximation_score": raw_pred_total is not None and not is_exact_ground_truth,
        "has_exact_ground_truth_score": is_exact_ground_truth,
        "has_ground_truth": qingtian_score_100 is not None,
        "independent_delta": independent_delta,
        "approximation_delta": approximation_delta,
        "independent_abs_delta": independent_abs_delta,
        "approximation_abs_delta": approximation_abs_delta,
        "abs_delta_improvement": abs_delta_improvement,
        "independent_delta_100": independent_delta_100,
        "approximation_delta_100": approximation_delta_100,
        "independent_abs_delta_100": independent_abs_delta_100,
        "approximation_abs_delta_100": approximation_abs_delta_100,
        "abs_delta_improvement_100": abs_delta_improvement_100,
        "alignment_status": alignment_status,
        "governance_hint": governance_hint,
        "current_score_failed": current_score_failed,
        "current_score_failure_message": current_score_failure_message
        if current_score_failed
        else None,
        "material_gate_blocked": bool(util_gate.get("blocked")),
        "material_gate_warned": bool(util_gate.get("warned")),
        "material_gate_reasons": [
            str(item).strip()
            for item in (
                util_gate.get("reasons") if isinstance(util_gate.get("reasons"), list) else []
            )
            if str(item).strip()
        ][:4],
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
            "exact_ground_truth_count": 0,
            "ground_truth_count": 0,
            "independent_avg": None,
            "approximation_avg": None,
            "qingtian_avg": None,
            "scale_label": None,
            "independent_abs_delta_avg": None,
            "approximation_abs_delta_avg": None,
            "abs_delta_improvement_avg": None,
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
    exact_ground_truth_rows = [row for row in rows if bool(row.get("has_exact_ground_truth_score"))]
    qingtian_rows = [row for row in rows if bool(row.get("has_ground_truth"))]
    scale_label = str(rows[0].get("scale_label") or "").strip() if rows else ""
    improvement_avg_native = _avg(
        [main._to_float_or_none(row.get("abs_delta_improvement")) for row in rows]
    )
    improvement_avg = _avg(
        [main._to_float_or_none(row.get("abs_delta_improvement_100")) for row in rows]
    )

    headline = "当前默认展示独立分。"
    if approximation_rows:
        headline = "当前默认展示当前分，并保留独立分作审计基线。"
    elif exact_ground_truth_rows:
        headline = "当前默认展示真实分，并保留独立分作审计基线。"
    if improvement_avg is not None:
        if improvement_avg > 0:
            headline = "当前分层整体上更接近青天，建议继续用评分治理稳态收敛。"
        elif improvement_avg < 0:
            headline = "独立层整体上更接近青天，建议先检查闭环样本和校准版本。"

    return {
        "submission_count": len(rows),
        "dual_track_count": len(approximation_rows),
        "exact_ground_truth_count": len(exact_ground_truth_rows),
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
        "scale_label": scale_label or None,
        "independent_abs_delta_avg": _avg(
            [main._to_float_or_none(row.get("independent_abs_delta")) for row in qingtian_rows]
        ),
        "approximation_abs_delta_avg": _avg(
            [main._to_float_or_none(row.get("approximation_abs_delta")) for row in qingtian_rows]
        ),
        "abs_delta_improvement_avg": improvement_avg_native,
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


def render_submission_dual_track_score_html(
    summary: Dict[str, object],
    *,
    is_pending: bool = False,
    is_blocked: bool = False,
) -> str:
    if is_pending:
        return '<span class="note">待评分</span>'
    display_label = str(summary.get("display_score_label") or "独立分")
    display_total = summary.get("display_total_score")
    scale_max = int(_main()._to_float_or_none(summary.get("scale_max")) or 100)
    detail_tokens: List[str] = []
    independent_score = summary.get("independent_score")
    approximation_score = summary.get("approximation_score")
    qingtian_score = summary.get("qingtian_score")
    if independent_score is not None:
        detail_tokens.append(f"独立: {independent_score}")
    if approximation_score is not None:
        detail_tokens.append(f"当前分: {approximation_score}")
    if qingtian_score is not None:
        detail_tokens.append(f"青天: {qingtian_score}")
    scale_label = str(summary.get("scale_label") or "").strip()
    if scale_label:
        detail_tokens.append(scale_label)
    lines: List[str] = []
    current_score_failed = bool(summary.get("current_score_failed"))
    current_score_failure_message = (
        str(summary.get("current_score_failure_message") or "").strip()
        or "计算中断异常：校准引擎处理失败，请重试"
    )
    if current_score_failed:
        lines.append('<div class="error">' + html.escape(current_score_failure_message) + "</div>")
    elif is_blocked:
        lines.append('<div class="warn">已生成分数，但本施组触发资料利用预警。</div>')
    if display_total is not None:
        primary_text = f"{display_label}: {display_total}"
        if scale_max == 5:
            primary_text += " / 5"
        lines.append("<div><strong>" + html.escape(primary_text) + "</strong></div>")
    elif detail_tokens:
        lines.append("<div><strong>" + html.escape(display_label) + "</strong></div>")
    if detail_tokens:
        lines.append('<div class="note">' + html.escape(" / ".join(detail_tokens)) + "</div>")
    return "".join(lines) or "-"


def render_submission_dual_track_diagnostic_html(
    summary: Dict[str, object],
    *,
    project_id: str,
    is_pending: bool = False,
    is_blocked: bool = False,
) -> str:
    if is_pending:
        return '<span class="note">待评分后生成双轨诊断。</span>'

    alignment_status = str(summary.get("alignment_status") or "").strip()
    alignment_label_map = {
        "approximation_better": "当前分层更接近青天",
        "independent_better": "独立层更接近青天",
        "tracks_tied": "双轨与青天偏差相当",
        "await_approximation": "已录入青天，等待当前分收敛",
        "await_ground_truth": "等待青天结果验证",
        "ground_truth_exact": "已命中真实评标",
        "current_score_failed": "计算中断异常",
        "independent_only": "当前仅有独立评分",
    }
    delta_tokens: List[str] = []
    independent_delta = summary.get("independent_delta")
    approximation_delta = summary.get("approximation_delta")
    improvement = summary.get("abs_delta_improvement")
    if independent_delta is not None:
        delta_tokens.append(f"独立偏差 {independent_delta}")
    if approximation_delta is not None:
        delta_tokens.append(f"当前分偏差 {approximation_delta}")
    if improvement is not None:
        delta_tokens.append(f"改善 {improvement}")

    lines: List[str] = []
    current_score_failed = bool(summary.get("current_score_failed"))
    current_score_failure_message = (
        str(summary.get("current_score_failure_message") or "").strip()
        or "计算中断异常：校准引擎处理失败，请重试"
    )
    gate_reasons = [
        str(item).strip()
        for item in (
            summary.get("material_gate_reasons")
            if isinstance(summary.get("material_gate_reasons"), list)
            else []
        )
        if str(item).strip()
    ]
    if current_score_failed:
        lines.append(
            '<div class="error"><strong>'
            + html.escape(current_score_failure_message)
            + "</strong></div>"
        )
        lines.append(
            '<div class="note">当前分层计算已中断，独立分仅保留为审计基线，不能视为当前分。</div>'
        )
    elif is_blocked:
        lines.append(
            '<div class="warn"><strong>已评分，但本施组对部分项目资料未形成足够证据关联。</strong></div>'
        )
        if gate_reasons:
            lines.append('<div class="warn">' + html.escape("；".join(gate_reasons[:2])) + "</div>")
        lines.append(
            '<div class="note">这是施组级资料利用预警，不是项目资料未上传。建议先看“满分优化清单”，再决定是否补资料复评。</div>'
        )
    elif delta_tokens:
        scale_label = str(summary.get("scale_label") or "").strip()
        lines.append(
            "<div><strong>"
            + html.escape(" / ".join(delta_tokens))
            + "</strong>"
            + (
                '<span class="note">（' + html.escape(scale_label) + "）</span>"
                if scale_label
                else ""
            )
            + "</div>"
        )
    else:
        lines.append('<div class="note">暂无青天对照偏差。</div>')
    if alignment_status:
        lines.append(
            '<div class="note">'
            + html.escape(alignment_label_map.get(alignment_status, alignment_status))
            + "</div>"
        )
    governance_hint = str(summary.get("governance_hint") or "").strip()
    if governance_hint:
        lines.append('<div class="note">' + html.escape(governance_hint) + "</div>")
    if project_id:
        lines.append(
            '<div style="margin-top:6px"><button type="button" class="secondary '
            'js-open-compare-report" data-project-id="'
            + html.escape(project_id)
            + '">查看满分优化清单（逐页）</button></div>'
        )
    return "".join(lines)


def render_submission_dual_track_overview_html(
    overview: Dict[str, object],
    *,
    project_id: str,
) -> str:
    if not overview or int(_main()._to_float_or_none(overview.get("submission_count")) or 0) <= 0:
        return ""

    blocked_count = int(_main()._to_float_or_none(overview.get("blocked_count")) or 0)
    metric_tokens: List[str] = [
        f"已生成评分 {int(_main()._to_float_or_none(overview.get('submission_count')) or 0)} 份",
        f"双轨样本 {int(_main()._to_float_or_none(overview.get('dual_track_count')) or 0)} 份",
        f"真实命中 {int(_main()._to_float_or_none(overview.get('exact_ground_truth_count')) or 0)} 份",
        f"青天对照 {int(_main()._to_float_or_none(overview.get('ground_truth_count')) or 0)} 份",
    ]
    if blocked_count > 0:
        metric_tokens.append(f"资料利用预警 {blocked_count} 份")
    optional_metrics = [
        ("independent_avg", "独立均分"),
        ("approximation_avg", "当前分均分"),
        ("qingtian_avg", "青天均分"),
        ("independent_abs_delta_avg", "独立平均绝对偏差"),
        ("approximation_abs_delta_avg", "当前分平均绝对偏差"),
        ("abs_delta_improvement_avg", "平均改善"),
    ]
    scale_label = str(overview.get("scale_label") or "").strip()
    for key, label in optional_metrics:
        value = _main()._to_float_or_none(overview.get(key))
        if value is None:
            continue
        suffix = (
            f"（{scale_label}）" if scale_label and ("delta" in key or "improvement" in key) else ""
        )
        metric_tokens.append(f"{label} {value}{suffix}")

    rendered = "<strong>双轨总览</strong>"
    headline = str(overview.get("headline") or "").strip()
    if blocked_count > 0:
        headline = (
            f"已生成评分 {int(_main()._to_float_or_none(overview.get('submission_count')) or 0)} 份，"
            f"其中 {blocked_count} 份触发资料利用预警；分数已保留，建议按满分优化清单补强后复评。"
        )
    if headline:
        rendered += '<p style="margin:6px 0 0 0;color:#1f2937">' + html.escape(headline) + "</p>"
    rendered += (
        '<p style="margin:6px 0 0 0;font-size:12px;color:#475569">'
        + html.escape("；".join(metric_tokens))
        + "</p>"
    )
    if project_id:
        rendered += (
            '<div style="margin-top:8px"><button type="button" class="secondary '
            'js-open-compare-report" data-project-id="'
            + html.escape(project_id)
            + '">查看满分优化清单（逐页）</button></div>'
        )
        rendered += (
            '<div style="margin-top:8px"><button type="button" class="secondary '
            'js-open-feedback-governance" data-project-id="'
            + html.escape(project_id)
            + '">查看评分治理（异常样本/校准/回退）</button></div>'
        )
    return rendered


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
