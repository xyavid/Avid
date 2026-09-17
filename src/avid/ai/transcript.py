"""messages 的唯一所有者。

结构不变量——每个 assistant 的 ``tool_calls`` 都必须有配对的 ``tool`` 结果——
由这里守护：只有 ``replace_all`` 与 ``splice`` 能改变结构，两者都先构造候选、
校验通过才落地。压缩、输入注入、TODO 提醒、Stop nudge 都只能走它的方法，
不再有"五个地方各改一遍 list、改完再事后校验"。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


class TranscriptError(ValueError):
    """这次改动会破坏消息结构，已被拒绝。"""


def text_of(value: Any) -> str:
    """把消息字段转成用于估算与落盘的文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def validate(messages: list[dict[str, Any]]) -> list[str]:
    """返回结构违规列表；空列表表示合法。"""
    problems: list[str] = []
    expected: set[str] = set()

    for index, message in enumerate(messages):
        if message.get("role") == "tool":
            call_id = str(message.get("tool_call_id"))
            if call_id not in expected:
                problems.append(f"#{index} 的 tool 结果没有对应的 tool_call：{call_id}")
            else:
                expected.discard(call_id)
            continue

        if expected:
            problems.append(f"#{index} 之前有 {len(expected)} 个 tool_call 没收到结果")
            expected = set()

        for call in message.get("tool_calls") or []:
            expected.add(str(call.get("id")))

    if expected:
        problems.append(f"结尾有 {len(expected)} 个 tool_call 没收到结果")
    return problems


def estimate_chars(messages: list[dict[str, Any]]) -> int:
    """粗估上下文字符数：正文 + tool_calls 序列化 + 每条的结构开销。"""
    total = 0
    for message in messages:
        total += len(text_of(message.get("content"))) + 16
        calls = message.get("tool_calls")
        if calls:
            total += len(json.dumps(calls, ensure_ascii=False, default=str))
    return total


class Transcript:
    """一份结构始终合法的消息序列。"""

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._messages: list[dict[str, Any]] = messages if messages is not None else []
        problems = validate(self._messages)
        if problems:
            raise TranscriptError("初始 messages 结构非法：" + "；".join(problems))

    # ---------- 读 ----------

    def __len__(self) -> int:
        return len(self._messages)

    def as_messages(self) -> list[dict[str, Any]]:
        """传给模型调用的副本。

        顶层 dict 是拷贝，改 key 不影响内部状态；嵌套的 ``tool_calls`` 列表是共享的，
        不要原地改它。
        """
        return [dict(message) for message in self._messages]

    def text_at(self, index: int) -> str:
        return text_of(self._messages[index].get("content"))

    def tool_indexes(self) -> list[int]:
        return [i for i, m in enumerate(self._messages) if m.get("role") == "tool"]

    def last_user_index(self) -> int | None:
        for index in range(len(self._messages) - 1, -1, -1):
            if self._messages[index].get("role") == "user":
                return index
        return None

    def tool_chars(self) -> int:
        return sum(len(self.text_at(index)) for index in self.tool_indexes())

    def estimate_chars(self) -> int:
        return estimate_chars(self._messages)

    def validate(self) -> list[str]:
        return validate(self._messages)

    def is_safe_boundary(self, index: int) -> bool:
        """在 index 处切开是否安全：前一条没有待回应的 tool_calls，且 index 处不是 tool。"""
        if index <= 0 or index >= len(self._messages):
            return True
        if self._messages[index].get("role") == "tool":
            return False
        return not self._messages[index - 1].get("tool_calls")

    # ---------- 写 ----------

    def append(self, message: dict[str, Any]) -> None:
        self._messages.append(message)

    def append_many(self, messages: Iterable[dict[str, Any]]) -> None:
        self._messages.extend(messages)

    def set_content(self, index: int, content: str) -> None:
        """改单条内容（落盘留路径、输入注入用）。不改结构，因此无需校验。"""
        if not 0 <= index < len(self._messages):
            raise TranscriptError(f"消息下标越界：{index}")
        self._messages[index]["content"] = content

    def replace_all(self, messages: list[dict[str, Any]]) -> None:
        """整体替换（摘要替换历史用）。会破坏结构就抛错，不改动现状。"""
        candidate = list(messages)
        self._accept(candidate, "整体替换")
        self._messages[:] = candidate

    def splice(
        self,
        start: int,
        stop: int,
        replacement: Iterable[dict[str, Any]] = (),
    ) -> None:
        """把 ``[start, stop)`` 换成 replacement。会破坏结构就抛错，不改动现状。"""
        if not (0 <= start <= stop <= len(self._messages)):
            raise TranscriptError(f"splice 区间非法：{start}..{stop}")
        candidate = self._messages[:start] + list(replacement) + self._messages[stop:]
        self._accept(candidate, f"裁剪 {start}..{stop}")
        self._messages[:] = candidate

    @staticmethod
    def _accept(candidate: list[dict[str, Any]], what: str) -> None:
        problems = validate(candidate)
        if problems:
            raise TranscriptError(f"{what} 会破坏消息结构：" + "；".join(problems))
