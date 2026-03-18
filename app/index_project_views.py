from __future__ import annotations

import html
from typing import Dict, List

from app import submission_dual_track_views as submission_dual_track_views_module


def _main():
    import app.main as main_mod

    return main_mod


def build_selected_project_material_rows_html(project_id: str) -> str:
    main = _main()
    materials_all = main.load_materials()
    selected_materials = [
        item for item in materials_all if str(item.get("project_id", "")) == project_id
    ]
    selected_materials.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    rows: List[str] = []
    for item in selected_materials:
        normalized_item, _ = main._normalize_material_row_for_parse(dict(item))
        material_id = html.escape(str(item.get("id", "")))
        filename_raw = str(normalized_item.get("filename", ""))
        material_type_label = html.escape(
            main._material_type_label(
                normalized_item.get("material_type"),
                filename=normalized_item.get("filename"),
            )
        )
        parse_status_label = html.escape(
            main._material_parse_status_label(
                normalized_item.get("parse_status"),
                parse_backend=normalized_item.get("parse_backend"),
                parse_error_message=normalized_item.get("parse_error_message"),
            )
        )
        created_at = html.escape(str(normalized_item.get("created_at", ""))[:19])
        rows.append(
            "<tr>"
            + f"<td>{material_type_label}</td>"
            + f"<td>{html.escape(filename_raw)}</td>"
            + f"<td>{parse_status_label}</td>"
            + f"<td>{created_at}</td>"
            + (
                "<td>"
                + f'<button type="button" class="btn-danger js-delete-material" data-material-id="{material_id}" data-project-id="{html.escape(str(normalized_item.get("project_id") or ""))}" data-filename="{html.escape(filename_raw)}" onclick="return window.__zhifeiFallbackDelete(event, \'material\', this.getAttribute(\'data-material-id\'), this.getAttribute(\'data-filename\'), this.getAttribute(\'data-project-id\'))">删除</button>'
                + "</td>"
            )
            + "</tr>"
        )
    return "".join(rows)


def build_selected_project_index_context(
    selected_project_id: str,
    *,
    projects: List[Dict[str, object]],
) -> Dict[str, object]:
    main = _main()
    selected_project = next(
        (project for project in projects if str(project.get("id", "")) == selected_project_id),
        {},
    )
    score_scale_max = (
        main._resolve_project_score_scale_max(selected_project)
        if selected_project
        else main.DEFAULT_SCORE_SCALE_MAX
    )
    allow_pred_score = bool(selected_project) and (
        main._select_calibrator_model(selected_project) is not None
    )
    material_knowledge_snapshot = (
        main._build_material_knowledge_profile(selected_project_id) if selected_project_id else {}
    )
    material_rows_html = ""
    materials_empty_display = "block"
    submission_rows_html = ""
    submissions_empty_display = "block"
    submission_dual_track_overview_html = ""
    submission_dual_track_overview_display = "none"
    if not selected_project_id:
        return {
            "selected_project": selected_project,
            "score_scale_max": score_scale_max,
            "allow_pred_score": allow_pred_score,
            "material_knowledge_snapshot": material_knowledge_snapshot,
            "material_rows_html": material_rows_html,
            "materials_empty_display": materials_empty_display,
            "submission_rows_html": submission_rows_html,
            "submissions_empty_display": submissions_empty_display,
            "submission_dual_track_overview_html": submission_dual_track_overview_html,
            "submission_dual_track_overview_display": submission_dual_track_overview_display,
        }

    try:
        material_rows_html = build_selected_project_material_rows_html(selected_project_id)
        materials_empty_display = "none" if material_rows_html else "block"
    except Exception:
        material_rows_html = ""
        materials_empty_display = "block"

    try:
        submissions_all = main.load_submissions()
        selected_submissions = [
            item
            for item in submissions_all
            if str(item.get("project_id", "")) == selected_project_id
        ]
        selected_submissions.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        render_context = (
            submission_dual_track_views_module.build_selected_project_submission_render_context(
                selected_project_id,
                selected_submissions,
                allow_pred_score=allow_pred_score,
                score_scale_max=score_scale_max,
                material_knowledge_snapshot=material_knowledge_snapshot,
            )
        )
        submission_rows_html = str(render_context.get("rows_html") or "")
        submissions_empty_display = "none" if submission_rows_html else "block"
        submission_dual_track_overview_html = str(render_context.get("overview_html") or "")
        submission_dual_track_overview_display = str(
            render_context.get("overview_display") or "none"
        )
    except Exception:
        submission_rows_html = ""
        submissions_empty_display = "block"
        submission_dual_track_overview_html = ""
        submission_dual_track_overview_display = "none"

    return {
        "selected_project": selected_project,
        "score_scale_max": score_scale_max,
        "allow_pred_score": allow_pred_score,
        "material_knowledge_snapshot": material_knowledge_snapshot,
        "material_rows_html": material_rows_html,
        "materials_empty_display": materials_empty_display,
        "submission_rows_html": submission_rows_html,
        "submissions_empty_display": submissions_empty_display,
        "submission_dual_track_overview_html": submission_dual_track_overview_html,
        "submission_dual_track_overview_display": submission_dual_track_overview_display,
    }
