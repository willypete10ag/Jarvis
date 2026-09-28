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

# Jarvis's persona / system prompt. This plain-text file (editable by hand) sets
# who Jarvis is, what he does, and how he speaks. If it's missing, a baked-in
# default is used. Override the location with JARVIS_PERSONA.
PERSONA_PATH: Path = Path(os.environ.get("JARVIS_PERSONA", PROJECT_ROOT / "persona.md"))

# Human-readable mirror of the task list. This is written on every change so a
# real, openable record of your tasks exists even if nothing is running.
TASKS_MIRROR_PATH: Path = NOTES_DIR / "tasks.md"

# Keep this many timestamped database backups before pruning the oldest.
BACKUP_RETENTION: int = 30


# ---------------------------------------------------------------------------
# The "brain": Claude, via the Anthropic API
# ---------------------------------------------------------------------------
# Jarvis used to run a local model (Qwen3-8B) over an OpenAI-compatible API.
# It now uses Claude through the `anthropic` SDK: much smarter, much faster, and
# no local GPU. The model is swappable without touching code - just set
# JARVIS_LLM_MODEL. Billing is pay-per-token via console.anthropic.com (a Claude
# Pro/Max subscription does NOT cover API usage - it's separate).

# API credentials. The key is a secret and lives in <root>/.env (git-ignored).
# `ANTHROPIC_API_KEY` is the SDK's standard name, so it's read directly.
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

# Optional. Only needed if your key is ORG-scoped rather than workspace-scoped
# (the API then rejects requests with "not scoped to a workspace"). Either set
# this to your workspace id (looks like `wrkspc_...`, from the console URL when
# viewing the workspace), or - simpler - create a workspace-scoped API key and
# leave this blank. When set, it's sent as the anthropic-workspace-id header.
ANTHROPIC_WORKSPACE_ID: str = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")

# The daily-driver model. Haiku 4.5 is the default: fastest + cheapest, which
# matters most for a real-time voice loop, and still far more capable than the
# old local model. Bump to `claude-sonnet-5` for more reasoning power, or
# `claude-opus-5` for maximum intelligence (both slower/pricier).
LLM_MODEL: str = os.environ.get("JARVIS_LLM_MODEL", "claude-haiku-4-5")

# API base URL. Normally the Anthropic default; override only to route through a
# proxy or gateway. (Kept for display in `jarvis brain --health`.)
LLM_BASE_URL: str = os.environ.get("JARVIS_LLM_BASE_URL", "https://api.anthropic.com")

# Seconds to wait on a single generation before giving up.
LLM_TIMEOUT: float = float(os.environ.get("JARVIS_LLM_TIMEOUT", "120"))


# ---------------------------------------------------------------------------
# Homelab connectors (Plex / *ARR media stack + system monitoring)
# ---------------------------------------------------------------------------
# Each service is optional: a tool is "live" only when its URL (and API key,
# where needed) are set, otherwise Jarvis reports it as not configured. Keys are
# secrets -> put them in <root>/.env. URLs default to the Pi's hostname; change
# JARVIS_*_URL if a service moves.
_PI = os.environ.get("JARVIS_PI_HOST", "raspberrypi.local")

SONARR_URL: str = os.environ.get("JARVIS_SONARR_URL", f"http://{_PI}:8989")
SONARR_API_KEY: str = os.environ.get("SONARR_API_KEY", "")
RADARR_URL: str = os.environ.get("JARVIS_RADARR_URL", f"http://{_PI}:7878")
RADARR_API_KEY: str = os.environ.get("RADARR_API_KEY", "")
PROWLARR_URL: str = os.environ.get("JARVIS_PROWLARR_URL", f"http://{_PI}:9696")
PROWLARR_API_KEY: str = os.environ.get("PROWLARR_API_KEY", "")

# Plex uses an X-Plex-Token (not an API key). Get yours by following Plex's
# "finding an authentication token" guide. Left blank -> Plex tools are inert.
PLEX_URL: str = os.environ.get("JARVIS_PLEX_URL", f"http://{_PI}:32400")
PLEX_TOKEN: str = os.environ.get("PLEX_TOKEN", "")

# SABnzbd (usenet). Its API key is under Config -> General.
SAB_URL: str = os.environ.get("JARVIS_SAB_URL", f"http://{_PI}:8080")
SAB_API_KEY: str = os.environ.get("SAB_API_KEY", "")

# netdata runs on the Pi and exposes a metrics REST API (default port 19999),
# so Jarvis can read Pi CPU/RAM/temp/disk and per-container stats over HTTP -
# no SSH needed. Blank -> Pi stats are skipped.
NETDATA_URL: str = os.environ.get("JARVIS_NETDATA_URL", f"http://{_PI}:19999")

# Seconds to wait on any homelab HTTP call before giving up (keep short so a
# down service fails fast instead of hanging a voice reply).
HOMELAB_TIMEOUT: float = float(os.environ.get("JARVIS_HOMELAB_TIMEOUT", "8"))


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

# --- TTS engine selection ---
# "cartesia" = Cartesia Sonic (cloud, human-sounding, low latency; needs a key).
# "kokoro"   = local Kokoro ONNX (free/offline, but robotic).
# "auto" (default) uses Cartesia when CARTESIA_API_KEY is set, else Kokoro.
TTS_PROVIDER: str = os.environ.get("JARVIS_TTS", "auto").strip().lower()

# --- Cartesia Sonic (cloud TTS) ---
# Key is a secret -> put it in .env as CARTESIA_API_KEY. Billing is per-character
# (the free tier is plenty for personal use). Voice defaults to "Archie", an
# en-GB male voice Cartesia recommends for agents; swap via JARVIS_CARTESIA_VOICE
# (browse voices at https://play.cartesia.ai/voices).
CARTESIA_API_KEY: str = os.environ.get("CARTESIA_API_KEY", "")
CARTESIA_MODEL: str = os.environ.get("JARVIS_CARTESIA_MODEL", "sonic-3.6")
CARTESIA_VOICE_ID: str = os.environ.get(
    "JARVIS_CARTESIA_VOICE", "ef191366-f52f-447a-a398-ed8c0f2943a1"  # "Archie", en-GB male
)
CARTESIA_SAMPLE_RATE: int = int(os.environ.get("JARVIS_CARTESIA_SAMPLE_RATE", "44100"))
# API version header Cartesia requires (pin so behavior is stable).
CARTESIA_VERSION: str = os.environ.get("JARVIS_CARTESIA_VERSION", "2026-08-14")

# --- Kokoro TTS (local ONNX fallback) ---
# Model files download into MODELS_DIR on first run.
KOKORO_MODEL_PATH: Path = MODELS_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH: Path = MODELS_DIR / "voices-v1.0.bin"
# Voice id. British male suits a "Jarvis"; swap for any Kokoro voice.
KOKORO_VOICE: str = os.environ.get("JARVIS_KOKORO_VOICE", "bm_george")
KOKORO_SPEED: float = float(os.environ.get("JARVIS_KOKORO_SPEED", "1.0"))

# Silence detection for the "listen until you stop talking" recorder.
# Used as the fallback endpointing timer when Silero VAD is unavailable.
VOICE_SILENCE_SECONDS: float = float(os.environ.get("JARVIS_VOICE_SILENCE_SECONDS", "1.2"))

# Voice-activity detection (Silero VAD, ONNX) for Discord voice endpointing.
# Replaces the fixed silence timer with real speech detection, so Jarvis replies
# sooner after you stop talking and ignores non-speech noise. The ~2 MB model is
# downloaded into MODELS_DIR on first use (like Kokoro); runs on the CPU via the
# onnxruntime we already have. Set JARVIS_VAD=0 to force the timer fallback.
SILERO_VAD_PATH: Path = MODELS_DIR / "silero_vad.onnx"
VAD_ENABLED: bool = os.environ.get("JARVIS_VAD", "1") != "0"
# Speech-probability thresholds (with hysteresis: enter on START, leave on END).
VAD_START_PROB: float = float(os.environ.get("JARVIS_VAD_START_PROB", "0.5"))
VAD_END_PROB: float = float(os.environ.get("JARVIS_VAD_END_PROB", "0.35"))
# Ignore speech blips shorter than this (coughs, clicks).
VAD_MIN_SPEECH_MS: int = int(os.environ.get("JARVIS_VAD_MIN_SPEECH_MS", "250"))
# End the utterance after this much trailing silence (down from the 1.2s timer).
VAD_END_SILENCE_MS: int = int(os.environ.get("JARVIS_VAD_END_SILENCE_MS", "500"))
# Keep a little audio after the last speech so word tails aren't clipped.
VAD_SPEECH_PAD_MS: int = int(os.environ.get("JARVIS_VAD_SPEECH_PAD_MS", "150"))
# Safety cap: force-endpoint an utterance that runs this long without a pause.
VAD_MAX_UTTERANCE_S: float = float(os.environ.get("JARVIS_VAD_MAX_UTTERANCE_S", "30"))

# Voice latency: after a tool runs, phrasing the confirmation with a *second* LLM
# call is the single biggest cost on task turns. When True (default), spoken
# replies skip that call and speak a cleaned version of the deterministic
# confirmation instead - much faster, slightly less chatty. Set JARVIS_VOICE_FAST=0
# to restore the natural-language phrasing pass.
VOICE_FAST_CONFIRMATIONS: bool = os.environ.get("JARVIS_VOICE_FAST", "1") != "0"


def ensure_dirs() -> None:
    """Create every directory Jarvis writes to. Safe to call repeatedly."""
    for d in (DATA_DIR, BACKUP_DIR, NOTES_DIR, LOG_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
