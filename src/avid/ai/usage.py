"""Normalize each provider's usage payload into one accounting shape."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Usage:
    """Usage of one model call; prompt_tokens includes the cache-hit portion."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    # None means the provider did not report the number; 0 would mean a confirmed miss.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    # Reasoning tokens are a subset of completion_tokens, reported to explain where output went.
    reasoning_tokens: int | None = None


def _as_int(value: Any) -> int | None:
    """Coerce loosely typed counts to int; bools and unparseable values yield None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _nested_int(raw: Mapping[str, Any] | None, key: str) -> int | None:
    if not isinstance(raw, Mapping):
        return None
    return _as_int(raw.get(key))


def usage_payload(data: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Extract the usage object from a response envelope, treating a bare usage object as one."""
    if not isinstance(data, Mapping):
        return {}
    for key in ("usage", "usageMetadata", "usage_metadata"):
        found = data.get(key)
        if isinstance(found, Mapping):
            return found
    return data


def _openai_like(payload: Mapping[str, Any]) -> Usage | None:
    prompt = _as_int(payload.get("prompt_tokens"))
    if prompt is None:
        return None
    completion = _as_int(payload.get("completion_tokens")) or 0
    total = _as_int(payload.get("total_tokens")) or (prompt + completion)
    cached = _nested_int(payload.get("prompt_tokens_details"), "cached_tokens")
    if cached is None:
        cached = _as_int(payload.get("cached_tokens"))
    if cached is None:
        cached = _as_int(payload.get("prompt_cache_hit_tokens"))
    # Reasoning tokens appear either nested in details or flattened by some gateways.
    reasoning = _nested_int(payload.get("completion_tokens_details"), "reasoning_tokens")
    if reasoning is None:
        reasoning = _as_int(payload.get("reasoning_tokens"))
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cache_read_tokens=cached,
        cache_write_tokens=None,
        reasoning_tokens=reasoning,
    )


def _anthropic_like(payload: Mapping[str, Any]) -> Usage | None:
    inbound = _as_int(payload.get("input_tokens"))
    if inbound is None:
        return None
    read = _as_int(payload.get("cache_read_input_tokens"))
    write = _as_int(payload.get("cache_creation_input_tokens"))
    completion = _as_int(payload.get("output_tokens")) or 0
    # input_tokens excludes cached tokens, so the total adds reads and writes back in.
    prompt = inbound + (read or 0) + (write or 0)
    # Anthropic folds reasoning into output_tokens and reports no separate counter.
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cache_read_tokens=read,
        cache_write_tokens=write,
    )


def _gemini_like(payload: Mapping[str, Any]) -> Usage | None:
    prompt = _as_int(payload.get("promptTokenCount"))
    if prompt is None:
        return None
    completion = _as_int(payload.get("candidatesTokenCount")) or 0
    total = _as_int(payload.get("totalTokenCount")) or (prompt + completion)
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cache_read_tokens=_as_int(payload.get("cachedContentTokenCount")),
        cache_write_tokens=None,
        reasoning_tokens=_as_int(payload.get("thoughtsTokenCount")),
    )


#: Dialects are matched by their discriminating field; the order is the priority.
_DIALECTS = (
    ("openai", _openai_like),
    ("anthropic", _anthropic_like),
    ("gemini", _gemini_like),
)


def normalize_usage(data: Mapping[str, Any] | None) -> Usage:
    """Convert a response or bare usage object into Usage, falling back to all zeros."""
    payload = usage_payload(data)
    # Unrecognized usage never raises: usage is observation and must not fail a successful call.
    for _, parse in _DIALECTS:
        parsed = parse(payload)
        if parsed is not None:
            return parsed
    # Without even a prompt count, keep the total if there is one and zero the rest.
    total = _as_int(payload.get("total_tokens")) or _as_int(payload.get("totalTokenCount"))
    if total:
        return Usage(prompt_tokens=0, completion_tokens=0, total_tokens=total)
    return Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)


def hit_ratio(usage: Usage) -> float | None:
    """Return cache reads over total input tokens, or None when the data is missing."""
    # None and 0% are different facts: no data versus a confirmed miss.
    if usage.cache_read_tokens is None or usage.prompt_tokens <= 0:
        return None
    # Clamp to 1.0 because a ratio above 100% only reads as a bug.
    return min(1.0, usage.cache_read_tokens / usage.prompt_tokens)


__all__ = ["Usage", "hit_ratio", "normalize_usage", "usage_payload"]
