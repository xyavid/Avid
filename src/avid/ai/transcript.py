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


# 每条消息的固定结构开销（role / 花括号 / 分隔符的粗估）。
_MESSAGE_OVERHEAD = 16


def message_chars(message: dict[str, Any]) -> int:
    """单条消息的粗估字符数：正文 + tool_calls 序列化 + 结构开销。"""
    total = len(text_of(message.get("content"))) + _MESSAGE_OVERHEAD
    calls = message.get("tool_calls")
    if calls:
        total += len(json.dumps(calls, ensure_ascii=False, default=str))
    return total


def estimate_chars(messages: list[dict[str, Any]]) -> int:
    """粗估上下文字符数：正文 + tool_calls 序列化 + 每条的结构开销。

    这是**全量**算法（改完 5 处调用点前只用于对照）。`Transcript` 自己维护增量值，
    每轮跑多次的编排（`context.prepare` 一轮至少算 3 次）不再重复扫全表。
    """
    return sum(message_chars(message) for message in messages)


class Transcript:
    """一份结构始终合法的消息序列。"""

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._messages: list[dict[str, Any]] = messages if messages is not None else []
        problems = validate(self._messages)
        if problems:
            raise TranscriptError("初始 messages 结构非法：" + "；".join(problems))
        # 增量维护的两个成本量：改动时按差量更新，读取是 O(1)。
        # 为什么值得维护：`context.prepare` 每轮至少算三次字符数（③④ 各自判断），
        # `micro_compact` 还在循环里每次落盘后重算——全量扫描是 O(消息数 × 轮数)，
        # 而内容其实只在 append / set_content / 结构改动时变。
        self._chars = 0
        self._tool_chars = 0
        self._recompute_costs()

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
        """工具结果正文总字符数（① 的预算依据）。O(1)：随写入增量维护。"""
        return self._tool_chars

    def estimate_chars(self) -> int:
        """整份上下文的粗估字符数（③④ 的阈值依据）。O(1)。"""
        return self._chars

    def _recompute_costs(self) -> None:
        """结构改动后重算（append/set_content 走差量，只有整体替换类才需要它）。"""
        self._chars = estimate_chars(self._messages)
        self._tool_chars = sum(
            len(self.text_at(index)) for index in self.tool_indexes()
        )

    def _track(self, message: dict[str, Any], sign: int = 1) -> None:
        """把一条消息的成本计入/移出缓存。"""
        self._chars += sign * message_chars(message)
        if message.get("role") == "tool":
            self._tool_chars += sign * len(text_of(message.get("content")))

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
        self._track(message)

    def append_many(self, messages: Iterable[dict[str, Any]]) -> None:
        incoming = list(messages)
        self._messages.extend(incoming)
        for message in incoming:
            self._track(message)

    def set_content(self, index: int, content: str) -> None:
        """改单条内容（落盘留路径、输入注入用）。不改结构，因此无需校验。"""
        if not 0 <= index < len(self._messages):
            raise TranscriptError(f"消息下标越界：{index}")
        message = self._messages[index]
        self._track(message, sign=-1)
        message["content"] = content
        self._track(message)

    def replace_all(self, messages: list[dict[str, Any]]) -> None:
        """整体替换（摘要替换历史用）。会破坏结构就抛错，不改动现状。"""
        candidate = list(messages)
        self._accept(candidate, "整体替换")
        self._messages[:] = candidate
        self._recompute_costs()

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
        self._recompute_costs()

    @staticmethod
    def _accept(candidate: list[dict[str, Any]], what: str) -> None:
        problems = validate(candidate)
        if problems:
            raise TranscriptError(f"{what} 会破坏消息结构：" + "；".join(problems))
