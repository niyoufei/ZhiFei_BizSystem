from __future__ import annotations

from typing import Callable, Dict, List

Record = Dict[str, object]
Records = List[Record]


def generate_and_persist(
    project_id: str,
    submissions: Records,
    *,
    build_learning_profile: Callable[[Records], Record],
    load_learning_profiles: Callable[[], Records],
    save_learning_profiles: Callable[[Records], None],
    now_iso: Callable[[], str],
) -> Record:
    profile = build_learning_profile(submissions)
    record = {
        "project_id": project_id,
        "dimension_multipliers": profile["dimension_multipliers"],
        "rationale": profile["rationale"],
        "updated_at": now_iso(),
    }
    profiles = [
        existing
        for existing in load_learning_profiles()
        if existing.get("project_id") != project_id
    ]
    profiles.append(record)
    save_learning_profiles(profiles)
    return record
