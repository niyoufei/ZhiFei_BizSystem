from __future__ import annotations

from pathlib import Path


class DocumentParseError(Exception):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        debug_detail: str | None = None,
        status_code: int = 422,
    ) -> None:
        super().__init__(detail)
        self.code = str(code or "document_parse_failed").strip() or "document_parse_failed"
        self.detail = str(detail or "解析失败").strip() or "解析失败"
        self.debug_detail = str(debug_detail or "").strip() or None
        self.status_code = int(status_code)


def coerce_document_parse_error(
    exc: Exception,
    *,
    filename: str = "",
) -> DocumentParseError:
    if isinstance(exc, DocumentParseError):
        return exc
    lower = str(exc or "").strip().lower()
    if isinstance(exc, FileNotFoundError):
        return DocumentParseError(
            "source_file_missing",
            "解析失败：源文件不存在，请重新上传后重试。",
            debug_detail=str(exc),
        )
    if any(token in lower for token in ("password", "encrypted", "needs pass", "decrypt")):
        return DocumentParseError(
            "document_encrypted",
            "解析失败：文件已加密或受密码保护，请解除保护后重试。",
            debug_detail=str(exc),
        )
    if "仅支持" in str(exc) or "unsupported" in lower or "not support" in lower:
        return DocumentParseError(
            "unsupported_document_format",
            "解析失败：文件格式不受支持，请转换为系统支持的文档格式后重试。",
            debug_detail=str(exc),
        )
    if any(
        token in lower
        for token in (
            "cannot open broken document",
            "eof marker not found",
            "malformed pdf",
            "trailer",
            "xref",
            "corrupt",
            "damaged",
            "bad offset",
        )
    ):
        return DocumentParseError(
            "document_corrupted",
            "解析失败：文件已损坏、结构异常或不是有效文档，请更换文件后重试。",
            debug_detail=str(exc),
        )
    suffix = Path(str(filename or "")).suffix.lower() or "该"
    return DocumentParseError(
        "document_parse_failed",
        f"解析失败：{suffix} 文件无法完成解析，请检查文件格式、内容完整性后重试。",
        debug_detail=str(exc),
    )
