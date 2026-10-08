"""OpenAI Responses API（/responses）：item 式输入输出 + 类型化 SSE 事件。

与 /chat/completions 的差异只在本模块内部消化：messages 翻译成 input items
（工具结果 → function_call_output、assistant 的工具调用 → function_call），
响应 output 数组翻回同形 Turn（正文 + chat-completions 形状的 tool_calls），
流式的类型化事件折进同一份 StreamState。循环与 transcript 对协议无感。
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Config
from .protocol import (
    DEFAULT_MAX_TOKENS,
    DeltaCallback,
    LLMError,
    PromptTooLongError,
    Turn,
    assistant_message,
    content_text,
    iter_sse_events,
    prompt_too_long,
    usage_of,
)
from .transport import RetryPolicy, send, send_stream, shared_client
from .usage import Usage


def endpoint_url(config: Config) -> str:
    return f"{config.base_url.rstrip('/')}/responses"


def _input_items(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate chat-completions messages into Responses input items.

    工具结果与工具调用是 item 类型（function_call_output / function_call），
    普通消息保持 role + 纯文本内容。
    """
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(message.get("content") or ""),
                }
            )
            continue
        if role == "assistant":
            text = content_text(message.get("content"))
            if text:
                items.append({"role": "assistant", "content": text})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or ""),
                    }
                )
            continue
        items.append({"role": role, "content": str(message.get("content") or "")})
    return items


def _tool_schemas(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Flatten chat-completions tool definitions into the Responses flat shape."""
    if not tools:
        return None
    flattened: list[dict[str, Any]] = []
    for item in tools:
        function = item.get("function") or {}
        flat: dict[str, Any] = {
            "type": "function",
            "name": function.get("name", ""),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        }
        if function.get("description"):
            flat["description"] = function["description"]
        flattened.append(flat)
    return flattened


def build_request(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Build the request body; the system prompt travels as top-level instructions."""
    request: dict[str, Any] = {
        "model": config.model,
        "input": _input_items(messages),
    }
    if system:
        request["instructions"] = system
    # A None max_tokens leaves the field out, so the provider decides the output cap.
    if max_tokens is not None:
        request["max_output_tokens"] = max_tokens
    flattened = _tool_schemas(tools)
    if flattened:
        request["tools"] = flattened
    # 推理强度：Responses API 把它收在 reasoning 对象里（同一个三档语义）
    if config.reasoning_effort:
        request["reasoning"] = {"effort": config.reasoning_effort}
    # BYOK passthrough merges last: an explicit override is deliberate.
    if config.extra_body:
        request.update(config.extra_body)
    return request


def _headers(config: Config, *, stream: bool = False) -> dict[str, str]:
    """Bearer auth only when a key exists; BYOK extra headers merge last."""
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    if stream:
        headers["Accept"] = "text/event-stream"
    if config.extra_headers:
        headers.update(config.extra_headers)
    return headers


def _reasoning_text(items: list[Any]) -> str:
    """Join the summary text of every reasoning item into one string."""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, Mapping) or item.get("type") != "reasoning":
            continue
        for piece in item.get("summary") or []:
            if isinstance(piece, Mapping) and isinstance(piece.get("text"), str):
                parts.append(piece["text"])
    return "".join(parts)


def _output_tool_calls(output: list[Any]) -> list[dict[str, Any]]:
    """Collect function_call items back into the chat-completions tool_calls shape."""
    calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "function_call":
            continue
        calls.append(
            {
                "id": str(item.get("call_id") or item.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(item.get("name") or ""),
                    "arguments": str(item.get("arguments") or ""),
                },
            }
        )
    return calls


def _finish_reason(data: Mapping[str, Any]) -> str:
    """Map the Responses status onto a chat-completions-style finish reason."""
    status = str(data.get("status") or "")
    if status == "incomplete":
        details = data.get("incomplete_details") or {}
        return str(details.get("reason") or "incomplete")
    return "stop" if status == "completed" else status


def parse_turn(data: dict[str, Any]) -> Turn:
    """Parse a non-streaming Responses payload into a Turn."""
    output = data.get("output")
    if not isinstance(output, list):
        # Only a truncated body is echoed: a full response can be large or sensitive.
        raise LLMError(f"响应缺少 output 数组：{repr(data)[:300]}")

    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        for piece in item.get("content") or []:
            if isinstance(piece, Mapping) and piece.get("type") == "output_text":
                text_parts.append(str(piece.get("text") or ""))
    text = "".join(text_parts)
    tool_calls = _output_tool_calls(output)

    # Only protocol fields are kept: extra provider fields would pollute the next request.
    return Turn(
        message=assistant_message(text, tool_calls),
        text=text,
        tool_calls=tool_calls,
        usage=usage_of(data),
        model=str(data.get("model", "")),
        finish_reason=_finish_reason(data),
        reasoning=_reasoning_text(output),
    )


def post(
    config: Config,
    request: dict[str, Any],
    *,
    client: httpx.Client | None = None,
    policy: RetryPolicy | None = None,
) -> dict:
    """POST a request and return the decoded JSON body, raising LLMError on failure."""
    headers = _headers(config)
    http = client or shared_client()
    url = endpoint_url(config)
    response = send(http, "POST", url, headers=headers, json_body=request, policy=policy)

    if response.status_code != 200:
        if prompt_too_long(response.status_code, response.text):
            raise PromptTooLongError(
                f"HTTP {response.status_code} — 上下文超限：{response.text[:300]}"
            )
        raise LLMError(f"HTTP {response.status_code} — {response.text[:500]}")

    try:
        return response.json()
    except ValueError as exc:
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
    """Immutable per-response accumulator, folded one typed event at a time."""

    content: str = ""
    reasoning: str = ""
    # output_index → partial function_call; a tuple keeps the state immutable.
    tool_calls: tuple[dict[str, Any], ...] = ()
    usage: Any = None
    model: str = ""
    finish_reason: str = ""

    def to_turn(self) -> Turn:
        tool_calls = [dict(call) for call in self.tool_calls if call.get("function", {}).get("name")]
        return Turn(
            message=assistant_message(self.content, tool_calls),
            text=self.content,
            tool_calls=tool_calls,
            usage=self.usage or Usage(0, 0, 0),
            model=self.model,
            finish_reason=self.finish_reason,
            reasoning=self.reasoning,
        )


def _copy_calls(calls: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [{**call, "function": dict(call.get("function") or {})} for call in calls]


def _blank_call() -> dict[str, Any]:
    return {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}


def merge_stream_chunk(state: StreamState, chunk: Mapping[str, Any]) -> StreamState:
    """Fold one typed SSE event into the state, returning a new state."""
    kind = str(chunk.get("type") or "")

    if kind == "response.output_text.delta":
        return StreamState(
            content=state.content + str(chunk.get("delta") or ""),
            reasoning=state.reasoning,
            tool_calls=state.tool_calls,
            usage=state.usage,
            model=state.model,
            finish_reason=state.finish_reason,
        )

    if kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
        return StreamState(
            content=state.content,
            reasoning=state.reasoning + str(chunk.get("delta") or ""),
            tool_calls=state.tool_calls,
            usage=state.usage,
            model=state.model,
            finish_reason=state.finish_reason,
        )

    if kind == "response.output_item.added":
        item = chunk.get("item") or {}
        if isinstance(item, Mapping) and item.get("type") == "function_call":
            calls = _copy_calls(state.tool_calls)
            calls.append(
                {
                    "id": str(item.get("call_id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or ""),
                    },
                }
            )
            return StreamState(
                content=state.content,
                reasoning=state.reasoning,
                tool_calls=tuple(calls),
                usage=state.usage,
                model=state.model,
                finish_reason=state.finish_reason,
            )

    if kind == "response.function_call_arguments.delta":
        calls = _copy_calls(state.tool_calls)
        index = chunk.get("output_index")
        if isinstance(index, int) and 0 <= index < len(calls):
            calls[index]["function"]["arguments"] = str(
                calls[index]["function"].get("arguments", "")
            ) + str(chunk.get("delta") or "")
            return StreamState(
                content=state.content,
                reasoning=state.reasoning,
                tool_calls=tuple(calls),
                usage=state.usage,
                model=state.model,
                finish_reason=state.finish_reason,
            )

    if kind in ("response.completed", "response.incomplete", "response.failed"):
        response = chunk.get("response") or {}
        if isinstance(response, Mapping):
            status = "incomplete" if kind == "response.incomplete" else "completed"
            return StreamState(
                content=state.content,
                reasoning=state.reasoning,
                tool_calls=state.tool_calls,
                usage=usage_of(response) if response.get("usage") else state.usage,
                model=str(response.get("model") or state.model),
                finish_reason=_finish_reason({**response, "status": status}),
            )

    return state


def delta_text(chunk: Mapping[str, Any]) -> str:
    """Return the visible text delta of one event; argument fragments are not user text."""
    if str(chunk.get("type") or "") == "response.output_text.delta":
        return str(chunk.get("delta") or "")
    return ""


def delta_reasoning(chunk: Mapping[str, Any]) -> str:
    """Return the reasoning delta of one event, kept apart from the visible reply text."""
    if str(chunk.get("type") or "") in (
        "response.reasoning_summary_text.delta",
        "response.reasoning_text.delta",
    ):
        return str(chunk.get("delta") or "")
    return ""


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
    headers = _headers(config, stream=True)

    http = client or shared_client()
    state = StreamState()
    url = endpoint_url(config)
    # The retry window ends at the response headers; body errors raise because deltas already fired.
    try:
        response = send_stream(http, "POST", url, headers=headers, json_body=request)
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
        raise LLMError(f"请求 {url} 失败：{exc}") from exc

    return state.to_turn()
