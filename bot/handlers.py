"""
handlers.py — All Telegram command and message handlers.

Each handler corresponds to one step in the conversation flow:
  /start      -> start_handler        (explains the bot)
  /check      -> check_handler        (begins a new evaluation session)
  /done       -> done_handler         (signals end of resume uploads, enters chat mode)
  /ask        -> ask_handler          (ask a question during or after analysis)
  /exit       -> exit_handler         (exit chat mode and clear session)
  /cancel     -> cancel_handler       (aborts the current session)
  /history    -> history_handler      (shows past scored resumes)
  /help       -> help_handler         (usage help)

  Message handlers (within the conversation):
  - JD input   (text or file)
  - Resume input (text or file, multiple allowed)
  - Follow-up question answers
  - Free-form chat in WAITING_FOR_CHAT state
"""

import logging
import os

from telegram import Update, Message
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode, ChatAction

from bot.conversation import (
    WAITING_FOR_JD,
    WAITING_FOR_RESUMES,
    WAITING_FOR_FOLLOWUP,
    WAITING_FOR_CHAT,
    get_session,
    reset_session,
    clear_session,
)
from core.scorer import (
    process_resume_file,
    process_resume_text,
    format_comparison_summary,
)
from core.llm_client import (
    get_followup_acknowledgement,
    answer_resume_question,
)
from db.storage import (
    create_session as db_create_session,
    save_resume_result,
    get_user_history,
    format_history_message,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

async def _send_typing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a 'typing...' chat action to show the bot is working."""
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )


async def _safe_reply(
    update: Update,
    text: str,
    parse_mode: str = ParseMode.MARKDOWN,
) -> None:
    """
    Send a reply, falling back to plain text if Markdown parsing fails.

    Args:
        update: The current Telegram Update.
        text: Message text to send.
        parse_mode: Telegram parse mode (default Markdown).
    """
    try:
        await update.message.reply_text(text, parse_mode=parse_mode)
    except Exception as exc:
        logger.warning("Markdown reply failed (%s), retrying as plain text.", exc)
        # Strip basic markdown and retry
        plain = text.replace("*", "").replace("_", "").replace("`", "")
        await update.message.reply_text(plain)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greet the user and explain what the bot does."""
    user = update.effective_user
    name = user.first_name if user else "there"

    welcome = (
        f"👋 Hello, *{name}!* Welcome to the *ATS Resume Checker Bot*!\n\n"
        "I help you evaluate resumes against job descriptions using AI — "
        "the same way Applicant Tracking Systems (ATS) do.\n\n"
        "📌 *What I can do:*\n"
        "  • Score your resume against a Job Description (0–100)\n"
        "  • Identify missing keywords and skill gaps\n"
        "  • Give concrete, actionable improvement suggestions\n"
        "  • Recommend courses to fill skill gaps\n"
        "  • Ask follow-up questions to strengthen your profile\n"
        "  • Compare multiple resume versions side by side\n"
        "  • 💬 *Answer your resume & career questions after analysis*\n\n"
        "🚀 *Commands:*\n"
        "  /check — Start a new resume evaluation\n"
        "  /ask — Ask a question about your resume (after analysis)\n"
        "  /history — View your past scored resumes\n"
        "  /cancel — Cancel the current session\n"
        "  /exit — Exit chat mode\n"
        "  /help — Show usage guide\n\n"
        "Type /check to get started! 🎯"
    )
    await _safe_reply(update, welcome)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the usage guide."""
    help_text = (
        "📖 *How to use the ATS Resume Checker Bot*\n\n"
        "*Step 1 — /check*\n"
        "Start a new evaluation session.\n\n"
        "*Step 2 — Send Job Description*\n"
        "Paste the JD as text, or upload a PDF/DOCX/TXT file.\n\n"
        "*Step 3 — Send Resume(s)*\n"
        "Upload one or more resumes (PDF/DOCX) or paste resume text.\n"
        "You can send multiple resumes to compare them!\n"
        "Type /done when you're finished uploading.\n\n"
        "*Step 4 — Review Results*\n"
        "I'll return an ATS score, strengths, gaps, suggestions, "
        "course recommendations, and follow-up questions for each resume.\n\n"
        "*Step 5 — Answer Follow-ups (optional)*\n"
        "Reply to my follow-up questions to get personalized advice.\n\n"
        "*Step 6 — 💬 Ask Me Anything!*\n"
        "After typing /done, ask me any question about your resume:\n"
        "  • \"How can I rewrite my summary for this role?\"\n"
        "  • \"What skills should I add?\"\n"
        "  • \"Can you write a better bullet point for my experience?\"\n"
        "During resume upload, use */ask <your question>*\n\n"
        "📋 */history* — See your last 10 scored resumes.\n"
        "🚫 */cancel* — Abort the current session at any time.\n"
        "🚪 */exit* — Exit chat mode and clear the session.\n\n"
        "💡 *Tips:*\n"
        "  • Use text-selectable PDFs (not scanned images)\n"
        "  • Files must be under 10 MB\n"
        "  • The more complete your resume, the better the score!"
    )
    await _safe_reply(update, help_text)


# ---------------------------------------------------------------------------
# /check — starts the conversation
# ---------------------------------------------------------------------------

async def check_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Begin a new resume evaluation session."""
    user_id = update.effective_user.id
    reset_session(user_id)

    await _safe_reply(
        update,
        "✅ *New evaluation session started!*\n\n"
        "📋 *Step 1 of 2 — Job Description*\n\n"
        "Please send me the *Job Description* you want to match against.\n"
        "You can:\n"
        "  • Paste the text directly in chat, or\n"
        "  • Upload a PDF, DOCX, or TXT file containing the JD\n\n"
        "Send /cancel at any time to abort.",
    )
    return WAITING_FOR_JD


# ---------------------------------------------------------------------------
# Receive JD (text or file)
# ---------------------------------------------------------------------------

async def jd_text_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle pasted Job Description text."""
    user_id = update.effective_user.id
    session = get_session(user_id)
    jd_text = (update.message.text or "").strip()

    if len(jd_text) < 50:
        await _safe_reply(
            update,
            "⚠️ The Job Description seems too short (under 50 characters).\n"
            "Please paste the full JD text so I can do a proper analysis.",
        )
        return WAITING_FOR_JD

    session.job_description = jd_text
    return await _jd_received(update, context, session, user_id)


async def jd_file_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle an uploaded file as the Job Description."""
    user_id = update.effective_user.id
    session = get_session(user_id)

    await _send_typing(update, context)

    document = update.message.document
    if not document:
        await update.message.reply_text("⚠️ Could not read the file. Please try again.")
        return WAITING_FOR_JD

    filename = document.file_name or "upload"
    logger.info("JD file upload: %s (mime=%s, size=%s)", filename, document.mime_type, document.file_size)

    try:
        jd_text = await _download_and_parse_file(document, context)
    except ValueError as exc:
        logger.warning("JD file parse error: %s", exc)
        await update.message.reply_text(f"⚠️ {exc}")
        return WAITING_FOR_JD
    except Exception as exc:
        logger.error("Unexpected error downloading/parsing JD file: %s", exc, exc_info=True)
        await update.message.reply_text(
            f"❌ Could not process the file '{filename}'.\n"
            f"Error: {exc}\n\n"
            "Please try again or paste the Job Description as text directly in chat."
        )
        return WAITING_FOR_JD

    if len(jd_text.strip()) < 50:
        await update.message.reply_text(
            "⚠️ The file appears to be empty or the text is too short. "
            "Please upload a file with the full Job Description.",
        )
        return WAITING_FOR_JD

    session.job_description = jd_text.strip()
    return await _jd_received(update, context, session, user_id)


async def _jd_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session,
    user_id: int,
) -> int:
    """Common logic after JD is accepted — save to DB and ask for resumes."""
    username = update.effective_user.username
    db_session_id = db_create_session(user_id, username, session.job_description)
    session.db_session_id = db_session_id

    jd_preview = session.job_description[:150].replace("\n", " ")
    if len(session.job_description) > 150:
        jd_preview += "..."

    await _safe_reply(
        update,
        f"✅ *Job Description received!*\n"
        f"_{jd_preview}_\n\n"
        "📄 *Step 2 of 2 — Send Resume(s)*\n\n"
        "Now send me one or more resumes to evaluate:\n"
        "  • Upload PDF or DOCX files, or\n"
        "  • Paste resume text directly in chat\n\n"
        "You can send *multiple resumes* one by one to compare them.\n"
        "When you're done, type */done* to see the results.\n\n"
        "💬 You can also use */ask <your question>* at any time to ask about your resume.\n\n"
        "Or type /cancel to abort.",
    )
    return WAITING_FOR_RESUMES


# ---------------------------------------------------------------------------
# Receive resume(s) — text or file
# ---------------------------------------------------------------------------

async def resume_text_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle pasted resume text during the WAITING_FOR_RESUMES state."""
    user_id = update.effective_user.id
    session = get_session(user_id)

    # If text looks like a command that slipped through, ignore
    text = (update.message.text or "").strip()
    if text.startswith("/"):
        return WAITING_FOR_RESUMES

    await _send_typing(update, context)
    session.resume_counter += 1
    label = f"Resume {session.resume_counter}"

    await _safe_reply(
        update,
        f"⏳ Analyzing *{label}*... This may take 10–30 seconds.",
    )

    try:
        message, result, resume_text = await process_resume_text(
            resume_text=text,
            job_description=session.job_description,
            resume_label=label,
        )
    except ValueError as exc:
        await _safe_reply(update, f"⚠️ {exc}")
        session.resume_counter -= 1
        return WAITING_FOR_RESUMES
    except RuntimeError as exc:
        await _safe_reply(
            update,
            f"❌ LLM analysis failed: {exc}\n\nPlease try again or use /cancel.",
        )
        session.resume_counter -= 1
        return WAITING_FOR_RESUMES

    # Store result and extracted text in memory and DB
    session.resumes.append({"label": label, "result": result})
    session.resume_texts.append(resume_text)
    if session.db_session_id:
        save_resume_result(session.db_session_id, user_id, label, result)

    await _safe_reply(update, message)

    # Ask follow-ups if present
    questions = result.get("follow_up_questions", [])
    if questions:
        session.current_questions = questions
        session.current_question_index = 0
        return await _ask_followup(update, context, session)

    await _safe_reply(
        update,
        "Send another resume, or type */done* to finish.\n"
        "💬 You can also use */ask <question>* to ask anything about your resume.",
    )
    return WAITING_FOR_RESUMES


async def resume_file_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle an uploaded PDF/DOCX resume file."""
    user_id = update.effective_user.id
    session = get_session(user_id)

    document = update.message.document
    if not document:
        await update.message.reply_text("⚠️ Could not read the file. Please try again.")
        return WAITING_FOR_RESUMES

    await _send_typing(update, context)
    filename = document.file_name or "resume"
    file_size = document.file_size or 0

    logger.info("Resume file upload: %s (mime=%s, size=%s)", filename, document.mime_type, file_size)

    session.resume_counter += 1
    label = filename  # Use the actual filename as label for clarity

    await update.message.reply_text(
        f"📎 Received {filename} — analyzing...\n"
        "This may take 10–30 seconds.",
    )

    try:
        file_bytes = await _download_file(document, context)
        message, result, resume_text = await process_resume_file(
            file_bytes=file_bytes,
            filename=filename,
            file_size=file_size,
            job_description=session.job_description,
            resume_label=label,
        )
    except ValueError as exc:
        logger.warning("Resume file parse error: %s", exc)
        await update.message.reply_text(f"⚠️ {exc}")
        session.resume_counter -= 1
        return WAITING_FOR_RESUMES
    except RuntimeError as exc:
        logger.error("LLM error for resume file: %s", exc)
        await update.message.reply_text(
            f"❌ Analysis failed: {exc}\n\nPlease try again or use /cancel."
        )
        session.resume_counter -= 1
        return WAITING_FOR_RESUMES
    except Exception as exc:
        logger.error("Unexpected error processing resume file: %s", exc, exc_info=True)
        await update.message.reply_text(
            f"❌ Unexpected error processing '{filename}': {exc}\n\n"
            "Please try again or paste your resume as text."
        )
        session.resume_counter -= 1
        return WAITING_FOR_RESUMES

    # Store result and extracted text in memory and DB
    session.resumes.append({"label": label, "result": result})
    session.resume_texts.append(resume_text)
    if session.db_session_id:
        save_resume_result(session.db_session_id, user_id, label, result)

    await _safe_reply(update, message)

    # Ask follow-ups if present
    questions = result.get("follow_up_questions", [])
    if questions:
        session.current_questions = questions
        session.current_question_index = 0
        return await _ask_followup(update, context, session)

    await update.message.reply_text(
        "Send another resume, or type /done to finish and see the comparison.\n"
        "💬 You can also use /ask <question> to ask anything about your resume."
    )
    return WAITING_FOR_RESUMES


# ---------------------------------------------------------------------------
# Follow-up questions
# ---------------------------------------------------------------------------

async def _ask_followup(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session
) -> int:
    """Send the next follow-up question to the user."""
    idx = session.current_question_index
    questions = session.current_questions

    if idx >= len(questions):
        # All questions answered — return to resume collection
        await _safe_reply(
            update,
            "Thanks for all your answers! 🙌\n\n"
            "Send another resume, or type */done* to finish.\n"
            "💬 Or use */ask <question>* to ask anything about your resume.",
        )
        return WAITING_FOR_RESUMES

    question = questions[idx]
    total = len(questions)

    await _safe_reply(
        update,
        f"❓ *Follow-up Question {idx + 1}/{total}*\n\n{question}\n\n"
        "_Reply to this question, or type /skip to skip to the next one._",
    )
    return WAITING_FOR_FOLLOWUP


async def followup_answer_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle the user's answer to a follow-up question."""
    user_id = update.effective_user.id
    session = get_session(user_id)

    answer = (update.message.text or "").strip()
    idx = session.current_question_index
    questions = session.current_questions

    if not questions or idx >= len(questions):
        # Shouldn't normally happen — redirect to resume collection
        return WAITING_FOR_RESUMES

    question = questions[idx]

    await _send_typing(update, context)

    # Get a personalised acknowledgement from the LLM
    ack = get_followup_acknowledgement(question=question, answer=answer)
    await _safe_reply(update, ack, parse_mode=None)

    # Advance to next question
    session.current_question_index += 1
    return await _ask_followup(update, context, session)


async def followup_skip_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Skip the current follow-up question."""
    user_id = update.effective_user.id
    session = get_session(user_id)

    session.current_question_index += 1
    return await _ask_followup(update, context, session)


# ---------------------------------------------------------------------------
# /done — finalize resume uploads, enter chat mode
# ---------------------------------------------------------------------------

async def done_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /done — show comparison summary and enter Q&A chat mode."""
    user_id = update.effective_user.id
    session = get_session(user_id)

    if not session.resumes:
        await _safe_reply(
            update,
            "⚠️ You haven't submitted any resumes yet.\n"
            "Send a resume or /cancel to abort.",
        )
        return WAITING_FOR_RESUMES

    if len(session.resumes) > 1:
        summary = format_comparison_summary(session.resumes)
        await _safe_reply(update, summary)

    await _safe_reply(
        update,
        "✅ *Analysis complete!*\n\n"
        "💬 *You can now ask me anything about your resume and this job!*\n\n"
        "For example:\n"
        "  • \"How can I rewrite my summary for this role?\"\n"
        "  • \"What skills should I add to improve my score?\"\n"
        "  • \"Can you rewrite one of my bullet points?\"\n"
        "  • \"What courses should I prioritize?\"\n\n"
        "Type your question below, or:\n"
        "  /check — Start a new evaluation\n"
        "  /exit — Clear this session\n"
        "  /history — View past results",
    )
    # Stay in WAITING_FOR_CHAT — do NOT clear the session so context is preserved
    return WAITING_FOR_CHAT


# ---------------------------------------------------------------------------
# /ask — ask a question during WAITING_FOR_RESUMES or WAITING_FOR_CHAT
# ---------------------------------------------------------------------------

async def ask_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Handle /ask <question> command.

    Works in both WAITING_FOR_RESUMES (during upload) and WAITING_FOR_CHAT
    (after /done). The question is answered using the full session context
    (JD + resume text + analysis results + conversation history).
    """
    user_id = update.effective_user.id
    session = get_session(user_id)

    # Extract question from command args
    raw = update.message.text or ""
    # Remove /ask prefix and any bot mention (e.g. /ask@MyBot)
    parts = raw.split(None, 1)
    question = parts[1].strip() if len(parts) > 1 else ""

    if not question:
        await _safe_reply(
            update,
            "💬 *Usage:* `/ask <your question>`\n\n"
            "For example: `/ask How can I improve my resume summary?`",
        )
        # Return the current state unchanged
        return WAITING_FOR_RESUMES if session.resumes else WAITING_FOR_JD

    if not session.job_description:
        await _safe_reply(
            update,
            "⚠️ No active session. Use /check to start a resume evaluation first.",
        )
        return ConversationHandler.END

    await _send_typing(update, context)

    # Build analysis results list from session
    analysis_results = [r["result"] for r in session.resumes]

    answer = answer_resume_question(
        question=question,
        job_description=session.job_description,
        resume_texts=session.resume_texts,
        analysis_results=analysis_results,
        chat_history=session.chat_history,
    )

    # Store in chat history for multi-turn context
    session.chat_history.append({"role": "user", "content": question})
    session.chat_history.append({"role": "assistant", "content": answer})

    await _safe_reply(update, answer, parse_mode=None)

    # Return to the same state we were in
    if not session.resumes:
        return WAITING_FOR_RESUMES
    # If already in chat mode, stay there; otherwise stay in resume upload mode
    return WAITING_FOR_CHAT if update.message.text and "/ask" in update.message.text else WAITING_FOR_RESUMES


# ---------------------------------------------------------------------------
# WAITING_FOR_CHAT — free-form Q&A after /done
# ---------------------------------------------------------------------------

async def chat_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Handle any text message in WAITING_FOR_CHAT state.

    After the user types /done, all plain text messages are treated as
    career/resume questions. The LLM answers using the full session context
    (JD, resume text(s), analysis results, and conversation history).
    """
    user_id = update.effective_user.id
    session = get_session(user_id)

    question = (update.message.text or "").strip()

    if not question:
        return WAITING_FOR_CHAT

    if not session.job_description:
        await _safe_reply(
            update,
            "⚠️ No active session context. Use /check to start a new evaluation.",
        )
        return ConversationHandler.END

    await _send_typing(update, context)

    # Build analysis results list from session
    analysis_results = [r["result"] for r in session.resumes]

    answer = answer_resume_question(
        question=question,
        job_description=session.job_description,
        resume_texts=session.resume_texts,
        analysis_results=analysis_results,
        chat_history=session.chat_history,
    )

    # Append to chat history for multi-turn memory
    session.chat_history.append({"role": "user", "content": question})
    session.chat_history.append({"role": "assistant", "content": answer})

    await _safe_reply(update, answer, parse_mode=None)

    return WAITING_FOR_CHAT


# ---------------------------------------------------------------------------
# /exit — exit chat mode and clear session
# ---------------------------------------------------------------------------

async def exit_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Exit chat mode, clear the session, and prompt the user to start fresh."""
    user_id = update.effective_user.id
    clear_session(user_id)

    await _safe_reply(
        update,
        "🚪 *Chat session ended.*\n\n"
        "Your session data has been cleared.\n"
        "Use /check to start a new resume evaluation, or /history to view past results.",
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /cancel
# ---------------------------------------------------------------------------

async def cancel_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Abort the current session."""
    user_id = update.effective_user.id
    clear_session(user_id)

    await _safe_reply(
        update,
        "🚫 Session cancelled. Use /check to start a new evaluation.",
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

async def history_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the user their most recent scored resumes."""
    user_id = update.effective_user.id
    await _send_typing(update, context)

    history = get_user_history(user_id, limit=10)
    message = format_history_message(history)
    await _safe_reply(update, message)


# ---------------------------------------------------------------------------
# Private file helpers
# ---------------------------------------------------------------------------

async def _download_file(document, context: ContextTypes.DEFAULT_TYPE) -> bytes:
    """Download a Telegram document and return its raw bytes.

    Uses a 60-second read timeout so large PDF/DOCX files don't time out.
    """
    tg_file = await context.bot.get_file(
        document.file_id,
        read_timeout=60,
        write_timeout=60,
        connect_timeout=15,
    )
    file_bytes = await tg_file.download_as_bytearray()
    return bytes(file_bytes)


async def _download_and_parse_file(
    document, context: ContextTypes.DEFAULT_TYPE
) -> str:
    """
    Download a file and extract its text content.

    Used for JD files (not resumes — resumes go through scorer.py).

    Returns:
        Extracted text string.

    Raises:
        ValueError: If the file is too large, unsupported, or unparseable.
    """
    from core.resume_parser import extract_text_from_file

    file_size = document.file_size or 0
    max_size = 10 * 1024 * 1024  # 10 MB

    if file_size > max_size:
        raise ValueError(
            f"File is too large ({file_size / (1024*1024):.1f} MB). "
            "Maximum allowed size is 10 MB."
        )

    filename = document.file_name or "upload.pdf"
    file_bytes = await _download_file(document, context)
    return extract_text_from_file(file_bytes, filename)


# ---------------------------------------------------------------------------
# Fallback handler for unexpected messages
# ---------------------------------------------------------------------------

async def unexpected_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle messages that arrive outside of any active conversation."""
    await _safe_reply(
        update,
        "ℹ️ No active session. Use /check to start a resume evaluation, "
        "or /help for usage instructions.",
    )
