from __future__ import annotations

from app.infrastructure.documents.uploaded_content import UploadedContentReaderDependencies


def get_default_uploaded_content_reader_dependencies() -> UploadedContentReaderDependencies:
    from app.application import runtime as runtime_module

    return UploadedContentReaderDependencies(
        document_cls=getattr(runtime_module, "Document", None),
        read_bytes_from_path=runtime_module.read_bytes,
        extract_pdf_text=runtime_module._extract_pdf_text,
        extract_dxf_text=runtime_module._extract_dxf_text,
        extract_dwg_text=runtime_module._extract_dwg_text,
        extract_image_content=runtime_module._extract_image_content,
    )
