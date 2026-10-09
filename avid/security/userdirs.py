"""The user-level Avid directory and the settings file kept there: one stdlib-only leaf shared by
the workspace registry, the audit log, sandbox masking and the settings page.

``sessions_dir()`` is the single resolution point for the session store
(``AVID_SESSIONS_DIR`` → ``settings.json``'s ``sessions_dir`` → ``<avid home>/sessions``), and
sandbox masking follows it, so it cannot live in the services layer; reads tolerate a missing or
corrupt file, writes merge keys and replace the file atomically, and secrets stay in secrets.json.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

# Environment variable that relocates the user-level directory; tests use it for isolation.
AVID_HOME_ENV = "AVID_HOME"
# Environment variable that relocates the session store; it wins over the settings file.
SESSIONS_DIR_ENV = "AVID_SESSIONS_DIR"
# Environment variable that relocates the derived index; it wins over the default below.
INDEX_DIR_ENV = "AVID_INDEX_DIR"
SETTINGS_FILE = "settings.json"

# Stamped into the file so the on-disk shape is self-describing.
SETTINGS_VERSION = 1
# Key holding the session store; absent means the default below.
SESSIONS_DIR_KEY = "sessions_dir"
# Relative to the Avid home, so AVID_HOME relocates the default store with everything else.
DEFAULT_SESSIONS_DIRNAME = "sessions"
# The derived index lives beside the settings, inside the same masked directory.
DEFAULT_INDEX_DIRNAME = "index"
INDEX_DB_FILENAME = "sessions.sqlite"


def avid_home() -> Path:
    """Resolve the user-level Avid directory: ``AVID_HOME`` when set, otherwise ``~/.avid``."""
    override = os.environ.get(AVID_HOME_ENV)
    return Path(override).expanduser() if override else Path.home() / ".avid"


def home_dir() -> Path:
    """Alias of ``avid_home`` kept for callers that already use this name."""
    return avid_home()


def settings_path() -> Path:
    return avid_home() / SETTINGS_FILE


def default_sessions_dir() -> Path:
    """The store used when neither the environment nor the settings file says otherwise."""
    return avid_home() / DEFAULT_SESSIONS_DIRNAME


def read_settings() -> dict[str, Any]:
    """Read the whole settings file; unreadable or malformed content reads as no settings."""
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_settings(patch: Mapping[str, Any]) -> None:
    """Merge ``patch`` into the settings file; a None value removes that key."""
    payload = read_settings()
    for name, value in patch.items():
        if value is None:
            payload.pop(name, None)
        else:
            payload[name] = value
    payload["version"] = SETTINGS_VERSION

    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sibling temp file + atomic replace, so an interrupted write cannot truncate the settings.
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def _configured_sessions_dir() -> str | None:
    """The settings-file value when it is a usable path, else None."""
    value = read_settings().get(SESSIONS_DIR_KEY)
    if isinstance(value, str) and value.strip():
        return value
    return None


def sessions_dir() -> Path:
    """The session store: the environment override, the settings file, then the default."""
    override = os.environ.get(SESSIONS_DIR_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    configured = _configured_sessions_dir()
    if configured is not None:
        return Path(configured).expanduser()
    return default_sessions_dir()


def sessions_dir_source() -> Literal["env", "settings", "default"]:
    """Which layer decided the store: ``env``, ``settings`` or ``default``."""
    override = os.environ.get(SESSIONS_DIR_ENV)
    if override and override.strip():
        return "env"
    return "settings" if _configured_sessions_dir() is not None else "default"


def default_index_dir() -> Path:
    """Where the derived index sits when nothing overrides it; beside the settings file."""
    return avid_home() / DEFAULT_INDEX_DIRNAME


def index_dir() -> Path:
    """The index directory: ``AVID_INDEX_DIR`` when set, otherwise the default."""
    override = os.environ.get(INDEX_DIR_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    return default_index_dir()


def index_dir_source() -> str:
    """Which layer decided the index location: ``env`` or ``default``."""
    override = os.environ.get(INDEX_DIR_ENV)
    return "env" if override and override.strip() else "default"


def index_path() -> Path:
    """The SQLite file; derived data that can be deleted and rebuilt at any time."""
    return index_dir() / INDEX_DB_FILENAME


__all__ = [
    "AVID_HOME_ENV",
    "INDEX_DIR_ENV",
    "SESSIONS_DIR_ENV",
    "SETTINGS_FILE",
    "SETTINGS_VERSION",
    "SESSIONS_DIR_KEY",
    "avid_home",
    "default_index_dir",
    "default_sessions_dir",
    "home_dir",
    "index_dir",
    "index_dir_source",
    "index_path",
    "read_settings",
    "sessions_dir",
    "sessions_dir_source",
    "settings_path",
    "write_settings",
]
