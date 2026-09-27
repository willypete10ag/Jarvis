"""Track API usage per calendar month, so you can see how much of each
provider's allowance you've used.

Two providers are metered:
- **Claude** (the brain): input + output tokens per call. The Anthropic API is
  pay-per-token (there is no monthly free tier), so we show tokens plus an
  estimated dollar cost at Haiku rates - handy for watching spend.
- **Cartesia** (TTS): characters synthesized per call, shown against the free
  tier (~20,000 characters/month).

Counters live in the existing SQLite DB, bucketed by local 'YYYY-MM', and are
incremented atomically. Metering must never break a real call, so every write
is best-effort (failures are logged at debug level and swallowed).
"""

from __future__ import annotations

import datetime as _dt
import logging

from jarvis.memory import db

log = logging.getLogger("jarvis.usage")

# --- Free-tier references / rates (for the "how much is left" view) ---
CARTESIA_FREE_CHARS = 20_000       # Cartesia free tier, characters per month
HAIKU_IN_PER_MTOK = 1.00           # $ per 1M input tokens (claude-haiku-4-5)
HAIKU_OUT_PER_MTOK = 5.00          # $ per 1M output tokens (claude-haiku-4-5)

_TABLE = """
CREATE TABLE IF NOT EXISTS usage (
    month          TEXT PRIMARY KEY,          -- local 'YYYY-MM'
    claude_calls   INTEGER NOT NULL DEFAULT 0,
    claude_in      INTEGER NOT NULL DEFAULT 0,
    claude_out     INTEGER NOT NULL DEFAULT 0,
    cartesia_calls INTEGER NOT NULL DEFAULT 0,
    cartesia_chars INTEGER NOT NULL DEFAULT 0
);
"""

_COLS = ("claude_calls", "claude_in", "claude_out", "cartesia_calls", "cartesia_chars")


def _month() -> str:
    return _dt.datetime.now().astimezone().strftime("%Y-%m")


def _bump(**deltas: int) -> None:
    """Atomically add to this month's counters. Best-effort; never raises."""
    try:
        month = _month()
        assignments = ", ".join(f"{c} = {c} + ?" for c in deltas)  # keys are ours, not user input
        values = [int(v) for v in deltas.values()]
        with db.transaction() as conn:
            conn.execute(_TABLE)
            conn.execute("INSERT OR IGNORE INTO usage(month) VALUES(?)", (month,))
            conn.execute(f"UPDATE usage SET {assignments} WHERE month = ?", (*values, month))
    except Exception:
        log.debug("usage bump failed (ignored)", exc_info=True)


def record_claude(prompt_tokens: int, completion_tokens: int) -> None:
    _bump(claude_calls=1, claude_in=prompt_tokens or 0, claude_out=completion_tokens or 0)


def record_cartesia(chars: int) -> None:
    _bump(cartesia_calls=1, cartesia_chars=chars or 0)


def summary(month: str | None = None) -> dict:
    """Return this month's usage totals plus derived cost / free-tier figures."""
    month = month or _month()
    row = None
    try:
        with db.transaction() as conn:
            conn.execute(_TABLE)
            row = conn.execute("SELECT * FROM usage WHERE month = ?", (month,)).fetchone()
    except Exception:
        log.debug("usage summary read failed", exc_info=True)

    d = {c: (row[c] if row else 0) for c in _COLS}
    d["month"] = month
    d["claude_cost_usd"] = (
        d["claude_in"] / 1e6 * HAIKU_IN_PER_MTOK + d["claude_out"] / 1e6 * HAIKU_OUT_PER_MTOK
    )
    d["cartesia_free_chars"] = CARTESIA_FREE_CHARS
    d["cartesia_pct"] = (d["cartesia_chars"] / CARTESIA_FREE_CHARS * 100) if CARTESIA_FREE_CHARS else 0.0
    d["cartesia_remaining"] = max(0, CARTESIA_FREE_CHARS - d["cartesia_chars"])
    return d


def _bar(pct: float, width: int = 20) -> str:
    filled = min(width, int(round(pct / 100 * width)))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def format_summary(month: str | None = None) -> str:
    """Human-readable usage report for the CLI or Discord."""
    d = summary(month)
    ct = d["cartesia_chars"]
    lines = [
        f"Usage for {d['month']}:",
        "",
        f"Cartesia (TTS): {ct:,} / {d['cartesia_free_chars']:,} free chars "
        f"({d['cartesia_pct']:.0f}%) {_bar(d['cartesia_pct'])}",
        f"  {d['cartesia_remaining']:,} chars left this month, across {d['cartesia_calls']:,} replies.",
        "",
        f"Claude (brain): {d['claude_in']:,} in + {d['claude_out']:,} out tokens "
        f"over {d['claude_calls']:,} calls.",
        f"  Estimated cost ~${d['claude_cost_usd']:.4f} (Haiku rates; pay-per-token, no monthly free tier).",
    ]
    return "\n".join(lines)
