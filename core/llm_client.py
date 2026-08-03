"""
llm_client.py — Unified LLM interface supporting Anthropic Claude, OpenAI, and Google Gemini.

The active provider is chosen via the LLM_PROVIDER environment variable:
  LLM_PROVIDER=gemini     → uses Google Gemini (recommended default)
  LLM_PROVIDER=anthropic  → uses Anthropic Claude
  LLM_PROVIDER=openai     → uses OpenAI GPT

All providers share the same public function signature so the rest of the
codebase never needs to know which backend is active.
"""

import json
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public type alias for the parsed LLM result
# ---------------------------------------------------------------------------
ATSResult = dict[str, Any]


def _clean_json_string(raw: str) -> str:
    """
    Strip any markdown code fences, smart quotes, or trailing commas,
    leaving only a clean valid JSON object string.
    """
    # Remove ```json ... ``` or ``` ... ``` fences
    raw = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE)
    raw = raw.strip().strip("`").strip()

    # Normalize smart quotes to standard double quotes
    raw = raw.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")

    # Find the first '{' and last '}' to isolate the JSON object
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]

    # Remove trailing commas before closing brackets/braces (e.g. [1, 2,] -> [1, 2])
    raw = re.sub(r",\s*([\]}])", r"\1", raw)

    return raw


def _repair_json(text: str) -> str:
    """Attempt to auto-repair unclosed quotes, brackets, and braces if JSON was truncated."""
    in_string = False
    escape = False
    stack = []

    for char in text:
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char in "{[":
                stack.append(char)
            elif char in "}]":
                if stack:
                    stack.pop()

    # If inside an unclosed string, close it
    if in_string:
        text += '"'

    # Remove trailing comma if present
    text = re.sub(r",\s*$", "", text.strip())

    # Close open brackets and braces in reverse order
    for open_char in reversed(stack):
        if open_char == "{":
            text += "}"
        elif open_char == "[":
            text += "]"

    return text


def _parse_response(raw_text: str) -> ATSResult:
    """
    Parse the LLM response text into a structured dict.

    Attempts JSON parsing with multiple cleanup and auto-repair strategies.

    Args:
        raw_text: Raw string returned by the LLM.

    Returns:
        Parsed dict matching the ATS result schema.

    Raises:
        ValueError: If the response cannot be parsed as valid JSON after retries.
    """
    current_text = raw_text

    for attempt in range(4):
        try:
            cleaned = _clean_json_string(current_text)
            result = json.loads(cleaned, strict=False)

            # Basic schema validation
            required_keys = {
                "score",
                "strengths",
                "missing_keywords",
                "suggestions",
                "course_suggestions",
                "follow_up_questions",
            }
            missing = required_keys - set(result.keys())
            if missing:
                logger.warning(
                    "LLM response missing keys on attempt %d: %s", attempt + 1, missing
                )
                for key in missing:
                    if key == "score":
                        result[key] = 0
                    else:
                        result[key] = []

            # Clamp score to [0, 100]
            result["score"] = max(0, min(100, int(result.get("score", 0))))
            return result

        except Exception as exc:
            logger.warning("JSON parse attempt %d failed: %s", attempt + 1, exc)
            if attempt == 0:
                current_text = _clean_json_string(raw_text)
            elif attempt == 1:
                # Remove non-printable control characters
                current_text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", raw_text)
            elif attempt == 2:
                # Attempt structural repair for truncated JSON
                current_text = _repair_json(_clean_json_string(raw_text))

    # Log raw output on final failure for easy debugging
    logger.error("Failed to parse LLM JSON response. Raw output was:\n%s", raw_text)
    raise ValueError(
        "The AI returned a response that could not be parsed as JSON. "
        "Please try again with /check."
    )


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

def _call_anthropic(prompt: str) -> str:
    """
    Send a prompt to Anthropic Claude and return the raw text response.

    Uses the model specified in ANTHROPIC_MODEL env var, defaulting to
    claude-3-5-sonnet-20241022.

    Args:
        prompt: Fully formatted prompt string.

    Returns:
        Raw text content from the first text block in the response.

    Raises:
        RuntimeError: On API errors, rate limits, or connection issues.
    """
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "The 'anthropic' package is not installed. Run: pip install anthropic"
        ) from exc

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Please add it to your .env file."
        )

    model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
    client = anthropic.Anthropic(api_key=api_key)

    logger.debug("Sending request to Anthropic model: %s", model)

    try:
        message = client.messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        # Extract text from the first content block
        return message.content[0].text
    except anthropic.RateLimitError as exc:
        raise RuntimeError(
            "Anthropic API rate limit exceeded. Please wait a moment and try again."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise RuntimeError(
            "Could not connect to Anthropic API. Please check your internet connection."
        ) from exc
    except anthropic.APIError as exc:
        raise RuntimeError(f"Anthropic API error: {exc}") from exc


# ---------------------------------------------------------------------------
# Google Gemini backend
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, force_json: bool = True) -> str:
    """
    Send a prompt to Google Gemini and return the raw text response.

    Uses the model specified in GEMINI_MODEL env var, defaulting to
    gemini-2.0-flash (fast, capable, and free-tier friendly).

    Args:
        prompt: Fully formatted prompt string.

    Returns:
        Raw text content from the Gemini response.

    Raises:
        RuntimeError: On API errors, rate limits, or connection issues.
    """
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "The 'google-genai' package is not installed. "
            "Run: pip install google-genai"
        ) from exc

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Please add it to your .env file.\n"
            "Get a free key at: https://aistudio.google.com/apikey"
        )

    primary_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    # Candidate models to try in order if a model hits a 429 rate/quota limit or 404 error.
    # Each model in Google AI Studio has its own separate rate limit and quota bucket!
    candidate_models = [
        primary_model,
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-2.0-flash-lite",
        "gemini-flash-latest",
        "gemini-2.5-pro",
    ]
    # Preserve order while eliminating duplicates
    fallback_models = list(dict.fromkeys(candidate_models))

    last_exception = None

    # Try model fallback sequence
    for model in fallback_models:
        logger.debug("Attempting Gemini request with model: %s", model)
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=4096,
                    **({
                        "response_mime_type": "application/json"
                    } if force_json else {}),
                ),
            )
            if response and response.text:
                logger.info("Successfully generated response using Gemini model: %s", model)
                return response.text
        except Exception as exc:
            last_exception = exc
            err_str = str(exc).lower()
            is_fallbackable = (
                "429" in err_str
                or "404" in err_str
                or "503" in err_str
                or "500" in err_str
                or "not found" in err_str
                or "rate limit" in err_str
                or "quota" in err_str
                or "resource_exhausted" in err_str
                or "resourceexhausted" in err_str
                or "unavailable" in err_str
                or "overloaded" in err_str
                or "high demand" in err_str
                or "service unavailable" in err_str
                or "try again later" in err_str
            )

            if is_fallbackable:
                logger.warning(
                    "Gemini model '%s' returned error (%s). Trying next fallback model...", model, exc
                )
                continue  # Try next model in fallback list immediately
            else:
                # Non-recoverable error (e.g. invalid API key) — fail early
                break

    # If all fallback models failed or non-rate-limit error occurred
    exc = last_exception or RuntimeError("All Gemini models failed.")
    err_str = str(exc).lower()

    if (
        "api key not valid" in err_str
        or "invalid api key" in err_str
        or "401" in err_str
        or "api_key_invalid" in err_str
        or "permission_denied" in err_str
    ):
        raise RuntimeError(
            "Invalid GEMINI_API_KEY.\n"
            "Get a valid free key at: https://aistudio.google.com/apikey"
        ) from exc
    elif "connection" in err_str or "network" in err_str or "timeout" in err_str:
        raise RuntimeError(
            "Could not connect to Google Gemini API. "
            "Please check your internet connection."
        ) from exc
    elif (
        "429" in err_str
        or "rate limit" in err_str
        or "quota" in err_str
        or "resource_exhausted" in err_str
        or "resourceexhausted" in err_str
    ):
        raise RuntimeError(
            "Google Gemini rate limit / daily quota exceeded across all models.\n\n"
            "Please wait a few minutes before trying again, or get a new API key at "
            "https://aistudio.google.com/apikey"
        ) from exc
    elif (
        "503" in err_str
        or "unavailable" in err_str
        or "overloaded" in err_str
        or "high demand" in err_str
    ):
        raise RuntimeError(
            "All Gemini models are temporarily overloaded (503).\n\n"
            "This is a temporary issue on Google's side — please wait 30–60 seconds and try again."
        ) from exc
    else:
        raise RuntimeError(
            f"Google Gemini API error ({type(exc).__name__}): {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------

def _call_openai(prompt: str) -> str:
    """
    Send a prompt to OpenAI and return the raw text response.

    Uses the model specified in OPENAI_MODEL env var, defaulting to
    gpt-4o-mini (cost-effective and capable).

    Args:
        prompt: Fully formatted prompt string.

    Returns:
        Raw text content from the first choice message.

    Raises:
        RuntimeError: On API errors, rate limits, or connection issues.
    """
    try:
        import openai  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "The 'openai' package is not installed. Run: pip install openai"
        ) from exc

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Please add it to your .env file."
        )

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = openai.OpenAI(api_key=api_key)

    logger.debug("Sending request to OpenAI model: %s", model)

    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=2048,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert ATS analyst. Always respond with valid JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,  # Low temperature for consistent, structured output
        )
        return response.choices[0].message.content or ""
    except openai.RateLimitError as exc:
        raise RuntimeError(
            "OpenAI API rate limit exceeded. Please wait a moment and try again."
        ) from exc
    except openai.APIConnectionError as exc:
        raise RuntimeError(
            "Could not connect to OpenAI API. Please check your internet connection."
        ) from exc
    except openai.APIError as exc:
        raise RuntimeError(f"OpenAI API error: {exc}") from exc


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def analyze_resume(job_description: str, resume_text: str) -> ATSResult:
    """
    Analyze a resume against a job description using the configured LLM.

    This is the single public entry point for LLM calls in the bot. It:
    1. Selects the provider from the LLM_PROVIDER environment variable.
    2. Formats the ATS prompt with the provided inputs.
    3. Calls the appropriate backend.
    4. Parses and validates the structured JSON response.
    5. Returns a typed dict with all ATS result fields.

    Args:
        job_description: Full job description text.
        resume_text: Extracted plain text of the resume.

    Returns:
        ATSResult dict with keys: score, strengths, missing_keywords,
        suggestions, course_suggestions, follow_up_questions.

    Raises:
        RuntimeError: On provider misconfiguration or API failures.
        ValueError: If the LLM response cannot be parsed as valid JSON.
    """
    from core.prompts import ATS_ANALYSIS_PROMPT  # lazy import to avoid circularity

    provider = os.getenv("LLM_PROVIDER", "openai").lower().strip()
    logger.info("Analyzing resume with provider: %s", provider)

    prompt = ATS_ANALYSIS_PROMPT.format(
        job_description=job_description,
        resume_text=resume_text,
    )

    if provider == "gemini":
        raw_response = _call_gemini(prompt)
    elif provider == "anthropic":
        raw_response = _call_anthropic(prompt)
    elif provider == "openai":
        raw_response = _call_openai(prompt)
    else:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER: '{provider}'. "
            "Set LLM_PROVIDER to 'gemini', 'anthropic', or 'openai' in your .env file."
        )

    logger.debug("Raw LLM response (first 300 chars): %s", raw_response[:300])
    return _parse_response(raw_response)


def get_followup_acknowledgement(question: str, answer: str) -> str:
    """
    Get a friendly acknowledgement of the user's follow-up answer.

    Uses the same LLM provider but with the simpler FOLLOWUP_ACK_PROMPT.
    Returns plain text (not JSON).

    Args:
        question: The follow-up question that was asked.
        answer: The candidate's response to that question.

    Returns:
        A conversational acknowledgement string.
    """
    from core.prompts import FOLLOWUP_ACK_PROMPT  # lazy import

    provider = os.getenv("LLM_PROVIDER", "openai").lower().strip()
    prompt = FOLLOWUP_ACK_PROMPT.format(question=question, answer=answer)

    try:
        if provider == "gemini":
            return _call_gemini(prompt, force_json=False)
        elif provider == "anthropic":
            return _call_anthropic(prompt)
        else:
            return _call_openai(prompt)
    except Exception as exc:
        logger.warning("Follow-up acknowledgement failed: %s", exc)
        return (
            "Thanks for sharing that! Make sure to include this experience in "
            "your resume to strengthen your match for the role."
        )


def answer_resume_question(
    question: str,
    job_description: str,
    resume_texts: list[str],
    analysis_results: list[dict],
    chat_history: list[dict],
) -> str:
    """
    Answer a free-form career/resume question from the user.

    Uses the RESUME_CHAT_PROMPT with full context: JD, resume text(s),
    analysis results, and multi-turn conversation history.

    This call uses plain-text mode (not JSON) — the result is sent directly
    to the user as a conversational message.

    Args:
        question: The user's question.
        job_description: JD text from the session.
        resume_texts: Extracted plain text for each submitted resume.
        analysis_results: List of ATSResult dicts (one per resume).
        chat_history: Running conversation history as list of
                      {"role": "user" | "assistant", "content": str}.

    Returns:
        Plain text answer from the LLM.
    """
    from core.prompts import RESUME_CHAT_PROMPT  # lazy import

    # Build resume context (cap each resume to keep within token limits)
    if resume_texts:
        parts = []
        for i, text in enumerate(resume_texts, 1):
            label = f"Resume {i}" if len(resume_texts) > 1 else "Resume"
            parts.append(f"--- {label} ---\n{text[:3000]}")
        resume_context = "\n\n".join(parts)
    else:
        resume_context = "No resume text available."

    # Build concise analysis summary
    if analysis_results:
        summary_lines = []
        for i, r in enumerate(analysis_results, 1):
            score = r.get("score", "N/A")
            gaps = r.get("missing_keywords", [])
            gaps_str = ", ".join(gaps[:6]) if gaps else "None identified"
            summary_lines.append(
                f"Resume {i}: ATS Score = {score}/100 | Missing keywords: {gaps_str}"
            )
        analysis_summary = "\n".join(summary_lines)
    else:
        analysis_summary = "No analysis completed yet."

    # Build conversation history string (last 10 messages for context)
    if chat_history:
        history_lines = []
        for msg in chat_history[-10:]:
            role_label = "Candidate" if msg["role"] == "user" else "Coach"
            history_lines.append(f"{role_label}: {msg['content']}")
        history_str = "\n".join(history_lines)
    else:
        history_str = "(No previous conversation in this session)"

    prompt = RESUME_CHAT_PROMPT.format(
        job_description=job_description[:2500],
        resume_context=resume_context,
        analysis_summary=analysis_summary,
        chat_history=history_str,
        question=question,
    )

    provider = os.getenv("LLM_PROVIDER", "gemini").lower().strip()
    logger.info("Answering resume question with provider: %s", provider)

    try:
        if provider == "gemini":
            return _call_gemini(prompt, force_json=False)
        elif provider == "anthropic":
            return _call_anthropic(prompt)
        else:
            return _call_openai(prompt)
    except Exception as exc:
        logger.warning("Resume Q&A LLM call failed: %s", exc)
        return (
            "I'm sorry, I couldn't process your question right now. "
            "Please try again in a moment."
        )
