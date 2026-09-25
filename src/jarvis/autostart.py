"""Register the worker to launch at logon via Windows Task Scheduler.

A thin, friendly wrapper around ``schtasks`` so you don't have to touch the Task
Scheduler UI. It launches the worker with ``pythonw.exe`` (no console window) so
Jarvis runs quietly in the background from the moment you log in.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_NAME = "JarvisWorker"


def _launcher() -> str:
    """Prefer pythonw.exe (windowless) so no console flashes at logon."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe)


def _run(args: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def install() -> tuple[bool, str]:
    """Create/replace the logon task."""
    command = f'"{_launcher()}" -m jarvis worker'
    return _run([
        "schtasks", "/Create", "/TN", TASK_NAME, "/TR", command,
        "/SC", "ONLOGON", "/RL", "LIMITED", "/F",
    ])


def remove() -> tuple[bool, str]:
    return _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])


def status() -> tuple[bool, str]:
    return _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])


def start_now() -> tuple[bool, str]:
    return _run(["schtasks", "/Run", "/TN", TASK_NAME])
