# 🤖 ATS Resume Checker Bot

A Telegram bot that uses AI (GPT-4 or Claude) to evaluate resumes against job descriptions — just like a real Applicant Tracking System (ATS). Send your resume and a job posting, get an instant score, spot your gaps, and get actionable improvement tips.

---

## ✨ Features

- **ATS Scoring (0–100)** — Weighted match score based on keyword alignment, relevant experience, skills, and resume format
- **Strengths Analysis** — Highlights what's already strong in your resume relative to the JD
- **Gap Detection** — Lists missing keywords, tools, certifications, and qualifications
- **Actionable Suggestions** — Concrete, specific steps to improve your resume (not generic advice)
- **Course Recommendations** — Suggests online courses to fill skill gaps (with platform names)
- **Follow-up Questions** — AI asks clarifying questions to uncover hidden strengths
- **Multi-Resume Comparison** — Submit multiple versions and get a ranked comparison
- **History Tracking** — View your past scored resumes with `/history`
- **Dual LLM Support** — Works with both Anthropic Claude and OpenAI GPT
- **File Upload Support** — Accepts PDF, DOCX, and TXT files for both JD and resume
- **SQLite Storage** — Lightweight local persistence, no external database needed

---

## 🔧 Prerequisites

| Requirement | Details |
|---|---|
| **Python** | 3.11 or higher |
| **Telegram Bot Token** | From [@BotFather](https://t.me/BotFather) on Telegram |
| **LLM API Key** | OpenAI (`sk-...`) or Anthropic (`sk-ant-...`) — one is enough |
| **Internet connection** | For Telegram polling and LLM API calls |

---

## 🚀 Setup Instructions

### 1. Download the project

```bash
git clone https://github.com/your-username/ats-telegram-bot.git
cd ats-telegram-bot
```

Or simply unzip the downloaded folder and open a terminal in it.

### 2. Create a virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure your environment

Copy the example config file and fill in your credentials:

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Open `.env` in any text editor and set the following values:

```ini
TELEGRAM_BOT_TOKEN=your-telegram-bot-token-here
LLM_PROVIDER=openai          # or "anthropic"
OPENAI_API_KEY=sk-...        # required if LLM_PROVIDER=openai
ANTHROPIC_API_KEY=sk-ant-... # required if LLM_PROVIDER=anthropic
```

### 5. Get a Telegram Bot Token (BotFather)

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Follow the prompts: choose a name and a username (must end in `bot`)
4. BotFather will give you a token like `123456789:ABCdef...`
5. Paste that into `.env` as `TELEGRAM_BOT_TOKEN`

### 6. Run the bot

```bash
python main.py
```

You should see:
```
2024-01-15 10:00:00 [INFO] main: Starting ATS Resume Checker Bot...
2024-01-15 10:00:00 [INFO] main: Configuration validated. LLM provider: openai
2024-01-15 10:00:01 [INFO] main: Bot is running. Press Ctrl+C to stop.
```

Open your Telegram bot and send `/start` to begin!

---

## 💬 Usage Guide

### Available Commands

| Command | What it does |
|---|---|
| `/start` | Welcome message and feature overview |
| `/check` | Begin a new resume evaluation session |
| `/done` | Finish sending resumes and see results |
| `/history` | View your last 10 scored resumes |
| `/skip` | Skip the current follow-up question |
| `/cancel` | Abort the current session |
| `/help` | Show usage instructions |

### Example Conversation

```
You:   /check

Bot:   ✅ New evaluation session started!
       📋 Step 1 of 2 — Job Description
       Please send me the Job Description...

You:   [Pastes the job posting text]

Bot:   ✅ Job Description received!
       📄 Step 2 of 2 — Send Resume(s)
       Now send me one or more resumes...

You:   [Uploads resume.pdf]

Bot:   📎 Received resume.pdf — analyzing...
       ⏳ This may take 10–30 seconds.

       📄 resume.pdf
       🟡 ATS Score: 68/100
       ████████████████░░░░
       
       ✅ Strengths
         • 5+ years Python experience matches JD requirement
         • Mentions AWS which is a key required skill
       
       ⚠️ Gaps / Missing Keywords
         • Kubernetes
         • Terraform
         • CI/CD pipelines
       
       💡 Suggestions to Improve
         1. Add a dedicated 'Skills' section listing Kubernetes and Terraform
         2. Quantify achievements (e.g. "reduced deployment time by 40%")
         ...
       
       🎓 Recommended Courses
         • Kubernetes for Beginners – Udemy
         • HashiCorp Terraform Associate – Coursera
       
       ❓ Follow-up Questions
         Q1: Do you have Kubernetes experience, even in a personal project?

You:   Yes, I used Kubernetes for a side project deploying a microservices app

Bot:   Great! Since you have hands-on Kubernetes experience, definitely add it
       to your resume under both Skills and the relevant project description...
       
       ❓ Q2: Have you used any CI/CD tools like Jenkins or GitHub Actions?

You:   /done   (or answer the question)

Bot:   ✅ Session complete!
```

### Comparing Multiple Resumes

```
You:   /check
       [Send JD]
       [Upload resume_v1.pdf]
       [Upload resume_v2.pdf]
       /done

Bot:   📊 Comparison Summary
       
       Resumes ranked by ATS match score:
       
       🥇 resume_v2.pdf — 🟢 82/100
       🥈 resume_v1.pdf — 🟡 65/100
       
       🏆 Best match: resume_v2.pdf with a score of 82/100
```

---

## 📁 Project Structure

```
ats-telegram-bot/
├── main.py                  # Entry point — starts the bot
├── requirements.txt         # Python dependencies
├── .env.example             # Config template (copy to .env)
├── .gitignore               # Keeps secrets and build files out of git
│
├── bot/
│   ├── __init__.py
│   ├── handlers.py          # Telegram command & message handlers
│   └── conversation.py      # State machine + in-memory session store
│
├── core/
│   ├── __init__.py
│   ├── llm_client.py        # Unified Anthropic/OpenAI interface
│   ├── resume_parser.py     # PDF/DOCX/text extraction
│   ├── prompts.py           # LLM prompt templates
│   └── scorer.py            # Pipeline orchestration + message formatting
│
├── db/
│   ├── __init__.py
│   ├── models.py            # SQLite schema & table creation
│   └── storage.py           # Save/retrieve sessions and results
│
├── data/                    # Created automatically at runtime
│   └── ats_bot.db           # SQLite database
│
└── tests/
    ├── __init__.py
    ├── test_resume_parser.py # Tests for file parsing logic
    └── test_scorer.py        # Tests for scoring & JSON parsing
```

---

## 🧮 How Scoring Works

The bot sends your resume and job description to an AI model with detailed instructions on how to score. The score (0–100) is calculated across four dimensions:

| Factor | Weight | What it checks |
|---|---|---|
| **Keyword Match** | 35% | Are the required skills, tools, and technologies from the JD present in your resume? |
| **Relevant Experience** | 30% | Does your work history align with the role's seniority and domain? |
| **Skills Alignment** | 20% | Do your stated skills directly map to what the JD asks for? |
| **Resume Formatting** | 15% | Is the resume structured cleanly with clear sections (Experience, Skills, Education)? |

> **Important:** This is an AI-estimated score, not a score from a certified ATS product (like Workday or Greenhouse). It is designed to closely approximate how ATS systems prioritize candidates, but results may vary.

### Score Interpretation

| Score | Rating | Meaning |
|---|---|---|
| 80–100 | 🟢 Excellent | Strong match — likely to pass ATS filters |
| 60–79 | 🟡 Good | Decent match — a few targeted tweaks will help |
| 40–59 | 🟠 Fair | Significant gaps — resume needs meaningful changes |
| 0–39 | 🔴 Poor | Weak match — consider whether this role is a fit |

---

## ⚠️ Known Limitations

- **Scanned PDFs** — Image-based PDFs (scans of paper resumes) cannot be parsed. Use a text-selectable PDF or paste your resume as text.
- **AI scoring variability** — LLM responses can vary slightly between runs. Results are a strong guide, not an absolute truth.
- **Not a certified ATS** — This bot approximates ATS behavior but is not connected to any real ATS product.
- **Context window limits** — Extremely long resumes or JDs (>50 pages) may be truncated by the LLM.
- **Rate limits** — Heavy usage may hit OpenAI/Anthropic rate limits. The bot will surface a friendly error message.
- **No real-time rescoring** — Answering follow-up questions gives advice but does not automatically recalculate the score. Use `/rescore` (future feature) for that.

---

## 🔮 Future Improvements

- **Webhook mode** — Replace polling with a webhook for more reliable production deployment
- **Cloud deployment** — Deploy to Railway, Render, or a VPS for 24/7 availability
- **Auto-rescoring** — Add a `/rescore` command that incorporates follow-up answers into a revised score
- **Multi-language support** — Detect and respond in the user's language
- **Cover letter generation** — Generate a tailored cover letter based on the resume + JD
- **Export to PDF** — Export the full analysis report as a PDF
- **Rate limiting** — Per-user cooldowns to prevent API cost spikes
- **Web dashboard** — A simple web UI to view and compare results

---

## 📄 License

MIT License — free to use and modify.
