"""模型调用层：OpenAI 兼容的 /chat/completions。

直接用 HTTP 不套 SDK：请求体与响应字段保持可见，阶段 4 的 trace 与评测依赖这一点。
协议选择见 dev/drafts/requirements.md 的 D-03 / D-08。

流式（F3）与非流式**同形**：`stream_completion` 返回的 `Turn` 与 `chat_completion`
逐字段可比，循环因此不需要知道模型是流式还是整条返回的（设计文档 §7.5）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
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


def _usage_of(data: Mapping[str, Any]) -> Usage:
    raw = data.get("usage") or {}
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens", 0)),
        completion_tokens=int(raw.get("completion_tokens", 0)),
        total_tokens=int(raw.get("total_tokens", 0)),
    )


def _content_text(raw: Any) -> str:
    """把 assistant 的 ``content`` 归一成正文。

    各家的等价写法必须走同一条路：``null`` / 缺字段 / 分片数组都要变成字符串，
    否则非流式路径会把 ``None`` 原样写进回传给模型的消息（而流式路径写 ``""``），
    两条路径宣称的"同形"就不成立。
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):  # content parts：拼其中的 text 字段
        return "".join(
            item.get("text", "")
            for item in raw
            if isinstance(item, dict) and isinstance(item.get("text"), str)
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
    text = _content_text(raw.get("content"))

    # 只保留协议字段：服务商可能附带的额外字段（如 reasoning_content）
    # 原样回传会污染下一轮请求。
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls

    return Turn(
        message=message,
        text=text,
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

    try:
        return response.json()
    except ValueError as exc:
        # 网关返回 HTML 错误页之类：必须收敛成 LLMError，否则调用方按
        # "模型层失败"分类的路径会漏掉它（svc 会把它归成 internal）。
        raise LLMError(f"响应不是合法 JSON：{response.text[:200]}") from exc


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


# ---------------- 流式（F3） ----------------

DeltaCallback = Callable[[str], None]


@dataclass(frozen=True)
class StreamState:
    """流式累加器的状态：**纯数据**，逐帧 fold 出来，便于按 fixture 断言。

    只保留 `parse_turn` 同样要用的字段，于是 `to_turn()` 的产物与非流式解析能逐字段
    比较（B9）。`tool_calls` 存元组而不是字典：状态不可变，fold 时才复制。
    """

    content: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    usage: Usage | None = None
    model: str = ""
    finish_reason: str = ""

    def to_turn(self) -> Turn:
        tool_calls = [dict(call) for call in self.tool_calls]
        # 与 parse_turn 同形：没有工具调用时不给 message 加这个键。
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if tool_calls:
            message["tool_calls"] = [dict(call) for call in tool_calls]
        return Turn(
            message=message,
            text=self.content,
            tool_calls=tool_calls,
            usage=self.usage or Usage(0, 0, 0),
            model=self.model,
            finish_reason=self.finish_reason,
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
        tool_calls=tuple(calls),
        usage=_usage_of(chunk) if chunk.get("usage") else state.usage,
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


def _decode_stream_frame(payload: str) -> dict[str, Any]:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LLMError(f"流式响应帧不是合法 JSON：{payload[:200]}") from exc
    if not isinstance(decoded, dict):
        raise LLMError(f"流式响应帧不是对象：{payload[:200]}")
    return decoded


def iter_sse_events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """SSE 行流 → JSON 帧。心跳注释、空帧与 `[DONE]` 都跳过。

    只依赖**行**的切分：半行缓冲由 `httpx.Response.iter_lines()` 负责，所以这里不必
    再实现一次「分块边界切在 JSON 中间」的容错——那是客户端 SSE 解析器的活（§5.4 第 6 条）。
    """
    buffered: list[str] = []
    for line in lines:
        if line == "":  # 空行 = 一帧结束
            payload = "\n".join(buffered)
            buffered.clear()
            if not payload.strip():
                continue
            if payload.strip() == "[DONE]":
                return
            yield _decode_stream_frame(payload)
            continue
        if line.startswith(":"):  # 心跳 / 注释
            continue
        if line.startswith("data:"):
            value = line[len("data:") :]
            buffered.append(value[1:] if value.startswith(" ") else value)
        # `event:` / `id:` / `retry:` 当前不用：OpenAI 兼容流只用 data 行。
    payload = "\n".join(buffered)  # 没有末尾空行也不丢最后一帧
    if payload.strip() and payload.strip() != "[DONE]":
        yield _decode_stream_frame(payload)


def stream_completion(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    on_delta: DeltaCallback | None = None,
    client: httpx.Client | None = None,
) -> Turn:
    """流式调用，返回与 `chat_completion` **同形**的 `Turn`。

    `on_delta` 每收到一段正文就回调一次；它做什么与本模块无关（svc 把它接到事件流上）。
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

    owns_client = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    state = StreamState()
    try:
        with http.stream(
            "POST", config.chat_completions_url, json=request, headers=headers
        ) as response:
            if response.status_code != 200:
                response.read()
                if _prompt_too_long(response):
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
    except httpx.HTTPError as exc:
        raise LLMError(f"请求 {config.chat_completions_url} 失败：{exc}") from exc
    finally:
        if owns_client:
            http.close()

    return state.to_turn()


def ask(config: Config, prompt: str, *, client: httpx.Client | None = None) -> Reply:
    turn = chat_completion(
        config, [{"role": "user", "content": prompt}], client=client
    )
    return Reply(text=turn.text, usage=turn.usage, model=turn.model)
