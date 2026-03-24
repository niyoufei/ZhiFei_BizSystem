from __future__ import annotations

import html
from typing import Dict, List, Optional

from app import qingtian_dual_track as qingtian_dual_track_module


def _main():
    import app.main as main_mod

    return main_mod


def _load_latest_qingtian_by_submission(
    submissions: List[Dict[str, object]],
) -> Dict[str, Dict[str, object]]:
    main = _main()
    submission_ids = {
        str(item.get("id") or "").strip()
        for item in submissions
        if str(item.get("id") or "").strip()
    }
    if not submission_ids:
        return {}
    return main._latest_records_by_submission(
        [
            row
            for row in main.load_qingtian_results()
            if str(row.get("submission_id") or "").strip() in submission_ids
        ]
    )


def build_submission_view(
    submission: Dict[str, object],
    *,
    project: Dict[str, object],
    project_id: str,
    material_knowledge_snapshot: Dict[str, object],
    latest_qingtian_by_submission: Optional[Dict[str, Dict[str, object]]] = None,
    allow_pred_score: bool,
    score_scale_max: int,
) -> Dict[str, object]:
    main = _main()
    effective_submission = (
        main._preview_submission_with_live_prediction(submission, project=project)
        if allow_pred_score
        else dict(submission)
    )
    view = dict(effective_submission)
    dual_track_summary = qingtian_dual_track_module.build_submission_dual_track_summary(
        effective_submission,
        latest_qingtian_by_submission=latest_qingtian_by_submission,
        allow_pred_score=allow_pred_score,
        score_scale_max=score_scale_max,
    )
    report_obj = effective_submission.get("report")
    if not isinstance(report_obj, dict):
        total_display = main._convert_score_from_100(
            effective_submission.get("total_score"),
            score_scale_max,
        )
        if total_display is not None:
            view["total_score"] = total_display
        view["report"] = {"dual_track_summary": dual_track_summary}
        return view

    report = dict(report_obj)
    main._ensure_report_score_self_awareness(
        report,
        project_id=project_id,
        material_knowledge_snapshot=material_knowledge_snapshot,
    )
    rule_total = main._to_float_or_none(report.get("rule_total_score"))
    if rule_total is None:
        rule_total = main._to_float_or_none(report.get("total_score"))
    if rule_total is None:
        rule_total = main._to_float_or_none(effective_submission.get("total_score"))
    if rule_total is None:
        rule_total = 0.0

    if not allow_pred_score:
        report["pred_total_score"] = None
        report["llm_total_score"] = None
        report["pred_confidence"] = None
        report["score_blend"] = None
        report["total_score"] = round(float(rule_total), 2)
        view["total_score"] = round(float(rule_total), 2)

    raw_total = main._to_float_or_none(report.get("total_score"))
    raw_rule = main._to_float_or_none(report.get("rule_total_score"))
    raw_pred = main._to_float_or_none(report.get("pred_total_score"))
    raw_llm = main._to_float_or_none(report.get("llm_total_score"))
    report["raw_total_score_100"] = raw_total
    report["raw_rule_total_score_100"] = raw_rule
    report["raw_pred_total_score_100"] = raw_pred
    report["raw_llm_total_score_100"] = raw_llm
    report["score_scale_max"] = score_scale_max
    report["score_scale_label"] = main._score_scale_label(score_scale_max)
    display_pred = main._convert_score_from_100(raw_pred, score_scale_max)
    display_rule = main._convert_score_from_100(raw_rule, score_scale_max)
    display_llm = main._convert_score_from_100(raw_llm, score_scale_max)
    display_total = main._convert_score_from_100(raw_total, score_scale_max)
    if display_total is None:
        display_total = main._convert_score_from_100(
            effective_submission.get("total_score"),
            score_scale_max,
        )
    report["pred_total_score"] = display_pred
    report["rule_total_score"] = display_rule
    report["llm_total_score"] = display_llm
    report["total_score"] = display_total
    report["dual_track_summary"] = dual_track_summary
    if display_total is not None:
        view["total_score"] = display_total
    view["report"] = report
    return view


def build_project_submission_views(
    project_id: str,
    submissions: List[Dict[str, object]],
    *,
    project: Dict[str, object],
    material_knowledge_snapshot: Optional[Dict[str, object]] = None,
    allow_pred_score: Optional[bool] = None,
    score_scale_max: Optional[int] = None,
) -> Dict[str, object]:
    main = _main()
    resolved_material_snapshot = (
        material_knowledge_snapshot
        if isinstance(material_knowledge_snapshot, dict)
        else main._build_material_knowledge_profile(project_id)
    )
    resolved_project = dict(project or {})
    resolved_project.setdefault("id", project_id)
    resolved_score_scale_max = (
        int(score_scale_max)
        if score_scale_max is not None
        else main._resolve_project_score_scale_max(resolved_project)
    )
    resolved_project_meta = (
        dict(resolved_project.get("meta")) if isinstance(resolved_project.get("meta"), dict) else {}
    )
    resolved_project_meta.setdefault("score_scale_max", resolved_score_scale_max)
    resolved_project["meta"] = resolved_project_meta
    resolved_allow_pred_score = (
        bool(allow_pred_score)
        if allow_pred_score is not None
        else (main._select_calibrator_model(resolved_project) is not None)
    )
    latest_qingtian_by_submission = _load_latest_qingtian_by_submission(submissions)
    submissions_view = [
        build_submission_view(
            item,
            project=resolved_project,
            project_id=project_id,
            material_knowledge_snapshot=resolved_material_snapshot,
            latest_qingtian_by_submission=latest_qingtian_by_submission,
            allow_pred_score=resolved_allow_pred_score,
            score_scale_max=resolved_score_scale_max,
        )
        for item in submissions
    ]
    return {
        "submissions_view": submissions_view,
        "material_knowledge_snapshot": resolved_material_snapshot,
        "allow_pred_score": resolved_allow_pred_score,
        "score_scale_max": resolved_score_scale_max,
        "latest_qingtian_by_submission": latest_qingtian_by_submission,
    }


def _type_short(material_type: str) -> str:
    if material_type == "tender_qa":
        return "招答"
    if material_type == "boq":
        return "清单"
    if material_type == "drawing":
        return "图纸"
    if material_type == "site_photo":
        return "照片"
    return material_type or "-"


def build_selected_project_submission_render_context(
    project_id: str,
    submissions: List[Dict[str, object]],
    *,
    allow_pred_score: bool,
    score_scale_max: int,
    material_knowledge_snapshot: Dict[str, object],
) -> Dict[str, str]:
    main = _main()
    bundle = build_project_submission_views(
        project_id,
        submissions,
        project={"id": project_id},
        material_knowledge_snapshot=material_knowledge_snapshot,
        allow_pred_score=allow_pred_score,
        score_scale_max=score_scale_max,
    )
    rows_html: List[str] = []
    dual_track_overview_rows: List[Dict[str, object]] = []
    blocked_overview_count = 0
    for view in bundle["submissions_view"]:
        report_obj = view.get("report")
        report = report_obj if isinstance(report_obj, dict) else {}
        report_meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
        scoring_status = str(report.get("scoring_status") or "").strip().lower()
        is_pending = scoring_status == "pending"
        is_blocked = scoring_status == "blocked"
        dual_track_summary = (
            report.get("dual_track_summary")
            if isinstance(report.get("dual_track_summary"), dict)
            else {}
        )
        if not is_pending and dual_track_summary:
            dual_track_overview_rows.append(dual_track_summary)
            if is_blocked:
                blocked_overview_count += 1
        score_cell = qingtian_dual_track_module.render_submission_dual_track_score_html(
            dual_track_summary,
            is_pending=is_pending,
            is_blocked=is_blocked,
        )
        diagnostic_cell = qingtian_dual_track_module.render_submission_dual_track_diagnostic_html(
            dual_track_summary,
            project_id=project_id,
            is_pending=is_pending,
            is_blocked=is_blocked,
        )

        util_gate = (
            report_meta.get("material_utilization_gate")
            if isinstance(report_meta.get("material_utilization_gate"), dict)
            else {}
        )
        evidence_trace = (
            report_meta.get("evidence_trace")
            if isinstance(report_meta.get("evidence_trace"), dict)
            else {}
        )
        score_self_awareness = (
            report_meta.get("score_self_awareness")
            if isinstance(report_meta.get("score_self_awareness"), dict)
            else {}
        )
        util_blocked = bool(util_gate.get("blocked"))
        evidence_hits = int(main._to_float_or_none(evidence_trace.get("total_hits")) or 0)
        evidence_file_hits = int(
            main._to_float_or_none(evidence_trace.get("source_files_hit_count")) or 0
        )
        if not is_pending and evidence_hits > 0:
            score_cell += (
                '<div class="note">证据命中: '
                + html.escape(str(evidence_hits))
                + " 条 / 文件覆盖: "
                + html.escape(str(evidence_file_hits))
                + " 份</div>"
            )

        util_summary = (
            report_meta.get("material_utilization")
            if isinstance(report_meta.get("material_utilization"), dict)
            else {}
        )
        util_by_type = (
            util_summary.get("by_type") if isinstance(util_summary.get("by_type"), dict) else {}
        )
        util_available_types = (
            util_summary.get("available_types")
            if isinstance(util_summary.get("available_types"), list)
            else []
        )
        coverage_tokens: List[str] = []
        for type_key in ["tender_qa", "boq", "drawing", "site_photo"]:
            if type_key not in util_available_types:
                coverage_tokens.append(_type_short(type_key) + "·")
                continue
            row = util_by_type.get(type_key) if isinstance(util_by_type, dict) else {}
            row = row if isinstance(row, dict) else {}
            retrieval_hit = int(main._to_float_or_none(row.get("retrieval_hit")) or 0)
            consistency_hit = int(main._to_float_or_none(row.get("consistency_hit")) or 0)
            coverage_tokens.append(
                _type_short(type_key) + ("✓" if (retrieval_hit + consistency_hit) > 0 else "×")
            )
        if not is_pending and coverage_tokens:
            score_cell += (
                '<div class="note">类型覆盖: ' + html.escape(" / ".join(coverage_tokens)) + "</div>"
            )

        evidence_files = (
            evidence_trace.get("source_files_hit")
            if isinstance(evidence_trace.get("source_files_hit"), list)
            else []
        )
        evidence_files = [str(item).strip() for item in evidence_files if str(item).strip()]
        if not is_pending and evidence_files:
            preview = "；".join(evidence_files[:2])
            suffix = " 等" if len(evidence_files) > 2 else ""
            score_cell += '<div class="note">命中文件: ' + html.escape(preview) + suffix + "</div>"

        awareness_score = main._to_float_or_none(score_self_awareness.get("score_0_100"))
        awareness_level = str(score_self_awareness.get("level") or "").strip()
        awareness_reasons = (
            score_self_awareness.get("reasons")
            if isinstance(score_self_awareness.get("reasons"), list)
            else []
        )
        if not is_pending and awareness_level:
            awareness_label = (
                "高"
                if awareness_level == "high"
                else ("中" if awareness_level == "medium" else "低")
            )
            reason_preview = "；".join(
                str(item).strip() for item in awareness_reasons[:1] if str(item).strip()
            )
            score_cell += (
                '<div class="note">评分置信度: '
                + html.escape(awareness_label)
                + (
                    ""
                    if awareness_score is None
                    else "（" + html.escape(f"{awareness_score:.1f}") + "）"
                )
                + ("" if not reason_preview else " / " + html.escape(reason_preview))
                + "</div>"
            )
        if util_blocked:
            score_cell += '<div class="error">资料利用门禁未达标（建议补齐资料后重评分）</div>'

        submission_id = html.escape(str(view.get("id", "")))
        filename_raw = str(view.get("filename", ""))
        created_at = html.escape(str(view.get("created_at", ""))[:19])
        rows_html.append(
            "<tr>"
            + f"<td>{html.escape(filename_raw)}</td>"
            + f"<td>{score_cell}</td>"
            + f"<td>{diagnostic_cell}</td>"
            + f"<td>{created_at}</td>"
            + (
                "<td>"
                + f'<button type="button" class="btn-danger js-delete-submission" data-submission-id="{submission_id}" data-project-id="{html.escape(str(view.get("project_id") or ""))}" data-filename="{html.escape(filename_raw)}" onclick="return window.__zhifeiFallbackDelete(event, \'submission\', this.getAttribute(\'data-submission-id\'), this.getAttribute(\'data-filename\'), this.getAttribute(\'data-project-id\'))">删除</button>'
                + "</td>"
            )
            + "</tr>"
        )

    overview_html = ""
    overview_display = "none"
    if dual_track_overview_rows:
        overview_payload = qingtian_dual_track_module.build_submission_dual_track_overview(
            dual_track_overview_rows
        )
        overview_payload["blocked_count"] = blocked_overview_count
        overview_html = qingtian_dual_track_module.render_submission_dual_track_overview_html(
            overview_payload,
            project_id=project_id,
        )
        overview_display = "block" if overview_html else "none"
    return {
        "rows_html": "".join(rows_html),
        "overview_html": overview_html,
        "overview_display": overview_display,
    }


def build_project_pre_score_rows(
    project_id: str,
    submissions_view: List[Dict[str, object]],
    *,
    allow_pred_score: bool,
    score_scale_max: int,
) -> List[Dict[str, object]]:
    main = _main()
    latest_reports = main._latest_records_by_submission(
        [row for row in main.load_score_reports() if str(row.get("project_id")) == project_id]
    )
    rows: List[Dict[str, object]] = []
    for submission in submissions_view:
        submission_id = str(submission.get("id"))
        latest = latest_reports.get(submission_id, {})
        suggestions = latest.get("suggestions") or []
        top_gain = 0.0
        if suggestions and isinstance(suggestions[0], dict):
            top_gain = float(suggestions[0].get("expected_gain", 0.0))
        pred_total_raw = latest.get("pred_total_score")
        if not allow_pred_score:
            pred_total_raw = None
        rule_total_raw = float(latest.get("rule_total_score", submission.get("total_score", 0.0)))
        pred_total = main._convert_score_from_100(pred_total_raw, score_scale_max)
        rule_total = main._convert_score_from_100(rule_total_raw, score_scale_max)
        top_gain_display = main._convert_score_from_100(top_gain, score_scale_max)
        rows.append(
            {
                "submission_id": submission_id,
                "bidder_name": submission.get("bidder_name")
                or submission.get("filename")
                or submission_id,
                "latest_report": {
                    "report_id": latest.get("id"),
                    "rule_total_score": float(rule_total if rule_total is not None else 0.0),
                    "pred_total_score": pred_total,
                    "rank_by_pred": None,
                    "rank_by_rule": None,
                    "top_expected_gain": round(
                        float(top_gain_display if top_gain_display is not None else 0.0), 2
                    ),
                    "updated_at": latest.get("created_at") or submission.get("created_at"),
                },
            }
        )

    pred_sorted = sorted(
        rows,
        key=lambda row: (
            -float(row["latest_report"]["pred_total_score"])
            if row["latest_report"]["pred_total_score"] is not None
            else float("inf")
        ),
    )
    for idx, row in enumerate(pred_sorted, start=1):
        if row["latest_report"]["pred_total_score"] is not None:
            row["latest_report"]["rank_by_pred"] = idx

    rule_sorted = sorted(rows, key=lambda row: -float(row["latest_report"]["rule_total_score"]))
    for idx, row in enumerate(rule_sorted, start=1):
        row["latest_report"]["rank_by_rule"] = idx
    return rows
