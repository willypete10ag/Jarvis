"""Homelab connectors: the Plex / *ARR media stack + system monitoring.

Every call is stdlib HTTP (urllib) with a short timeout, so a service that's
down fails fast instead of stalling a voice reply. Each public function returns
a short, speech-friendly string (Jarvis speaks these aloud). A service that
isn't configured - missing URL or API key - says so plainly rather than erroring.

Live now: Sonarr (tasks/queue) and system monitoring (this PC via psutil, the Pi
via netdata, service health via pings). Radarr / Plex / SABnzbd light up once you
add their keys to .env (RADARR_API_KEY, PLEX_TOKEN, SAB_API_KEY).
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from jarvis import config

log = logging.getLogger("jarvis.homelab")


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------
def _get(url: str, headers: Optional[dict[str, str]] = None, timeout: Optional[float] = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout or config.HOMELAB_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, payload: dict, headers: Optional[dict[str, str]] = None, timeout: Optional[float] = None) -> Any:
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers=h)
    with urllib.request.urlopen(req, timeout=timeout or config.HOMELAB_TIMEOUT) as r:
        raw = r.read()
        return json.loads(raw.decode("utf-8")) if raw.strip() else {}


def _arr(base: str, key: str, path: str, ver: str = "v3") -> Any:
    return _get(f"{base.rstrip('/')}/api/{ver}/{path}", headers={"X-Api-Key": key})


def _reachable(url: str, headers: Optional[dict[str, str]] = None) -> bool:
    """True if the URL answers with *any* HTTP status (even 401) within timeout."""
    try:
        req = urllib.request.Request(url, headers=headers or {}, method="GET")
        urllib.request.urlopen(req, timeout=config.HOMELAB_TIMEOUT)
        return True
    except urllib.error.HTTPError:
        return True  # answered (e.g. 401) -> service is up
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Media: downloads, library status, search
# ---------------------------------------------------------------------------
def _queue_items(base: str, key: str) -> list[dict]:
    data = _arr(base, key, "queue?pageSize=200&includeUnknownSeriesItems=true"
                          "&includeEpisode=true&includeSeries=true&includeMovie=true")
    return data.get("records", []) if isinstance(data, dict) else []


def _short_item(r: dict) -> str:
    """A speech-friendly 'Show S01E07 at 45 percent via torrent' from a queue record."""
    ep = r.get("episode") or {}
    ser = r.get("series") or {}
    mov = r.get("movie") or {}
    if ser and ep:
        label = f"{ser.get('title','')} S{ep.get('seasonNumber',0):02d}E{ep.get('episodeNumber',0):02d}".strip()
    elif mov:
        label = mov.get("title", "")
    else:  # fallback: chop scene junk off the raw release title
        raw = r.get("title", "something")
        label = re.split(r"[\[(]", raw)[0].strip() or raw[:40]
    size = r.get("size") or 0
    left = r.get("sizeleft") or 0
    pct = int((size - left) / size * 100) if size else 0
    proto = (r.get("protocol") or "").lower()
    via = " via usenet" if proto == "usenet" else (" via torrent" if proto == "torrent" else "")
    return f"{label} at {pct} percent{via}"


def downloading(kind: str = "all") -> str:
    """What's downloading. ``kind``: 'shows' (Sonarr), 'movies' (Radarr), or 'all'."""
    kind = (kind or "all").lower()
    want_shows = kind in ("all", "shows", "show", "tv", "series")
    want_movies = kind in ("all", "movies", "movie", "films", "film")

    sources = []
    if want_shows:
        sources.append(("shows", config.SONARR_URL, config.SONARR_API_KEY))
    if want_movies:
        sources.append(("movies", config.RADARR_URL, config.RADARR_API_KEY))
    configured = [s for s in sources if s[2]]
    if not configured:
        if not (config.SONARR_API_KEY or config.RADARR_API_KEY):
            return "No download services are configured yet."
        return "Sonarr isn't configured yet." if want_shows else "Radarr isn't configured yet."

    items: list[str] = []
    for label, base, key in configured:
        try:
            items += [_short_item(r) for r in _queue_items(base, key)]
        except Exception as e:
            log.warning("%s queue check failed: %s", label, e)
            items.append(f"(couldn't reach {label})")
    noun = "shows" if kind.startswith("show") or kind in ("tv", "series") else \
           ("movies" if want_movies and not want_shows else "downloads")
    if not items:
        return f"No {noun} are downloading right now." if noun != "downloads" else "Nothing's downloading right now."
    n = len(items)
    shown = items[:3]
    tail = f", and {n - 3} more" if n > 3 else ""
    lead = f"One {noun[:-1] if noun.endswith('s') and n == 1 else noun}" if n == 1 else f"{n} {noun}"
    return f"{lead} downloading: " + "; ".join(shown) + tail + "."


def series_status(title: str) -> str:
    """Report how complete a tracked show is, and what's missing."""
    if not config.SONARR_API_KEY:
        return "Sonarr isn't configured, so I can't check your shows yet."
    title = (title or "").strip()
    if not title:
        return "Which show did you mean?"
    try:
        series = _arr(config.SONARR_URL, config.SONARR_API_KEY, "series")
    except Exception as e:
        log.warning("series lookup failed: %s", e)
        return "I couldn't reach Sonarr just now."
    tl = title.lower()
    match = next((s for s in series if tl in s.get("title", "").lower()), None)
    if not match:
        return f"You're not tracking a show called {title}."
    st = match.get("statistics", {}) or {}
    have, total = st.get("episodeFileCount", 0), st.get("episodeCount", 0)
    name = match.get("title", title)
    if total and have >= total:
        return f"{name} is complete - all {total} episodes."
    missing = total - have
    return f"{name}: {have} of {total} episodes, {missing} still missing."


def search_missing(title: str) -> str:
    """Kick off a Sonarr search for a tracked show's missing episodes."""
    if not config.SONARR_API_KEY:
        return "Sonarr isn't configured yet."
    title = (title or "").strip()
    try:
        series = _arr(config.SONARR_URL, config.SONARR_API_KEY, "series")
    except Exception:
        return "I couldn't reach Sonarr just now."
    match = next((s for s in series if title.lower() in s.get("title", "").lower()), None)
    if not match:
        return f"You're not tracking a show called {title}."
    try:
        _post(f"{config.SONARR_URL.rstrip('/')}/api/v3/command",
              {"name": "MissingEpisodeSearch", "seriesId": match["id"]},
              headers={"X-Api-Key": config.SONARR_API_KEY})
    except Exception as e:
        log.warning("search command failed: %s", e)
        return f"I found {match.get('title')}, but couldn't start the search."
    return f"On it - searching for the missing episodes of {match.get('title')}."


# ---------------------------------------------------------------------------
# Plex: now playing / on deck (needs PLEX_TOKEN)
# ---------------------------------------------------------------------------
def _plex(path: str) -> Any:
    url = f"{config.PLEX_URL.rstrip('/')}{path}"
    sep = "&" if "?" in path else "?"
    url = f"{url}{sep}X-Plex-Token={urllib.parse.quote(config.PLEX_TOKEN)}"
    return _get(url, headers={"Accept": "application/json"})


def now_playing() -> str:
    """What's streaming on Plex right now, including whether it's transcoding."""
    if not config.PLEX_TOKEN:
        return "Plex isn't connected yet - add a PLEX_TOKEN to switch this on."
    try:
        mc = _plex("/status/sessions").get("MediaContainer", {})
    except Exception as e:
        log.warning("plex sessions failed: %s", e)
        return "I couldn't reach Plex just now."
    items = mc.get("Metadata", []) or []
    if not items:
        return "Nothing's playing on Plex right now."
    parts = []
    for it in items:
        who = (it.get("User", {}) or {}).get("title", "someone")
        grand = it.get("grandparentTitle", "")
        title = it.get("title", "")
        what = f"{grand} - {title}" if grand else title
        player = (it.get("Player", {}) or {}).get("product", "")
        decision = (it.get("TranscodeSession") is not None)
        how = "transcoding" if decision else "direct play"
        parts.append(f"{who} is watching {what} on {player}, {how}".strip())
    return "; ".join(parts) + "."


def on_deck() -> str:
    """A few things queued up to watch (Plex On Deck)."""
    if not config.PLEX_TOKEN:
        return "Plex isn't connected yet - add a PLEX_TOKEN to switch this on."
    try:
        mc = _plex("/library/onDeck").get("MediaContainer", {})
    except Exception:
        return "I couldn't reach Plex just now."
    items = mc.get("Metadata", []) or []
    if not items:
        return "Nothing's on deck right now."
    names = []
    for it in items[:5]:
        grand = it.get("grandparentTitle", "")
        names.append(f"{grand} - {it.get('title','')}" if grand else it.get("title", ""))
    return "On deck: " + ", ".join(n for n in names if n) + "."


# ---------------------------------------------------------------------------
# System monitoring: this PC (psutil), the Pi (netdata), service health
# ---------------------------------------------------------------------------
def _pc_stats() -> Optional[str]:
    try:
        import psutil
    except Exception:
        return None
    try:
        cpu = psutil.cpu_percent(interval=0.3)
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\" if hasattr(psutil, "disk_usage") else "/")
        up_h = (time.time() - psutil.boot_time()) / 3600
        return (f"This PC: {cpu:.0f} percent CPU, {vm.percent:.0f} percent RAM "
                f"({vm.used/1e9:.1f} of {vm.total/1e9:.1f} gig), "
                f"{disk.free/1e9:.0f} gig free on C, up {up_h:.0f} hours")
    except Exception as e:
        log.warning("pc stats failed: %s", e)
        return None


def _netdata_latest(chart: str) -> dict[str, float]:
    """Return {dimension: latest value} for a netdata chart."""
    url = f"{config.NETDATA_URL.rstrip('/')}/api/v1/data?chart={urllib.parse.quote(chart)}&points=1&after=-1&format=json&group=average"
    d = _get(url)
    labels = d.get("labels", [])
    rows = d.get("data", [])
    if not rows:
        return {}
    row = rows[0]
    return {labels[i]: row[i] for i in range(1, len(labels)) if row[i] is not None}


def _pi_stats() -> Optional[str]:
    if not config.NETDATA_URL:
        return None
    try:
        cpu = _netdata_latest("system.cpu")
        cpu_pct = 100 - cpu.get("idle", 100) if cpu else None
        ram = _netdata_latest("system.ram")
        ram_pct = None
        if ram:
            total = sum(v for v in ram.values())
            used = total - ram.get("free", 0) - ram.get("cached", 0) - ram.get("buffers", 0)
            ram_pct = used / total * 100 if total else None
        load = _netdata_latest("system.load")
        temp = None
        for k, v in (_netdata_latest("sensors.temperature_cpu_thermal-virtual-0_temp1_input") or {}).items():
            temp = v
            break
        bits = ["Pi:"]
        if cpu_pct is not None:
            bits.append(f"{cpu_pct:.0f} percent CPU,")
        if ram_pct is not None:
            bits.append(f"{ram_pct:.0f} percent RAM,")
        if load.get("load1") is not None:
            bits.append(f"load {load['load1']:.1f},")
        if temp is not None:
            bits.append(f"{temp:.0f} degrees")
        s = " ".join(bits).rstrip(",")
        return s if len(bits) > 1 else None
    except Exception as e:
        log.warning("pi stats (netdata) failed: %s", e)
        return None


def _service_health() -> str:
    checks = [
        ("Sonarr", config.SONARR_URL, config.SONARR_API_KEY),
        ("Radarr", config.RADARR_URL, config.RADARR_API_KEY),
        ("Prowlarr", config.PROWLARR_URL, config.PROWLARR_API_KEY),
        ("Plex", config.PLEX_URL, config.PLEX_TOKEN),
        ("SABnzbd", config.SAB_URL, config.SAB_API_KEY),
    ]
    up, down = [], []
    for name, url, key in checks:
        if not url:
            continue
        (up if _reachable(url) else down).append(name)
    parts = []
    if up:
        parts.append("up: " + ", ".join(up))
    if down:
        parts.append("DOWN: " + ", ".join(down))
    return ("Services " + "; ".join(parts) + ".") if parts else ""


def system_status() -> str:
    """A quick health readout: this PC, the Pi, and which services are up."""
    chunks = [c for c in (_pc_stats(), _pi_stats(), _service_health()) if c]
    return " ".join(chunks) if chunks else "I couldn't read any system stats right now."
