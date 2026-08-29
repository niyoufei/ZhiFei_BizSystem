from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List

Record = Dict[str, object]
LoadRecords = Callable[[], List[Record]]
SaveRecords = Callable[[List[Record]], None]


def delete_material(
    *,
    project_id: str,
    material_id: str,
    ensure_data_dirs: Callable[[], None],
    load_projects: LoadRecords,
    load_materials: LoadRecords,
    save_materials: SaveRecords,
    invalidate_material_index_cache: Callable[[str], None],
    project_not_found_error: Callable[[], Exception],
    material_not_found_error: Callable[[], Exception],
) -> Dict[str, object]:
    ensure_data_dirs()
    projects = load_projects()
    if not any(project["id"] == project_id for project in projects):
        raise project_not_found_error()
    materials = load_materials()
    found = None
    for material in materials:
        if material.get("id") == material_id and material.get("project_id") == project_id:
            found = material
            break
    if not found:
        raise material_not_found_error()
    path = Path(found["path"])
    if path.exists():
        path.unlink()
    materials = [material for material in materials if material.get("id") != material_id]
    save_materials(materials)
    invalidate_material_index_cache(project_id)
    return {"ok": True, "id": material_id}
