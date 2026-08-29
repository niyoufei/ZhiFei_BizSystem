from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

Record = Dict[str, object]
LoadRecords = Callable[[], List[Record]]
SaveRecords = Callable[[List[Record]], None]
FindProject = Callable[[str, List[Record]], Record]
EnsureProjectProfile = Callable[[Record, List[Record]], Tuple[Record, bool]]


def ensure_project_expert_profile(
    project: Record,
    all_profiles: List[Record],
    *,
    new_expert_profile: Callable[[str, Dict[str, int]], Record],
    default_weights_raw: Callable[[], Dict[str, int]],
    now_iso: Callable[[], str],
) -> Tuple[Record, bool]:
    profile_id = str(project.get("expert_profile_id") or "")
    if profile_id:
        for profile in all_profiles:
            if profile.get("id") == profile_id:
                return profile, False

    profile_name = f"{project.get('name', '项目')} 默认配置"
    created = new_expert_profile(profile_name, default_weights_raw())
    all_profiles.append(created)
    project["expert_profile_id"] = created["id"]
    project["updated_at"] = now_iso()
    return created, True


def get_project_expert_profile(
    *,
    project_id: str,
    ensure_data_dirs: Callable[[], None],
    load_projects: LoadRecords,
    find_project: FindProject,
    ensure_project_v2_fields: Callable[[Record], bool],
    load_expert_profiles: LoadRecords,
    ensure_project_profile: EnsureProjectProfile,
    save_projects: SaveRecords,
    save_expert_profiles: SaveRecords,
) -> Tuple[Record, Record]:
    ensure_data_dirs()
    projects = load_projects()
    project = find_project(project_id, projects)

    project_changed = ensure_project_v2_fields(project)
    profiles = load_expert_profiles()
    profile, created = ensure_project_profile(project, profiles)
    if project_changed or created:
        save_projects(projects)
    if created:
        save_expert_profiles(profiles)
    return project, profile


def update_project_expert_profile(
    *,
    project_id: str,
    name: Optional[str],
    weights_raw: Dict[str, int],
    force_unlock: bool,
    ensure_data_dirs: Callable[[], None],
    load_projects: LoadRecords,
    find_project: FindProject,
    ensure_project_v2_fields: Callable[[Record], bool],
    assert_project_profile_operation_unlocked: Callable[[Record, bool], None],
    coerce_weights_raw: Callable[[Dict[str, int]], Dict[str, int]],
    new_expert_profile: Callable[[str, Dict[str, int]], Record],
    load_expert_profiles: LoadRecords,
    save_expert_profiles: SaveRecords,
    save_projects: SaveRecords,
    now_iso: Callable[[], str],
) -> Tuple[Record, Record]:
    ensure_data_dirs()
    projects = load_projects()
    project = find_project(project_id, projects)

    ensure_project_v2_fields(project)
    assert_project_profile_operation_unlocked(project, force_unlock)
    coerced_weights_raw = coerce_weights_raw(weights_raw)
    profile_name = (name or "").strip() or (
        f"{project.get('name', '项目')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    profile = new_expert_profile(profile_name, coerced_weights_raw)

    profiles = load_expert_profiles()
    profiles.append(profile)
    save_expert_profiles(profiles)

    project["expert_profile_id"] = profile["id"]
    project["updated_at"] = now_iso()
    save_projects(projects)
    return project, profile
