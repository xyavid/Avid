"""模型调用层：OpenAI 兼容的 /chat/completions。

直接用 HTTP 不套 SDK：请求体与响应字段保持可见，阶段 4 的 trace 与评测依赖这一点。
协议选择见 dev/drafts/requirements.md 的 D-03 / D-08。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import Config

TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_TOKENS = 8000


class LLMError(Exception):
    """调用失败。信息包含状态码与响应正文片段，便于定位。"""


class PromptTooLongError(LLMError):
    """请求超出模型上下文长度。循环据此做一次兜底压缩后重试。"""


# 各家措辞不同，命中任一即可判定为上下文超限。
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
)


def _prompt_too_long(response: httpx.Response) -> bool:
    if response.status_code not in (400, 413, 422):
        return False
    body = response.text.lower()
    return any(sign in body for sign in _PROMPT_TOO_LONG_SIGNS)


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class Reply:
    text: str
    usage: Usage
    model: str


@dataclass(frozen=True)
class Turn:
    """一轮模型响应。message 是清洗过的 assistant 消息，可直接追加进 messages。"""

    message: dict[str, Any]
    text: str
    tool_calls: list[dict[str, Any]]
    usage: Usage
    model: str
    finish_reason: str


def build_payload(config: Config, prompt: str) -> dict[str, Any]:
    return {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
    }


def build_request(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """system 走 messages 首条，不写回调用方的 messages。"""
    request: dict[str, Any] = {
        "model": config.model,
        "max_tokens": max_tokens,
        "messages": ([{"role": "system", "content": system}] if system else []) + list(messages),
    }
    if tools:
        request["tools"] = list(tools)
    return request


def _usage_of(data: dict[str, Any]) -> Usage:
    raw = data.get("usage") or {}
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens", 0)),
        completion_tokens=int(raw.get("completion_tokens", 0)),
        total_tokens=int(raw.get("total_tokens", 0)),
    )


def parse_turn(data: dict[str, Any]) -> Turn:
    try:
        choice = data["choices"][0]
        raw = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"响应缺少 choices[0].message：{data!r}") from exc

    tool_calls = list(raw.get("tool_calls") or [])

    # 只保留协议字段：服务商可能附带的额外字段（如 reasoning_content）
    # 原样回传会污染下一轮请求。
    message: dict[str, Any] = {"role": "assistant", "content": raw.get("content", "")}
    if tool_calls:
        message["tool_calls"] = tool_calls

    return Turn(
        message=message,
        text=raw.get("content") or "",
        tool_calls=tool_calls,
        usage=_usage_of(data),
        model=str(data.get("model", "")),
        finish_reason=str(choice.get("finish_reason", "")),
    )


def parse_reply(data: dict[str, Any]) -> Reply:
    turn = parse_turn(data)
    return Reply(text=turn.text, usage=turn.usage, model=turn.model)


def post(config: Config, request: dict[str, Any], *, client: httpx.Client | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    owns_client = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = http.post(config.chat_completions_url, json=request, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMError(f"请求 {config.chat_completions_url} 失败：{exc}") from exc
    finally:
        if owns_client:
            http.close()

    if response.status_code != 200:
        if _prompt_too_long(response):
            raise PromptTooLongError(
                f"HTTP {response.status_code} — 上下文超限：{response.text[:300]}"
            )
        raise LLMError(f"HTTP {response.status_code} — {response.text[:500]}")

    return response.json()


def chat_completion(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    client: httpx.Client | None = None,
) -> Turn:
    request = build_request(
        config, messages, system=system, tools=tools, max_tokens=max_tokens
    )
    return parse_turn(post(config, request, client=client))


def ask(config: Config, prompt: str, *, client: httpx.Client | None = None) -> Reply:
    turn = chat_completion(
        config, [{"role": "user", "content": prompt}], client=client
    )
    return Reply(text=turn.text, usage=turn.usage, model=turn.model)
