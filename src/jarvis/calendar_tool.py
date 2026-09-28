"""Calendar (read-only) over CalDAV - Apple Calendar via iCloud.

Apple Calendar syncs to iCloud, which speaks CalDAV, so Jarvis can read your
agenda with your Apple ID + an app-specific password (from appleid.apple.com;
NOT your real password, and revocable in one click). **Read-only for now**: this
lists events, it never creates or changes them.

Named ``calendar_tool`` (not ``calendar``) so it can't shadow the stdlib module.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from jarvis import config

log = logging.getLogger("jarvis.calendar")


def _connect() -> Any:
    import caldav

    client = caldav.DAVClient(
        url=config.CALDAV_URL,
        username=config.APPLE_ID,
        password=config.APPLE_APP_PASSWORD,
    )
    return client.principal()


def _fmt_time(d: dt.datetime) -> str:
    """12-hour clock without a leading zero (Windows-safe, no %-I)."""
    h = d.hour % 12 or 12
    ap = "am" if d.hour < 12 else "pm"
    return f"{h}:{d.minute:02d} {ap}" if d.minute else f"{h} {ap}"


def _window(when: str) -> tuple[dt.datetime, dt.datetime, str]:
    """Resolve a 'when' phrase to a (start, end, label) window in local time."""
    now = dt.datetime.now().astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    w = (when or "today").strip().lower()
    if w in ("", "today"):
        return midnight, midnight + dt.timedelta(days=1), "today"
    if w == "tomorrow":
        return midnight + dt.timedelta(days=1), midnight + dt.timedelta(days=2), "tomorrow"
    if w in ("week", "this week", "upcoming", "next 7 days"):
        return now, midnight + dt.timedelta(days=7), "this week"
    # Anything else ("Friday", "next Monday") -> resolve one day via the parser.
    try:
        from jarvis.timeparse import parse_when

        d = dt.datetime.fromisoformat(parse_when(when)).astimezone()
        day = d.replace(hour=0, minute=0, second=0, microsecond=0)
        return day, day + dt.timedelta(days=1), d.strftime("%A")
    except Exception:
        return midnight, midnight + dt.timedelta(days=1), "today"


def agenda(when: str = "today") -> str:
    """Return a speech-friendly agenda for the requested window (read-only)."""
    if not (config.APPLE_ID and config.APPLE_APP_PASSWORD):
        return ("Your calendar isn't connected yet - add your Apple ID and an "
                "app-specific password to switch it on.")
    try:
        principal = _connect()
        calendars = principal.calendars()
    except Exception as e:
        log.warning("caldav connect failed: %s", e)
        return "I couldn't reach your calendar just now."

    start, end, label = _window(when)
    events: list[tuple[dt.datetime, bool, str]] = []
    for cal in calendars:
        try:
            hits = cal.search(start=start, end=end, event=True, expand=True)
        except Exception:
            continue
        for ev in hits:
            try:
                comp = ev.icalendar_component
                summary = str(comp.get("summary", "(no title)"))
                raw = comp.get("dtstart").dt if comp.get("dtstart") else None
                if isinstance(raw, dt.datetime):
                    events.append((raw.astimezone(), False, summary))
                elif isinstance(raw, dt.date):
                    events.append((dt.datetime.combine(raw, dt.time.min).astimezone(), True, summary))
            except Exception:
                continue

    if not events:
        return f"Nothing on your calendar {label}."
    events.sort(key=lambda e: e[0])
    parts = []
    for when_dt, all_day, summary in events[:8]:
        parts.append(f"{summary} all day" if all_day else f"{summary} at {_fmt_time(when_dt)}")
    more = f", and {len(events) - 8} more" if len(events) > 8 else ""
    return f"{label.capitalize()}: " + ", ".join(parts) + more + "."
