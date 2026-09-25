"""Task domain layer: create, query, and update tasks + record their history.

Every mutating function also writes an immutable row into ``task_events`` and
refreshes the human-readable markdown mirror, so the durable record and the
readable record never drift from the database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from jarvis.memory import db

# Task lifecycle. Kept as plain strings for easy storage and Discord display.
STATUS_INBOX = "inbox"              # captured, not yet started
STATUS_ACTIVE = "active"            # Jarvis is working it
STATUS_WAITING = "waiting_approval" # needs your sign-off before it acts
STATUS_BLOCKED = "blocked"          # stuck, needs input/info
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

OPEN_STATUSES = (STATUS_INBOX, STATUS_ACTIVE, STATUS_WAITING, STATUS_BLOCKED)
ALL_STATUSES = OPEN_STATUSES + (STATUS_DONE, STATUS_CANCELLED)

PRIORITIES = ("low", "normal", "high")


def now_iso() -> str:
    """Current time as a UTC ISO-8601 string (the storage format for all times)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Task:
    id: int
    title: str
    details: str
    status: str
    priority: str
    next_action: str
    source: str
    created_at: str
    updated_at: str
    due_at: Optional[str]
    remind_at: Optional[str]
    reminded_at: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        return cls(**{k: row[k] for k in row.keys()})


def _log(conn: sqlite3.Connection, task_id: int, kind: str, detail: str = "") -> None:
    """Append an immutable event to the audit log."""
    conn.execute(
        "INSERT INTO task_events(task_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
        (task_id, now_iso(), kind, detail),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def add_task(
    title: str,
    details: str = "",
    *,
    priority: str = "normal",
    due_at: Optional[str] = None,
    remind_at: Optional[str] = None,
    source: str = "cli",
) -> Task:
    """Create a new task, log its creation, and refresh the mirror."""
    if not title.strip():
        raise ValueError("Task title cannot be empty.")
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")

    ts = now_iso()
    with db.transaction() as conn:
        cur = conn.execute(
            """INSERT INTO tasks
               (title, details, status, priority, next_action, source,
                created_at, updated_at, due_at, remind_at)
               VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?)""",
            (title.strip(), details.strip(), STATUS_INBOX, priority, source,
             ts, ts, due_at, remind_at),
        )
        task_id = int(cur.lastrowid)
        _log(conn, task_id, "created", title.strip())
        if due_at:
            _log(conn, task_id, "due_set", due_at)
        if remind_at:
            _log(conn, task_id, "reminder_set", remind_at)
        task = _get(conn, task_id)

    _refresh_mirror()
    return task


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def _get(conn: sqlite3.Connection, task_id: int) -> Task:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(f"No task with id {task_id}")
    return Task.from_row(row)


def get_task(task_id: int) -> Task:
    with db.transaction() as conn:
        return _get(conn, task_id)


def list_tasks(*, status: Optional[str] = None, include_closed: bool = False) -> list[Task]:
    """List tasks, open-only by default, newest-updated first."""
    query = "SELECT * FROM tasks"
    params: list[object] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    elif not include_closed:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        query += f" WHERE status IN ({placeholders})"
        params.extend(OPEN_STATUSES)
    query += " ORDER BY updated_at DESC"

    with db.transaction() as conn:
        rows = conn.execute(query, params).fetchall()
    return [Task.from_row(r) for r in rows]


def get_events(task_id: int) -> list[sqlite3.Row]:
    with db.transaction() as conn:
        return conn.execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
def _touch(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now_iso(), task_id))


def set_status(task_id: int, status: str) -> Task:
    if status not in ALL_STATUSES:
        raise ValueError(f"status must be one of {ALL_STATUSES}")
    with db.transaction() as conn:
        old = _get(conn, task_id)
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        _touch(conn, task_id)
        _log(conn, task_id, "status_change", f"{old.status} -> {status}")
        task = _get(conn, task_id)
    _refresh_mirror()
    return task


def add_note(task_id: int, note: str) -> None:
    """Attach a freeform working note (stored as an immutable event)."""
    if not note.strip():
        return
    with db.transaction() as conn:
        _get(conn, task_id)  # existence check
        _touch(conn, task_id)
        _log(conn, task_id, "note", note.strip())
    _refresh_mirror()


def set_next_action(task_id: int, next_action: str) -> Task:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET next_action = ? WHERE id = ?", (next_action.strip(), task_id)
        )
        _touch(conn, task_id)
        _log(conn, task_id, "next_action", next_action.strip())
        task = _get(conn, task_id)
    _refresh_mirror()
    return task


def set_reminder(task_id: int, remind_at: Optional[str]) -> Task:
    """Set (or clear, with None) when Jarvis should nudge you about this task."""
    with db.transaction() as conn:
        # Clearing/resetting the reminder makes it eligible to fire again.
        conn.execute(
            "UPDATE tasks SET remind_at = ?, reminded_at = NULL WHERE id = ?",
            (remind_at, task_id),
        )
        _touch(conn, task_id)
        _log(conn, task_id, "reminder_set", remind_at or "(cleared)")
        task = _get(conn, task_id)
    _refresh_mirror()
    return task


def set_due(task_id: int, due_at: Optional[str]) -> Task:
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET due_at = ? WHERE id = ?", (due_at, task_id))
        _touch(conn, task_id)
        _log(conn, task_id, "due_set", due_at or "(cleared)")
        task = _get(conn, task_id)
    _refresh_mirror()
    return task


# ---------------------------------------------------------------------------
# Reminders (used by the background worker later)
# ---------------------------------------------------------------------------
def due_reminders(*, at: Optional[str] = None) -> list[Task]:
    """Open tasks whose reminder time has passed and haven't been reminded yet."""
    at = at or now_iso()
    with db.transaction() as conn:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        rows = conn.execute(
            f"""SELECT * FROM tasks
                WHERE remind_at IS NOT NULL
                  AND remind_at <= ?
                  AND reminded_at IS NULL
                  AND status IN ({placeholders})
                ORDER BY remind_at""",
            (at, *OPEN_STATUSES),
        ).fetchall()
    return [Task.from_row(r) for r in rows]


def mark_reminded(task_id: int) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET reminded_at = ? WHERE id = ?", (now_iso(), task_id)
        )
        _log(conn, task_id, "reminder_sent", "")


# Imported lazily to avoid a circular import at module load time.
def _refresh_mirror() -> None:
    from jarvis.memory import mirror
    mirror.write_mirror()
