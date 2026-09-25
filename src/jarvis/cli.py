"""Command-line interface for Jarvis' task memory.

This is the first usable surface: you can capture, list, and manage tasks right
now, before any model or Discord bot exists. The background worker and Discord
bot will call the same ``jarvis.memory.tasks`` functions underneath.

Usage examples:
    python -m jarvis add "Book dentist appointment" --due "1w" --remind "2d"
    python -m jarvis list
    python -m jarvis show 1
    python -m jarvis note 1 "Called, left voicemail"
    python -m jarvis done 1
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from jarvis import config
from jarvis.memory import db, mirror
from jarvis.memory import tasks as T
from jarvis.timeparse import parse_when


def _fmt(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


def _print_task_line(t: T.Task) -> None:
    flag = {"high": "!", "low": ".", "normal": " "}.get(t.priority, " ")
    due = f"  due {_fmt(t.due_at)}" if t.due_at else ""
    rem = f"  remind {_fmt(t.remind_at)}" if t.remind_at else ""
    print(f"[{flag}] #{t.id:<3} {t.status:<16} {t.title}{due}{rem}")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> int:
    db.init_db()
    mirror.write_mirror()
    print(f"Initialized Jarvis memory at {config.DB_PATH}")
    print(f"Readable mirror: {config.TASKS_MIRROR_PATH}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    due = parse_when(args.due) if args.due else None
    remind = parse_when(args.remind) if args.remind else None
    task = T.add_task(
        args.title,
        details=args.details or "",
        priority=args.priority,
        due_at=due,
        remind_at=remind,
        source="cli",
    )
    print(f"Added task #{task.id}: {task.title}")
    if due:
        print(f"  due:    {_fmt(due)}")
    if remind:
        print(f"  remind: {_fmt(remind)}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    items = T.list_tasks(status=args.status, include_closed=args.all)
    if not items:
        print("No tasks.")
        return 0
    for t in items:
        _print_task_line(t)
    print(f"\n{len(items)} task(s).")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        t = T.get_task(args.id)
    except KeyError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"#{t.id}  {t.title}")
    print(f"  status:   {t.status}")
    print(f"  priority: {t.priority}")
    if t.details:
        print(f"  details:  {t.details}")
    if t.next_action:
        print(f"  next:     {t.next_action}")
    print(f"  created:  {_fmt(t.created_at)}")
    print(f"  due:      {_fmt(t.due_at)}")
    print(f"  remind:   {_fmt(t.remind_at)}" + ("  (sent)" if t.reminded_at else ""))
    print("  history:")
    for ev in T.get_events(t.id):
        detail = f"  {ev['detail']}" if ev["detail"] else ""
        print(f"    {_fmt(ev['ts'])}  {ev['kind']}{detail}")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    T.add_note(args.id, args.text)
    print(f"Noted on #{args.id}.")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    T.set_next_action(args.id, args.text)
    print(f"Set next action on #{args.id}.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    t = T.set_status(args.id, args.status)
    print(f"#{t.id} -> {t.status}")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    t = T.set_status(args.id, T.STATUS_DONE)
    print(f"Completed #{t.id}: {t.title}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    t = T.set_status(args.id, T.STATUS_CANCELLED)
    print(f"Cancelled #{t.id}: {t.title}")
    return 0


def cmd_remind(args: argparse.Namespace) -> int:
    when = None if args.when.lower() in ("clear", "none") else parse_when(args.when)
    t = T.set_reminder(args.id, when)
    print(f"#{t.id} reminder: {_fmt(t.remind_at)}")
    return 0


def cmd_due(args: argparse.Namespace) -> int:
    when = None if args.when.lower() in ("clear", "none") else parse_when(args.when)
    t = T.set_due(args.id, when)
    print(f"#{t.id} due: {_fmt(t.due_at)}")
    return 0


def cmd_reminders(args: argparse.Namespace) -> int:
    due = T.due_reminders()
    if not due:
        print("No reminders currently due.")
        return 0
    print("Reminders due now:")
    for t in due:
        _print_task_line(t)
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    path = mirror.backup_db(tag="manual")
    print(f"Backup written: {path}" if path else "No database to back up yet.")
    return 0


def cmd_brain(args: argparse.Namespace) -> int:
    from jarvis.brain import client as brain

    if args.health:
        ok, info = brain.health()
        if not ok:
            print(f"Brain unreachable: {info}", file=sys.stderr)
            return 1
        print(f"Brain is up at {config.LLM_BASE_URL}")
        print(f"Configured model: {config.LLM_MODEL}")
        print("Models served:")
        for m in info:
            print(f"  {m}")
        return 0

    if not args.prompt:
        print("Error: provide a prompt, or use --health.", file=sys.stderr)
        return 1

    try:
        res = brain.ask(
            args.prompt,
            system=args.system,
            model=args.model,
            thinking=args.think,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    except brain.BrainError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(res.content)
    if args.verbose:
        if res.reasoning:
            print("\n--- reasoning ---", file=sys.stderr)
            print(res.reasoning, file=sys.stderr)
        print(
            f"\n[{res.model}] {res.completion_tokens} tok in "
            f"{res.elapsed_s:.2f}s = {res.tokens_per_second:.1f} tok/s"
            + ("  (truncated: hit max_tokens)" if res.truncated else ""),
            file=sys.stderr,
        )
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    from jarvis.brain import bench

    models = args.model or [config.LLM_MODEL]
    reports = bench.run(models, max_tokens=args.max_tokens)
    print(bench.format_report(reports))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from jarvis import worker

    return worker.run(interval=args.interval, once=args.once)


def cmd_capture(args: argparse.Namespace) -> int:
    from jarvis.brain import agent

    print(agent.handle(args.message))
    return 0


def cmd_discord(args: argparse.Namespace) -> int:
    from jarvis import discord_bot

    return discord_bot.run()


def cmd_autostart(args: argparse.Namespace) -> int:
    from jarvis import autostart

    actions = {
        "install": autostart.install,
        "remove": autostart.remove,
        "status": autostart.status,
        "start": autostart.start_now,
    }
    ok, msg = actions[args.action]()
    if msg:
        print(msg)
    if args.action == "install" and ok:
        print(f"\nJarvis will now start at logon (task '{autostart.TASK_NAME}').")
        print("Start it right now without logging out:  jarvis autostart start")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis", description="Jarvis task memory CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database and mirror").set_defaults(func=cmd_init)

    pa = sub.add_parser("add", help="add a task")
    pa.add_argument("title")
    pa.add_argument("--details", "-d", default="")
    pa.add_argument("--priority", "-p", choices=T.PRIORITIES, default="normal")
    pa.add_argument("--due", help="when it's due (e.g. '1w', 'tomorrow 15:00')")
    pa.add_argument("--remind", help="when to nudge you (e.g. '2d', '3h')")
    pa.set_defaults(func=cmd_add)

    pl = sub.add_parser("list", help="list tasks")
    pl.add_argument("--all", "-a", action="store_true", help="include done/cancelled")
    pl.add_argument("--status", "-s", choices=T.ALL_STATUSES)
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("show", help="show one task with full history")
    ps.add_argument("id", type=int)
    ps.set_defaults(func=cmd_show)

    pn = sub.add_parser("note", help="add a working note to a task")
    pn.add_argument("id", type=int)
    pn.add_argument("text")
    pn.set_defaults(func=cmd_note)

    px = sub.add_parser("next", help="set the next action for a task")
    px.add_argument("id", type=int)
    px.add_argument("text")
    px.set_defaults(func=cmd_next)

    pst = sub.add_parser("status", help="set a task's status")
    pst.add_argument("id", type=int)
    pst.add_argument("status", choices=T.ALL_STATUSES)
    pst.set_defaults(func=cmd_status)

    pd = sub.add_parser("done", help="mark a task done")
    pd.add_argument("id", type=int)
    pd.set_defaults(func=cmd_done)

    pc = sub.add_parser("cancel", help="cancel a task")
    pc.add_argument("id", type=int)
    pc.set_defaults(func=cmd_cancel)

    pr = sub.add_parser("remind", help="set/clear a reminder time")
    pr.add_argument("id", type=int)
    pr.add_argument("when", help="time, or 'clear'")
    pr.set_defaults(func=cmd_remind)

    pdue = sub.add_parser("due", help="set/clear a due date")
    pdue.add_argument("id", type=int)
    pdue.add_argument("when", help="time, or 'clear'")
    pdue.set_defaults(func=cmd_due)

    sub.add_parser("reminders", help="show reminders due now").set_defaults(func=cmd_reminders)
    sub.add_parser("backup", help="write a manual DB backup").set_defaults(func=cmd_backup)

    pbr = sub.add_parser("brain", help="ask the local LLM a one-shot question (smoke test)")
    pbr.add_argument("prompt", nargs="?", default="", help="the prompt to send")
    pbr.add_argument("--system", help="optional system prompt")
    pbr.add_argument("--model", help="model id (default: configured LLM_MODEL)")
    pbr.add_argument("--think", action="store_true", help="enable Qwen3 thinking mode (slower, deeper)")
    pbr.add_argument("--temperature", "-t", type=float, default=0.7)
    pbr.add_argument("--max-tokens", type=int, default=1024, dest="max_tokens")
    pbr.add_argument("--health", action="store_true", help="check the server and list models instead")
    pbr.add_argument("--verbose", "-v", action="store_true", help="also show reasoning, tokens, tok/s")
    pbr.set_defaults(func=cmd_brain)

    pbe = sub.add_parser("bench", help="benchmark model(s): latency, tok/s, tool-use")
    pbe.add_argument("--model", action="append", help="model id (repeatable to compare); default: configured")
    pbe.add_argument("--max-tokens", type=int, default=300, dest="max_tokens")
    pbe.set_defaults(func=cmd_bench)

    pw = sub.add_parser("worker", help="run the background worker (reminders + daily backup)")
    pw.add_argument("--interval", type=float, default=60.0, help="seconds between ticks (default 60)")
    pw.add_argument("--once", action="store_true", help="run a single tick and exit (for testing)")
    pw.set_defaults(func=cmd_worker)

    pas = sub.add_parser("autostart", help="run the worker automatically at logon (Windows)")
    pas.add_argument("action", choices=("install", "remove", "status", "start"),
                     help="install/remove the logon task, check status, or start it now")
    pas.set_defaults(func=cmd_autostart)

    pcap = sub.add_parser("capture", help="capture a task from plain English (via the LLM)")
    pcap.add_argument("message", help="e.g. 'remind me to call the dentist tomorrow at 2pm'")
    pcap.set_defaults(func=cmd_capture)

    sub.add_parser("discord", help="run the Discord bot (DM capture + phone reminders)").set_defaults(func=cmd_discord)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
