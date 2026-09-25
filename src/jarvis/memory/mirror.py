"""Human-readable mirror + database backups.

The SQLite file is the source of truth, but a binary DB is useless if you just
want to *glance* at your tasks, or if something ever goes wrong with the app.
So on every change we also render a plain-markdown snapshot of all open tasks to
``data/notes/tasks.md`` - openable in any editor, no software required - and we
keep timestamped copies of the database in ``data/backups``.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone

from jarvis import config
from jarvis.memory import db, tasks as tasks_mod


def _fmt(ts: str | None) -> str:
    """Render a stored UTC ISO timestamp in the local timezone for humans."""
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


def write_mirror() -> None:
    """Render all open tasks (plus recently closed ones) to tasks.md."""
    config.ensure_dirs()

    open_tasks = tasks_mod.list_tasks(include_closed=False)
    lines: list[str] = []
    lines.append("# Jarvis - Task List")
    lines.append("")
    lines.append(f"_Auto-generated mirror. Source of truth: `{config.DB_PATH.name}`._")
    lines.append(f"_Last updated: {_fmt(tasks_mod.now_iso())} (local)._")
    lines.append("")

    if not open_tasks:
        lines.append("No open tasks. ✨")
    else:
        # Group by status so the most actionable buckets are visible at a glance.
        for status in tasks_mod.OPEN_STATUSES:
            bucket = [t for t in open_tasks if t.status == status]
            if not bucket:
                continue
            lines.append(f"## {status.replace('_', ' ').title()} ({len(bucket)})")
            lines.append("")
            for t in bucket:
                flag = {"high": "\U0001f534 ", "low": "⚪ ", "normal": ""}.get(t.priority, "")
                lines.append(f"- **[#{t.id}] {flag}{t.title}**")
                if t.details:
                    lines.append(f"    - {t.details}")
                if t.next_action:
                    lines.append(f"    - Next: {t.next_action}")
                if t.due_at:
                    lines.append(f"    - Due: {_fmt(t.due_at)}")
                if t.remind_at:
                    reminded = " (sent)" if t.reminded_at else ""
                    lines.append(f"    - Remind: {_fmt(t.remind_at)}{reminded}")
            lines.append("")

    config.TASKS_MIRROR_PATH.write_text("\n".join(lines), encoding="utf-8")


def backup_db(tag: str = "") -> str | None:
    """Copy the live DB to a timestamped file in data/backups. Returns its path.

    Uses SQLite's online backup API so it is safe to run while the DB is in use.
    Prunes to config.BACKUP_RETENTION most recent backups.
    """
    if not config.DB_PATH.exists():
        return None
    config.ensure_dirs()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = f"-{tag}" if tag else ""
    dest = config.BACKUP_DIR / f"jarvis-{stamp}{suffix}.db"

    src = db.connect()
    try:
        with src:
            dst = __import__("sqlite3").connect(str(dest))
            try:
                src.backup(dst)
            finally:
                dst.close()
    finally:
        src.close()

    _prune_backups()
    return str(dest)


def _prune_backups() -> None:
    backups = sorted(config.BACKUP_DIR.glob("jarvis-*.db"))
    excess = len(backups) - config.BACKUP_RETENTION
    for old in backups[:max(0, excess)]:
        try:
            old.unlink()
        except OSError:
            pass
