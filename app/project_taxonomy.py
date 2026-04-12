from __future__ import annotations

import re
import unicodedata
from typing import Optional

PROJECT_TYPE_OPTIONS = (
    "装修及景观",
    "高标准农田",
    "生态环境",
    "服务方案",
    "其他项目",
)

PROJECT_TYPE_ALIASES = {
    "装修及景观项目": "装修及景观",
    "高标准农田项目": "高标准农田",
    "生态环境项目": "生态环境",
}

BID_METHOD_OPTIONS = (
    "AI合理价格法",
    "AI综合评估法（三阶段）",
    "综合评估法（三阶段）",
    "评定分离",
)

BID_METHOD_ALIASES = {
    "AI评标": "AI综合评估法（三阶段）",
    "技术评分合理价格法": "AI合理价格法",
    "三阶段": "综合评估法（三阶段）",
}

REMOVED_BID_METHODS = frozenset({"技术评分最低标价法"})


def _normalize_catalog_value(value: object) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).replace("\u3000", " ")
    return re.sub(r"\s+", " ", raw).strip()


def normalize_project_type(value: object) -> Optional[str]:
    normalized = _normalize_catalog_value(value)
    if not normalized:
        return None
    candidate = PROJECT_TYPE_ALIASES.get(normalized, normalized)
    return candidate if candidate in PROJECT_TYPE_OPTIONS else None


def normalize_bid_method(value: object) -> Optional[str]:
    normalized = _normalize_catalog_value(value)
    if not normalized:
        return None
    if normalized in REMOVED_BID_METHODS:
        return None
    candidate = BID_METHOD_ALIASES.get(normalized, normalized)
    return candidate if candidate in BID_METHOD_OPTIONS else None


def is_removed_bid_method(value: object) -> bool:
    return _normalize_catalog_value(value) in REMOVED_BID_METHODS
