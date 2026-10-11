"""Where the web tools' configuration comes from: the search key from the secrets store, the
endpoint and timeout from the settings file, and environment variables that win over both — the
same precedence session storage uses.

The model-facing tool and the settings endpoint both resolve through here, so the UI cannot show a
value the tool would not use.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from .providers.byok import delete_secret, read_secrets, set_secret
from .security import userdirs

#: The search key lives in the same 0600 store as the model keys, under this reference.
SECRET_REF = "tavily"
SIGNUP_URL = "https://app.tavily.com"

ENV_SEARCH_URL = "AVID_WEB_SEARCH_URL"
ENV_TIMEOUT = "AVID_WEB_TIMEOUT_SECONDS"
ENV_ALLOW_PRIVATE = "AVID_WEB_ALLOW_PRIVATE_HOSTS"

DEFAULT_SEARCH_URL = "https://api.tavily.com"
DEFAULT_TIMEOUT_SECONDS = 30.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 300.0

#: The settings-file block holding the values the settings UI owns.
SETTINGS_KEY = "web"
SEARCH_URL_KEY = "search_url"
TIMEOUT_KEY = "timeout_seconds"

#: Who decided a value: the environment wins over the file, which wins over the default.
Source = Literal["env", "settings", "default"]


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _block() -> dict[str, Any]:
    """The ``web`` block of the settings file; anything malformed reads as empty."""
    stored = userdirs.read_settings().get(SETTINGS_KEY)
    return dict(stored) if isinstance(stored, dict) else {}


def _stored_text(name: str) -> str | None:
    raw = _block().get(name)
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _stored_number(name: str) -> float | None:
    raw = _block().get(name)
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _clamp(value: float) -> float:
    return max(MIN_TIMEOUT_SECONDS, min(value, MAX_TIMEOUT_SECONDS))


def resolved_search_url() -> tuple[str, Source]:
    """The endpoint root the tools will call, and who decided it."""
    env = _env(ENV_SEARCH_URL)
    if env:
        return env, "env"
    stored = _stored_text(SEARCH_URL_KEY)
    if stored:
        return stored, "settings"
    return DEFAULT_SEARCH_URL, "default"


def resolved_timeout() -> tuple[float, Source]:
    """The per-call budget, and who decided it; a malformed env value still decides, it just
    cannot be honoured."""
    env = _env(ENV_TIMEOUT)
    if env:
        try:
            return _clamp(float(env)), "env"
        except ValueError:
            return DEFAULT_TIMEOUT_SECONDS, "env"
    stored = _stored_number(TIMEOUT_KEY)
    if stored is not None:
        return _clamp(stored), "settings"
    return DEFAULT_TIMEOUT_SECONDS, "default"


def allow_private_hosts() -> bool:
    """Reaching loopback and private ranges is deliberately env-only: loosening that guardrail
    stays explicit and carries friction instead of being one click in the UI."""
    return _env(ENV_ALLOW_PRIVATE).lower() in {"1", "true", "yes", "on"}


def read_search_key() -> str:
    return (read_secrets().get(SECRET_REF) or "").strip()


def write_search_key(value: str) -> None:
    set_secret(SECRET_REF, value)


def delete_search_key() -> None:
    delete_secret(SECRET_REF)


def save_settings(*, search_url: str | None, timeout_seconds: float | None) -> None:
    """Write the whole ``web`` block; None drops that key, so the default applies again."""
    block: dict[str, Any] = {}
    if search_url:
        block[SEARCH_URL_KEY] = search_url
    if timeout_seconds is not None:
        block[TIMEOUT_KEY] = _clamp(timeout_seconds)
    userdirs.write_settings({SETTINGS_KEY: block or None})
