"""
Sia AI — Configuration
======================
Speed-optimized defaults:
  LLM    → Groq  llama-3.3-70b-versatile  (~300ms responses, free)
  TTS    → Sarvam TTS                      (~300ms, Indian voices, free tier)
  STT    → Sarvam Saaras v3               (unchanged, already fast)
  RAG    → gated (only runs if docs exist)
"""
from __future__ import annotations
import os, platform, sys
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")

def _env(k, d=""): return os.getenv(k, d).strip()
def _int(k, d):
    try: return int(os.getenv(k, str(d)))
    except: return d
def _float(k, d):
    try: return float(os.getenv(k, str(d)))
    except: return d
def _bool(k, d):
    return os.getenv(k, str(d)).strip().lower() in ("1","true","yes","on")
def _os():
    s = platform.system().lower()
    return "windows" if s=="windows" else "mac" if s=="darwin" else "linux"

class Settings:
    # ── LLM — Groq is now default (fastest, free) ─────────────────────────
    ANTHROPIC_API_KEY: str   = _env("ANTHROPIC_API_KEY")
    GROQ_API_KEY:      str   = _env("GROQ_API_KEY")
    OPENAI_API_KEY:    str   = _env("OPENAI_API_KEY")
    GOOGLE_API_KEY:    str   = _env("GOOGLE_API_KEY")
    # DEFAULT changed: claude → groq
    LLM_PROVIDER:      str   = _env("LLM_PROVIDER", "groq")
    # DEFAULT changed: claude-sonnet-4-6 → llama-3.3-70b-versatile
    LLM_MODEL:         str   = _env("LLM_MODEL", "llama-3.3-70b-versatile")
    LLM_TEMPERATURE:   float = _float("LLM_TEMPERATURE", 0.7)
    LLM_MAX_TOKENS:    int   = _int("LLM_MAX_TOKENS", 512)   # reduced: faster for voice

    # ── TTS — Sarvam is now primary (Indian voices, fast, free tier) ──────
    # Sarvam TTS
    SARVAM_API_KEY:      str = _env("SARVAM_API_KEY")         # same key as STT
    SARVAM_TTS_SPEAKER:  str = _env("SARVAM_TTS_SPEAKER", "ishita")  # bulbul:v3 speakers: shubh, ishita, priya, aditya, ritu, neha, kavya, rahul …
    SARVAM_TTS_MODEL:    str = _env("SARVAM_TTS_MODEL", "bulbul:v3")
    SARVAM_STT_MODEL:    str = _env("SARVAM_STT_MODEL", "saaras:v3")

    # ElevenLabs (kept as optional override — set ELEVENLABS_API_KEY to use)
    ELEVENLABS_API_KEY: str = _env("ELEVENLABS_API_KEY")
    ELEVENLABS_VOICE_ID:str = _env("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")

    # ── Optional future tool API keys (add to .env as you get them) ───────
    TAVILY_API_KEY:      str = _env("TAVILY_API_KEY")          # web search
    NEWS_API_KEY:        str = _env("NEWS_API_KEY")            # news headlines
    OPENWEATHER_API_KEY: str = _env("OPENWEATHER_API_KEY")     # weather
    ALPHA_VANTAGE_KEY:   str = _env("ALPHA_VANTAGE_KEY")       # stock prices
    YOUTUBE_API_KEY:     str = _env("YOUTUBE_API_KEY")         # youtube

    # ── LiveKit ───────────────────────────────────────────────────────────
    LIVEKIT_URL:        str = _env("LIVEKIT_URL")
    LIVEKIT_API_KEY:    str = _env("LIVEKIT_API_KEY")
    LIVEKIT_API_SECRET: str = _env("LIVEKIT_API_SECRET")

    # ── Sarvam STT ────────────────────────────────────────────────────────
    # SARVAM_API_KEY already declared above (shared for STT + TTS)

    # ── RAG ───────────────────────────────────────────────────────────────
    CHROMA_DOCS_PATH:   str  = _env("CHROMA_DOCS_PATH",   str(_ROOT/"data"/"chroma_db"/"documents"))
    CHROMA_MEMORY_PATH: str  = _env("CHROMA_MEMORY_PATH", str(_ROOT/"data"/"chroma_db"/"memory"))
    RAG_CHUNK_SIZE:     int  = _int("RAG_CHUNK_SIZE",  500)
    RAG_OVERLAP:        int  = _int("RAG_OVERLAP",      50)
    RAG_TOP_K:          int  = _int("RAG_TOP_K",         5)
    EMBED_MODEL:        str  = _env("EMBED_MODEL", "all-MiniLM-L6-v2")

    # ── Voice ─────────────────────────────────────────────────────────────
    SAMPLE_RATE:        int   = _int("SAMPLE_RATE", 16000)
    SILENCE_THRESHOLD:  float = _float("SILENCE_THRESHOLD", 0.01)
    SILENCE_DURATION:   float = _float("SILENCE_DURATION", 1.5)
    WAKE_WORD:          str   = _env("WAKE_WORD", "hey sia")
    MAX_RECORD_SECONDS: int   = _int("MAX_RECORD_SECONDS", 30)
    WHISPER_MODEL:      str   = _env("WHISPER_MODEL", "small")
    WHISPER_LANGUAGE:   str   = _env("WHISPER_LANGUAGE", "auto")
    WHISPER_FP16:       bool  = _bool("WHISPER_FP16", False)
    TTS_ENGINE:         str   = _env("TTS_ENGINE", "sarvam")   # changed: elevenlabs → sarvam

    # ── Memory ────────────────────────────────────────────────────────────
    MAX_HISTORY:  int = _int("MAX_HISTORY", 20)
    HISTORY_PATH: str = _env("HISTORY_PATH", str(_ROOT/"data"/"history.json"))

    # ── API server ────────────────────────────────────────────────────────
    HOST: str = _env("HOST", "127.0.0.1")
    PORT: int = _int("PORT", 8000)
    CORS_ORIGINS: list[str] = [
        o.strip() for o in
        _env("CORS_ORIGINS","http://localhost:3000,http://localhost:5173").split(",")
        if o.strip()
    ]

    # ── System ────────────────────────────────────────────────────────────
    PLATFORM:           str  = _env("SIA_OS", _os())
    SCREENSHOT_DIR:     str  = _env("SCREENSHOT_DIR", str(Path.home()/"Desktop"))
    VOICE_ENABLED:      bool = _bool("VOICE_ENABLED", False)
    WAKEWORD_ENABLED:   bool = _bool("WAKEWORD_ENABLED", True)
    PLAYWRIGHT_ENABLED: bool = _bool("PLAYWRIGHT_ENABLED", True)


    # ── Agent engine (v4) ────────────────────────────────────────────────
    # SIA_ENGINE=graph  → LangGraph supervisor + tools + permission gate (default)
    # SIA_ENGINE=legacy → original regex + JSON-plan router (kept as fallback)
    SIA_ENGINE:        str   = _env("SIA_ENGINE", "graph")
    USER_NAME:         str   = _env("USER_NAME", "Ved")
    # Model routing: chat (fast) vs planner (tool calling) vs vision (screen)
    CHAT_PROVIDER:     str   = _env("CHAT_PROVIDER", "groq")
    CHAT_MODEL:        str   = _env("CHAT_MODEL", "llama-3.3-70b-versatile")
    PLANNER_PROVIDER:  str   = _env("PLANNER_PROVIDER", "openai")
    PLANNER_MODEL:     str   = _env("PLANNER_MODEL", "gpt-4o-mini")
    VISION_PROVIDER:   str   = _env("VISION_PROVIDER", "openai")
    VISION_MODEL:      str   = _env("VISION_MODEL", "gpt-4o-mini")
    PLANNER_MAX_TOKENS: int  = _int("PLANNER_MAX_TOKENS", 1500)
    AGENT_MAX_STEPS:   int   = _int("AGENT_MAX_STEPS", 25)     # tool calls per sub-agent
    TASK_MAX_REPLANS:  int   = _int("TASK_MAX_REPLANS", 2)
    CHAT_WAIT_SECONDS: float = _float("CHAT_WAIT_SECONDS", 45)  # /api/chat waits this long, then returns task id

    # ── Storage ──────────────────────────────────────────────────────────
    DB_PATH:           str   = _env("DB_PATH", str(_ROOT/"data"/"sia.db"))
    CHECKPOINT_PATH:   str   = _env("CHECKPOINT_PATH", str(_ROOT/"data"/"checkpoints.db"))
    WORKSPACE_DIR:     str   = _env("WORKSPACE_DIR", str(_ROOT/"data"/"workspace"))

    # ── Permissions ──────────────────────────────────────────────────────
    # Highest risk level that runs without asking. 1=read-only, 2=low-risk reversible.
    AUTO_APPROVE_LEVEL: int  = _int("AUTO_APPROVE_LEVEL", 2)
    # Level 4 (destructive) actions: "confirm" (typed confirmation) or "block"
    LEVEL4_POLICY:     str   = _env("LEVEL4_POLICY", "confirm")
    APPROVAL_TIMEOUT_MIN: int = _int("APPROVAL_TIMEOUT_MIN", 60)
    # Folders SIA may read/write (';'-separated). Defaults: home Desktop/Documents/Downloads + workspace
    ALLOWED_ROOTS:     str   = _env("ALLOWED_ROOTS", "")
    # Extra shell commands (first word) allowed for the dev agent, ';'-separated
    EXTRA_ALLOWED_COMMANDS: str = _env("EXTRA_ALLOWED_COMMANDS", "")

    # ── Browser (Playwright) ─────────────────────────────────────────────
    BROWSER_PROFILE_DIR: str = _env("BROWSER_PROFILE_DIR", str(_ROOT/"data"/"browser_profile"))
    BROWSER_CHANNEL:   str   = _env("BROWSER_CHANNEL", "chrome")   # chrome | msedge | chromium
    BROWSER_HEADLESS:  bool  = _bool("BROWSER_HEADLESS", False)
    DOWNLOAD_DIR:      str   = _env("DOWNLOAD_DIR", str(Path.home()/"Downloads"))

    # ── Optional voice transport ─────────────────────────────────────────
    LIVEKIT_ENABLED:   bool  = _bool("LIVEKIT_ENABLED", False)

    def validate(self):
        from backend.core.logger import get_logger
        log = get_logger("config")

        # ── LLM key check + auto-fallback chain ───────────────────────────
        if not any([self.ANTHROPIC_API_KEY, self.GROQ_API_KEY,
                    self.OPENAI_API_KEY, self.GOOGLE_API_KEY]):
            log.error("No LLM API key found. Add GROQ_API_KEY to .env (free at console.groq.com)")
            sys.exit(1)

        # Auto-select best available provider
        if self.LLM_PROVIDER == "groq" and not self.GROQ_API_KEY:
            if self.GOOGLE_API_KEY:
                log.warning("GROQ_API_KEY missing — falling back to gemini")
                self.LLM_PROVIDER = "google"
                self.LLM_MODEL    = "gemini-2.0-flash"
            elif self.OPENAI_API_KEY:
                log.warning("GROQ_API_KEY missing — falling back to openai")
                self.LLM_PROVIDER = "openai"
                self.LLM_MODEL    = "gpt-4o-mini"

        # ── TTS check ─────────────────────────────────────────────────────
        if self.TTS_ENGINE == "sarvam" and not self.SARVAM_API_KEY:
            log.warning("SARVAM_API_KEY not set — TTS will use Edge TTS fallback")

        log.info(
            "Config OK  provider=%s  model=%s  tts=%s  platform=%s",
            self.LLM_PROVIDER, self.LLM_MODEL, self.TTS_ENGINE, self.PLATFORM,
        )

    def dump(self):
        return {
            "llm_provider": self.LLM_PROVIDER,
            "llm_model":    self.LLM_MODEL,
            "platform":     self.PLATFORM,
            "voice":        self.VOICE_ENABLED,
            "tts":          self.TTS_ENGINE,
            "port":         self.PORT,
        }

settings = Settings()
