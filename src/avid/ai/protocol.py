"""Shared protocol vocabulary: Turn and Reply shapes, error types, and the SSE decoder."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from .usage import Usage, normalize_usage

#: Default output cap; None omits max_tokens so the provider decides the limit.
# A fixed cap lets reasoning tokens starve the visible reply, which then looks like a finished turn.
DEFAULT_MAX_TOKENS: int | None = None


class LLMError(Exception):
    """A model call failed; the message carries the status code and a body excerpt."""


class PromptTooLongError(LLMError):
    """The request exceeded the context window, so the caller may compact and retry once."""


# Wording differs per provider; any of these substrings marks a context-overflow error.
_PROMPT_TOO_LONG_SIGNS = (
    "prompt is too long",
    "prompt_too_long",
    "context length",
    "context_length_exceeded",
    "maximum context",
    "too many tokens",
    "request too large",
    "reduce the length",
    "input is too long",
    "exceeds the maximum number of tokens",
)


def prompt_too_long(status_code: int, body: str) -> bool:
    """Report whether a status code and body mean the prompt exceeded the context window."""
    # Server errors are not recoverable input problems, even when the body mentions context length.
    if status_code not in (400, 413, 422):
        return False
    text = body.lower()
    return any(sign in text for sign in _PROMPT_TOO_LONG_SIGNS)


@dataclass(frozen=True)
class Reply:
    text: str
    usage: Usage
    model: str


@dataclass(frozen=True)
class Turn:
    """One model response; message is a cleaned assistant message ready to append to messages."""

    message: dict[str, Any]
    text: str
    tool_calls: list[dict[str, Any]]
    usage: Usage
    model: str
    finish_reason: str
    # Reasoning stays out of message: echoing it back pollutes or is rejected by the endpoint.
    reasoning: str = ""


DeltaCallback = Callable[[str], None]


def content_text(raw: Any) -> str:
    """Normalize an assistant content field to text so streaming and non-streaming agree."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):  # Content parts: concatenate their text fields.
        return "".join(
            item.get("text", "")
            for item in raw
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


def usage_of(data: Any) -> Usage:
    """Normalize a response envelope's usage fields; dialect detection lives in ai/usage.py."""
    return normalize_usage(data)


def _decode_stream_frame(payload: str) -> dict[str, Any]:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LLMError(f"流式响应帧不是合法 JSON：{payload[:200]}") from exc
    if not isinstance(decoded, dict):
        raise LLMError(f"流式响应帧不是对象：{payload[:200]}")
    return decoded


def iter_sse_events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Turn an SSE line stream into JSON frames, skipping heartbeats, blank frames and [DONE]."""
    # Partial frames are already reassembled: the caller iterates whole lines, so no parser here.
    buffered: list[str] = []
    for line in lines:
        if line == "":  # An empty line ends the current frame.
            payload = "\n".join(buffered)
            buffered.clear()
            if not payload.strip():
                continue
            if payload.strip() == "[DONE]":
                return
            yield _decode_stream_frame(payload)
            continue
        if line.startswith(":"):  # Heartbeat or comment line.
            continue
        if line.startswith("data:"):
            value = line[len("data:") :]
            buffered.append(value[1:] if value.startswith(" ") else value)
        # event:/id:/retry: fields are unused: all three providers carry payloads on data lines.
    payload = "\n".join(buffered)  # Flush a trailing frame that had no blank line after it.
    if payload.strip() and payload.strip() != "[DONE]":
        yield _decode_stream_frame(payload)


def assistant_message(text: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an OpenAI-shaped assistant message, omitting tool_calls when there are none."""
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message
