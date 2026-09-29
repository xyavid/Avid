"""Gemini generateContent（原生协议，不走 OpenAI 兼容端点）。

为什么值得单开一条路：Gemini 的 function calling 与 usageMetadata（含
cachedContentTokenCount / thoughtsTokenCount）在原生 API 下才是完整语义。
请求从**仓库内部的 OpenAI 形状 messages** 转换过来，响应转回同形的
:class:`Turn`——循环与压缩管线不感知协议差异。

Gemini 与 OpenAI 形状的两处结构性差异，转换规则如下：

* **工具调用没有 id**，只有函数名。Turn 里给每次 functionCall 合成稳定 id
  （``call_0``、``call_1``…按出现顺序）；反向转换（role:"tool" 的结果消息）
  用先前 assistant 消息里的 id→名字表把 ``tool_call_id`` 还原成函数名——
  ``functionResponse`` 靠名字回给，这是 Gemini 的原生匹配方式。
* **连续同角色内容合并**成一个 contents 条目（工具结果与紧随的用户消息、
  多条工具结果），避免对角色交替的任何赌注。

usage 直接交给 ``ai/usage.py`` 的 gemini 方言（promptTokenCount 含缓存命中，
与 OpenAI 口径一致）。
"""

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
from ..transport import RetryPolicy, send, send_stream, shared_client

# finishReason → OpenAI 兼容口径。未知的原样透传（小写化）。
_FINISH_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
}


def generate_url(config: Config, *, streaming: bool = False) -> str:
    base = config.base_url.rstrip("/")
    action = "streamGenerateContent?alt=sse" if streaming else "generateContent"
    return f"{base}/models/{config.model}:{action}"


def _headers(config: Config) -> dict[str, str]:
    return {"x-goog-api-key": config.api_key, "Content-Type": "application/json"}


def _text_of(raw: Any) -> str:
    return raw if isinstance(raw, str) else ""


def _parts_of(message: dict[str, Any]) -> list[dict[str, Any]]:
    """一条 OpenAI 形状消息 → Gemini parts（tool 消息除外，见 build_contents）。"""
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
    """OpenAI 形状 messages → Gemini contents。

    id→名字表随 assistant 消息累积：tool 消息的 ``tool_call_id`` 在这里还原成
    函数名（``functionResponse`` 按名字匹配）。连续同角色合并成一条 contents。
    """
    out: list[dict[str, Any]] = []
    id_to_name: dict[str, str] = {}

    def append(role: str, parts: list[dict[str, Any]]) -> None:
        if not parts:
            return
        if out and out[-1]["role"] == role:
            out[-1]["parts"].extend(parts)
        else:
            out.append({"role": role, "parts": parts})

    for message in messages:
        role = message.get("role")
        if role == "assistant":
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
                            # response 必须是 JSON 对象，文本结果包一层 result。
                            "response": {"result": _text_of(message.get("content"))},
                        }
                    }
                ],
            )
        else:  # user
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
        # 合成稳定 id：仓库内部按 id 配对 tool 消息，转回 Gemini 时按名字还原。
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
    """吃一条 parts，返回 (正文增量, 思维链增量)。"""
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
    """流式 chunks（或一整个非流式响应）→ 同形 Turn。"""

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
