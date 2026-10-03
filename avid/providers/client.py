"""Call facade: dispatch each request to the provider module named by config.resolved_provider."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

import httpx

from .config import Config, model_info_enabled

# Re-exported: providers and this facade share one vocabulary of types and errors.
from .protocol import (
    DEFAULT_MAX_TOKENS,
    DeltaCallback,
    Reply,
    Turn,
)
from .protocol import LLMError as LLMError
from .protocol import PromptTooLongError as PromptTooLongError
from .protocol import iter_sse_events as iter_sse_events
from .providers import openai_compat as openai_compat

# OpenAI-compatible public names kept here for existing tests and direct callers.
from .providers.openai_compat import (  # noqa: F401
    StreamState,
    build_payload,
    build_request,
    delta_reasoning,
    delta_text,
    merge_stream_chunk,
    parse_reply,
    parse_turn,
    post,
)

# Re-exported for tests and callers that take the transport names from this facade.
from .transport import CONNECT_TIMEOUT_SECONDS as CONNECT_TIMEOUT_SECONDS
from .transport import TIMEOUT_SECONDS as TIMEOUT_SECONDS
from .transport import RetryPolicy as RetryPolicy
from .transport import _timeout as _timeout
from .transport import shared_client

# The explicit alias marks a deliberate re-export from this module.
from .usage import Usage as Usage
from .usage import normalize_usage as normalize_usage

# Process-wide cache of probed model windows, re-exported so tests can clear it.
_MODEL_WINDOWS: dict[tuple[str, str], int | None] = {}
_MODEL_WINDOW_LOCK = threading.Lock()


def chat_completion(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    client: httpx.Client | None = None,
) -> Turn:
    """Run a non-streaming completion through the provider named by config.resolved_provider."""
    # An unset max_tokens falls back to the BYOK per-model output cap when one is declared.
    cap = max_tokens if max_tokens is not None else config.max_output
    return _impl(config.resolved_provider).chat(
        config, messages, system=system, tools=tools, max_tokens=cap, client=client
    )


def stream_completion(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    on_delta: DeltaCallback | None = None,
    on_reasoning: DeltaCallback | None = None,
    client: httpx.Client | None = None,
) -> Turn:
    """Run a streaming completion and return the same Turn shape as chat_completion."""
    return _impl(config.resolved_provider).stream(
        config,
        messages,
        system=system,
        tools=tools,
        max_tokens=max_tokens if max_tokens is not None else config.max_output,
        on_delta=on_delta,
        on_reasoning=on_reasoning,
        client=client,
    )


def _impl(provider: str):
    from .providers import impl

    return impl(provider)


def ask(config: Config, prompt: str, *, client: httpx.Client | None = None) -> Reply:
    """Run one non-streaming prompt and return its text, usage and model."""
    turn = chat_completion(config, [{"role": "user", "content": prompt}], client=client)
    return Reply(text=turn.text, usage=turn.usage, model=turn.model)


# Model-window probe: ask {base_url}/models once when the window is unknown, return None on any
# failure, and cache the answer for the life of the process.

#: Probe timeout: this is a metadata request and must not share the 60-second read timeout.
MODEL_INFO_TIMEOUT_SECONDS = 5.0

#: Equivalent field names across gateways (context_length, max_model_len, and so on).
_WINDOW_KEYS = ("context_length", "context_window", "max_model_len", "max_context_length")


def _window_of(item: Mapping[str, Any]) -> int | None:
    for key in _WINDOW_KEYS:
        value = item.get(key)
        if isinstance(value, bool):  # bool is a subclass of int and must be rejected.
            continue
        if isinstance(value, int) and value > 0:
            return value
    return None


def fetch_context_length(
    config: Config, *, client: httpx.Client | None = None
) -> int | None:
    """Return the context window the provider advertises for this model, or None if unknown."""
    if not model_info_enabled():
        return None
    # Only OpenAI-compatible gateways expose /models; the native APIs have no equivalent endpoint.
    if config.resolved_provider != "openai":
        return None
    key = (config.base_url, config.model)
    # A cached entry, including a cached None, is returned without another request.
    with _MODEL_WINDOW_LOCK:
        if key in _MODEL_WINDOWS:
            return _MODEL_WINDOWS[key]
    found = _ask_context_length(config, client=client)
    with _MODEL_WINDOW_LOCK:
        _MODEL_WINDOWS[key] = found
    return found


def _ask_context_length(
    config: Config, *, client: httpx.Client | None = None
) -> int | None:
    url = f"{config.base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {config.api_key}"}
    http = client or shared_client()
    # Every failure is swallowed: the probe is best effort and never fails a model call.
    try:
        response = http.get(url, headers=headers, timeout=MODEL_INFO_TIMEOUT_SECONDS)
        if response.status_code != 200:
            return None
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    items = data.get("data") if isinstance(data, Mapping) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, Mapping) and str(item.get("id")) == config.model:
            return _window_of(item)
    return None
