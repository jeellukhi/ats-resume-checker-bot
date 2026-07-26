"""
models.py — SQLite database schema and table creation.

Uses Python's built-in sqlite3 module (no external ORM needed).
Tables created here:
  - sessions: Tracks each /check session (user + JD + timestamp)
  - resume_results: One row per resume analyzed in a session
"""

import sqlite3
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Default database path — can be overridden via DB_PATH env var
DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "ats_bot.db"


def get_db_path() -> Path:
    """Return the database file path from env or the default."""
    db_path_str = os.getenv("DB_PATH", str(DEFAULT_DB_PATH))
    return Path(db_path_str)


def get_connection() -> sqlite3.Connection:
    """
    Create and return a SQLite connection with row_factory set so rows can
    be accessed like dicts.

    Returns:
        A configured sqlite3.Connection instance.
    """
    db_path = get_db_path()
    # Ensure the directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for better concurrent read performance
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    """
    Create all tables if they do not already exist.

    This is called once on bot startup. Safe to call multiple times
    (uses CREATE TABLE IF NOT EXISTS).
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Sessions table: one row per /check invocation
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                username    TEXT,
                job_description TEXT NOT NULL,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Resume results table: one row per resume analyzed
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS resume_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER NOT NULL REFERENCES sessions(id),
                user_id         INTEGER NOT NULL,
                resume_label    TEXT NOT NULL,
                ats_score       INTEGER NOT NULL,
                strengths       TEXT,       -- JSON array stored as text
                missing_keywords TEXT,      -- JSON array stored as text
                suggestions     TEXT,       -- JSON array stored as text
                course_suggestions TEXT,    -- JSON array stored as text
                follow_up_questions TEXT,   -- JSON array stored as text
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()
        logger.info("Database initialized at: %s", get_db_path())
    finally:
        conn.close()
