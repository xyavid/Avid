"""模型调用层门面：按 provider 分发到 ``providers/``。

直接用 HTTP 不套 SDK：请求体与响应字段保持可见，阶段 4 的 trace 与评测依赖这一点。

阶段 30 起，``client.py`` 只回答"这次调用走哪家协议"：OpenAI 兼容、Anthropic
Messages、Gemini generateContent 三条实现路径在 ``providers/`` 里，每家对循环
返回**同形**的 :class:`Turn`（流式与非流式也同形，B9）。传输与重试在
``ai/transport.py``。这里同时保留 OpenAI 兼容路径的既有公开名与窗口探测
（``fetch_context_length``，OpenAI 兼容网关专属）作为再导出，老调用方不动。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

import httpx

from .config import Config, model_info_enabled
from .protocol import (  # 再导出：三个 provider 与门面共用同一套词表
    DEFAULT_MAX_TOKENS,
    DeltaCallback,
    LLMError,
    PromptTooLongError,
    Reply,
    Turn,
    iter_sse_events,
)
from .providers import openai_compat
from .transport import (  # 再导出：测试与调用方从 client 拿这些名字
    CONNECT_TIMEOUT_SECONDS,
    TIMEOUT_SECONDS,
    RetryPolicy,
    _timeout,
    shared_client,
)
from .usage import Usage as Usage  # 再导出：`from avid.ai.client import Usage` 的老路径照旧
from .usage import normalize_usage

# OpenAI 兼容路径的公开名（老测试与直调方继续从这里 import）。
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

# 进程内缓存（重导出给测试清空用——见 test_usage.py）。
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
    """按 ``config.resolved_provider`` 分发的非流式调用。"""
    return _impl(config.resolved_provider).chat(
        config, messages, system=system, tools=tools, max_tokens=max_tokens, client=client
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
    """按 ``config.resolved_provider`` 分发的流式调用，返回与 `chat_completion` 同形的 Turn。"""
    return _impl(config.resolved_provider).stream(
        config,
        messages,
        system=system,
        tools=tools,
        max_tokens=max_tokens,
        on_delta=on_delta,
        on_reasoning=on_reasoning,
        client=client,
    )


def _impl(provider: str):
    from .providers import impl

    return impl(provider)


def ask(config: Config, prompt: str, *, client: httpx.Client | None = None) -> Reply:
    turn = chat_completion(config, [{"role": "user", "content": prompt}], client=client)
    return Reply(text=turn.text, usage=turn.usage, model=turn.model)


# ---------------- 模型窗口探测（OpenAI 兼容网关专属） ----------------
#
# 环境变量与内置表都查不到窗口时，问一次 `{base_url}/models`：OpenAI 兼容网关普遍在
# 这里给 `context_length`，于是"占用率"能在不改配置的前提下显示出来。Anthropic 与
# Gemini 的原生 API 没有这个端点：窗口靠内置表或 AVID_CONTEXT_WINDOW。
#
# 三条纪律：**问不到就回 None**（绝不抛错、绝不猜）、**进程内缓存**（成功与失败都缓存
# 一次，重启进程才会重试）、**只在窗口缺失时问**（调用点负责，见 `runtime/loop.py`）。

#: 探测超时：这是元数据请求，不该跟模型调用共用 60 秒读超时。
MODEL_INFO_TIMEOUT_SECONDS = 5.0

#: 各家中等价的名字（OpenRouter 用 context_length，vLLM 用 max_model_len，等等）。
_WINDOW_KEYS = ("context_length", "context_window", "max_model_len", "max_context_length")


def _window_of(item: Mapping[str, Any]) -> int | None:
    for key in _WINDOW_KEYS:
        value = item.get(key)
        if isinstance(value, bool):  # `True` 是 int 的子类，必须排掉
            continue
        if isinstance(value, int) and value > 0:
            return value
    return None


def fetch_context_length(
    config: Config, *, client: httpx.Client | None = None
) -> int | None:
    """问 provider 要这个模型的上下文窗口。**任何失败都回 None**。

    为什么要它：占用率的分母只在内置表里有值时才有，而自建网关/新模型的窗口只有
    服务商知道。问不到（网络、鉴权、端点不认、模型没列出来）就退回"只报 tokens"，
    不影响任何一次模型调用。

    ``AVID_MODEL_INFO=off`` 关掉它（单测与明确不想联网的部署用）。
    """
    if not model_info_enabled():
        return None
    # /models 探测是 OpenAI 兼容网关的习惯：原生 API 没有等价端点，不白打一次。
    if config.resolved_provider != "openai":
        return None
    key = (config.base_url, config.model)
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
