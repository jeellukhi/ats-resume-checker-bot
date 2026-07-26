"""
storage.py — Save and retrieve session and resume result data from SQLite.

All database I/O lives here so the rest of the bot never writes SQL directly.
"""

import json
import logging
from typing import Any

from db.models import get_connection

logger = logging.getLogger(__name__)


def create_session(user_id: int, username: str | None, job_description: str) -> int:
    """
    Insert a new session row and return its auto-incremented ID.

    Args:
        user_id: Telegram user ID.
        username: Telegram username (may be None).
        job_description: Full JD text for this session.

    Returns:
        The new session's integer ID.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO sessions (user_id, username, job_description) VALUES (?, ?, ?)",
            (user_id, username, job_description),
        )
        conn.commit()
        session_id = cursor.lastrowid
        logger.debug("Created session %d for user %d.", session_id, user_id)
        return session_id
    finally:
        conn.close()


def save_resume_result(
    session_id: int,
    user_id: int,
    resume_label: str,
    result: dict[str, Any],
) -> int:
    """
    Save an individual resume analysis result linked to a session.

    Args:
        session_id: The parent session ID.
        user_id: Telegram user ID.
        resume_label: Display label for this resume.
        result: ATSResult dict from llm_client.analyze_resume().

    Returns:
        The new result row's integer ID.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO resume_results
                (session_id, user_id, resume_label, ats_score, strengths,
                 missing_keywords, suggestions, course_suggestions, follow_up_questions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                user_id,
                resume_label,
                result.get("score", 0),
                json.dumps(result.get("strengths", [])),
                json.dumps(result.get("missing_keywords", [])),
                json.dumps(result.get("suggestions", [])),
                json.dumps(result.get("course_suggestions", [])),
                json.dumps(result.get("follow_up_questions", [])),
            ),
        )
        conn.commit()
        result_id = cursor.lastrowid
        logger.debug(
            "Saved resume result %d (score=%d) for session %d.",
            result_id,
            result.get("score", 0),
            session_id,
        )
        return result_id
    finally:
        conn.close()


def get_user_history(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """
    Retrieve the most recent resume results for a user.

    Args:
        user_id: Telegram user ID.
        limit: Maximum number of results to return (default 10).

    Returns:
        List of dicts with keys: resume_label, ats_score, created_at,
        session_id, strengths, missing_keywords, suggestions.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                rr.resume_label,
                rr.ats_score,
                rr.created_at,
                rr.session_id,
                rr.strengths,
                rr.missing_keywords,
                rr.suggestions
            FROM resume_results rr
            WHERE rr.user_id = ?
            ORDER BY rr.created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

        history = []
        for row in rows:
            entry = dict(row)
            # Deserialize JSON fields
            for field in ("strengths", "missing_keywords", "suggestions"):
                try:
                    entry[field] = json.loads(entry[field] or "[]")
                except (json.JSONDecodeError, TypeError):
                    entry[field] = []
            history.append(entry)

        return history
    finally:
        conn.close()


def format_history_message(history: list[dict[str, Any]]) -> str:
    """
    Format a history list into a Telegram-ready message string.

    Args:
        history: List returned by get_user_history().

    Returns:
        Formatted string for display in Telegram.
    """
    if not history:
        return (
            "📭 You have no scored resumes yet.\n"
            "Use /check to analyze your first resume!"
        )

    lines = ["📋 *Your Recent Resume History*", ""]
    for i, entry in enumerate(history, 1):
        score = entry["ats_score"]
        label = entry["resume_label"]
        created = entry["created_at"]
        # Emoji based on score
        if score >= 80:
            emoji = "🟢"
        elif score >= 60:
            emoji = "🟡"
        elif score >= 40:
            emoji = "🟠"
        else:
            emoji = "🔴"

        lines.append(f"{i}. {emoji} *{label}* — *{score}/100*")
        lines.append(f"   📅 {created}")

        top_missing = entry.get("missing_keywords", [])[:3]
        if top_missing:
            lines.append(f"   ⚠️ Missing: {', '.join(top_missing)}")
        lines.append("")

    return "\n".join(lines).rstrip()
