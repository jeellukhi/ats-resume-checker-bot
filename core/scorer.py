"""
scorer.py — Orchestrates the full resume analysis pipeline.

Ties together resume parsing, LLM analysis, and Telegram-ready message
formatting. This is the layer the bot handlers call.
"""

import logging
from typing import Any

from core.llm_client import ATSResult, analyze_resume
from core.resume_parser import extract_text_from_file, validate_text_length

logger = logging.getLogger(__name__)

# Maximum file size accepted (10 MB in bytes)
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


def _score_bar(score: int) -> str:
    """Build a simple visual progress bar for the ATS score."""
    filled = round(score / 5)  # 20 blocks total → each block = 5 points
    empty = 20 - filled
    bar = "█" * filled + "░" * empty
    return bar


def _score_emoji(score: int) -> str:
    """Return an emoji representing the score range."""
    if score >= 80:
        return "🟢"
    elif score >= 60:
        return "🟡"
    elif score >= 40:
        return "🟠"
    else:
        return "🔴"


def format_ats_result(result: ATSResult, resume_label: str = "Resume") -> str:
    """
    Format an ATSResult dict into a human-readable Telegram message.

    Uses Telegram MarkdownV2-safe characters (plain Markdown is easier for
    multi-line messages; we use the 'Markdown' parse mode).

    Args:
        result: Parsed ATSResult dict from llm_client.analyze_resume().
        resume_label: Display name for this resume (e.g. "Resume 1", filename).

    Returns:
        Formatted string ready to send via Telegram.
    """
    score = result.get("score", 0)
    emoji = _score_emoji(score)
    bar = _score_bar(score)

    lines = [
        f"📄 *{resume_label}*",
        "",
        f"{emoji} *ATS Score: {score}/100*",
        f"`{bar}`",
        "",
    ]

    # Strengths
    strengths = result.get("strengths", [])
    if strengths:
        lines.append("✅ *Strengths*")
        for s in strengths:
            lines.append(f"  • {s}")
        lines.append("")

    # Missing Keywords / Gaps
    missing = result.get("missing_keywords", [])
    if missing:
        lines.append("⚠️ *Gaps / Missing Keywords*")
        for kw in missing:
            lines.append(f"  • {kw}")
        lines.append("")

    # Suggestions
    suggestions = result.get("suggestions", [])
    if suggestions:
        lines.append("💡 *Suggestions to Improve*")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"  {i}. {s}")
        lines.append("")

    # Course Suggestions
    courses = result.get("course_suggestions", [])
    if courses:
        lines.append("🎓 *Recommended Courses / Certifications*")
        for c in courses:
            lines.append(f"  • {c}")
        lines.append("")

    # Follow-up Questions
    questions = result.get("follow_up_questions", [])
    if questions:
        lines.append("❓ *Follow-up Questions*")
        lines.append(
            "_Answer these to help me give you even better advice:_"
        )
        for i, q in enumerate(questions, 1):
            lines.append(f"  *Q{i}:* {q}")

    return "\n".join(lines)


def format_comparison_summary(results: list[dict[str, Any]]) -> str:
    """
    Build a ranked comparison summary when multiple resumes were submitted.

    Args:
        results: List of dicts, each with keys 'label' (str) and 'result' (ATSResult).

    Returns:
        Formatted comparison summary string.
    """
    if not results:
        return "No results to compare."

    # Sort by score descending
    ranked = sorted(results, key=lambda r: r["result"].get("score", 0), reverse=True)

    lines = [
        "📊 *Comparison Summary*",
        "",
        "Resumes ranked by ATS match score:",
        "",
    ]

    medals = ["🥇", "🥈", "🥉"]
    for i, entry in enumerate(ranked):
        medal = medals[i] if i < len(medals) else f"#{i+1}"
        score = entry["result"].get("score", 0)
        label = entry["label"]
        emoji = _score_emoji(score)
        bar = _score_bar(score)
        lines.append(f"{medal} *{label}* — {emoji} *{score}/100*")
        lines.append(f"  `{bar}`")
        lines.append("")

    if len(ranked) > 1:
        top = ranked[0]
        lines.append(
            f"🏆 *Best match:* {top['label']} with a score of "
            f"{top['result'].get('score', 0)}/100"
        )

    return "\n".join(lines)


async def process_resume_file(
    file_bytes: bytes,
    filename: str,
    file_size: int,
    job_description: str,
    resume_label: str = "Resume",
) -> tuple[str, ATSResult, str]:
    """
    Full pipeline: validate file -> parse text -> call LLM -> return formatted message.

    This is the main function the bot calls for uploaded file resumes.

    Args:
        file_bytes: Raw bytes of the uploaded file.
        filename: Original filename (for extension detection and display).
        file_size: File size in bytes (for size validation).
        job_description: The job description text to compare against.
        resume_label: Display label for this resume.

    Returns:
        Tuple of (formatted_message: str, raw_result: ATSResult, resume_text: str).
        The resume_text is the extracted plain text, retained for chat context.

    Raises:
        ValueError: On file too large, unsupported format, parse failure, or
                    text too short.
        RuntimeError: On LLM API errors.
    """
    # File size check
    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File '{filename}' is too large ({file_size / (1024*1024):.1f} MB). "
            f"Maximum allowed size is 10 MB."
        )

    logger.info(
        "Processing resume file: %s (%d bytes)", filename, file_size
    )

    # Extract text
    resume_text = extract_text_from_file(file_bytes, filename)
    resume_text = validate_text_length(resume_text, label="Resume", min_chars=100)

    # Analyze with LLM
    result = analyze_resume(job_description, resume_text)

    # Format for Telegram
    message = format_ats_result(result, resume_label=resume_label)
    return message, result, resume_text


async def process_resume_text(
    resume_text: str,
    job_description: str,
    resume_label: str = "Resume",
) -> tuple[str, ATSResult, str]:
    """
    Full pipeline for pasted plain-text resumes.

    Args:
        resume_text: Raw resume text pasted by the user.
        job_description: The job description text.
        resume_label: Display label for this resume.

    Returns:
        Tuple of (formatted_message: str, raw_result: ATSResult, resume_text: str).
        The resume_text is returned (after length validation) for chat context.
    """
    resume_text = validate_text_length(resume_text, label="Resume", min_chars=100)
    logger.info("Processing pasted resume text (%d chars).", len(resume_text))
    result = analyze_resume(job_description, resume_text)
    message = format_ats_result(result, resume_label=resume_label)
    return message, result, resume_text
