"""OpenAI 兼容协议（/chat/completions）。

阶段 30 起从 ``ai/client.py`` 迁入：请求构造、Turn 解析、流式累加器的逻辑逐字
保留，只有传输改走 ``ai/transport.py``（获得重试）。``ai/client.py`` 对这些名字
再导出，老调用方与老测试不动。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import Config
from ..usage import Usage
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
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """system 走 messages 首条，不写回调用方的 messages。

    max_tokens 为 None（默认）时**不放这个字段**：让服务商决定上限，也避免把
    推理模型的思维链计进我们的固定配额里。
    """
    request: dict[str, Any] = {
        "model": config.model,
        "messages": ([{"role": "system", "content": system}] if system else []) + list(messages),
    }
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    if tools:
        request["tools"] = list(tools)
    return request


def _reasoning_text(raw: Mapping[str, Any]) -> str:
    """把一段 delta / message 里的思维链归一成字符串。

    三家写法：`reasoning`（OpenAI 兼容）/ `reasoning_content`（DeepSeek 系）/
    `reasoning_details`（分片数组，取其中的 text）。归一的口径与 `content_text`
    一致——流式与非流式两条路径必须给出同一个值（B9）。
    """
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
    try:
        choice = data["choices"][0]
        raw = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        # 只回一段截断的响应：完整响应体可能很大，也可能含不该进日志的内容。
        raise LLMError(f"响应缺少 choices[0].message：{repr(data)[:300]}") from exc

    tool_calls = list(raw.get("tool_calls") or [])
    text = content_text(raw.get("content"))

    # 只保留协议字段：服务商可能附带的额外字段（如 reasoning_content）
    # 原样回传会污染下一轮请求。
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
    turn = parse_turn(data)
    return Reply(text=turn.text, usage=turn.usage, model=turn.model)


def post(
    config: Config,
    request: dict[str, Any],
    *,
    client: httpx.Client | None = None,
    policy: RetryPolicy | None = None,
) -> dict:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    # 注入的 client（测试）归调用方管；没有就复用进程级的那个（不关它）。
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
        # 网关返回 HTML 错误页之类：必须收敛成 LLMError，否则调用方按
        # "模型层失败"分类的路径会漏掉它（svc 会把它归成 internal）。
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
    request = build_request(
        config, messages, system=system, tools=tools, max_tokens=max_tokens
    )
    return parse_turn(post(config, request, client=client))


# ---------------- 流式（F3） ----------------


@dataclass(frozen=True)
class StreamState:
    """流式累加器的状态：**纯数据**，逐帧 fold 出来，便于按 fixture 断言。

    只保留 `parse_turn` 同样要用的字段，于是 `to_turn()` 的产物与非流式解析能逐字段
    比较（B9）。`tool_calls` 存元组而不是字典：状态不可变，fold 时才复制。
    """

    content: str = ""
    reasoning: str = ""
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
    """浅拷贝外层、深拷贝 `function`：fold 会改 arguments，不能碰到入参的嵌套字典。"""
    return [{**call, "function": dict(call.get("function") or {})} for call in calls]


def merge_stream_chunk(state: StreamState, chunk: Mapping[str, Any]) -> StreamState:
    """把一帧流式响应并进状态。**纯函数**：不改入参，返回新状态。

    最容易写错的是 `tool_calls`：`function.arguments` 是**分片**到达的，必须按 `index`
    归并后逐段拼接，拼完才是一条合法的 JSON 字符串。同一批调用的分片可以交错，所以
    归并只认 index、不认到达顺序。非流式路径看不到这一步，因此这里单独受测。

    `choices` 为空是合法的：带 `stream_options.include_usage` 时末帧只带 `usage`。
    """
    choices = chunk.get("choices") or []
    choice = choices[0] if choices else {}
    delta = choice.get("delta") or {}

    calls = _copy_tool_calls(state.tool_calls)
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
            # 名字通常只来一片；按增量拼接对「分片送名字」的端点同样成立。
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
    """一帧里的正文增量。工具调用的参数分片不算：它们不是给人看的文本。"""
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    return str(delta.get("content") or "")


def delta_reasoning(chunk: Mapping[str, Any]) -> str:
    """一帧里的思维链增量。与 `delta_text` 分开：它不是要显示成回复的正文。"""
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
    """流式调用，返回与 `chat` **同形**的 `Turn`。

    `on_delta` 每收到一段正文就回调一次，`on_reasoning` 每收到一段**思维链**回调一次；
    它们做什么与本模块无关（svc 把它接到事件流上）。两个回调分开是有意的：正文要合成
    回复气泡，思维链只是"它在想"——粘在一起会让空正文的那一轮看起来像真有回复。
    回调抛错会中断这次调用——这是有意的：实现方只该做入队，不该失败。
    """
    request = build_request(
        config, messages, system=system, tools=tools, max_tokens=max_tokens
    )
    request["stream"] = True
    # 兼容端点会用这一项在末帧补 usage；不认它的端点会忽略，代价只是 usage 归零。
    request["stream_options"] = {"include_usage": True}
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    http = client or shared_client()
    state = StreamState()
    # 流式的重试窗口在 send_stream 里只到响应头为止；响应体阶段的网络错误在
    # 这里收敛成 LLMError，不重试——delta 已经回调出去了，重放会造成重复正文。
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
