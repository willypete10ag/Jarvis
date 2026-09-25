"""Jarvis' long-term and working memory.

Two tiers, per the agreed design:

- **Permanent memory** (``memories`` table): durable facts worth keeping -
  preferences, contacts, appointment outcomes/decisions, and background context
  on tasks. Things land here by being *promoted*: Jarvis auto-saves clear facts,
  saves anything you explicitly tell it to, and asks first when it is unsure.

- **Working memory** (``working_memory`` table): the running conversation, so
  follow-ups ("move that to Friday") make sense. It is scoped to the current day
  and wiped automatically once the day rolls over.

Everything lives in the same local SQLite database as the tasks, so it is just as
durable and just as private (never leaves the machine).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from jarvis.memory import db

CATEGORIES = ("preference", "contact", "outcome", "task_context", "fact")

# The memory tables were added in schema v2; make sure they exist before use
# (callers like `jarvis capture` don't otherwise run init_db). Cheap after once.
_initialized = False


def _ensure() -> None:
    global _initialized
    if not _initialized:
        db.init_db()
        _initialized = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().date().isoformat()


@dataclass
class Memory:
    id: int
    category: str
    subject: str
    content: str
    task_id: Optional[int]
    source: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Memory":
        return cls(**{k: row[k] for k in row.keys()})


# ---------------------------------------------------------------------------
# Permanent memory
# ---------------------------------------------------------------------------
def remember(
    content: str,
    *,
    category: str = "fact",
    subject: str = "",
    task_id: Optional[int] = None,
    source: str = "agent",
) -> Memory:
    """Save a durable fact and return it."""
    content = (content or "").strip()
    if not content:
        raise ValueError("memory content cannot be empty")
    if category not in CATEGORIES:
        category = "fact"
    _ensure()
    ts = _now_iso()
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO memories(category, subject, content, task_id, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (category, subject.strip(), content, task_id, source, ts, ts),
        )
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (cur.lastrowid,)).fetchone()
    return Memory.from_row(row)


def list_memories(*, category: Optional[str] = None) -> list[Memory]:
    _ensure()
    with db.transaction() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM memories WHERE category = ? ORDER BY id", (category,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM memories ORDER BY id").fetchall()
    return [Memory.from_row(r) for r in rows]


def search_memories(query: str) -> list[Memory]:
    _ensure()
    q = f"%{query.strip()}%"
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM memories WHERE content LIKE ? OR subject LIKE ? ORDER BY id",
            (q, q),
        ).fetchall()
    return [Memory.from_row(r) for r in rows]


def forget(memory_id: int) -> bool:
    """Delete one memory by id. Returns True if something was removed."""
    _ensure()
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cur.rowcount > 0


def forget_matching(query: str) -> int:
    """Delete every memory matching ``query``. Returns how many were removed."""
    _ensure()
    q = f"%{query.strip()}%"
    with db.transaction() as conn:
        cur = conn.execute(
            "DELETE FROM memories WHERE content LIKE ? OR subject LIKE ?", (q, q)
        )
        return cur.rowcount


def recall_block(limit: int = 50) -> str:
    """All permanent memories, formatted for injection into the model's prompt."""
    mems = list_memories()
    if not mems:
        return ""
    lines = []
    for m in mems[-limit:]:
        subj = f"{m.subject}: " if m.subject else ""
        lines.append(f"- ({m.category}) {subj}{m.content}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Working (daily) memory
# ---------------------------------------------------------------------------
def _wipe_stale(conn: sqlite3.Connection) -> None:
    """Drop working-memory rows from previous days (the automatic daily wipe)."""
    conn.execute("DELETE FROM working_memory WHERE day <> ?", (_today(),))


def add_turn(role: str, content: str, *, channel: str = "") -> None:
    content = (content or "").strip()
    if not content:
        return
    _ensure()
    with db.transaction() as conn:
        _wipe_stale(conn)
        conn.execute(
            "INSERT INTO working_memory(ts, day, role, channel, content) VALUES (?, ?, ?, ?, ?)",
            (_now_iso(), _today(), role, channel, content),
        )


def recent_turns(limit: int = 10) -> list[dict[str, str]]:
    """Today's most recent conversation turns, oldest-first (for prompt context)."""
    _ensure()
    with db.transaction() as conn:
        _wipe_stale(conn)
        rows = conn.execute(
            "SELECT role, content FROM working_memory WHERE day = ? ORDER BY id DESC LIMIT ?",
            (_today(), limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def wipe_working() -> int:
    """Clear all working memory now. Returns how many turns were removed."""
    _ensure()
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM working_memory")
        return cur.rowcount
