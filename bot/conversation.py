"""
conversation.py — Conversation state constants and in-memory session store.

The bot uses python-telegram-bot's ConversationHandler to manage multi-step
interactions. States are defined here and imported wherever needed.

In-memory store (UserSession) holds per-user runtime data between handler
calls (e.g. the current JD, collected resumes, active follow-up questions).
This is intentionally kept separate from the SQLite storage which handles
persistence.
"""

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Conversation state constants — used by ConversationHandler
# ---------------------------------------------------------------------------

# Waiting for the user to provide the job description
WAITING_FOR_JD = 1

# Waiting for the user to send resumes (or /done)
WAITING_FOR_RESUMES = 2

# Waiting for the user to answer a follow-up question
WAITING_FOR_FOLLOWUP = 3


# ---------------------------------------------------------------------------
# In-memory per-user session data
# ---------------------------------------------------------------------------

@dataclass
class UserSession:
    """
    Holds the runtime state for one active /check session.

    Attributes:
        job_description: The JD text provided by the user.
        resumes: List of dicts, each with 'label' and 'result' (ATSResult).
        db_session_id: The SQLite session row ID (set after JD is saved).
        current_questions: The follow-up questions from the last scored resume.
        current_question_index: Which follow-up question is currently active.
    """

    job_description: str = ""
    resumes: list[dict[str, Any]] = field(default_factory=list)
    db_session_id: int | None = None
    current_questions: list[str] = field(default_factory=list)
    current_question_index: int = 0
    resume_counter: int = 0  # increments for auto-labelling resumes


# Global dict mapping user_id → UserSession
# (Lives in memory; cleared when the bot restarts)
_sessions: dict[int, UserSession] = {}


def get_session(user_id: int) -> UserSession:
    """Return the existing session for user_id, or create a fresh one."""
    if user_id not in _sessions:
        _sessions[user_id] = UserSession()
    return _sessions[user_id]


def clear_session(user_id: int) -> None:
    """Remove the in-memory session for user_id (called after /check completes)."""
    _sessions.pop(user_id, None)


def reset_session(user_id: int) -> UserSession:
    """Clear any existing session and return a fresh one."""
    clear_session(user_id)
    return get_session(user_id)
