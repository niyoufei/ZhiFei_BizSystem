from __future__ import annotations

from typing import Callable, Dict, List, Optional

Record = Dict[str, object]
Records = List[Record]
RecordLoader = Callable[[], Records]
ProjectSaver = Callable[[Records], None]


def recover_missing_project_from_artifacts(
    project_id: str,
    projects: Records,
    *,
    load_projects: RecordLoader,
    load_submissions: RecordLoader,
    load_materials: RecordLoader,
    load_ground_truth: RecordLoader,
    load_evolution_reports: Callable[[], Dict[str, Record]],
    save_projects: ProjectSaver,
    ensure_project_v2_fields: Callable[[Record], bool],
    now_iso: Callable[[], str],
    default_score_scale_max: int,
    default_region: str,
    default_qingtian_model_version: str,
    default_scoring_engine_locked: str,
    default_calibrator_locked: str,
) -> Optional[Record]:
    projects[:] = load_projects()
    project_id = str(project_id or "").strip()
    if not project_id:
        return None
    for project in projects:
        if str(project.get("id") or "") == project_id:
            return project

    submissions = [
        row for row in load_submissions() if str(row.get("project_id") or "") == project_id
    ]
    materials = [row for row in load_materials() if str(row.get("project_id") or "") == project_id]
    ground_truth = [
        row for row in load_ground_truth() if str(row.get("project_id") or "") == project_id
    ]
    evolution_reports = load_evolution_reports()
    if (
        not submissions
        and not materials
        and not ground_truth
        and project_id not in evolution_reports
    ):
        return None

    name_seed = ""
    for row in materials + submissions:
        filename = str(row.get("filename") or "").strip()
        if filename:
            name_seed = filename
            break
    if name_seed:
        stem = name_seed.rsplit(".", 1)[0].strip()
        recovered_name = (stem or name_seed) + "（恢复）"
    else:
        recovered_name = f"恢复项目_{project_id[:8]}"

    time_points: List[str] = []
    for row in submissions:
        created_at = str(row.get("created_at") or "").strip()
        updated_at = str(row.get("updated_at") or "").strip()
        if created_at:
            time_points.append(created_at)
        if updated_at:
            time_points.append(updated_at)
    for row in materials + ground_truth:
        created_at = str(row.get("created_at") or "").strip()
        if created_at:
            time_points.append(created_at)
    evolution_updated_at = str(
        (evolution_reports.get(project_id) or {}).get("updated_at") or ""
    ).strip()
    if evolution_updated_at:
        time_points.append(evolution_updated_at)
    created_at = min(time_points) if time_points else now_iso()
    updated_at = max(time_points) if time_points else now_iso()

    score_scale_max = default_score_scale_max
    for submission in submissions:
        report = submission.get("report")
        if not isinstance(report, dict):
            continue
        meta = report.get("meta")
        if not isinstance(meta, dict):
            continue
        raw_scale = meta.get("score_scale_max")
        if str(raw_scale) == "5":
            score_scale_max = 5
            break
        if str(raw_scale) == "100":
            score_scale_max = 100

    recovered: Record = {
        "id": project_id,
        "name": recovered_name,
        "meta": {"score_scale_max": score_scale_max},
        "region": default_region,
        "expert_profile_id": None,
        "qingtian_model_version": default_qingtian_model_version,
        "scoring_engine_version_locked": default_scoring_engine_locked,
        "calibrator_version_locked": default_calibrator_locked,
        "status": "scoring_preparation",
        "created_at": created_at,
        "updated_at": updated_at,
    }
    ensure_project_v2_fields(recovered)
    projects.append(recovered)
    save_projects(projects)
    return recovered


def recover_latest_orphan_project(
    projects: Records,
    *,
    load_submissions: RecordLoader,
    load_materials: RecordLoader,
    recover_missing_project: Callable[[str, Records], Optional[Record]],
) -> Optional[Record]:
    existing_ids = {str(project.get("id") or "") for project in projects}
    latest_project_id = ""
    latest_at = ""

    for row in load_submissions():
        project_id = str(row.get("project_id") or "").strip()
        if not project_id or project_id in existing_ids:
            continue
        timestamp = str(row.get("updated_at") or row.get("created_at") or "").strip()
        if timestamp and timestamp > latest_at:
            latest_at = timestamp
            latest_project_id = project_id
    for row in load_materials():
        project_id = str(row.get("project_id") or "").strip()
        if not project_id or project_id in existing_ids:
            continue
        timestamp = str(row.get("created_at") or "").strip()
        if timestamp and timestamp > latest_at:
            latest_at = timestamp
            latest_project_id = project_id
    if not latest_project_id:
        return None
    return recover_missing_project(latest_project_id, projects)


def create_project_record(
    *,
    name: str,
    meta: Optional[Record],
    load_projects: RecordLoader,
    save_projects: ProjectSaver,
    duplicate_name_error: Callable[[], Exception],
    new_id: Callable[[], str],
    now_iso: Callable[[], str],
    ensure_project_v2_fields: Callable[[Record], bool],
    default_region: str,
    default_qingtian_model_version: str,
    default_scoring_engine_locked: str,
    default_calibrator_locked: str,
) -> Record:
    projects = load_projects()
    if any(project.get("name") == name for project in projects):
        raise duplicate_name_error()
    record: Record = {
        "id": new_id(),
        "name": name,
        "meta": meta or {},
        "region": default_region,
        "expert_profile_id": None,
        "qingtian_model_version": default_qingtian_model_version,
        "scoring_engine_version_locked": default_scoring_engine_locked,
        "calibrator_version_locked": default_calibrator_locked,
        "status": "scoring_preparation",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    ensure_project_v2_fields(record)
    projects.append(record)
    save_projects(projects)
    return record


def list_project_records(
    *,
    load_projects: RecordLoader,
    save_projects: ProjectSaver,
    ensure_project_v2_fields: Callable[[Record], bool],
    recovery_enabled: bool,
    recover_latest_orphan_project: Callable[[Records], Optional[Record]],
) -> Records:
    projects = load_projects()
    if recovery_enabled:
        active_projects = [
            project
            for project in projects
            if str(project.get("id") or "") != "p1"
            and not str(project.get("name") or "").startswith("E2E_")
        ]
        if not active_projects and recover_latest_orphan_project(projects) is not None:
            projects = load_projects()

    changed = False
    for project in projects:
        changed = ensure_project_v2_fields(project) or changed
    if changed:
        save_projects(projects)
    return projects
