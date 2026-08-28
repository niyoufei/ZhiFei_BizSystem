from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Tuple

Record = Dict[str, object]


def write_material_upload(
    *,
    project_id: str,
    normalized_material_type: str,
    normalized_name: str,
    materials_dir: Path,
    read_content: Callable[[], bytes],
    commit_uploaded_material_record: Callable[[str, str, str, Path], Record],
    invalidate_material_index_cache: Callable[[str], None],
    rebuild_project_anchors_and_requirements: Callable[[str], Tuple[List[Record], List[Record]]],
) -> Record:
    project_dir = materials_dir / project_id / normalized_material_type
    project_dir.mkdir(parents=True, exist_ok=True)
    target = project_dir / normalized_name
    content = read_content()
    target.write_bytes(content)

    record = commit_uploaded_material_record(
        project_id,
        normalized_material_type,
        normalized_name,
        target,
    )
    invalidate_material_index_cache(project_id)

    constraint_sync: Record = {"rebuilt": False}
    try:
        anchors, requirements = rebuild_project_anchors_and_requirements(project_id)
        constraint_sync = {
            "rebuilt": True,
            "anchors": len(anchors),
            "requirements": len(requirements),
        }
    except Exception as exc:
        constraint_sync = {"rebuilt": False, "error": f"{type(exc).__name__}: {exc}"}

    return {"status": "ok", "material": record, "constraint_sync": constraint_sync}
