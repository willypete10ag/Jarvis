"""Parser for human-friendly time input.

Accepts things like: ``30m``, ``2h``, ``3d``, ``1w`` (relative to now),
``tomorrow``, ``today 15:00``, ``2026-10-01``, ``2026-10-01 14:30``, ``15:00``.
Returns a UTC ISO-8601 string (Jarvis' storage format) or raises ValueError.

The fast, exact cases above are handled by hand (no dependency, no ambiguity).
Anything they don't match - "next Tuesday", "in 3 weeks", "end of month" -
falls through to the ``parsedatetime`` package, which does real natural-language
resolution. We push this date math into code rather than trusting a small local
model to do it (it gets weekday arithmetic wrong). If ``parsedatetime`` isn't
installed, only the exact cases work and the rest raise ValueError as before.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_REL_RE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)
_UNIT = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_when(text: str, *, now: datetime | None = None) -> str:
    """Parse a human time string into a UTC ISO-8601 timestamp string."""
    now = now or datetime.now().astimezone()  # local, tz-aware
    s = text.strip()
    if not s:
        raise ValueError("empty time string")

    # Relative offset, e.g. "45m", "2h", "3d", "1w"
    m = _REL_RE.match(s)
    if m:
        amount, unit = int(m.group(1)), m.group(2).lower()
        dt = now + timedelta(**{_UNIT[unit]: amount})
        return _to_utc_iso(dt)

    low = s.lower()
    day_base: datetime | None = None
    rest = s
    if low.startswith("today"):
        day_base, rest = now, s[len("today"):]
    elif low.startswith("tomorrow"):
        day_base, rest = now + timedelta(days=1), s[len("tomorrow"):]

    if day_base is not None:
        hh, mm = _parse_hhmm(rest) if rest.strip() else (9, 0)  # default 9am
        dt = day_base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return _to_utc_iso(dt)

    # Bare time "15:00" -> today, or tomorrow if already past.
    if re.match(r"^\d{1,2}:\d{2}$", s):
        hh, mm = _parse_hhmm(s)
        dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return _to_utc_iso(dt)

    # Explicit date / datetime formats.
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                dt = dt.replace(hour=9)  # default 9am for date-only
            dt = dt.replace(tzinfo=now.tzinfo)
            return _to_utc_iso(dt)
        except ValueError:
            continue

    # Natural language ("next Friday", "in 3 weeks", "Friday at 2pm").
    dt = _natural_language(s, now)
    if dt is not None:
        return _to_utc_iso(dt)

    raise ValueError(
        f"Could not understand time '{text}'. Try '2h', 'tomorrow 15:00', "
        f"'next Friday', or '2026-10-01 14:30'."
    )


def _natural_language(text: str, now: datetime) -> datetime | None:
    """Resolve free-form dates via ``parsedatetime``; None if unavailable/unparsable.

    Returns a *naive* local datetime; the caller's ``_to_utc_iso`` localizes it,
    which applies the correct DST offset for the target date (not today's).
    parsedatetime resolves "next Friday" to the upcoming Friday, which is what
    you want for reminders and deadlines.
    """
    try:
        import parsedatetime
    except ModuleNotFoundError:
        return None

    cal = parsedatetime.Calendar()
    dt, status = cal.parseDT(text, sourceTime=now.replace(tzinfo=None))
    if status == 0:  # nothing date-like found
        return None
    return dt  # naive local time


def _parse_hhmm(text: str) -> tuple[int, int]:
    t = text.strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", t)
    if not m:
        raise ValueError(f"bad time-of-day '{text}' (want HH:MM)")
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(f"time out of range '{text}'")
    return hh, mm


def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
