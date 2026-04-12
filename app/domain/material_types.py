from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Dict, List

from fastapi import HTTPException

MATERIAL_TYPE_DEFAULT = "tender_qa"
MATERIAL_TYPE_LABELS = {
    "tender_qa": "招标文件和答疑",
    "boq": "清单",
    "drawing": "图纸",
    "site_photo": "现场照片",
}
MATERIAL_TYPE_ALIASES = {
    "": MATERIAL_TYPE_DEFAULT,
    "material": MATERIAL_TYPE_DEFAULT,
    "materials": MATERIAL_TYPE_DEFAULT,
    "tender": "tender_qa",
    "bid": "tender_qa",
    "qa": "tender_qa",
    "qa_reply": "tender_qa",
    "list": "boq",
    "bill_of_quantities": "boq",
    "drawing_file": "drawing",
    "drawings": "drawing",
    "photo": "site_photo",
    "photos": "site_photo",
    "site_images": "site_photo",
}
MATERIAL_TYPE_ALLOWED_EXTS: Dict[str, tuple[str, ...]] = {
    "tender_qa": (
        ".txt",
        ".md",
        ".pdf",
        ".doc",
        ".docx",
        ".docm",
        ".json",
        ".xlsx",
        ".xls",
        ".xlsm",
        ".csv",
        ".dxf",
        ".dwg",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
    ),
    "boq": (".xlsx", ".xls", ".xlsm", ".csv", ".pdf", ".doc", ".docx", ".txt", ".json"),
    "drawing": (
        ".pdf",
        ".doc",
        ".docx",
        ".xlsx",
        ".xls",
        ".dxf",
        ".dwg",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".json",
        ".txt",
    ),
    "site_photo": (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"),
}
MATERIAL_ALLOWED_EXTS = tuple(
    sorted({ext for exts in MATERIAL_TYPE_ALLOWED_EXTS.values() for ext in exts})
)
MATERIAL_TYPE_ALLOWED_MIME_TOKENS: Dict[str, tuple[str, ...]] = {
    "tender_qa": (
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/pdf",
        "application/json",
        "application/msword",
        "wordprocessingml",
        "spreadsheetml",
        "ms-excel",
        "application/dxf",
        "image/vnd.dxf",
        "application/acad",
        "application/x-autocad",
        "drawing/x-dxf",
        "image/",
    ),
    "boq": (
        "text/plain",
        "text/csv",
        "application/pdf",
        "application/json",
        "application/msword",
        "wordprocessingml",
        "spreadsheetml",
        "ms-excel",
    ),
    "drawing": (
        "text/plain",
        "application/pdf",
        "application/json",
        "application/msword",
        "wordprocessingml",
        "spreadsheetml",
        "ms-excel",
        "application/dxf",
        "image/vnd.dxf",
        "application/acad",
        "application/x-autocad",
        "drawing/x-dxf",
        "image/",
    ),
    "site_photo": ("image/",),
}
MATERIAL_ALLOWED_MIME_TOKENS = tuple(
    sorted({token for tokens in MATERIAL_TYPE_ALLOWED_MIME_TOKENS.values() for token in tokens})
)
MATERIAL_TYPE_DIMENSION_PRIORITY: Dict[str, List[str]] = {
    "tender_qa": ["01", "08", "09", "07"],
    "boq": ["13", "15", "11", "04"],
    "drawing": ["14", "16", "07", "12"],
    "site_photo": ["02", "03", "07", "08"],
}
MATERIAL_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "tender_qa": ["答疑", "澄清", "变更", "条款", "工期", "质量标准", "计价规则"],
    "boq": ["清单", "工程量", "综合单价", "措施费", "暂估价", "设备", "甲供材"],
    "drawing": ["图纸", "节点", "平面", "剖面", "BIM", "碰撞", "深化"],
    "site_photo": ["现场", "照片", "临边", "扬尘", "消防", "高处", "塔吊"],
}


def normalize_uploaded_filename(filename: str) -> str:
    raw = unicodedata.normalize("NFKC", str(filename or "")).replace("\u3000", " ").strip()
    base = Path(raw).name.strip()
    while base.endswith("."):
        base = base[:-1].rstrip()
    return base


def infer_material_type_from_filename(filename: object) -> str:
    normalized = normalize_uploaded_filename(str(filename or "")).lower()
    ext = Path(normalized).suffix.lower()
    name = normalized

    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        if any(k in name for k in ("现场", "实景", "照片", "photo", "image", "img")):
            return "site_photo"
        return "drawing"
    if ext in {".dxf", ".dwg"}:
        return "drawing"
    if ext in {".xlsx", ".xls", ".xlsm", ".csv"}:
        return "boq"
    if any(k in name for k in ("清单", "boq", "bill_of_quantities", "工程量")):
        return "boq"
    if any(k in name for k in ("图纸", "总图", "平面", "立面", "剖面", "cad", "详图", "节点图")):
        return "drawing"
    if any(k in name for k in ("现场", "实景", "照片", "photo", "image", "img")):
        return "site_photo"
    return MATERIAL_TYPE_DEFAULT


def normalize_material_type(material_type: object, *, filename: object = "") -> str:
    raw = str(material_type or "").strip().lower().replace("-", "_")
    if not raw:
        return infer_material_type_from_filename(filename)
    normalized = MATERIAL_TYPE_ALIASES.get(raw, raw)
    if normalized in MATERIAL_TYPE_LABELS:
        return normalized
    return infer_material_type_from_filename(filename)


def parse_material_type_or_422(material_type: object, *, filename: object = "") -> str:
    raw = str(material_type or "").strip()
    if not raw:
        return normalize_material_type("", filename=filename)
    normalized = normalize_material_type(raw, filename=filename)
    raw_key = raw.lower().replace("-", "_")
    if raw_key in MATERIAL_TYPE_ALIASES or raw_key in MATERIAL_TYPE_LABELS:
        return normalized
    supported = "、".join(MATERIAL_TYPE_LABELS.keys())
    raise HTTPException(status_code=422, detail=f"material_type 不支持：{raw}（支持：{supported}）")


def material_type_label(material_type: object, *, filename: object = "") -> str:
    return MATERIAL_TYPE_LABELS.get(
        normalize_material_type(material_type, filename=filename), "项目资料"
    )


def material_type_ext_hint(material_type: object, *, filename: object = "") -> str:
    normalized = normalize_material_type(material_type, filename=filename)
    exts = MATERIAL_TYPE_ALLOWED_EXTS.get(normalized) or MATERIAL_ALLOWED_EXTS
    return "、".join(exts)


def is_allowed_material_upload(filename: str, content_type: str, material_type: str) -> bool:
    normalized = normalize_uploaded_filename(filename).lower()
    allowed_exts = MATERIAL_TYPE_ALLOWED_EXTS.get(material_type) or MATERIAL_ALLOWED_EXTS
    if normalized and any(normalized.endswith(ext) for ext in allowed_exts):
        return True
    ctype = str(content_type or "").lower().strip()
    allowed_tokens = (
        MATERIAL_TYPE_ALLOWED_MIME_TOKENS.get(material_type) or MATERIAL_ALLOWED_MIME_TOKENS
    )
    return any(token in ctype for token in allowed_tokens)
