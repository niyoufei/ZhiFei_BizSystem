from __future__ import annotations

import re
import unicodedata
from typing import Optional

from fastapi import HTTPException

from app.project_taxonomy import is_removed_bid_method, normalize_bid_method, normalize_project_type


def normalize_project_name_key(name: object) -> str:
    raw = unicodedata.normalize("NFKC", str(name or "")).replace("\u3000", " ")
    return re.sub(r"\s+", " ", raw).strip()


def normalize_project_type_input_or_422(value: object) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = normalize_project_type(value)
    if normalized is None:
        raise HTTPException(status_code=422, detail="项目类型无效，请从下拉列表重新选择")
    return normalized


def normalize_bid_method_input_or_422(value: object) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if is_removed_bid_method(value):
        raise HTTPException(
            status_code=422, detail="评标方式“技术评分最低标价法”已停用，请重新选择"
        )
    normalized = normalize_bid_method(value)
    if normalized is None:
        raise HTTPException(status_code=422, detail="评标方式无效，请从下拉列表重新选择")
    return normalized


def find_project_by_name(
    projects: list[dict[str, object]],
    name: object,
) -> Optional[dict[str, object]]:
    target = normalize_project_name_key(name)
    if not target:
        return None
    for project in projects:
        if normalize_project_name_key(project.get("name")) == target:
            return project
    return None


def project_matches_id(project: dict[str, object], project_id: str) -> bool:
    return str(project.get("id") or "") == str(project_id or "")


def project_exists(project_id: str, projects: list[dict[str, object]]) -> bool:
    return any(project_matches_id(project, project_id) for project in projects)


def find_project_optional(
    project_id: str,
    projects: list[dict[str, object]],
) -> Optional[dict[str, object]]:
    return next((project for project in projects if project_matches_id(project, project_id)), None)
