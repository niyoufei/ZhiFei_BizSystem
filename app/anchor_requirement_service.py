from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from app.storage import atomic_json_transaction

Record = Dict[str, object]
LoadRecords = Callable[[], List[Record]]
SaveRecords = Callable[[List[Record]], None]


def rebuild_project_anchors_and_requirements(
    *,
    project_id: str,
    default_region: str,
    default_scoring_engine_locked: str,
    build_constraints_source_text: Callable[[str], str],
    load_projects: LoadRecords,
    extract_project_anchors_from_text: Callable[[str, str], List[Record]],
    build_project_requirements_from_anchors: Callable[..., List[Record]],
    load_project_anchors: LoadRecords,
    save_project_anchors: SaveRecords,
    load_project_requirements: LoadRecords,
    save_project_requirements: SaveRecords,
    project_not_found_error: Callable[[], Exception],
) -> Tuple[List[Record], List[Record]]:
    merged_text = build_constraints_source_text(project_id)
    project = next(
        (p for p in load_projects() if str(p.get("id")) == project_id),
        {},
    )
    region = str(project.get("region") or default_region)
    scoring_engine_version = str(
        project.get("scoring_engine_version_locked") or default_scoring_engine_locked
    )
    anchors = extract_project_anchors_from_text(project_id, merged_text)
    requirements = build_project_requirements_from_anchors(
        project_id,
        anchors,
        region=region,
        scoring_engine_version=scoring_engine_version,
    )

    @atomic_json_transaction("project_anchors", "project_requirements", "projects")
    def commit() -> None:
        if not any(str(p.get("id")) == project_id for p in load_projects()):
            raise project_not_found_error()
        all_anchors = [
            anchor
            for anchor in load_project_anchors()
            if str(anchor.get("project_id")) != project_id
        ]
        all_requirements = [
            requirement
            for requirement in load_project_requirements()
            if str(requirement.get("project_id")) != project_id
        ]
        all_anchors.extend(anchors)
        all_requirements.extend(requirements)
        save_project_anchors(all_anchors)
        save_project_requirements(all_requirements)

    commit()
    return anchors, requirements
