from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.storage import (
    atomic_json_transaction,
    load_project_context,
    load_projects,
    save_project_context,
)


@atomic_json_transaction("project_context")
def set_project_context(
    *,
    project_id: str,
    text: str,
    filename: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Atomically replace one project's context while preserving other entries."""
    projects = load_projects()
    if not any(project["id"] == project_id for project in projects):
        return None

    context = load_project_context()
    data = {
        "text": text,
        "filename": filename,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    context[project_id] = data
    save_project_context(context)
    return {"project_id": project_id, **data}


def get_project_context(*, project_id: str) -> Optional[Dict[str, Any]]:
    """Return one project's context using the public endpoint defaults."""
    projects = load_projects()
    if not any(project["id"] == project_id for project in projects):
        return None

    data = load_project_context().get(project_id)
    if not data:
        return {
            "project_id": project_id,
            "text": "",
            "filename": None,
            "updated_at": None,
        }
    return {
        "project_id": project_id,
        "text": data.get("text", ""),
        "filename": data.get("filename"),
        "updated_at": data.get("updated_at"),
    }
