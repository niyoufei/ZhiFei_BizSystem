from __future__ import annotations

from typing import Any, Callable, Dict, Iterable

Record = Dict[str, object]
Records = list[Record]
Callback = Callable[..., Any]


def sync_feedback_weights(
    project_id: str,
    weight_update: Record,
    *,
    dimension_ids: Iterable[str],
    load_evolution_reports: Callable[[], Dict[str, Record]],
    save_evolution_reports: Callable[[Dict[str, Record]], None],
    now_iso: Callable[[], str],
) -> Record:
    if not bool(weight_update.get("updated")):
        return {"synced": False, "reason": "weight_not_updated"}
    multipliers = weight_update.get("new_dimension_multipliers") or {}
    if not isinstance(multipliers, dict) or not multipliers:
        return {"synced": False, "reason": "missing_multipliers"}

    reports = load_evolution_reports()
    evo = reports.get(project_id) or {}
    scoring_evolution = (
        evo.get("scoring_evolution") if isinstance(evo.get("scoring_evolution"), dict) else {}
    )
    scoring_evolution = dict(scoring_evolution or {})
    scoring_evolution["dimension_multipliers"] = {
        dim_id: float(multipliers.get(dim_id, 1.0)) for dim_id in dimension_ids
    }
    scoring_evolution.setdefault("rationale", {})
    scoring_evolution["updated_by_feedback"] = True
    scoring_evolution["updated_by_feedback_at"] = now_iso()
    evo["scoring_evolution"] = scoring_evolution
    evo.setdefault("project_id", project_id)
    evo.setdefault("sample_count", 0)
    evo["updated_at"] = now_iso()
    reports[project_id] = evo
    save_evolution_reports(reports)
    return {
        "synced": True,
        "dimension_multipliers_count": len(scoring_evolution.get("dimension_multipliers") or {}),
    }


def refresh_from_ground_truth(
    project_id: str,
    *,
    load_projects: Callable[[], Records],
    resolve_project_score_scale_max: Callback,
    load_ground_truth: Callable[[], Records],
    normalize_ground_truth_record: Callback,
    load_project_context: Callable[[], Dict[str, Record]],
    merge_materials_text: Callable[[str], str],
    build_evolution_report: Callback,
    load_evolution_reports: Callable[[], Dict[str, Record]],
    save_evolution_reports: Callable[[Dict[str, Record]], None],
) -> Record:
    projects = load_projects()
    project = next((p for p in projects if str(p.get("id")) == project_id), None)
    if project is None:
        return {"refreshed": False, "reason": "project_not_found"}

    project_score_scale = resolve_project_score_scale_max(project)
    records_raw = [
        record for record in load_ground_truth() if str(record.get("project_id")) == project_id
    ]
    records = [
        normalize_ground_truth_record(
            record if isinstance(record, dict) else {},
            default_score_scale_max=project_score_scale,
        )
        for record in records_raw
    ]
    ctx_data = load_project_context().get(project_id) or {}
    project_context = str(ctx_data.get("text") or "").strip()
    materials_text = merge_materials_text(project_id)
    if materials_text:
        project_context = (
            (project_context + "\n\n" + materials_text) if project_context else materials_text
        )

    report = build_evolution_report(project_id, records, project_context)
    reports = load_evolution_reports()
    previous = reports.get(project_id) or {}
    if isinstance(previous.get("enhanced_by"), str):
        report["enhanced_by"] = previous.get("enhanced_by")
    reports[project_id] = report
    save_evolution_reports(reports)
    return {
        "refreshed": True,
        "sample_count": int(report.get("sample_count", 0) or 0),
    }


def generate_and_persist(
    project_id: str,
    project: Record,
    *,
    resolve_project_score_scale_max: Callback,
    load_ground_truth: Callable[[], Records],
    normalize_ground_truth_record: Callback,
    load_project_context: Callable[[], Dict[str, Record]],
    merge_materials_text: Callable[[str], str],
    build_evolution_report: Callback,
    enhance_evolution_report: Callback,
    load_evolution_reports: Callable[[], Dict[str, Record]],
    save_evolution_reports: Callable[[Dict[str, Record]], None],
) -> Record:
    project_score_scale = resolve_project_score_scale_max(project)
    records_raw = [
        record for record in load_ground_truth() if record.get("project_id") == project_id
    ]
    records = [
        normalize_ground_truth_record(
            record if isinstance(record, dict) else {},
            default_score_scale_max=project_score_scale,
        )
        for record in records_raw
    ]
    ctx_data = load_project_context().get(project_id) or {}
    project_context = (ctx_data.get("text") or "").strip()
    materials_text = merge_materials_text(project_id)
    if materials_text:
        project_context = (
            (project_context + "\n\n" + materials_text) if project_context else materials_text
        )

    report = build_evolution_report(project_id, records, project_context)
    enhanced = enhance_evolution_report(project_id, report, records, project_context)
    if enhanced is not None:
        report["high_score_logic"] = enhanced.get("high_score_logic", report["high_score_logic"])
        report["writing_guidance"] = enhanced.get("writing_guidance", report["writing_guidance"])
        report["sample_count"] = enhanced.get("sample_count", report["sample_count"])
        report["updated_at"] = enhanced.get("updated_at", report["updated_at"])
        report["enhanced_by"] = enhanced.get("enhanced_by")

    reports = load_evolution_reports()
    reports[project_id] = report
    save_evolution_reports(reports)
    return report
