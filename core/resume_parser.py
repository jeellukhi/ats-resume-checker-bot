"""
resume_parser.py — Extract plain text from PDF, DOCX, or raw text input.

Supported formats:
  - PDF: via pdfplumber (primary) with PyPDF2 fallback
  - DOCX: via python-docx
  - Plain text: passed through as-is

All functions return a plain string. They raise ValueError with a
human-readable message on failure so callers can forward it to the user.
"""

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract text from a PDF file given its raw bytes.

    Tries pdfplumber first (better layout handling), then falls back to
    PyPDF2 if pdfplumber fails or returns empty text.

    Args:
        file_bytes: Raw bytes of the PDF file.

    Returns:
        Extracted plain text string.

    Raises:
        ValueError: If the PDF cannot be parsed or yields no extractable text.
    """
    text = ""

    # --- Primary: pdfplumber ---
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages_text = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    pages_text.append(page_text)
            text = "\n".join(pages_text).strip()
            logger.debug("pdfplumber extracted %d characters from PDF.", len(text))
    except Exception as exc:
        logger.warning("pdfplumber failed: %s — trying PyPDF2 fallback.", exc)

    # --- Fallback: PyPDF2 ---
    if not text:
        try:
            import PyPDF2  # type: ignore

            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    pages_text.append(page_text)
            text = "\n".join(pages_text).strip()
            logger.debug("PyPDF2 extracted %d characters from PDF.", len(text))
        except Exception as exc:
            logger.error("PyPDF2 also failed: %s", exc)

    if not text:
        raise ValueError(
            "Could not extract any text from the PDF. The file may be scanned "
            "(image-based) or corrupted. Please try a text-selectable PDF or "
            "paste the resume text directly in chat."
        )

    return text


def extract_text_from_docx(file_bytes: bytes) -> str:
    """
    Extract text from a DOCX file given its raw bytes.

    Extracts text from all paragraphs in the document body.

    Args:
        file_bytes: Raw bytes of the DOCX file.

    Returns:
        Extracted plain text string.

    Raises:
        ValueError: If the DOCX cannot be parsed.
    """
    try:
        from docx import Document  # type: ignore

        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        text = "\n".join(paragraphs).strip()
        logger.debug("python-docx extracted %d characters from DOCX.", len(text))
    except Exception as exc:
        logger.error("python-docx failed: %s", exc)
        raise ValueError(
            f"Could not read the DOCX file (it may be corrupted or use an "
            f"unsupported format). Error: {exc}"
        ) from exc

    if not text:
        raise ValueError(
            "The DOCX file appears to be empty or contains only non-text "
            "elements (images, tables without text, etc.). Please paste the "
            "resume content as plain text."
        )

    return text


def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    """
    Route file bytes to the correct parser based on file extension.

    Args:
        file_bytes: Raw bytes of the uploaded file.
        filename: Original filename (used to determine file type).

    Returns:
        Extracted plain text string.

    Raises:
        ValueError: If the file type is unsupported or parsing fails.
    """
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        return extract_text_from_pdf(file_bytes)
    elif suffix in (".docx", ".doc"):
        return extract_text_from_docx(file_bytes)
    elif suffix in (".txt", ".md"):
        # Plain text files — decode and return
        try:
            return file_bytes.decode("utf-8", errors="replace").strip()
        except Exception as exc:
            raise ValueError(f"Could not decode text file: {exc}") from exc
    else:
        raise ValueError(
            f"Unsupported file type: '{suffix}'. Please upload a PDF, DOCX, "
            "or TXT file, or paste the text directly in chat."
        )


def validate_text_length(text: str, label: str = "Text", min_chars: int = 50) -> str:
    """
    Validate that extracted or pasted text is long enough to be useful.

    Args:
        text: The text to validate.
        label: Human-readable label used in error messages (e.g. "Resume").
        min_chars: Minimum character count required.

    Returns:
        The original text if valid.

    Raises:
        ValueError: If text is too short.
    """
    cleaned = text.strip()
    if len(cleaned) < min_chars:
        raise ValueError(
            f"{label} is too short ({len(cleaned)} characters). "
            f"Please provide a complete {label.lower()} with at least "
            f"{min_chars} characters."
        )
    return cleaned
