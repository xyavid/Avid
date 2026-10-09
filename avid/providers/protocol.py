"""Shared protocol vocabulary: Turn and Reply shapes, error types, and the SSE decoder."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
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


#: 端点不认 reasoning_effort 时的可执行提示（阶段 55）：只在 400/422 且这次真带了
#: 推理强度时附上，措辞是「这条像是」——猜错也不至于断言错。
_REASONING_EFFORT_HINT = (
    "（这条像是端点不认 reasoning_effort：在输入区把这次的推理强度改回「不设」，"
    "或者到「设置 → 模型」里把这一档从该模型的档位列表里去掉；"
    "也可以在提供商的「透传请求体字段」里换一个它认识的字段名）"
)


def http_error(status_code: int, body: str, *, sent_reasoning_effort: bool = False) -> LLMError:
    """The one way to turn "the endpoint answered with an error" into an exception.

    Keeping it in one place means the body excerpt, the status code and the repair hints stay
    consistent across the three protocols.
    """
    message = f"HTTP {status_code} — {body[:500]}"
    if sent_reasoning_effort and status_code in (400, 422):
        message += _REASONING_EFFORT_HINT
    return LLMError(message)


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


def content_parts(raw: Any) -> list[Any] | None:
    """分块内容（阶段 59）：列表即内容数组，其余（str / None）返回 None 走纯文本路径。

    存储形态是中立的（见 avid/attachments.py），翻成各家 wire 形状的事在三个适配器里。
    元素按 Any 收：数组里出现什么由外部输入决定，逐块判型是适配器自己的事。
    """
    return raw if isinstance(raw, list) else None


def image_data_url(part: Mapping[str, Any]) -> str:
    """图片块 → data URL（OpenAI 与 Responses 两家都用它）。"""
    return f"data:{part.get('mime')};base64,{part.get('data')}"


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
