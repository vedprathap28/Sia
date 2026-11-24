# SIA — Smart Intelligent Assistant

SIA is a personal AI assistant for Windows that **does things on your computer**, by text or by voice.
You talk to it ("Hey Sia, book a hotel in Goa for 12 to 14 December"), and a team of specialised
agents plans the task, works in a real browser and in your desktop apps, and **asks you before any
risky step** (booking, sending, submitting, deleting).

- 🎙️ **Voice**: "Hey Sia" wake word, push-to-talk mic, spoken replies in English, Hindi, Telugu and Marathi;
  approve actions by saying *yes / haan / avunu / ho*
- 🌐 **Browser agent**: opens sites, searches, fills forms, compares tabs, downloads/uploads (Playwright)
- 🏨 **Travel & bookings**: hotels, buses, trains, flights, movies; prepares the booking and stops at the payment page
- 🖥️ **Desktop control**: launch/switch apps, click, type, shortcuts, read the screen (Windows UI Automation + vision)
- 📧 **Gmail & Google Calendar** through the browser (read, summarise, draft, send with approval)
- 📁 **Files & Office**: find/read files and PDFs, create Word, Excel and PowerPoint documents
- 🧑‍💻 **Dev agent**: inspect a repo, run tests, propose diffs (you approve each edit), git
- 🧠 **Memory & documents**: remembers what you ask it to; answers from your uploaded documents (RAG)
- ⏰ **Schedules**: daily briefing, job-watch and custom cron tasks, through the same approval gate
- 🔊 Quick built-ins: volume, brightness, media, YouTube, WhatsApp, reminders, clipboard AI

---

## How it works

```
 React UI ──/api/chat──► FastAPI ──► Task manager ──► LangGraph supervisor
   ▲  ▲                                                   │ plans 1–6 steps
   │  └──── /api/ws/events (live plan, tool calls,        ▼
   │         approval cards, final answer)          ┌───────────────┐
   │                                                │ 10 agents     │ browser · research · computer ·
   └── /api/voice/speak, /api/voice/transcribe      │ (one loop)    │ productivity · communication ·
                                                    └──────┬────────┘ jobs · travel · dev · knowledge · system
                                                           ▼
                                              Permission gate (risk 1–4)
                                                           ▼
                                                 78 typed tools → your PC / browser
```

- The **supervisor** (planner model) either answers directly or makes a short plan; each step is run by a
  specialised agent with its own tool set. Failed steps are re-planned.
- Every tool has a **risk level**: 1 read-only · 2 low-risk · 3 needs approval · 4 destructive (typed `CONFIRM`,
  never approvable by voice). Clicking *Book / Reserve / Pay / Submit / Send* is automatically level 3.
- Tasks are **checkpointed in SQLite**: they survive a backend restart and resume where they stopped.
- Web pages and emails are treated as **untrusted input** (prompt-injection guard); SIA never types
  passwords, OTPs, card, UPI or bank details — you do that yourself in the browser window.

---

## Requirements

| | |
|---|---|
| OS | Windows 10/11 (backend tests also run on Linux/macOS; desktop control is Windows-only) |
| Python | 3.11 – 3.13 |
| Node.js | 18+ |
| Browser | Google Chrome or Microsoft Edge (for the UI's wake word and for SIA's browser agent) |
| Keys | **Groq** (chat) and **OpenAI** (planner/agents, gpt-4o-mini). Optional: Sarvam (Indian voices), Tavily |
| Voice (optional) | `ffmpeg` on PATH for local Whisper speech-to-text — or a `SARVAM_API_KEY` instead |

---

## Quick start (Windows)

```bat
git clone https://github.com/<your-username>/SIA.git
cd SIA
setup.bat          :: venv, Python + npm packages, Playwright browser, creates .env
notepad .env       :: add GROQ_API_KEY and OPENAI_API_KEY
start.bat          :: starts backend + frontend and opens http://localhost:5173
```

### Manual setup (any OS)

```bash
python -m venv venv
venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
pip install -r backend/requirements.txt
python -m playwright install chromium
copy .env.example .env           # macOS/Linux: cp .env.example .env  — then add your keys

python -m backend.main           # backend  → http://localhost:8000  (API docs at /docs)

cd frontend
npm install
npm run dev                      # frontend → http://localhost:5173
```

---

## Using voice

| Mode | How |
|---|---|
| **Hands-free** | Click the glowing orb (bottom-right). Say **"Hey Sia, …"**. Saying just "Hey Sia" gets a "Yeah?" — then say the command. |
| **Push-to-talk** | Click the 🎤 in the message box, speak, click again. |
| **Approve by voice** | When SIA asks *"I need your approval: … Say yes or no"*, answer **yes / haan / avunu / ho** or **no / nahi / vaddu / nako**. Destructive (level 4) actions must be confirmed on screen. |
| **Interrupt** | Say **"stop" / "ruko" / "bas"** while SIA is talking. |

- Wake word uses the browser's speech recognition: use **Chrome or Edge** and allow the microphone.
- Speech-to-text: Sarvam (`SARVAM_API_KEY`, auto-detects the language) → local Whisper (`WHISPER_MODEL`, needs ffmpeg).
- Text-to-speech: Sarvam → Edge TTS → offline Windows voice → the browser's own voice, so SIA always answers.
- SIA ignores what the mic hears while it is talking (so it never answers its own questions); say the wake word to barge in.

## Example: booking a hotel

> **You:** Hey Sia, book a hotel in Goa from 12 to 14 December for 2 people, something under 5,000 a night.
>
> SIA opens Booking.com / MakeMyTrip in its browser window, searches, compares the best options and
> shows them. If the dates or city are missing it asks you once.
>
> **SIA:** *"I need your approval: Click button Reserve. Say yes or no."* → **You:** "Yes."
>
> SIA fills guest details from your saved profile (Memory → *profile*), then asks again before
> *Continue to payment*. It **stops at the payment page**: *"I've prepared your booking at Ibis Calangute,
> 12–14 Dec, total ₹8,400. Please pay in the browser window."*

Tip: log in to your booking sites once in SIA's browser window; the session is kept in `data/browser_profile`.

More things to try: *"summarise my unread emails"*, *"find the latest invoice PDF in Downloads and put the
totals in an Excel sheet"*, *"open YouTube and play lo-fi"*, *"remind me at 6 to call mom"*,
*"run the tests in D:\code\myapp and fix the failing one"*, *"Goa mein kal ke liye hotel dikhao"*.

---

## Configuration

All settings live in `.env` (see `.env.example` for the full list; changes are in `CHANGELOG.md`). The important ones:

| Variable | Default | Meaning |
|---|---|---|
| `GROQ_API_KEY`, `OPENAI_API_KEY` | – | Required LLM keys |
| `USER_NAME` | `Ved` | How SIA addresses you |
| `AUTO_APPROVE_LEVEL` | `2` | Actions up to this risk level run without asking (`1` = ask for everything) |
| `LEVEL4_POLICY` | `confirm` | `confirm` (type CONFIRM) or `block` destructive actions |
| `ALLOWED_ROOTS` | Desktop, Documents, Downloads, Pictures, Music, Videos | Folders SIA may read/write (`;`-separated) |
| `BROWSER_CHANNEL` | `chrome` | `chrome`, `msedge` or `chromium` |
| `SARVAM_API_KEY` | – | Natural Indian-language voices + multilingual speech-to-text |
| `WHISPER_MODEL` / `WHISPER_LANGUAGE` | `small` / `auto` | Local speech-to-text |
| `SIA_ENGINE` | `graph` | `legacy` = the old single-shot router |
| `HOST` | `127.0.0.1` | Keep on localhost — SIA can control your computer |

Most of these can also be changed live in the app (**Settings** in the header).

---

## Project structure

```
SIA/
├── backend/
│   ├── main.py                 FastAPI app (startup, routers)
│   ├── api/                    routes.py (chat, voice, documents, history) · agent_routes.py (tasks, approvals, memory, schedules, WebSocket)
│   ├── graph/                  supervisor.py (LangGraph plan → act → verify → replan) · agents.py (10 agents)
│   ├── tools/                  78 tools: browser/ computer/ files/ productivity/ communication/ development/ knowledge/
│   ├── task_manager/           task runs, approvals, pause/resume, recovery, event bus
│   ├── security/               permissions (risk gate), sandbox (allowed folders), prompt-injection guard
│   ├── voice/                  tts.py (Sarvam/Edge/offline) · stt.py (Whisper) · livekit_agent.py (optional)
│   ├── agent/                  language detection, quick local commands, legacy engine
│   ├── llm/ memory/ rag/ database/ background_workers/ actions/ core/
│   └── tests/                  pytest suite (115 tests)
├── frontend/                   React + Vite UI (chat, voice orb, task panel, approval cards, memory, settings)
├── scripts/voice_check.py      manual speaker/microphone check
├── data/                       runtime data — chat history, DBs, browser profile (git-ignored)
├── setup.bat · start.bat       Windows setup / launcher
└── .env.example                configuration template
```

---

## Tests

```bash
pip install pytest pytest-asyncio
python -m pytest -q
```

The suite runs without API keys or Windows: LLMs are replaced by scripted models, and the browser tests
drive a real headless Chrome against local pages. It covers the agent graph (plans, parallel tool calls,
approvals, denials, replanning, cancel, crash recovery), the permission gate and sandbox, the browser
(password/payment fields never filled, risky clicks need approval), a full **hotel booking** flow that must
stop at the payment page, and the **voice** pipeline (language detection, spoken text, TTS/STT endpoints
and fallbacks). GitHub Actions runs it on every push (`.github/workflows/tests.yml`).

For a real hardware check of speakers + microphone: `python scripts/voice_check.py`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `No LLM API key found` on start | Add `GROQ_API_KEY` / `OPENAI_API_KEY` to `.env` |
| Orb does nothing / "Web Speech API not supported" | Use Chrome or Edge; allow the microphone for `localhost:5173` |
| 🎤 says "ffmpeg is not installed" | `winget install ffmpeg` (then reopen the terminal), or set `SARVAM_API_KEY` |
| SIA's voice sounds robotic | Edge TTS needs internet; set `SARVAM_API_KEY` for the best Indian voices |
| Browser agent can't start Chrome | `python -m playwright install chromium` and set `BROWSER_CHANNEL=chromium` |
| A site asks to log in / CAPTCHA | SIA pauses and asks you to finish it in its browser window, then reply "done" |
| Approval card never appears | Check the backend terminal; the UI needs the WebSocket at `ws://localhost:8000/api/ws/events` |

## Security notes

- Never commit `.env` (it is git-ignored). If a key was ever shared or committed, rotate it.
- `data/` holds your chat history and the **logged-in browser profile** — it is git-ignored; keep it private.
- The backend listens on `127.0.0.1` only. Don't expose it to your network.

## License

Add a license of your choice (for example MIT) before publishing.
