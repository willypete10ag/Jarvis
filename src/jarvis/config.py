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

DATA_DIR: Path = PROJECT_ROOT / "data"
BACKUP_DIR: Path = DATA_DIR / "backups"
NOTES_DIR: Path = DATA_DIR / "notes"
LOG_DIR: Path = PROJECT_ROOT / "logs"

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


def ensure_dirs() -> None:
    """Create every directory Jarvis writes to. Safe to call repeatedly."""
    for d in (DATA_DIR, BACKUP_DIR, NOTES_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
