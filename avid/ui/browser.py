"""Opening a real application window: which browser is on this machine, and how to ask for no chrome."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import webbrowser

logger = logging.getLogger("avid.ui.browser")

#: Override for a browser this list does not know: a command line, arguments included.
BROWSER_ENV = "AVID_BROWSER_CMD"

#: Chromium family only: `--app=` is what removes the address bar, and every entry understands it.
#: The `.exe` names are here for WSL, where the browser lives on the Windows side of the boundary.
_CHROMIUM = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "brave-browser",
    "chrome.exe",
    "msedge.exe",
)

_MACOS_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


def _find(candidate: str) -> str | None:
    if candidate.startswith("/"):
        return candidate if os.access(candidate, os.X_OK) else None
    return shutil.which(candidate)


def resolve_command(spec: str | None = None) -> list[str] | None:
    """The browser command to launch with, or None when this machine has nothing usable."""
    raw = (spec if spec is not None else os.environ.get(BROWSER_ENV, "")).strip()
    if raw:
        parts = shlex.split(raw)
        resolved = _find(parts[0]) if parts else None
        return [resolved, *parts[1:]] if resolved else None
    for candidate in (*_MACOS_APPS, *_CHROMIUM):
        found = _find(candidate)
        if found:
            return [found]
    return None


def open_window(url: str, command: list[str] | None) -> str:
    """Opens `url` as an application window; returns how it went, for the caller to report."""
    if command:
        try:
            subprocess.Popen(
                [*command, f"--app={url}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return "app-window"
        except OSError as exc:
            logger.warning("起浏览器失败（%s），退回默认浏览器", exc)
    try:
        opened = webbrowser.open(url)
    except webbrowser.Error:  # pragma: no cover - depends on what the host has registered
        opened = False
    return "browser-tab" if opened else "none"


__all__ = ["BROWSER_ENV", "open_window", "resolve_command"]
