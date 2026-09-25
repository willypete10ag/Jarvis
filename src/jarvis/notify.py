"""Notification channels: how Jarvis reaches you when something needs attention.

Pluggable by design, because *where* a reminder shows up will change as the
project grows:

- **Console** - always works; the reminder is printed and logged.
- **Windows toast** - a real pop-up in the corner of your screen, so a reminder
  reaches you even when you're not looking at a terminal. Best-effort: if it
  can't fire (no PowerShell, headless session), it degrades silently to console.
- **Discord** - added later, so reminders reach your phone.

The worker asks for :func:`build_default` and just calls ``.send(title, body)``;
it never needs to know which channels exist.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Protocol, runtime_checkable

log = logging.getLogger("jarvis.notify")


@runtime_checkable
class Notifier(Protocol):
    def send(self, title: str, body: str) -> bool:
        """Deliver one notification. Returns True if it was delivered."""
        ...


class ConsoleNotifier:
    """Prints the notification. The one channel that can always deliver."""

    def send(self, title: str, body: str) -> bool:
        print(f"\n🔔 {title}\n   {body}")
        return True


# PowerShell snippet that raises a native Windows toast. The text is passed via
# environment variables (not string-interpolated from Python) so a task title
# containing quotes or other characters can't break or inject into the script;
# inside PowerShell it's XML-escaped before going into the toast payload.
#
# We build the toast XML with LoadXml rather than editing the template's node
# collection in place - the latter throws "Collection was modified" under WinRT.
# We borrow PowerShell's registered AppUserModelID so the toast reliably appears.
_TOAST_PS = r"""
try {
  $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
  $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]
  $title = [Security.SecurityElement]::Escape($env:JARVIS_TOAST_TITLE)
  $body  = [Security.SecurityElement]::Escape($env:JARVIS_TOAST_BODY)
  $payload = "<toast><visual><binding template='ToastText02'><text id='1'>$title</text><text id='2'>$body</text></binding></visual></toast>"
  $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
  $xml.LoadXml($payload)
  $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
  $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
} catch { exit 1 }
"""


class WindowsToastNotifier:
    """Raises a native Windows toast via PowerShell (no third-party package)."""

    def __init__(self) -> None:
        self._ps = shutil.which("powershell") or shutil.which("pwsh")

    @property
    def available(self) -> bool:
        return self._ps is not None

    def send(self, title: str, body: str) -> bool:
        if not self._ps:
            return False
        env = {**os.environ, "JARVIS_TOAST_TITLE": title, "JARVIS_TOAST_BODY": body}
        try:
            proc = subprocess.run(
                [self._ps, "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", _TOAST_PS],
                env=env,
                capture_output=True,
                timeout=15,
            )
            if proc.returncode != 0:
                log.warning("toast notifier failed (rc=%s)", proc.returncode)
                return False
            return True
        except Exception as e:  # never let a notification crash the worker
            log.warning("toast notifier error: %s", e)
            return False


class MultiNotifier:
    """Fan a notification out to several channels; delivered if any succeeds."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    def send(self, title: str, body: str) -> bool:
        delivered = False
        for n in self.notifiers:
            try:
                delivered = n.send(title, body) or delivered
            except Exception:  # isolate a bad channel from the rest
                log.exception("notifier %r raised", n)
        return delivered


def build_default() -> MultiNotifier:
    """The channel set for the current phase: console + Windows toast if present."""
    channels: list[Notifier] = [ConsoleNotifier()]
    toast = WindowsToastNotifier()
    if toast.available:
        channels.append(toast)
    return MultiNotifier(channels)
