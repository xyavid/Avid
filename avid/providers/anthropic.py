"""Anthropic Messages API: convert OpenAI-shaped messages to and from the native protocol."""

from __future__ import annotations

import contextlib
import json
from typing import Any

import httpx

from .config import Config
from .protocol import (
    DeltaCallback,
    LLMError,
    PromptTooLongError,
    Turn,
    assistant_message,
    http_error,
    iter_sse_events,
    prompt_too_long,
    usage_of,
)
from .transport import send, send_stream, shared_client

#: Anthropic requires max_tokens; this value is used when the caller passes none.
DEFAULT_MAX_TOKENS = 16384

#: Anthropic API version sent on every request.
ANTHROPIC_VERSION = "2023-06-01"

# stop_reason to the OpenAI-compatible vocabulary; unknown values pass through lowercased.
_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
}


def messages_url(config: Config) -> str:
    """Return the Messages endpoint, tolerating a base URL that already ends with /v1."""
    base = config.base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/v1/messages"


def _headers(config: Config) -> dict[str, str]:
    # A missing key still sends the version header so the error comes from the endpoint,
    # not from a malformed request; BYOK extra headers merge last.
    headers = {
        "anthropic-version": ANTHROPIC_VERSION,
        "Content-Type": "application/json",
    }
    if config.api_key:
        headers["x-api-key"] = config.api_key
    if config.extra_headers:
        headers.update(config.extra_headers)
    return headers


def _text_of(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    return ""


def _tool_use_block(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function") or {}
    # Malformed argument JSON falls back to an empty input object instead of failing the call.
    try:
        args = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    return {
        "type": "tool_use",
        "id": str(call.get("id", "")),
        "name": str(function.get("name", "")),
        "input": args if isinstance(args, dict) else {},
    }


def build_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-shaped messages to Anthropic shape, buffering tool results together."""
    out: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []

    def flush_results() -> None:
        # Consecutive tool results must share one user message, so they are buffered first.
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for message in messages:
        role = message.get("role")
        if role == "tool":
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_call_id", "")),
                    "content": _text_of(message.get("content")),
                }
            )
            continue
        if role == "assistant":
            flush_results()
            blocks: list[dict[str, Any]] = []
            text = _text_of(message.get("content"))
            if text:
                blocks.append({"type": "text", "text": text})
            blocks.extend(_tool_use_block(call) for call in message.get("tool_calls") or [])
            out.append({"role": "assistant", "content": blocks})
            continue
        # Consecutive user messages are merged: Anthropic requires alternating roles.
        flush_results()
        text = _text_of(message.get("content"))
        if out and out[-1].get("role") == "user" and isinstance(out[-1]["content"], str):
            out[-1]["content"] = out[-1]["content"] + "\n\n" + text
        else:
            out.append({"role": "user", "content": text})
    flush_results()
    return out


def build_request(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Build the Messages request body, falling back to the default max_tokens."""
    request: dict[str, Any] = {
        "model": config.model,
        "max_tokens": max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS,
        "messages": build_messages(messages),
    }
    if system:
        request["system"] = system
    if tools:
        # OpenAI's parameters field becomes Anthropic's input_schema.
        request["tools"] = [
            {
                "name": item["function"]["name"],
                "description": item["function"].get("description", ""),
                "input_schema": item["function"].get("parameters", {"type": "object"}),
            }
            for item in tools
        ]
    # 推理强度不在这里映射：Anthropic 的对应物是 thinking 预算（token 数），与 low/medium/high
    # 不是同一档语义，硬编一张对照表就是把别人的调参当成我们的决定。要开就在该提供商的
    # extra_body 里直接写 thinking（它会异步到 extra_body 之后合并，覆盖得动）。
    # BYOK passthrough (routing params etc.) merges last: an explicit override is deliberate.
    if config.extra_body:
        request.update(config.extra_body)
    return request


def _finish_of(stop_reason: Any) -> str:
    reason = str(stop_reason or "")
    return _STOP_REASONS.get(reason, reason.lower())


def _call_of(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(block.get("id", "")),
        "type": "function",
        "function": {
            "name": str(block.get("name", "")),
            "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
        },
    }


def parse_turn(data: dict[str, Any]) -> Turn:
    """Parse a non-streaming Messages response into a Turn."""
    texts: list[str] = []
    reasonings: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in data.get("content") or []:
        kind = block.get("type")
        if kind == "text":
            texts.append(str(block.get("text") or ""))
        elif kind == "thinking":
            reasonings.append(str(block.get("thinking") or ""))
        elif kind == "tool_use":
            calls.append(_call_of(block))
    text = "".join(texts)
    return Turn(
        message=assistant_message(text, calls),
        text=text,
        tool_calls=calls,
        usage=usage_of(data),
        model=str(data.get("model", "")),
        finish_reason=_finish_of(data.get("stop_reason")),
        reasoning="".join(reasonings),
    )


def chat(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    client: httpx.Client | None = None,
) -> Turn:
    """Send one non-streaming Messages request and return the parsed Turn."""
    request = build_request(
        config, messages, system=system, tools=tools, max_tokens=max_tokens
    )
    http = client or shared_client()
    response = send(
        http, "POST", messages_url(config), headers=_headers(config), json_body=request
    )
    if response.status_code != 200:
        if prompt_too_long(response.status_code, response.text):
            raise PromptTooLongError(
                f"HTTP {response.status_code} — 上下文超限：{response.text[:300]}"
            )
        raise http_error(
                    response.status_code,
                    response.text,
                    sent_reasoning_effort=bool(config.reasoning_effort),
                )
    try:
        return parse_turn(response.json())
    except ValueError as exc:
        raise LLMError(f"响应不是合法 JSON：{response.text[:200]}") from exc


# Streaming blocks are keyed by content_block index, which arrives before the deltas filling it.
_BLOCK_KINDS = {"text": "text", "thinking": "thinking", "tool_use": "tool"}


class _Accumulator:
    """Fold the Messages event stream into a Turn, returning per-frame text and reasoning deltas."""

    def __init__(self) -> None:
        self.model = ""
        self.blocks: dict[int, dict[str, Any]] = {}
        self.stop_reason = ""
        self.input_usage: dict[str, int] = {}
        self.output_tokens = 0

    def _block(self, index: int, kind: str = "text", **extra: Any) -> dict[str, Any]:
        return self.blocks.setdefault(
            index, {"kind": kind, "text": "", "id": "", "name": "", "args": "", **extra}
        )

    def feed(self, event: dict[str, Any]) -> tuple[str, str]:
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message") or {}
            self.model = str(message.get("model") or self.model)
            usage = message.get("usage") or {}
            for key in ("input_tokens", "cache_read_input_tokens",
                        "cache_creation_input_tokens"):
                if key in usage:
                    self.input_usage[key] = int(usage[key] or 0)
            return "", ""
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            self._block(
                int(event.get("index", 0)),
                _BLOCK_KINDS.get(str(block.get("type")), "text"),
                id=str(block.get("id", "")),
                name=str(block.get("name", "")),
            )
            return "", ""
        if kind == "content_block_delta":
            slot = self.blocks.get(int(event.get("index", 0)))
            if slot is None:
                return "", ""
            delta = event.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                piece = str(delta.get("text") or "")
                slot["text"] += piece
                return piece, ""
            if dtype == "thinking_delta":
                piece = str(delta.get("thinking") or "")
                slot["text"] += piece
                return "", piece
            if dtype == "input_json_delta":
                slot["args"] += str(delta.get("partial_json") or "")
            return "", ""
        if kind == "message_delta":
            delta = event.get("delta") or {}
            self.stop_reason = str(delta.get("stop_reason") or self.stop_reason)
            usage = event.get("usage") or {}
            if usage.get("output_tokens") is not None:
                self.output_tokens = int(usage["output_tokens"] or 0)
            return "", ""
        return "", ""

    def to_turn(self) -> Turn:
        texts: list[str] = []
        reasonings: list[str] = []
        calls: list[dict[str, Any]] = []
        for slot in self.blocks.values():
            if slot["kind"] == "text":
                texts.append(str(slot["text"]))
            elif slot["kind"] == "thinking":
                reasonings.append(str(slot["text"]))
            elif slot["kind"] == "tool":
                calls.append(
                    {
                        "id": slot["id"],
                        "type": "function",
                        "function": {
                            "name": slot["name"],
                            "arguments": slot["args"] or "{}",
                        },
                    }
                )
        text = "".join(texts)
        # Usage arrives split across message_start and message_delta, so both halves are merged.
        usage_input = {**self.input_usage, "output_tokens": self.output_tokens}
        return Turn(
            message=assistant_message(text, calls),
            text=text,
            tool_calls=calls,
            usage=usage_of(usage_input),
            model=self.model,
            finish_reason=_finish_of(self.stop_reason),
            reasoning="".join(reasonings),
        )


def stream(
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
    """Send one streaming Messages request and return the assembled Turn."""
    request = build_request(
        config, messages, system=system, tools=tools, max_tokens=max_tokens
    )
    request["stream"] = True
    headers = {**_headers(config), "Accept": "text/event-stream"}

    http = client or shared_client()
    acc = _Accumulator()
    try:
        response = send_stream(
            http, "POST", messages_url(config), headers=headers, json_body=request
        )
        with contextlib.closing(response):
            if response.status_code != 200:
                # A streamed response must be read before its body can be inspected.
                response.read()
                if prompt_too_long(response.status_code, response.text):
                    raise PromptTooLongError(
                        f"HTTP {response.status_code} — 上下文超限：{response.text[:300]}"
                    )
                raise http_error(
                    response.status_code,
                    response.text,
                    sent_reasoning_effort=bool(config.reasoning_effort),
                )
            for event in iter_sse_events(response.iter_lines()):
                text_piece, thinking_piece = acc.feed(event)
                if on_delta is not None and text_piece:
                    on_delta(text_piece)
                if on_reasoning is not None and thinking_piece:
                    on_reasoning(thinking_piece)
    except httpx.HTTPError as exc:
        raise LLMError(f"请求 {messages_url(config)} 失败：{exc}") from exc

    return acc.to_turn()
