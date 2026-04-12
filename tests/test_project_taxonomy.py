from __future__ import annotations

from app.project_taxonomy import (
    is_removed_bid_method,
    normalize_bid_method,
    normalize_project_type,
)


def test_normalize_project_type_maps_legacy_values() -> None:
    assert normalize_project_type("装修及景观项目") == "装修及景观"
    assert normalize_project_type("高标准农田项目") == "高标准农田"
    assert normalize_project_type("生态环境") == "生态环境"


def test_normalize_bid_method_maps_legacy_values() -> None:
    assert normalize_bid_method("AI评标") == "AI综合评估法（三阶段）"
    assert normalize_bid_method("技术评分合理价格法 ") == "AI合理价格法"
    assert normalize_bid_method("三阶段") == "综合评估法（三阶段）"


def test_removed_bid_method_is_detected() -> None:
    assert is_removed_bid_method("技术评分最低标价法") is True
    assert normalize_bid_method("技术评分最低标价法") is None
