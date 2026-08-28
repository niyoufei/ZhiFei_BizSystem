from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Dict, List

Record = Dict[str, object]
LoadRecords = Callable[[], List[Record]]
SaveRecords = Callable[[List[Record]], None]


def delete_project_cascade(
    *,
    project_id: str,
    materials_dir: Path,
    ensure_data_dirs: Callable[[], None],
    load_projects: LoadRecords,
    save_projects: SaveRecords,
    load_materials: LoadRecords,
    save_materials: SaveRecords,
    invalidate_material_index_cache: Callable[[str], None],
    load_submissions: LoadRecords,
    save_submissions: SaveRecords,
    load_score_reports: LoadRecords,
    save_score_reports: SaveRecords,
    load_evidence_units: LoadRecords,
    save_evidence_units: SaveRecords,
    load_qingtian_results: LoadRecords,
    save_qingtian_results: SaveRecords,
    load_delta_cases: LoadRecords,
    save_delta_cases: SaveRecords,
    load_calibration_samples: LoadRecords,
    save_calibration_samples: SaveRecords,
    load_patch_packages: LoadRecords,
    save_patch_packages: SaveRecords,
    load_patch_deployments: LoadRecords,
    save_patch_deployments: SaveRecords,
    load_project_anchors: LoadRecords,
    save_project_anchors: SaveRecords,
    load_project_requirements: LoadRecords,
    save_project_requirements: SaveRecords,
    load_learning_profiles: LoadRecords,
    save_learning_profiles: SaveRecords,
    load_score_history: LoadRecords,
    save_score_history: SaveRecords,
    load_project_context: Callable[[], Dict[str, object]],
    save_project_context: Callable[[Dict[str, object]], None],
    load_ground_truth: LoadRecords,
    save_ground_truth: SaveRecords,
    load_evolution_reports: Callable[[], Dict[str, object]],
    save_evolution_reports: Callable[[Dict[str, object]], None],
    load_expert_profiles: LoadRecords,
    save_expert_profiles: SaveRecords,
    project_not_found_error: Callable[[], Exception],
) -> Dict[str, object]:
    ensure_data_dirs()
    projects = load_projects()
    target = next((p for p in projects if str(p.get("id")) == project_id), None)
    if target is None:
        raise project_not_found_error()
    target_name = str(target.get("name") or project_id)
    target_profile_id = str(target.get("expert_profile_id") or "")

    removed_counts = {
        "materials": 0,
        "submissions": 0,
        "score_reports": 0,
        "ground_truth": 0,
        "delta_cases": 0,
        "calibration_samples": 0,
        "patch_packages": 0,
    }

    save_projects([p for p in projects if p.get("id") != project_id])

    materials = load_materials()
    project_materials = [m for m in materials if m.get("project_id") == project_id]
    removed_counts["materials"] = len(project_materials)
    for material in project_materials:
        path = Path(str(material.get("path") or ""))
        if path.exists() and path.is_file():
            try:
                path.unlink()
            except Exception:
                pass
    save_materials([m for m in materials if m.get("project_id") != project_id])
    invalidate_material_index_cache(project_id)

    project_dir = materials_dir / project_id
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)

    submissions = load_submissions()
    project_submission_ids = {
        str(s.get("id")) for s in submissions if s.get("project_id") == project_id
    }
    removed_counts["submissions"] = len(project_submission_ids)
    save_submissions([s for s in submissions if s.get("project_id") != project_id])

    score_reports = load_score_reports()
    removed_counts["score_reports"] = sum(
        1 for r in score_reports if r.get("project_id") == project_id
    )
    save_score_reports([r for r in score_reports if r.get("project_id") != project_id])
    evidence_units = load_evidence_units()
    save_evidence_units(
        [u for u in evidence_units if str(u.get("submission_id")) not in project_submission_ids]
    )
    qingtian_results = load_qingtian_results()
    save_qingtian_results(
        [q for q in qingtian_results if str(q.get("submission_id")) not in project_submission_ids]
    )
    delta_cases = load_delta_cases()
    removed_counts["delta_cases"] = sum(
        1
        for d in delta_cases
        if str(d.get("project_id")) == project_id
        or str(d.get("submission_id")) in project_submission_ids
    )
    save_delta_cases(
        [
            d
            for d in delta_cases
            if str(d.get("project_id")) != project_id
            and str(d.get("submission_id")) not in project_submission_ids
        ]
    )
    calibration_samples = load_calibration_samples()
    removed_counts["calibration_samples"] = sum(
        1
        for s in calibration_samples
        if str(s.get("project_id")) == project_id
        or str(s.get("submission_id")) in project_submission_ids
    )
    save_calibration_samples(
        [
            s
            for s in calibration_samples
            if str(s.get("project_id")) != project_id
            and str(s.get("submission_id")) not in project_submission_ids
        ]
    )
    patch_packages = load_patch_packages()
    removed_patch_ids = {
        str(p.get("id")) for p in patch_packages if str(p.get("project_id")) == project_id
    }
    removed_counts["patch_packages"] = len(removed_patch_ids)
    save_patch_packages([p for p in patch_packages if str(p.get("project_id")) != project_id])
    patch_deployments = load_patch_deployments()
    save_patch_deployments(
        [
            d
            for d in patch_deployments
            if str(d.get("project_id")) != project_id
            and str(d.get("patch_id")) not in removed_patch_ids
        ]
    )

    anchors = load_project_anchors()
    save_project_anchors([a for a in anchors if a.get("project_id") != project_id])
    requirements = load_project_requirements()
    save_project_requirements([r for r in requirements if r.get("project_id") != project_id])

    learning_profiles = load_learning_profiles()
    save_learning_profiles([p for p in learning_profiles if p.get("project_id") != project_id])

    score_history = load_score_history()
    save_score_history([h for h in score_history if h.get("project_id") != project_id])

    context = load_project_context()
    if project_id in context:
        context.pop(project_id, None)
        save_project_context(context)

    ground_truth = load_ground_truth()
    removed_counts["ground_truth"] = sum(
        1 for r in ground_truth if r.get("project_id") == project_id
    )
    save_ground_truth([r for r in ground_truth if r.get("project_id") != project_id])

    reports = load_evolution_reports()
    if project_id in reports:
        reports.pop(project_id, None)
        save_evolution_reports(reports)

    if target_profile_id:
        remaining_projects = load_projects()
        in_use = any(
            str(p.get("expert_profile_id") or "") == target_profile_id for p in remaining_projects
        )
        if not in_use:
            profiles = load_expert_profiles()
            profiles = [ep for ep in profiles if str(ep.get("id") or "") != target_profile_id]
            save_expert_profiles(profiles)

    return {
        "project_id": project_id,
        "project_name": target_name,
        "removed_counts": removed_counts,
    }
