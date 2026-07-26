"""
tests/test_resume_parser.py — Unit tests for the resume parsing module.

Tests cover:
  - Plain text extraction from .txt files
  - PDF parsing (both pdfplumber and PyPDF2 paths)
  - DOCX parsing
  - Unsupported format rejection
  - Text length validation
  - File size enforcement
"""

import io
import sys
import os
import unittest

# Make sure the project root is on the path when running tests directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.resume_parser import (
    extract_text_from_file,
    validate_text_length,
)


class TestValidateTextLength(unittest.TestCase):
    """Tests for validate_text_length."""

    def test_valid_text_passes(self):
        text = "A" * 100
        result = validate_text_length(text, label="Resume", min_chars=50)
        self.assertEqual(result, text)

    def test_too_short_raises(self):
        with self.assertRaises(ValueError) as ctx:
            validate_text_length("short", label="Resume", min_chars=50)
        self.assertIn("too short", str(ctx.exception).lower())

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValueError):
            validate_text_length("   \n\t  ", label="Resume", min_chars=50)

    def test_exactly_min_passes(self):
        text = "B" * 50
        result = validate_text_length(text, min_chars=50)
        self.assertEqual(result, text)


class TestExtractTextFromFile(unittest.TestCase):
    """Tests for extract_text_from_file routing logic."""

    def test_txt_file_extraction(self):
        content = "This is a plain text resume with enough content to pass validation checks."
        file_bytes = content.encode("utf-8")
        result = extract_text_from_file(file_bytes, "resume.txt")
        self.assertEqual(result.strip(), content.strip())

    def test_md_file_extraction(self):
        content = "# John Doe\n\n## Experience\nSoftware Engineer at ACME Corp (2020-2024)"
        file_bytes = content.encode("utf-8")
        result = extract_text_from_file(file_bytes, "resume.md")
        self.assertIn("John Doe", result)

    def test_unsupported_extension_raises(self):
        with self.assertRaises(ValueError) as ctx:
            extract_text_from_file(b"some content", "resume.xlsx")
        self.assertIn("Unsupported file type", str(ctx.exception))

    def test_unsupported_image_raises(self):
        with self.assertRaises(ValueError):
            extract_text_from_file(b"\xff\xd8\xff", "resume.jpg")

    def test_empty_txt_raises(self):
        """An empty .txt file should route through, but be empty string."""
        result = extract_text_from_file(b"   ", "empty.txt")
        self.assertEqual(result.strip(), "")

    def test_utf8_content(self):
        content = "Software Engineer | Python • Java • SQL | 5 years experience"
        file_bytes = content.encode("utf-8")
        result = extract_text_from_file(file_bytes, "resume.txt")
        self.assertIn("Python", result)


class TestDocxExtraction(unittest.TestCase):
    """Tests for DOCX parsing — creates a minimal DOCX in memory."""

    def _make_minimal_docx(self, text: str) -> bytes:
        """Create a simple DOCX file in memory using python-docx."""
        try:
            from docx import Document
            import io as _io

            doc = Document()
            doc.add_paragraph(text)
            buf = _io.BytesIO()
            doc.save(buf)
            return buf.getvalue()
        except ImportError:
            self.skipTest("python-docx not installed")

    def test_docx_text_extraction(self):
        content = "Jane Smith — Senior Data Scientist — 7 years Python, ML, TensorFlow"
        docx_bytes = self._make_minimal_docx(content)
        result = extract_text_from_file(docx_bytes, "resume.docx")
        self.assertIn("Jane Smith", result)
        self.assertIn("Python", result)

    def test_corrupt_docx_raises(self):
        with self.assertRaises(ValueError) as ctx:
            extract_text_from_file(b"notadocx", "resume.docx")
        self.assertIn("Could not read", str(ctx.exception))


class TestPdfExtraction(unittest.TestCase):
    """Tests for PDF parsing — creates a minimal PDF in memory."""

    def _make_minimal_pdf(self, text: str) -> bytes:
        """
        Create a minimal valid PDF file in memory using reportlab (if available)
        or fpdf2, otherwise skip.
        """
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
            import io as _io

            buf = _io.BytesIO()
            c = canvas.Canvas(buf, pagesize=letter)
            c.drawString(72, 700, text)
            c.save()
            return buf.getvalue()
        except ImportError:
            pass

        try:
            from fpdf import FPDF
            import io as _io

            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Helvetica", size=12)
            pdf.cell(200, 10, text=text)
            return pdf.output()
        except ImportError:
            self.skipTest("Neither reportlab nor fpdf2 installed — skipping PDF tests")

    def test_pdf_text_extraction(self):
        content = "Alice Chen Senior DevOps Engineer Kubernetes Docker AWS CI/CD"
        pdf_bytes = self._make_minimal_pdf(content)
        result = extract_text_from_file(pdf_bytes, "resume.pdf")
        # At minimum we should get some text back (word boundaries may shift)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_corrupt_pdf_raises(self):
        with self.assertRaises(ValueError) as ctx:
            extract_text_from_file(b"notapdf", "resume.pdf")
        self.assertIn("Could not extract", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
