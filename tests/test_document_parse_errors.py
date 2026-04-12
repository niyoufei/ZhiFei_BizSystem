from __future__ import annotations

from app.domain.documents.parse_errors import (
    DocumentParseError,
    coerce_document_parse_error,
)


def test_coerce_document_parse_error_preserves_existing_error() -> None:
    error = DocumentParseError("document_corrupted", "解析失败：文件损坏。")
    assert coerce_document_parse_error(error) is error


def test_coerce_document_parse_error_maps_missing_file() -> None:
    error = coerce_document_parse_error(FileNotFoundError("/tmp/missing.pdf"))
    assert error.code == "source_file_missing"
    assert "源文件不存在" in error.detail


def test_coerce_document_parse_error_maps_unsupported_suffix() -> None:
    error = coerce_document_parse_error(
        ValueError("unsupported container"),
        filename="sample.xyz",
    )
    assert error.code == "unsupported_document_format"
    assert "文件格式不受支持" in error.detail


def test_coerce_document_parse_error_uses_filename_suffix_in_fallback_detail() -> None:
    error = coerce_document_parse_error(ValueError("boom"), filename="sample.pdf")
    assert error.code == "document_parse_failed"
    assert ".pdf" in error.detail
