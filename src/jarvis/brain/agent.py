"""Natural-language task capture: turn a plain-English message into real actions.

Jarvis reads a message ("remind me to call the dentist next Tuesday at 2pm"),
lets the local model decide which task tool to call (the tool-calling we
benchmarked), executes it against the durable task store, and returns a short
human confirmation.

Shared by the Discord bot and the ``jarvis capture`` command so both behave
identically. The confirmation text is built here, deterministically, from what
actually happened - not from a second model call - so the reply always matches
the real state of the task list.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from jarvis.brain import client as brain
from jarvis.memory import tasks as T
from jarvis.timeparse import parse_when

log = logging.getLogger("jarvis.agent")


# The tools the model may call. Kept small and unambiguous so an 8B model picks
# reliably. Time fields accept the human formats jarvis.timeparse understands.
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Add and start tracking a new task or reminder for the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short description of the task"},
                    "due": {
                        "type": "string",
                        "description": "Optional deadline. Relative ('2h','3d','1w') or absolute 'YYYY-MM-DD HH:MM'.",
                    },
                    "remind": {
                        "type": "string",
                        "description": "Optional time to nudge the user. Same formats as due.",
                    },
                    "priority": {"type": "string", "enum": ["low", "normal", "high"]},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List the user's current open tasks.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark a task as done, by its numeric id.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "The #id of the task"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "Attach a note to an existing task, by its numeric id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "note": {"type": "string"},
                },
                "required": ["task_id", "note"],
            },
        },
    },
]


def _system_prompt() -> str:
    now = datetime.now().astimezone()
    return (
        "You are Jarvis, a personal task assistant running on the user's PC. "
        "Read the user's message and call the single most appropriate tool. "
        f"Right now it is {now:%A, %Y-%m-%d %H:%M} local time. "
        "For 'due' and 'remind', copy the user's own time wording verbatim "
        "(e.g. 'next Friday', 'in 3 days', 'tomorrow at 2pm', '2026-10-01 14:30'). "
        "Do NOT convert or do date math yourself - the system resolves the phrase "
        "reliably. If the user is only chatting and there is nothing to do, reply "
        "briefly without calling a tool."
    )


def _fmt(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%a %b %d, %H:%M")
    except (ValueError, TypeError):
        return ts


# ---------------------------------------------------------------------------
# Tool handlers - each returns the confirmation line(s) to show the user.
# ---------------------------------------------------------------------------
def _h_add_task(args: dict[str, Any]) -> str:
    title = str(args.get("title", "")).strip()
    if not title:
        return "I couldn't tell what the task was - can you rephrase?"

    warnings: list[str] = []
    parsed: dict[str, str | None] = {"due": None, "remind": None}
    for field in ("due", "remind"):
        raw = args.get(field)
        if raw and str(raw).strip():
            try:
                parsed[field] = parse_when(str(raw))
            except ValueError:
                warnings.append(f"couldn't read {field} '{raw}'")

    priority = str(args.get("priority", "normal")).lower()
    if priority not in T.PRIORITIES:
        priority = "normal"

    task = T.add_task(
        title,
        priority=priority,
        due_at=parsed["due"],
        remind_at=parsed["remind"],
        source="agent",
    )

    bits = [f"✅ Added **#{task.id}**: {task.title}"]
    if task.due_at:
        bits.append(f"due {_fmt(task.due_at)}")
    if task.remind_at:
        bits.append(f"remind {_fmt(task.remind_at)}")
    if priority == "high":
        bits.append("priority: high")
    line = " · ".join(bits)
    if warnings:
        line += f"\n_({'; '.join(warnings)} - set it with e.g. 'remind me in 2h')_"
    return line


def _h_list_tasks(_args: dict[str, Any]) -> str:
    items = T.list_tasks(include_closed=False)
    if not items:
        return "You have no open tasks. ✨"
    lines = ["**Your open tasks:**"]
    for t in items:
        extra = []
        if t.due_at:
            extra.append(f"due {_fmt(t.due_at)}")
        if t.remind_at and not t.reminded_at:
            extra.append(f"remind {_fmt(t.remind_at)}")
        tail = f" ({', '.join(extra)})" if extra else ""
        flag = "🔴 " if t.priority == "high" else ""
        lines.append(f"• #{t.id} {flag}{t.title}{tail}")
    return "\n".join(lines)


def _h_complete_task(args: dict[str, Any]) -> str:
    task_id = int(args["task_id"])
    t = T.set_status(task_id, T.STATUS_DONE)
    return f"✅ Marked **#{t.id}** done: {t.title}"


def _h_add_note(args: dict[str, Any]) -> str:
    task_id = int(args["task_id"])
    note = str(args.get("note", "")).strip()
    T.add_note(task_id, note)
    return f"📝 Noted on **#{task_id}**."


_HANDLERS = {
    "add_task": _h_add_task,
    "list_tasks": _h_list_tasks,
    "complete_task": _h_complete_task,
    "add_note": _h_add_note,
}


def _dispatch(tool_call: dict[str, Any]) -> str:
    fn = tool_call.get("function", {}) or {}
    name = fn.get("name", "")
    raw_args = fn.get("arguments", "{}")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except (json.JSONDecodeError, TypeError):
        args = {}

    handler = _HANDLERS.get(name)
    if handler is None:
        return f"(I tried to use an unknown tool '{name}'.)"
    try:
        return handler(args)
    except KeyError as e:  # e.g. no such task id
        return f"⚠️ I couldn't find that: {e}"
    except Exception as e:  # never crash the caller over one bad tool call
        log.exception("tool %s failed", name)
        return f"⚠️ Something went wrong doing that ({e})."


def handle(message: str) -> str:
    """Interpret one user message, act on it, and return a reply string."""
    message = (message or "").strip()
    if not message:
        return "Say something and I'll help."
    try:
        res = brain.chat(
            [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": message},
            ],
            tools=TOOLS,
            max_tokens=512,
            temperature=0,
        )
    except brain.BrainError as e:
        return f"⚠️ My brain is offline right now ({e})."

    if not res.tool_calls:
        return res.content or "Okay."

    replies = [_dispatch(tc) for tc in res.tool_calls]
    return "\n".join(r for r in replies if r) or "Done."
