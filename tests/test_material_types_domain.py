from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domain.material_types import (
    infer_material_type_from_filename,
    is_allowed_material_upload,
    material_type_ext_hint,
    material_type_label,
    normalize_material_type,
    normalize_uploaded_filename,
    parse_material_type_or_422,
)


def test_normalize_uploaded_filename_strips_path_and_trailing_dot() -> None:
    assert normalize_uploaded_filename(" foo/bar/清单.xlsx. ") == "清单.xlsx"


def test_infer_material_type_from_filename_prefers_site_photo_keywords() -> None:
    assert infer_material_type_from_filename("现场照片.JPG") == "site_photo"


def test_normalize_material_type_uses_alias_or_filename_fallback() -> None:
    assert normalize_material_type("drawings") == "drawing"
    assert normalize_material_type("", filename="工程量清单.xlsx") == "boq"


def test_parse_material_type_or_422_rejects_unknown_value() -> None:
    with pytest.raises(HTTPException) as exc_info:
        parse_material_type_or_422("unknown-type", filename="资料.pdf")
    assert exc_info.value.status_code == 422
    assert "material_type 不支持" in str(exc_info.value.detail)


def test_material_type_label_and_ext_hint_are_stable() -> None:
    assert material_type_label("site_photo") == "现场照片"
    assert ".png" in material_type_ext_hint("site_photo")


def test_is_allowed_material_upload_accepts_extension_or_matching_mime() -> None:
    assert is_allowed_material_upload("资料.docx", "", "tender_qa") is True
    assert is_allowed_material_upload("无后缀", "image/jpeg", "site_photo") is True
