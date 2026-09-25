"""Central configuration and filesystem paths for Jarvis.

Everything that needs to know *where* things live imports from here, so there is
a single source of truth for the project layout. Paths are derived relative to
this file, so the project can be moved or cloned without editing anything.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Project layout
# ---------------------------------------------------------------------------
# config.py lives at:  <root>/src/jarvis/config.py
# so the project root is three parents up.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (no dependency).

    Real environment variables always win, so nothing here overrides a value the
    user has already exported. Malformed lines are skipped quietly.
    """
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


# Load secrets (Discord token, etc.) from <root>/.env before reading settings.
# The .env file is git-ignored; secrets never enter the repo.
_load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR: Path = PROJECT_ROOT / "data"
BACKUP_DIR: Path = DATA_DIR / "backups"
NOTES_DIR: Path = DATA_DIR / "notes"
LOG_DIR: Path = PROJECT_ROOT / "logs"
# Large model files (STT/TTS weights). Git-ignored; downloaded on first use.
MODELS_DIR: Path = DATA_DIR / "models"

# The durable task database. Override with JARVIS_DB env var (handy for tests).
DB_PATH: Path = Path(os.environ.get("JARVIS_DB", DATA_DIR / "jarvis.db"))

# Human-readable mirror of the task list. This is written on every change so a
# real, openable record of your tasks exists even if nothing is running.
TASKS_MIRROR_PATH: Path = NOTES_DIR / "tasks.md"

# Keep this many timestamped database backups before pruning the oldest.
BACKUP_RETENTION: int = 30


# ---------------------------------------------------------------------------
# Local LLM ("brain") runtime
# ---------------------------------------------------------------------------
# Jarvis talks to a local model over an OpenAI-compatible API. Any runtime that
# speaks that protocol works, so the brain is swappable without touching code:
#   LM Studio -> http://localhost:1234/v1   (default)
#   Ollama    -> http://localhost:11434/v1
# Override any of these at runtime with the matching JARVIS_* env var.
LLM_BASE_URL: str = os.environ.get("JARVIS_LLM_BASE_URL", "http://localhost:1234/v1")

# The daily-driver model. Qwen3-8B was chosen over 14B for VRAM headroom and
# ~2x speed on the 12 GB GPU (see HANDOFF.md §"model"). This base id resolves
# regardless of any load-instance suffix LM Studio appends (e.g. ":2").
LLM_MODEL: str = os.environ.get("JARVIS_LLM_MODEL", "qwen/qwen3-8b")

# OpenAI-compatible servers require *some* API key; LM Studio and Ollama ignore
# its value, so a placeholder is fine. A real key (if ever needed) goes in .env.
LLM_API_KEY: str = os.environ.get("JARVIS_LLM_API_KEY", "lm-studio")

# Seconds to wait on a single generation before giving up.
LLM_TIMEOUT: float = float(os.environ.get("JARVIS_LLM_TIMEOUT", "120"))


# ---------------------------------------------------------------------------
# Discord (task capture + reminders that reach your phone)
# ---------------------------------------------------------------------------
# The bot token is a secret; it lives in <root>/.env (git-ignored), never here.
DISCORD_TOKEN: str = os.environ.get("DISCORD_TOKEN", "")

# The user Jarvis reports to. If left blank, the bot learns it from the first
# person to DM it and remembers it (stored in the DB's meta table).
DISCORD_OWNER_ID: str = os.environ.get("DISCORD_OWNER_ID", "")

# How often the bot checks for due reminders to deliver over Discord (seconds).
DISCORD_REMINDER_INTERVAL: float = float(
    os.environ.get("JARVIS_DISCORD_REMINDER_INTERVAL", "30")
)


# ---------------------------------------------------------------------------
# Voice (speech-to-text + text-to-speech, both on the CPU so the GPU stays
# dedicated to the LLM)
# ---------------------------------------------------------------------------
# faster-whisper model size. base.en is a good speed/accuracy balance on a
# strong CPU; use small.en for more accuracy, tiny.en for more speed.
STT_MODEL: str = os.environ.get("JARVIS_STT_MODEL", "base.en")

# Kokoro TTS model files (downloaded into MODELS_DIR on first run).
KOKORO_MODEL_PATH: Path = MODELS_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH: Path = MODELS_DIR / "voices-v1.0.bin"
# Voice id. British male suits a "Jarvis"; swap for any Kokoro voice.
KOKORO_VOICE: str = os.environ.get("JARVIS_KOKORO_VOICE", "bm_george")
KOKORO_SPEED: float = float(os.environ.get("JARVIS_KOKORO_SPEED", "1.0"))

# Silence detection for the "listen until you stop talking" recorder.
VOICE_SILENCE_SECONDS: float = float(os.environ.get("JARVIS_VOICE_SILENCE_SECONDS", "1.2"))


def ensure_dirs() -> None:
    """Create every directory Jarvis writes to. Safe to call repeatedly."""
    for d in (DATA_DIR, BACKUP_DIR, NOTES_DIR, LOG_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
