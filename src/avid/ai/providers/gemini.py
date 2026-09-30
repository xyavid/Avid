"""Gemini generateContent: convert OpenAI-shaped messages to and from the native protocol."""

from __future__ import annotations

import contextlib
import json
from typing import Any

import httpx

from ..config import Config
from ..protocol import (
    DEFAULT_MAX_TOKENS,
    DeltaCallback,
    LLMError,
    PromptTooLongError,
    Turn,
    assistant_message,
    iter_sse_events,
    prompt_too_long,
    usage_of,
)
from ..transport import send, send_stream, shared_client

# finishReason to the OpenAI-compatible vocabulary; unknown values pass through lowercased.
_FINISH_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
}


def generate_url(config: Config, *, streaming: bool = False) -> str:
    """Return the generateContent or streamGenerateContent URL for the configured model."""
    base = config.base_url.rstrip("/")
    # Streaming uses ?alt=sse so the response is an ordinary SSE line stream.
    action = "streamGenerateContent?alt=sse" if streaming else "generateContent"
    return f"{base}/models/{config.model}:{action}"


def _headers(config: Config) -> dict[str, str]:
    return {"x-goog-api-key": config.api_key, "Content-Type": "application/json"}


def _text_of(raw: Any) -> str:
    return raw if isinstance(raw, str) else ""


def _parts_of(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one OpenAI-shaped message to Gemini parts, excluding tool-result messages."""
    parts: list[dict[str, Any]] = []
    text = _text_of(message.get("content"))
    if text:
        parts.append({"text": text})
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            args = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        parts.append(
            {
                "functionCall": {
                    "name": str(function.get("name", "")),
                    "args": args if isinstance(args, dict) else {},
                }
            }
        )
    return parts


def build_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-shaped messages to Gemini contents, resolving tool_call_id to a name."""
    out: list[dict[str, Any]] = []
    id_to_name: dict[str, str] = {}

    def append(role: str, parts: list[dict[str, Any]]) -> None:
        # Consecutive same-role entries are merged into one contents item.
        if not parts:
            return
        if out and out[-1]["role"] == role:
            out[-1]["parts"].extend(parts)
        else:
            out.append({"role": role, "parts": parts})

    for message in messages:
        role = message.get("role")
        if role == "assistant":
            # functionResponse matches by name, so the id-to-name table grows from assistant turns.
            for call in message.get("tool_calls") or []:
                id_to_name[str(call.get("id", ""))] = str(
                    (call.get("function") or {}).get("name", "")
                )
            append("model", _parts_of(message))
        elif role == "tool":
            name = id_to_name.get(str(message.get("tool_call_id", "")), "")
            append(
                "user",
                [
                    {
                        "functionResponse": {
                            "name": name,
                            # The response field must be a JSON object, so text is wrapped.
                            "response": {"result": _text_of(message.get("content"))},
                        }
                    }
                ],
            )
        else:  # Every other role is a user message.
            append("user", _parts_of(message))
    return out


def build_request(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Build the generateContent request body from config, messages, system and tools."""
    request: dict[str, Any] = {"contents": build_contents(messages)}
    if system:
        request["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        request["tools"] = [
            {"functionDeclarations": [item["function"] for item in tools]}
        ]
    if max_tokens is not None:
        request["generationConfig"] = {"maxOutputTokens": max_tokens}
    return request


def _finish_of(finish_reason: Any) -> str:
    reason = str(finish_reason or "")
    return _FINISH_REASONS.get(reason, reason.lower())


def _call_of(index: int, name: str, args: Any) -> dict[str, Any]:
    return {
        # Gemini sends no call id, so a synthetic one is derived from the call order.
        "id": f"call_{index}",
        "type": "function",
        "function": {
            "name": str(name),
            "arguments": json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False),
        },
    }


def _parts_into(
    parts: list[dict[str, Any]],
    texts: list[str],
    reasonings: list[str],
    calls: list[dict[str, Any]],
) -> tuple[str, str]:
    """Consume one parts list, appending to the accumulators and returning this frame's deltas."""
    text_piece = ""
    thinking_piece = ""
    for part in parts:
        if part.get("thought"):
            piece = str(part.get("text") or "")
            reasonings.append(piece)
            thinking_piece += piece
        elif "text" in part:
            piece = str(part.get("text") or "")
            texts.append(piece)
            text_piece += piece
        elif "functionCall" in part:
            call = part["functionCall"]
            calls.append(_call_of(len(calls), call.get("name", ""), call.get("args")))
    return text_piece, thinking_piece


class _Accumulator:
    """Fold streaming chunks, or one whole non-streaming response, into a Turn."""

    def __init__(self) -> None:
        self.model = ""
        self.texts: list[str] = []
        self.reasonings: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self.finish_reason = ""
        self.usage_metadata: dict[str, Any] | None = None

    def feed(self, chunk: dict[str, Any]) -> tuple[str, str]:
        if chunk.get("modelVersion"):
            self.model = str(chunk["modelVersion"])
        if isinstance(chunk.get("usageMetadata"), dict):
            self.usage_metadata = chunk["usageMetadata"]
        text_piece = thinking_piece = ""
        for candidate in chunk.get("candidates") or []:
            content = candidate.get("content") or {}
            piece, thought = _parts_into(
                content.get("parts") or [], self.texts, self.reasonings, self.calls
            )
            text_piece += piece
            thinking_piece += thought
            if candidate.get("finishReason"):
                self.finish_reason = str(candidate["finishReason"])
        return text_piece, thinking_piece

    def to_turn(self) -> Turn:
        text = "".join(self.texts)
        return Turn(
            message=assistant_message(text, self.calls),
            text=text,
            tool_calls=list(self.calls),
            usage=usage_of(self.usage_metadata or {}),
            model=self.model,
            finish_reason=_finish_of(self.finish_reason),
            reasoning="".join(self.reasonings),
        )


def _raise_for_status(response: httpx.Response, url: str) -> None:
    # Context overflow is recoverable for the caller; every other failure is a plain LLMError.
    if response.status_code == 200:
        return
    if prompt_too_long(response.status_code, response.text):
        raise PromptTooLongError(
            f"HTTP {response.status_code} — 上下文超限：{response.text[:300]}"
        )
    raise LLMError(f"HTTP {response.status_code} — {response.text[:500]}")


def chat(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    client: httpx.Client | None = None,
) -> Turn:
    """Send one non-streaming generateContent request and return the parsed Turn."""
    request = build_request(
        config, messages, system=system, tools=tools, max_tokens=max_tokens
    )
    url = generate_url(config)
    http = client or shared_client()
    response = send(http, "POST", url, headers=_headers(config), json_body=request)
    _raise_for_status(response, url)
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"响应不是合法 JSON：{response.text[:200]}") from exc
    # The non-streaming path reuses the streaming accumulator so both shapes stay identical.
    acc = _Accumulator()
    acc.feed(data)
    return acc.to_turn()


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
    """Send one streaming generateContent request and return the assembled Turn."""
    request = build_request(
        config, messages, system=system, tools=tools, max_tokens=max_tokens
    )
    url = generate_url(config, streaming=True)
    headers = {**_headers(config), "Accept": "text/event-stream"}

    http = client or shared_client()
    acc = _Accumulator()
    try:
        response = send_stream(http, "POST", url, headers=headers, json_body=request)
        with contextlib.closing(response):
            _raise_for_status(response, url)
            for chunk in iter_sse_events(response.iter_lines()):
                text_piece, thinking_piece = acc.feed(chunk)
                if on_delta is not None and text_piece:
                    on_delta(text_piece)
                if on_reasoning is not None and thinking_piece:
                    on_reasoning(thinking_piece)
    except httpx.HTTPError as exc:
        raise LLMError(f"请求 {url} 失败：{exc}") from exc

    return acc.to_turn()
