"""
main.py — Bot entry point.

Loads environment variables, initialises the database, registers all
Telegram handlers, and starts polling for updates.

Run with:
    python main.py
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Load .env before importing anything that reads env vars
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# Configure logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "")  # Set LOG_FILE=bot.log in .env to log to file

handlers_list = [logging.StreamHandler(sys.stdout)]
if LOG_FILE:
    handlers_list.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=handlers_list,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import project modules (after env is loaded)
# ---------------------------------------------------------------------------
from bot.conversation import WAITING_FOR_JD, WAITING_FOR_RESUMES, WAITING_FOR_FOLLOWUP
from bot.handlers import (
    start_handler,
    help_handler,
    check_handler,
    jd_text_handler,
    jd_file_handler,
    resume_text_handler,
    resume_file_handler,
    done_handler,
    cancel_handler,
    history_handler,
    followup_answer_handler,
    followup_skip_handler,
    unexpected_message_handler,
)
from db.models import init_db


def _validate_config() -> str:
    """
    Validate required environment variables before starting the bot.

    Returns:
        The Telegram bot token.

    Raises:
        SystemExit: If critical config is missing.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.critical(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Please add it to your .env file and try again."
        )
        sys.exit(1)

    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider not in ("gemini", "anthropic", "openai"):
        logger.critical(
            "LLM_PROVIDER must be 'gemini', 'anthropic', or 'openai'. Got: '%s'", provider
        )
        sys.exit(1)

    if provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
        logger.critical(
            "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set.\n"
            "Get a free key at: https://aistudio.google.com/apikey"
        )
        sys.exit(1)

    if provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        logger.critical(
            "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set."
        )
        sys.exit(1)

    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        logger.critical(
            "LLM_PROVIDER=openai but OPENAI_API_KEY is not set."
        )
        sys.exit(1)

    logger.info("Configuration validated. LLM provider: %s", provider)
    return token


def build_application(token: str) -> Application:
    """
    Build and configure the Telegram Application with all handlers registered.

    Handler registration order matters — ConversationHandler must be added
    before fallback handlers.

    Args:
        token: Telegram bot token.

    Returns:
        Configured Application instance.
    """
    # Use longer timeouts so large PDF uploads don't time out during download.
    # read_timeout=60 covers slow file downloads; write_timeout=60 covers uploads.
    request = HTTPXRequest(
        connect_timeout=15,
        read_timeout=60,
        write_timeout=60,
        pool_timeout=30,
    )
    app = Application.builder().token(token).request(request).build()

    # ------------------------------------------------------------------
    # Conversation handler — multi-step /check flow
    # ------------------------------------------------------------------
    # Accept ALL document uploads — format validation happens inside the handler
    # so we can send a friendly error message instead of silently ignoring the file.
    document_filter = filters.Document.ALL

    conversation = ConversationHandler(
        entry_points=[CommandHandler("check", check_handler)],
        states={
            # State: waiting for the Job Description
            WAITING_FOR_JD: [
                MessageHandler(document_filter, jd_file_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, jd_text_handler),
            ],
            # State: waiting for resume(s) — or /done
            WAITING_FOR_RESUMES: [
                CommandHandler("done", done_handler),
                CommandHandler("skip", followup_skip_handler),
                MessageHandler(document_filter, resume_file_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, resume_text_handler),
            ],
            # State: waiting for answer to a follow-up question
            WAITING_FOR_FOLLOWUP: [
                CommandHandler("skip", followup_skip_handler),
                CommandHandler("done", done_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, followup_answer_handler),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        allow_reentry=True,  # Lets users restart /check even mid-session
    )

    app.add_handler(conversation)

    # ------------------------------------------------------------------
    # Standalone command handlers (outside conversation)
    # ------------------------------------------------------------------
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("history", history_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))

    # Catch-all for messages outside any active conversation
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND, unexpected_message_handler
        )
    )

    app.add_error_handler(error_handler)

    logger.info("All handlers registered.")
    return app


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler — logs every unhandled exception and notifies the user."""
    logger.error("Unhandled exception:", exc_info=context.error)

    # Try to send a friendly message to the user
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "\u274c An unexpected error occurred. Please try again.\n"
                "If the problem persists, use /cancel to reset and start fresh."
            )
        except Exception:
            pass  # If we can't even send the error message, just log and move on


async def post_init(application: Application) -> None:
    """Set bot commands in the Telegram menu after the app initialises."""
    commands = [
        BotCommand("start", "Welcome message and overview"),
        BotCommand("check", "Start a new resume evaluation"),
        BotCommand("done", "Finish uploading resumes and see results"),
        BotCommand("history", "View your past scored resumes"),
        BotCommand("skip", "Skip the current follow-up question"),
        BotCommand("cancel", "Cancel the current session"),
        BotCommand("help", "Show usage instructions"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands menu set.")


def main() -> None:
    """Application entry point."""
    logger.info("Starting ATS Resume Checker Bot...")

    # Validate config
    token = _validate_config()

    # Initialise SQLite database
    init_db()

    # Build application
    app = build_application(token)
    app.post_init = post_init

    logger.info("Bot is running. Press Ctrl+C to stop.")

    # Start polling (blocking)
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,  # Ignore updates that arrived while bot was offline
    )


if __name__ == "__main__":
    main()
