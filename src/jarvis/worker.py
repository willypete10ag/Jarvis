"""The always-on background worker - what makes Jarvis *proactive* instead of a
tool you have to remember to run.

It ticks on a timer. Each tick it:

1. **Fires due reminders** - any open task whose reminder time has passed and
   that hasn't been reminded yet. Each fires exactly once (the DB records that it
   was sent), so a restart never re-nags you about the same thing.
2. **Writes a daily safety backup** of the database (once per calendar day).

Run it by hand while testing, or have Windows Task Scheduler launch it at logon
(see ``jarvis autostart install``):

    jarvis worker            # loop until Ctrl+C
    jarvis worker --once     # a single tick, then exit (handy for testing)
    jarvis worker --interval 30
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime

from jarvis import config, notify
from jarvis.memory import db, mirror
from jarvis.memory import tasks as T

log = logging.getLogger("jarvis.worker")

# Set by the signal handlers so the loop can exit cleanly between ticks.
_stop = False

_BACKUP_META_KEY = "last_daily_backup"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def _setup_logging() -> None:
    """Log to both the console and logs/worker.log. Idempotent."""
    config.ensure_dirs()
    root = logging.getLogger("jarvis")
    if root.handlers:  # already configured (e.g. called twice)
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    file_handler = logging.FileHandler(config.LOG_DIR / "worker.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)


def _install_signal_handlers() -> None:
    def _handle(signum, _frame):  # noqa: ANN001
        global _stop
        _stop = True
        log.info("received signal %s - shutting down after this tick", signum)

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, _handle)
            except (ValueError, OSError):
                pass  # not on the main thread / not supported here


# ---------------------------------------------------------------------------
# Small meta helpers (persist worker state in the DB's meta table)
# ---------------------------------------------------------------------------
def _get_meta(key: str) -> str | None:
    with db.transaction() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def _set_meta(key: str, value: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ---------------------------------------------------------------------------
# Tick work
# ---------------------------------------------------------------------------
def _reminder_body(t: T.Task) -> str:
    parts: list[str] = []
    if t.priority == "high":
        parts.append("[HIGH]")
    if t.due_at:
        parts.append(f"due {mirror._fmt(t.due_at)}")
    if t.next_action:
        parts.append(f"next: {t.next_action}")
    tail = ("  (" + ", ".join(parts) + ")") if parts else ""
    return f"#{t.id} {t.title}{tail}"


def fire_due_reminders(notifier: notify.Notifier) -> int:
    """Notify for every reminder that's come due, marking each as sent."""
    due = T.due_reminders()
    for t in due:
        notifier.send(f"Reminder: {t.title}", _reminder_body(t))
        T.mark_reminded(t.id)
        log.info("fired reminder for task #%s (%s)", t.id, t.title)
    return len(due)


def maybe_daily_backup() -> bool:
    """Write one backup per calendar day. Returns True if a backup was made."""
    today = datetime.now().date().isoformat()
    if _get_meta(_BACKUP_META_KEY) == today:
        return False
    path = mirror.backup_db(tag="daily")
    _set_meta(_BACKUP_META_KEY, today)
    if path:
        log.info("daily backup -> %s", path)
    return path is not None


def tick(notifier: notify.Notifier) -> int:
    """One unit of work. Returns the number of reminders fired."""
    fired = fire_due_reminders(notifier)
    maybe_daily_backup()
    return fired


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------
def _interruptible_sleep(seconds: float) -> None:
    """Sleep in 1-second slices so a stop signal is acted on promptly."""
    slept = 0.0
    while slept < seconds and not _stop:
        time.sleep(min(1.0, seconds - slept))
        slept += 1.0


def run(*, interval: float = 60.0, once: bool = False) -> int:
    """Run the worker. With ``once=True``, do a single tick and return."""
    global _stop
    _stop = False
    _setup_logging()
    db.init_db()  # ensure schema (and the meta table) exist
    _install_signal_handlers()

    notifier = notify.build_default()
    log.info("worker starting (interval=%ss, once=%s)", interval, once)

    while not _stop:
        try:
            tick(notifier)
        except Exception:
            log.exception("tick failed - continuing")
        if once:
            break
        _interruptible_sleep(interval)

    log.info("worker stopped")
    return 0
