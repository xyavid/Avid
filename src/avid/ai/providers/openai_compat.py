"""OpenAI-compatible /chat/completions protocol: request building, Turn parsing, stream folding."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import Config
from ..protocol import (
    DEFAULT_MAX_TOKENS,
    DeltaCallback,
    LLMError,
    PromptTooLongError,
    Reply,
    Turn,
    assistant_message,
    content_text,
    iter_sse_events,
    prompt_too_long,
    usage_of,
)
from ..transport import RetryPolicy, send, send_stream, shared_client
from ..usage import Usage


def build_payload(config: Config, prompt: str) -> dict[str, Any]:
    """Build the minimal request body for a single user prompt."""
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if config.extra_body:
        payload.update(config.extra_body)
    return payload


def build_request(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Build the request body, prepending the system message without mutating the caller's list."""
    request: dict[str, Any] = {
        "model": config.model,
        "messages": ([{"role": "system", "content": system}] if system else []) + list(messages),
    }
    # A None max_tokens leaves the field out, so the provider decides the output cap.
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    if tools:
        request["tools"] = list(tools)
    # BYOK passthrough (routing params etc.) merges last: an explicit override is deliberate.
    if config.extra_body:
        request.update(config.extra_body)
    return request


def _headers(config: Config, *, stream: bool = False) -> dict[str, str]:
    """Bearer auth only when a key exists (local Ollama has none); BYOK extra headers merge last."""
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    if stream:
        headers["Accept"] = "text/event-stream"
    if config.extra_headers:
        headers.update(config.extra_headers)
    return headers


def _reasoning_text(raw: Mapping[str, Any]) -> str:
    """Normalize the three known spellings of reasoning text into one string."""
    for key in ("reasoning_content", "reasoning"):
        value = raw.get(key)
        if isinstance(value, str):
            return value
    details = raw.get("reasoning_details")
    if isinstance(details, list):
        return "".join(
            item.get("text", "")
            for item in details
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        )
    return ""


def parse_turn(data: dict[str, Any]) -> Turn:
    """Parse a non-streaming chat-completions response into a Turn."""
    try:
        choice = data["choices"][0]
        raw = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        # Only a truncated body is echoed: a full response can be large or sensitive.
        raise LLMError(f"响应缺少 choices[0].message：{repr(data)[:300]}") from exc

    tool_calls = list(raw.get("tool_calls") or [])
    text = content_text(raw.get("content"))

    # Only protocol fields are kept: extra provider fields would pollute the next request.
    return Turn(
        message=assistant_message(text, tool_calls),
        text=text,
        tool_calls=tool_calls,
        usage=usage_of(data),
        model=str(data.get("model", "")),
        finish_reason=str(choice.get("finish_reason", "")),
        reasoning=_reasoning_text(raw),
    )


def parse_reply(data: dict[str, Any]) -> Reply:
    """Parse a chat-completions response into the legacy Reply shape."""
    turn = parse_turn(data)
    return Reply(text=turn.text, usage=turn.usage, model=turn.model)


def post(
    config: Config,
    request: dict[str, Any],
    *,
    client: httpx.Client | None = None,
    policy: RetryPolicy | None = None,
) -> dict:
    """POST a request and return the decoded JSON body, raising LLMError on failure."""
    headers = _headers(config)

    # An injected client belongs to the caller; otherwise the shared process client is reused.
    http = client or shared_client()
    response = send(
        http, "POST", config.chat_completions_url, headers=headers, json_body=request,
        policy=policy,
    )

    if response.status_code != 200:
        if prompt_too_long(response.status_code, response.text):
            raise PromptTooLongError(
                f"HTTP {response.status_code} — 上下文超限：{response.text[:300]}"
            )
        raise LLMError(f"HTTP {response.status_code} — {response.text[:500]}")

    try:
        return response.json()
    except ValueError as exc:
        # A gateway may return an HTML error page, which must still surface as LLMError.
        raise LLMError(f"响应不是合法 JSON：{response.text[:200]}") from exc


def chat(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    client: httpx.Client | None = None,
) -> Turn:
    """Send one non-streaming request and return the parsed Turn."""
    request = build_request(
        config, messages, system=system, tools=tools, max_tokens=max_tokens
    )
    return parse_turn(post(config, request, client=client))


@dataclass(frozen=True)
class StreamState:
    """Immutable per-response accumulator state, folded one chunk at a time."""

    content: str = ""
    reasoning: str = ""
    # A tuple keeps the state immutable; folding copies the calls before changing them.
    tool_calls: tuple[dict[str, Any], ...] = ()
    usage: Any = None
    model: str = ""
    finish_reason: str = ""

    def to_turn(self) -> Turn:
        tool_calls = [dict(call) for call in self.tool_calls]
        return Turn(
            message=assistant_message(self.content, tool_calls),
            text=self.content,
            tool_calls=tool_calls,
            usage=self.usage or Usage(0, 0, 0),
            model=self.model,
            finish_reason=self.finish_reason,
            reasoning=self.reasoning,
        )


def _blank_tool_call() -> dict[str, Any]:
    return {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}


def _copy_tool_calls(calls: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Copy the call list and each function dict, so folding never mutates its input."""
    return [{**call, "function": dict(call.get("function") or {})} for call in calls]


def merge_stream_chunk(state: StreamState, chunk: Mapping[str, Any]) -> StreamState:
    """Fold one stream chunk into the state, returning a new state without touching the input."""
    choices = chunk.get("choices") or []
    # An empty choices list is legal: the final usage-only frame carries none.
    choice = choices[0] if choices else {}
    delta = choice.get("delta") or {}

    calls = _copy_tool_calls(state.tool_calls)
    # Argument fragments arrive interleaved, so they are joined per index, not per arrival.
    for piece in delta.get("tool_calls") or []:
        index = int(piece.get("index", len(calls)))
        while len(calls) <= index:
            calls.append(_blank_tool_call())
        target = calls[index]
        if piece.get("id"):
            target["id"] = str(piece["id"])
        if piece.get("type"):
            target["type"] = str(piece["type"])
        function = piece.get("function") or {}
        if function.get("name"):
            # A name usually arrives in one piece, but appending also covers split names.
            target["function"]["name"] = str(target["function"].get("name", "")) + str(
                function["name"]
            )
        if function.get("arguments"):
            target["function"]["arguments"] = str(
                target["function"].get("arguments", "")
            ) + str(function["arguments"])

    finish = choice.get("finish_reason")
    return StreamState(
        content=state.content + str(delta.get("content") or ""),
        reasoning=state.reasoning + _reasoning_text(delta),
        tool_calls=tuple(calls),
        usage=usage_of(chunk) if chunk.get("usage") else state.usage,
        model=str(chunk.get("model") or state.model),
        finish_reason=str(finish) if finish else state.finish_reason,
    )


def delta_text(chunk: Mapping[str, Any]) -> str:
    """Return the text delta of one chunk; tool-call argument fragments are not user text."""
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    return str(delta.get("content") or "")


def delta_reasoning(chunk: Mapping[str, Any]) -> str:
    """Return the reasoning delta of one chunk, kept apart from the visible reply text."""
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    return _reasoning_text(choices[0].get("delta") or {})


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
    """Send one streaming request and return the assembled Turn."""
    request = build_request(
        config, messages, system=system, tools=tools, max_tokens=max_tokens
    )
    request["stream"] = True
    # include_usage asks for a final usage frame; endpoints that ignore it only lose usage.
    request["stream_options"] = {"include_usage": True}
    headers = _headers(config, stream=True)

    http = client or shared_client()
    state = StreamState()
    # The retry window ends at the response headers; body errors raise because deltas already fired.
    try:
        response = send_stream(
            http, "POST", config.chat_completions_url, headers=headers, json_body=request
        )
        with contextlib.closing(response):
            if response.status_code != 200:
                response.read()
                if prompt_too_long(response.status_code, response.text):
                    raise PromptTooLongError(
                        f"HTTP {response.status_code} — 上下文超限：{response.text[:300]}"
                    )
                raise LLMError(f"HTTP {response.status_code} — {response.text[:500]}")
            for chunk in iter_sse_events(response.iter_lines()):
                state = merge_stream_chunk(state, chunk)
                if on_delta is not None:
                    text = delta_text(chunk)
                    if text:
                        on_delta(text)
                if on_reasoning is not None:
                    thinking = delta_reasoning(chunk)
                    if thinking:
                        on_reasoning(thinking)
    except httpx.HTTPError as exc:
        raise LLMError(f"请求 {config.chat_completions_url} 失败：{exc}") from exc

    return state.to_turn()
