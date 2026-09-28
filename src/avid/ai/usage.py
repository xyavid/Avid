"""provider usage → 统一口径（provider adapter）。

本模块是**唯一**认识各家 usage 字段名的地方。它只依赖标准库：不 import 内核其它
模块，也不认识 httpx——这样"哪家怎么算缓存"这件事可以在 fixture 上单独受测。

统一后的三个问题各自只有一个答案：

* 这次请求的输入量 → ``Usage.prompt_tokens``
* 有据可查的缓存读/写 → ``cache_read_tokens`` / ``cache_write_tokens``（``None`` = 这家
  或这次上报里没有这个数，**不是** 0）
* 命中率 → :func:`hit_ratio`，分母是含命中部分的输入总量

各家的等价写法（见 :data:`_DIALECTS` 与 ``tests/test_usage.py`` 的 fixture）：

============  ====================================================  ==================
provider      字段                                                  统一后
============  ====================================================  ==================
OpenAI        ``prompt_tokens_details.cached_tokens``               ``cache_read``
OpenAI 兼容   ``cached_tokens`` / ``prompt_cache_hit_tokens``       ``cache_read``
（DeepSeek 等）
Anthropic     ``cache_read_input_tokens``                           ``cache_read``
Anthropic     ``cache_creation_input_tokens``                       ``cache_write``
Gemini        ``usageMetadata.cachedContentTokenCount``             ``cache_read``
============  ====================================================  ==================

两处口径差异是**有意的**，改了就不再是同一条账：

* Anthropic 的 ``input_tokens`` **不含**缓存部分，所以 ``prompt_tokens`` 是
  ``input + read + write`` 的和；OpenAI 兼容的 ``prompt_tokens`` 本来就含缓存部分，
  直接取用。
* 非 Anthropic 家都没有"写入缓存"这个计数（自动缓存不收写入费），
  ``cache_write_tokens`` 因此是 ``None``，界面显示「—」而不是 0。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Usage:
    """一次模型调用的用量。``prompt_tokens`` 含缓存命中的部分（同一口径见模块文档）。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    # None = 这次上报里没有这个数。不要用 0 冒充：0 是"确实一次都没命中"。
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    # 推理 token 是 ``completion_tokens`` 的**子集**（各家口径都是"含在输出里"），
    # 不是另加的一块。它存在的意义是解释"输出都花在哪了"：现场那条空正文响应的
    # 9466 个输出 token 全在思维链上，只看 completion 看不出这一层。
    reasoning_tokens: int | None = None


def _as_int(value: Any) -> int | None:
    """宽松取整：网关把计数写成 ``"12"`` 或 ``12.0`` 都算数，其它一律 None。

    ``bool`` 要显式排掉——``True`` 是 ``int`` 的子类，不排会变成 1 这种假数字。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _nested_int(raw: Mapping[str, Any] | None, key: str) -> int | None:
    if not isinstance(raw, Mapping):
        return None
    return _as_int(raw.get(key))


def usage_payload(data: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """从响应信封里取出 usage 对象。

    取不到信封就**把入参当 usage 本身**：单测与"上游只回一小段 JSON"的场景都靠这条，
    否则调用方要自己拼一层 ``{"usage": ...}``。
    """
    if not isinstance(data, Mapping):
        return {}
    for key in ("usage", "usageMetadata", "usage_metadata"):
        found = data.get(key)
        if isinstance(found, Mapping):
            return found
    return data


def _openai_like(payload: Mapping[str, Any]) -> Usage | None:
    prompt = _as_int(payload.get("prompt_tokens"))
    if prompt is None:
        return None
    completion = _as_int(payload.get("completion_tokens")) or 0
    total = _as_int(payload.get("total_tokens")) or (prompt + completion)
    cached = _nested_int(payload.get("prompt_tokens_details"), "cached_tokens")
    if cached is None:
        cached = _as_int(payload.get("cached_tokens"))
    if cached is None:
        cached = _as_int(payload.get("prompt_cache_hit_tokens"))
    # 推理 token 两种写法都见过：OpenAI 兼容放在 details 里，有的网关直接平铺。
    reasoning = _nested_int(payload.get("completion_tokens_details"), "reasoning_tokens")
    if reasoning is None:
        reasoning = _as_int(payload.get("reasoning_tokens"))
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cache_read_tokens=cached,
        cache_write_tokens=None,
        reasoning_tokens=reasoning,
    )


def _anthropic_like(payload: Mapping[str, Any]) -> Usage | None:
    inbound = _as_int(payload.get("input_tokens"))
    if inbound is None:
        return None
    read = _as_int(payload.get("cache_read_input_tokens"))
    write = _as_int(payload.get("cache_creation_input_tokens"))
    completion = _as_int(payload.get("output_tokens")) or 0
    # input_tokens 不含缓存部分，所以总量要加回来（模块文档里的第二条口径差异）。
    prompt = inbound + (read or 0) + (write or 0)
    # Anthropic 把思维链算在 output_tokens 里，没有单独的计数字段：不猜，留 None。
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cache_read_tokens=read,
        cache_write_tokens=write,
    )


def _gemini_like(payload: Mapping[str, Any]) -> Usage | None:
    prompt = _as_int(payload.get("promptTokenCount"))
    if prompt is None:
        return None
    completion = _as_int(payload.get("candidatesTokenCount")) or 0
    total = _as_int(payload.get("totalTokenCount")) or (prompt + completion)
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cache_read_tokens=_as_int(payload.get("cachedContentTokenCount")),
        cache_write_tokens=None,
        reasoning_tokens=_as_int(payload.get("thoughtsTokenCount")),
    )


#: 方言按**判别字段**排他，顺序即优先级。加一家就加一行与一组 fixture。
_DIALECTS = (
    ("openai", _openai_like),
    ("anthropic", _anthropic_like),
    ("gemini", _gemini_like),
)


def normalize_usage(data: Mapping[str, Any] | None) -> Usage:
    """响应（或裸 usage 对象）→ :class:`Usage`。认不出来就是全零。

    认不出来**不报错**：usage 只是观测，不该让一次成功的模型调用失败。调用方
    在界面上显示「—」，比抛异常把整轮丢掉合理。
    """
    payload = usage_payload(data)
    for _, parse in _DIALECTS:
        parsed = parse(payload)
        if parsed is not None:
            return parsed
    # 连 prompt_tokens 都没有：仍尽量把总数留下，其余归零。
    total = _as_int(payload.get("total_tokens")) or _as_int(payload.get("totalTokenCount"))
    if total:
        return Usage(prompt_tokens=0, completion_tokens=0, total_tokens=total)
    return Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)


def hit_ratio(usage: Usage) -> float | None:
    """缓存命中率 = 命中读 / 输入总量。

    分母用 ``prompt_tokens``（含命中部分）而不是"未命中部分"：前者是这次请求真实的
    输入规模，各家的 ``prompt_tokens`` 都已按同一口径归一（见模块文档），所以这个
    比值跨 provider 可比。

    没有缓存数据（``cache_read_tokens is None``）或输入为 0 时返回 ``None``：**没有这个
    数**与"命中率 0%"是两件事，界面据此显示「—」而不是 0%。
    """
    if usage.cache_read_tokens is None or usage.prompt_tokens <= 0:
        return None
    # 上游谎报（命中比总量还大）时按 1.0 截断：界面上的"超过 100%"只会被当成 bug。
    return min(1.0, usage.cache_read_tokens / usage.prompt_tokens)


__all__ = ["Usage", "hit_ratio", "normalize_usage", "usage_payload"]
