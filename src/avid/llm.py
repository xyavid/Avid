"""最小可用的 OpenAI 兼容模型调用。

直接用 HTTP 打 /chat/completions，不套 SDK：请求体与响应字段保持可见，
阶段 1 加工具调用时只是往 payload 里多塞几个字段。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import Config

TIMEOUT_SECONDS = 60.0


class LLMError(Exception):
    """调用失败。信息包含状态码与响应正文片段，便于定位。"""


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


def build_payload(config: Config, prompt: str) -> dict:
    return {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
    }


def parse_reply(data: dict) -> Reply:
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"响应缺少 choices[0].message.content：{data!r}") from exc

    raw = data.get("usage") or {}
    usage = Usage(
        prompt_tokens=int(raw.get("prompt_tokens", 0)),
        completion_tokens=int(raw.get("completion_tokens", 0)),
        total_tokens=int(raw.get("total_tokens", 0)),
    )
    return Reply(text=text, usage=usage, model=str(data.get("model", "")))


def ask(config: Config, prompt: str, *, client: httpx.Client | None = None) -> Reply:
    """发一次提问。传入 client 可复用连接或注入测试 transport。"""
    payload = build_payload(config, prompt)
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    owns_client = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = http.post(config.chat_completions_url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMError(f"请求 {config.chat_completions_url} 失败：{exc}") from exc
    finally:
        if owns_client:
            http.close()

    if response.status_code != 200:
        raise LLMError(f"HTTP {response.status_code} — {response.text[:500]}")

    return parse_reply(response.json())
