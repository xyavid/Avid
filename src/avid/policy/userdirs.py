"""User-level Avid directory: one stdlib-only leaf shared by the workspace registry, the audit log and sandbox masking."""

from __future__ import annotations

import os
from pathlib import Path

# Environment variable that relocates the user-level directory; tests use it for isolation.
AVID_HOME_ENV = "AVID_HOME"


def avid_home() -> Path:
    """Resolve the user-level Avid directory: ``AVID_HOME`` when set, otherwise ``~/.avid``."""
    override = os.environ.get(AVID_HOME_ENV)
    return Path(override).expanduser() if override else Path.home() / ".avid"


def home_dir() -> Path:
    """Alias of ``avid_home`` kept for callers that already use this name."""
    return avid_home()


__all__ = ["AVID_HOME_ENV", "avid_home", "home_dir"]
