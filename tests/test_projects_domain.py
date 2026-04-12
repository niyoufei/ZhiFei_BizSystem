from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domain.projects import (
    find_project_by_name,
    find_project_optional,
    normalize_bid_method_input_or_422,
    normalize_project_name_key,
    normalize_project_type_input_or_422,
    project_exists,
)


def test_normalize_project_name_key_collapses_whitespace_and_full_width_space() -> None:
    assert normalize_project_name_key("  合肥\u3000新区   项目 ") == "合肥 新区 项目"


def test_normalize_project_type_input_or_422_accepts_catalog_alias() -> None:
    assert normalize_project_type_input_or_422("装修及景观项目") == "装修及景观"


def test_normalize_project_type_input_or_422_rejects_invalid_value() -> None:
    with pytest.raises(HTTPException) as exc_info:
        normalize_project_type_input_or_422("无效类型")
    assert exc_info.value.status_code == 422
    assert "项目类型无效" in str(exc_info.value.detail)


def test_normalize_bid_method_input_or_422_accepts_alias() -> None:
    assert normalize_bid_method_input_or_422("AI评标") == "AI综合评估法（三阶段）"


def test_normalize_bid_method_input_or_422_rejects_removed_value() -> None:
    with pytest.raises(HTTPException) as exc_info:
        normalize_bid_method_input_or_422("技术评分最低标价法")
    assert exc_info.value.status_code == 422
    assert "已停用" in str(exc_info.value.detail)


def test_find_project_helpers_use_normalized_name_and_id() -> None:
    projects = [
        {"id": "p1", "name": " 合肥新区项目 "},
        {"id": "p2", "name": "第二个项目"},
    ]
    matched = find_project_by_name(projects, "合肥新区项目")
    assert matched == projects[0]
    assert find_project_optional("p2", projects) == projects[1]
    assert project_exists("p1", projects) is True
    assert project_exists("missing", projects) is False
