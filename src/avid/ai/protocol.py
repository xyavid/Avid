"""协议层的共享词表：Turn / Reply / 错误类型 / SSE 解码。

三个 provider 模块（openai_compat / anthropic / gemini）与门面 `client.py` 都从这里
拿同一套类型，于是「流式与非流式同形」「跨 provider 同形」有单一权威可对照。

本模块不认识 httpx（除了 `prompt_too_long` 收的是已取出的状态码与正文文本），
不认识任何一家的字段名——那属于各自的 provider 模块。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from .usage import Usage, normalize_usage

#: 默认**不设**输出上限（None = 请求体里不带 max_tokens 字段，上限交给服务商）。
#: 曾经写死 8000：推理模型的可见输出与思维链争同一份配额，被吃光时正文为空、
#: finish_reason=length，而没有 tool_calls 的轮次会被循环当成「模型答完了」——一次
#: 空答复就这样「成功」收尾（现场会话 01a0d277，见 dev/diagnosis/2026-09-27-stop-and-compaction.md）。
#: 需要复现某次实验的用量时，调用方仍然可以显式传一个数字。
DEFAULT_MAX_TOKENS: int | None = None


class LLMError(Exception):
    """调用失败。信息包含状态码与响应正文片段，便于定位。"""


class PromptTooLongError(LLMError):
    """请求超出模型上下文长度。循环据此做一次兜底压缩后重试。"""


# 各家措辞不同，命中任一即可判定为上下文超限。Gemini 的措辞是它的 REST 错误
# 原文（"input token count ... exceeds the maximum number of tokens allowed"）。
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


def prompt_too_long(status_code: int, body: str) -> bool:
    """状态码与响应正文 → 是否上下文超限。

    状态码必须对（400/413/422）：500 里出现 "context length" 字样是服务端故障，
    不是可恢复的输入问题（.test_server_error_with_overflow_wording_is_not_treated_as_overflow）。
    """
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
    """一轮模型响应。message 是清洗过的 assistant 消息，可直接追加进 messages。"""

    message: dict[str, Any]
    text: str
    tool_calls: list[dict[str, Any]]
    usage: Usage
    model: str
    finish_reason: str
    # 思维链原文（A2）。**不进 message**：回传会污染下一轮，多数端点也拒收这些字段。
    # 它不参与"模型答了什么"的判断，但决定了用户能不能看懂"这一轮为什么没有正文"。
    reasoning: str = ""


DeltaCallback = Callable[[str], None]


def content_text(raw: Any) -> str:
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


def usage_of(data: Any) -> Usage:
    """响应信封 → 统一口径。方言识别全在 ``ai/usage.py``（流式与非流式共用它）。"""
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
    """SSE 行流 → JSON 帧。心跳注释、空帧与 `[DONE]` 都跳过。

    只依赖**行**的切分：半行缓冲由 `httpx.Response.iter_lines()` 负责，所以这里不必
    再实现一次「分块边界切在 JSON 中间」的容错——那是客户端 SSE 解析器的活（§5.4 第 6 条）。
    Anthropic 与 Gemini 的 SSE 也是 data 行（Gemini 走 `?alt=sse`），共用这一个解码器。
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
        # `event:` / `id:` / `retry:` 当前不用：三家都只用 data 行携带载荷。
    payload = "\n".join(buffered)  # 没有末尾空行也不丢最后一帧
    if payload.strip() and payload.strip() != "[DONE]":
        yield _decode_stream_frame(payload)


def assistant_message(text: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """OpenAI 形状的 assistant 消息。没有工具调用时不给 message 加这个键。"""
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message
