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


def ensure_dirs() -> None:
    """Create every directory Jarvis writes to. Safe to call repeatedly."""
    for d in (DATA_DIR, BACKUP_DIR, NOTES_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
