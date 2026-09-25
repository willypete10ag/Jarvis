"""SQLite connection management and schema for Jarvis' durable memory.

Uses WAL (write-ahead logging) so an abrupt shutdown - power loss, a crash,
closing the laptop mid-write - cannot corrupt the database, and readers never
block the background writer.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from jarvis import config

# Schema version lets us migrate safely later without guessing the DB's shape.
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    details      TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'inbox',
    priority     TEXT    NOT NULL DEFAULT 'normal',
    next_action  TEXT    NOT NULL DEFAULT '',
    source       TEXT    NOT NULL DEFAULT 'cli',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    due_at       TEXT,
    remind_at    TEXT,
    reminded_at  TEXT
);

-- Append-only history. Rows are only ever INSERTed, never UPDATEd or DELETEd,
-- so the full life story of every task survives even if the task row changes.
CREATE TABLE IF NOT EXISTS task_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    ts       TEXT    NOT NULL,
    kind     TEXT    NOT NULL,
    detail   TEXT    NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status    ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_remind_at ON tasks(remind_at);
CREATE INDEX IF NOT EXISTS idx_events_task_id  ON task_events(task_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    """Open a tuned connection to the task database, creating it if needed."""
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Durability + concurrency pragmas.
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    """Create the schema (idempotent) and stamp the schema version."""
    with connect() as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()


def get_meta(key: str) -> str | None:
    """Read a value from the small key/value ``meta`` table (or None)."""
    with transaction() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    """Upsert a value into the ``meta`` table (worker/bot state lives here)."""
    with transaction() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Context manager yielding a connection wrapped in a single transaction.

    Commits on success, rolls back on any exception, and always closes.
    """
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
