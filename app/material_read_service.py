from __future__ import annotations

from typing import Callable, Dict, List, Optional

RecordLoader = Callable[[], List[Dict[str, object]]]
MaterialTypeNormalizer = Callable[..., str]


def get_material_catalog_projection(
    *,
    project_id: str,
    load_projects: RecordLoader,
    load_materials: RecordLoader,
    normalize_material_type: MaterialTypeNormalizer,
) -> Optional[List[Dict[str, object]]]:
    """Return the normalized material catalog for one existing project."""
    projects = load_projects()
    if not any(project["id"] == project_id for project in projects):
        return None

    materials = [
        material for material in load_materials() if material.get("project_id") == project_id
    ]
    materials.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)

    normalized_rows: List[Dict[str, object]] = []
    for material in materials:
        row = dict(material)
        row["material_type"] = normalize_material_type(
            row.get("material_type"), filename=row.get("filename")
        )
        normalized_rows.append(row)
    return normalized_rows
