# SIA v4.1 — pre-GitHub test & fix pass

## Tested
- 115 automated tests (was 86) — `python -m pytest -q`; new suites: `test_hotel_booking.py` (real headless
  Chrome against a local hotel site through the full agent graph) and `test_voice.py`.
- End-to-end in Chrome with a simulated microphone: "Hey Sia" → command → spoken approvals → "yes"/"haan"
  by voice → final spoken answer; push-to-talk mic flow; typed chat with approval cards.

## Fixed
1. **Language detection**: English sentences containing "to" (e.g. "set a reminder to call mom",
   "bus to vizag") were detected as Marathi, so SIA replied and spoke in Marathi. Rewritten word lists;
   Hinglish ("Goa mein hotel book karo") is now detected as Hindi.
2. **Task engine race**: approving quickly made the old run's cleanup forget the resumed run, so Cancel
   and `/api/chat` waiting stopped working for that task.
3. **Hotel booking**: travel agent now covers hotels/stays (Booking.com, MakeMyTrip, Goibibo, Agoda, Airbnb),
   asks once for missing dates/guests, compares options, and stops at payment. Card/UPI/CVV/bank fields
   are now blocked in code (not just in the prompt).
4. **Voice — tap-to-talk approvals**: saying "yes" with the mic button started a new task instead of
   approving; approvals are now spoken and answerable by voice after any voice command.
5. **Voice — echo**: the wake-word listener could hear SIA's own voice and answer its own question
   card; recognition is ignored while (and just after) SIA speaks unless the wake word is used.
6. **Voice — "Hey Sia" … pause … command**: the follow-up command (without the wake word) was ignored.
7. **Voice — double speech**: the generic "check the approval card" line talked over the real approval prompt.
8. **Voice — silent failures**: backend TTS failure now falls back to an offline Windows voice and then
   the browser's voice; mic/transcription errors are shown in the chat.
9. **Sarvam TTS** used the retired request shape (`inputs`, bulbul:v1, speaker meera) → HTTP 400;
   now `text` + `bulbul:v3` (speaker configurable). Sarvam STT auto-detects the language.
10. **Whisper** ignored `WHISPER_MODEL`/`WHISPER_LANGUAGE` and forced English; missing ffmpeg is reported.
11. `/api/voice/speak` and `/api/voice/transcribe` blocked the server while running; now off the event loop.
12. Spoken replies no longer read out markdown tables, bullets or URLs.
13. **Security**: chat messages were inserted as raw HTML (text from web pages/emails could run script in
    the UI). Messages are now escaped; tables, lists, headings and links render safely.
14. Backend now listens on `127.0.0.1` by default; `setup.bat` pointed at a missing requirements file
    (fixed, plus `start.bat`); personal chat history removed from the package; `data/` git-ignored.

---

# SIA v4 — what changed (delivered so far)

## Done and tested (86 backend tests pass: `python -m pytest`; UI flows checked in Chrome)
- **Phase 1.5 fixes**: "I can't sleep"/"restart the song"/"cancel shutdown" no longer trigger power actions;
  ved_facts.json path bug; Sarvam TTS 400s (long text now split); stale RAG chunks on re-upload;
  Chrome path auto-detected; secrets redacted from logs; `.env` removed from the package (rotate your keys).
- **Orchestration core**: LangGraph supervisor + 10 agents (browser, research, computer, productivity,
  communication, jobs, travel, dev, knowledge, system) in `backend/graph/`; 78 typed tools in `backend/tools/`;
  permission gate (L1–L4, approvals, typed CONFIRM for deletes); ask-user / login / CAPTCHA pauses;
  SQLite task store + checkpoints (tasks resume after a restart); cancel; replanning; verification.
- **Tools**: Playwright browser (DOM refs, tabs, downloads, uploads; never types passwords), desktop
  computer-use (UI Automation, keyboard, vision locate + click, before/after screen check), sandboxed files,
  Word/Excel/PowerPoint, dev (allow-listed commands, tests, diff-approved edits, git), Gmail + Calendar via
  browser, RAG/memory/web search, job matching + tracker, read-only SQL over SIA's DB.
- **API** (`backend/api/agent_routes.py`): /api/ws/events, /api/tasks, /api/approvals, /api/memory/items,
  /api/settings/permissions, /api/schedules, /api/tools, /api/agents, /api/applications.
  `/api/chat` keeps its old response shape (plus task_id/task_status).
- **Scheduler**: persistent cron schedules (daily briefing, job watch presets) using the same approval gate.

- **Frontend** (React, no new npm packages):
  - Header: Tasks (badge = active tasks, amber = waiting for you), Memory, Settings buttons.
  - Tasks drawer: live plan, current agent, every tool call with ✓ / ✗ and verified marks, cost, Cancel.
  - Approval cards: exact action + details, Approve / Deny with optional note; deletes need typed CONFIRM;
    question cards for "which one?" and "log in, then type done".
  - Long tasks: the chat bubble is replaced by the final answer when the task finishes (WebSocket).
  - Memory screen: add / search / delete by category (profile, preference, project, workflow, fact).
  - Settings: auto-approve level, destructive-action policy, models, allowed folders, schedules
    (daily briefing / job watch presets, custom cron, run now), job-application tracker.
  - Voice: replies speak the short `spoken` version in the detected language; say "stop" / "ruko" / "bas"
    to interrupt; voice approvals ("yes / haan / avunu / ho" or "no / nahi / vaddu / nako"), level-4
    actions are never approvable by voice; spoken answers go straight to question cards.

## Known limits
- Desktop computer-use and the Gmail/Calendar selectors were written for Windows/Gmail but could only be
  tested with mocks here (Linux). Expect small selector/coordinate fixes on first real use.
- Wake word still uses the browser's Web Speech API (Chrome/Edge).

## Setup
1. `pip install -r backend/requirements.txt`
2. `python -m playwright install chromium`
3. Copy `.env.example` to `.env`, add GROQ_API_KEY and OPENAI_API_KEY
4. `python -m backend.main`, then `cd frontend && npm install && npm run dev`

## Internal test pass (latest)
Checked: static analysis (pyflakes, every module imports), 86 automated tests, real OpenAI/Groq request
payloads built offline for every agent, real headless-Chrome browser run, and these UI flows in Chrome:
chat → approval → result, question card → answer, deny, cancel from the Tasks panel, memory add,
schedules preset, settings.

Fixed during the pass:
1. When the model called two tools at once and the first hit a login page, the next LLM request was
   invalid (unanswered tool call) and the task failed. All calls in a batch are now answered first.
2. If the chat model decided a "conversation" was really an action (e.g. "put on some lofi"), SIA said
   "playing it" without doing anything. Those actions now run through the permission gate.
3. Tasks running when the backend stopped were marked "cancelled" and never resumed. They now stay
   in progress and continue from their checkpoint on the next start.
4. Cancelling a task left the chat bubble on "I'm working on it". It now updates to "Okay, I stopped that task."
5. After SIA edited a Python file, the next test run could still execute the old code (stale bytecode
   cache when the edit kept the same length within one second). Edits now clear that cache.
6. Clearer failure wording ("I couldn't finish this: …").
