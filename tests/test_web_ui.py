"""Tests for web_ui module."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from docx import Document

try:
    import pymupdf
except Exception:  # noqa: BLE001 - environment-dependent optional dependency
    pymupdf = None


class TestReadUploadedFile:
    """Tests for read_uploaded_file function."""

    def test_read_txt_file(self):
        """Test reading a .txt file."""
        from app.web_ui import read_uploaded_file

        mock_file = MagicMock()
        mock_file.name = "test.txt"
        mock_file.read.return_value = "测试文本内容".encode("utf-8")

        result = read_uploaded_file(mock_file)
        assert result == "测试文本内容"

    def test_read_docx_file(self):
        """Test reading a .docx file."""
        from app.web_ui import read_uploaded_file

        # Create a real DOCX in memory
        doc = Document()
        doc.add_paragraph("第一段内容")
        doc.add_paragraph("第二段内容")

        docx_buffer = io.BytesIO()
        doc.save(docx_buffer)
        docx_bytes = docx_buffer.getvalue()

        mock_file = MagicMock()
        mock_file.name = "test.docx"
        mock_file.read.return_value = docx_bytes

        result = read_uploaded_file(mock_file)
        assert "第一段内容" in result
        assert "第二段内容" in result

    def test_read_pdf_file(self):
        """Test reading a .pdf file."""
        from app.web_ui import read_uploaded_file

        if pymupdf is None:
            pytest.skip("PyMuPDF not available in this environment")

        # Create a real PDF in memory
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "PDF Test Content", fontname="helv")
        pdf_buffer = io.BytesIO()
        doc.save(pdf_buffer)
        doc.close()
        pdf_bytes = pdf_buffer.getvalue()

        mock_file = MagicMock()
        mock_file.name = "test.pdf"
        mock_file.read.return_value = pdf_bytes

        result = read_uploaded_file(mock_file)
        assert "PDF Test Content" in result

    def test_read_unsupported_format(self):
        """Test reading an unsupported file format raises error."""
        from app.web_ui import read_uploaded_file

        mock_file = MagicMock()
        mock_file.name = "test.xyz"

        with pytest.raises(ValueError, match="不支持的文件格式"):
            read_uploaded_file(mock_file)

    def test_read_uppercase_extension(self):
        """Test reading files with uppercase extensions."""
        from app.web_ui import read_uploaded_file

        mock_file = MagicMock()
        mock_file.name = "TEST.TXT"
        mock_file.read.return_value = "Upper case test".encode("utf-8")

        result = read_uploaded_file(mock_file)
        assert result == "Upper case test"


class TestWebUIModuleImport:
    """Tests for web_ui module structure."""

    def test_main_function_exists(self):
        """Test that main function exists."""
        from app.web_ui import main

        assert callable(main)

    def test_read_uploaded_file_exists(self):
        """Test that read_uploaded_file function exists."""
        from app.web_ui import read_uploaded_file

        assert callable(read_uploaded_file)


class TestWebUIIntegration:
    """Integration tests for web UI scoring flow."""

    def test_score_flow_with_txt_content(self):
        """Test the scoring flow with text content."""
        from app.config import load_config
        from app.engine.scorer import score_text

        config = load_config()
        text = "施工组织设计：本工程采用安全文明施工措施。"
        report = score_text(text, config.rubric, config.lexicon)

        assert hasattr(report, "total_score")
        assert hasattr(report, "dimension_scores")

    def test_export_report_integration(self):
        """Test report export to DOCX."""
        from app.config import load_config
        from app.engine.docx_exporter import export_report_to_docx
        from app.engine.scorer import score_text

        config = load_config()
        text = "施工组织设计测试文档"
        report = score_text(text, config.rubric, config.lexicon)
        report_dict = report.model_dump()

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            output_path = export_report_to_docx(report_dict, tmp.name)
            assert Path(output_path).exists()
            assert Path(output_path).stat().st_size > 0
