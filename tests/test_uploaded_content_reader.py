from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from app.infrastructure.documents.uploaded_content import (
    read_uploaded_file_content,
    read_uploaded_file_content_for_parse_mode,
    read_uploaded_file_preview_for_project_name,
)


def test_read_uploaded_file_content_passes_material_type_to_pdf_reader() -> None:
    pdf_reader = Mock(return_value="pdf-text")

    result = read_uploaded_file_content(
        b"%PDF-test",
        "sample.pdf",
        material_type="tender_qa",
        document_cls=None,
        read_bytes_from_path=lambda path: path.read_bytes(),
        extract_pdf_text=pdf_reader,
        extract_dxf_text=lambda payload: "dxf",
        extract_dwg_text=lambda payload, filename: "dwg",
        extract_image_content=lambda content, filename, file_path=None: "img",
    )

    assert result == "pdf-text"
    pdf_reader.assert_called_once_with(
        b"%PDF-test",
        "sample.pdf",
        material_type="tender_qa",
        file_path=None,
    )


def test_read_uploaded_file_content_uses_binary_snippet_for_doc() -> None:
    result = read_uploaded_file_content(
        "施工组织设计总说明".encode("utf-8"),
        "sample.doc",
        document_cls=None,
        read_bytes_from_path=lambda path: path.read_bytes(),
        extract_pdf_text=lambda *args, **kwargs: "pdf",
        extract_dxf_text=lambda payload: "dxf",
        extract_dwg_text=lambda payload, filename: "dwg",
        extract_image_content=lambda content, filename, file_path=None: "img",
    )

    assert "施工组织设计总说明" in result


def test_read_uploaded_file_content_uses_read_bytes_for_path(tmp_path: Path) -> None:
    sample = tmp_path / "sample.bin"
    sample.write_text("从路径读取的正文", encoding="utf-8")
    reader = Mock(side_effect=lambda path: path.read_bytes())

    result = read_uploaded_file_content(
        None,
        "sample.doc",
        file_path=sample,
        document_cls=None,
        read_bytes_from_path=reader,
        extract_pdf_text=lambda *args, **kwargs: "pdf",
        extract_dxf_text=lambda payload: "dxf",
        extract_dwg_text=lambda payload, filename: "dwg",
        extract_image_content=lambda content, filename, file_path=None: "img",
    )

    assert "从路径读取的正文" in result
    reader.assert_called_once_with(sample)


def test_read_uploaded_file_preview_for_project_name_passes_pdf_preview_options() -> None:
    preview_reader = Mock(return_value="preview-text")

    result = read_uploaded_file_preview_for_project_name(
        b"%PDF-test",
        "招标文件.pdf",
        document_cls=None,
        read_bytes_from_path=lambda path: path.read_bytes(),
        read_uploaded_file_content_impl=lambda *args, **kwargs: "full-text",
        extract_pdf_text_preview=preview_reader,
    )

    assert result == "preview-text"
    preview_reader.assert_called_once_with(
        b"%PDF-test",
        "招标文件.pdf",
        material_type="tender_qa",
        max_pages=10,
        max_chars=32000,
        ocr_pages=3,
        stop_when_project_name_found=True,
        file_path=None,
    )


def test_read_uploaded_file_content_for_parse_mode_uses_boq_excerpt_in_full_mode() -> None:
    boq_excerpt_reader = Mock(return_value="boq-full-excerpt")
    full_reader = Mock(return_value="full-text")

    result = read_uploaded_file_content_for_parse_mode(
        b"fake-boq-content",
        "工程量清单.xlsx",
        material_type="boq",
        parse_mode="full",
        normalize_material_type=lambda material_type, filename=None: "boq",
        read_uploaded_file_content_impl=full_reader,
        extract_pdf_text_preview=lambda *args, **kwargs: "preview",
        extract_boq_tabular_preview_text=boq_excerpt_reader,
        preview_max_pages_by_type={"boq": 4},
        preview_max_chars_by_type={"boq": 16000},
        preview_ocr_pages_by_type={"boq": 2},
        preview_max_sheets_by_type={"boq": 2},
        preview_max_rows_by_type={"boq": 180},
        text_max_sheets_by_type={"boq": 4},
        text_max_rows_by_type={"boq": 600},
    )

    assert result == "boq-full-excerpt"
    boq_excerpt_reader.assert_called_once_with(
        b"fake-boq-content",
        "工程量清单.xlsx",
        max_sheets=4,
        max_rows_per_sheet=600,
        file_path=None,
    )
    full_reader.assert_not_called()
